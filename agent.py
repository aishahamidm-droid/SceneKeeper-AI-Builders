import os
import json
import re
import clickhouse_connect
from dotenv import load_dotenv
from google import genai
from google.genai import types
from mcp_client import run_mcp_query
from gemini_reliability import generate_content_with_retry

load_dotenv()

# Initialize Gemini Client
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def validate_production_sql(sql: str, active_production_id: str):
    """
    Validates that any query against production tables explicitly filters by active_production_id.
    Raises ValueError if unscoped.
    """
    sk_tables = ["scenes", "character_states", "prop_tracker"]
    pattern = r"\b(" + "|".join(sk_tables) + r")\b"

    if re.search(pattern, sql, re.IGNORECASE):
        expected_s = f"production_id = '{active_production_id}'"
        expected_d = f'production_id = "{active_production_id}"'

        clean_sql = " ".join(sql.split())

        if expected_s not in clean_sql and expected_d not in clean_sql:
            raise ValueError(
                f"GUARDRAIL REJECTION: Query against production tables must explicitly filter by {expected_s}. "
                f"Attempted Query: {sql}"
            )

def execute_clickhouse_write(query: str):
    """Executes DDL/INSERT statements directly via clickhouse-connect."""
    client = clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8443")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        secure=os.getenv("CLICKHOUSE_SECURE", "true").lower() == "true",
    )
    return client.command(query)

def execute_clickhouse_query(
    sql_query: str,
    active_production_id: str = "demo_sample"
) -> str:
    """Executes a production-scoped SQL query via the verified mcp-clickhouse runtime."""
    try:
        validate_production_sql(sql_query, active_production_id)
        result = run_mcp_query(sql_query)
        return str(result)
    except ValueError as e:
        return f"SQL Execution Blocked: {str(e)}"
    except Exception as e:
        return f"MCP ClickHouse Error: {str(e)}"
# System instruction for SceneKeeper agent
SYSTEM_INSTRUCTION = """
You are SceneKeeper, an AI script supervisor and continuity agent for film productions.
Your task is to help filmmakers maintain strict continuity across scenes, props, character states, and wardrobe.
You have access to a ClickHouse database named 'scenekeeper' via the official mcp-clickhouse integration.
The database contains 3 tables:
1. scenekeeper.scenes (scene_id, scene_number, title, location, time_of_day, script_summary)
2. scenekeeper.character_states (state_id, scene_id, character_name, wardrobe_description, physical_condition, knowledge_state, notes)
3. scenekeeper.prop_tracker (prop_id, scene_id, prop_name, held_by, condition, location_in_set)

When the user asks a continuity or production question:
1. Construct valid ClickHouse SQL queries to fetch relevant records.
2. Call the execute_clickhouse_query function to retrieve the data.
3. Analyze the results for continuity inconsistencies (e.g., mismatched wardrobe, moving injuries, or prop location errors).
4. Provide a clear, actionable report for the production team.
Request independent queries in the same tool-call turn when possible. Reuse evidence
already returned in this conversation; request more data when it is missing, incomplete,
or a query failed. Do not repeat successful queries unless fresh data is needed.
"""

def generate_with_retry(contents, tools, max_retries=3, delay=2):
    """Retry this Gemini request within the shared quota/backoff limits."""
    return generate_content_with_retry(
        gemini_client,
        max_attempts=max_retries,
        initial_delay=delay,
        model="gemini-3.6-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=tools,
            temperature=0.1
        )
    )

def query_agent(
    prompt: str,
    active_production_id: str = "demo_sample",
    max_turns: int = 10
):
    print(f"\n[User Query]: {prompt}\n" + "-"*50)
    
    tools = [types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="execute_clickhouse_query",
                description="Runs a SQL query against ClickHouse via the official mcp-clickhouse MCP server.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "sql_query": types.Schema(
                            type=types.Type.STRING,
                            description="The SQL query to execute in ClickHouse."
                        )
                    },
                    required=["sql_query"]
                )
            )
        ]
    )]

    # Start conversation contents
    contents = [
    types.Content(
        role="user",
        parts=[
            types.Part.from_text(
                text=(
                    f"ACTIVE PRODUCTION ID: '{active_production_id}'\n"
                    f"Every SQL query against scenekeeper.scenes, "
                    f"scenekeeper.character_states, or scenekeeper.prop_tracker "
                    f"MUST explicitly filter production_id = '{active_production_id}'.\n\n"
                    f"USER REQUEST:\n{prompt}"
                )
            )
        ]
    )
]

    for turn in range(max_turns):
        response = generate_with_retry(contents, tools)

        candidate = response.candidates[0]
        contents.append(candidate.content)

        function_calls = [part.function_call for part in candidate.content.parts if part.function_call]
        
        if not function_calls:
            print(f"[SceneKeeper Final Response]:\n{response.text}")
            return response.text

        # Handle function execution turn
        function_responses = []
        for fn_call in function_calls:
            if fn_call.name == "execute_clickhouse_query":
                sql = fn_call.args.get("sql_query") or fn_call.args.get("query")
                print(f"[Executing via mcp-clickhouse MCP Server]: {sql}")
                db_result = execute_clickhouse_query(
    sql,
    active_production_id=active_production_id
)
                print(f"[mcp-clickhouse Result]: {db_result}\n")
                
                function_responses.append(
                    types.Part.from_function_response(
                        name="execute_clickhouse_query",
                        response={"result": db_result}
                    )
                )

        contents.append(types.Content(role="user", parts=function_responses))

        print("[Warning]: Reached maximum turns. Forcing final synthesis...")
    final_response = generate_with_retry(
        contents + [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text="Summarize all gathered data and produce the final continuity report now."
                    )
                ]
            )
        ],
        tools=[]
    )
    return final_response.text
if __name__ == "__main__":
    query_agent("Check for any wardrobe or prop continuity errors for John between Scene 1 and Scene 2.")
