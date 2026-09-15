import os
import asyncio
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

async def call_clickhouse_mcp_tool(sql_query: str) -> str:
    """Connects to mcp-clickhouse via stdio and executes a query using the official MCP protocol."""
    server_env = os.environ.copy()
    
    server_params = StdioServerParameters(
        command="mcp-clickhouse",
        args=[],
        env=server_env
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # Discover available tools
            tools_response = await session.list_tools()
            if not tools_response.tools:
                raise RuntimeError("No tools exposed by mcp-clickhouse server.")

            # Identify the query tool
            query_tool = next(
                (t for t in tools_response.tools if any(k in t.name.lower() for k in ["run_query", "query", "sql", "execute"])),
                tools_response.tools[0]
            )

            # Inspect input schema properties
            schema_props = {}
            if hasattr(query_tool, "input_schema") and query_tool.input_schema:
                if isinstance(query_tool.input_schema, dict):
                    schema_props = query_tool.input_schema.get("properties", {})
                elif hasattr(query_tool.input_schema, "properties"):
                    schema_props = query_tool.input_schema.properties or {}

            # Determine query parameter name (defaults to 'query')
            query_param_name = next(
                (param for param in ["query", "sql", "sql_query", "statement"] if param in schema_props),
                "query"
            )

            # Execute tool call over MCP stdio transport
            result = await session.call_tool(
                query_tool.name, 
                arguments={query_param_name: sql_query}
            )
            
            output_parts = []
            for item in result.content:
                output_parts.append(getattr(item, 'text', str(item)))
            return "\n".join(output_parts)

def run_mcp_query(sql_query: str) -> str:
    """Synchronous exported helper used by agent.py to query ClickHouse via MCP."""
    return asyncio.run(call_clickhouse_mcp_tool(sql_query))

async def discover_and_test_mcp():
    """Diagnostic runner for standalone script execution."""
    print("--- Executing Standalone MCP Test ---")
    res = await call_clickhouse_mcp_tool("SELECT version()")
    print("[MCP Server Execution Result]:\n", res)

if __name__ == "__main__":
    asyncio.run(discover_and_test_mcp())