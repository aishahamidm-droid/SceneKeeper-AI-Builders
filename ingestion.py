import os
import re
import uuid
import clickhouse_connect
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from gemini_reliability import generate_content_with_retry

# ---------------------------------------------------------------------------
# Pydantic Schemas for Structured Gemini Extraction
# ---------------------------------------------------------------------------

class CharacterStateExtracted(BaseModel):
    scene_number: str = Field(description="Scene number string, e.g. '1', '2', '2A'")
    character_name: str
    wardrobe_description: str = Field(default="")
    physical_condition: str = Field(default="")
    knowledge_state: str = Field(default="")
    notes: str = Field(default="")

class PropTrackedExtracted(BaseModel):
    scene_number: str = Field(description="Scene number string, e.g. '1', '2', '2A'")
    prop_name: str
    held_by: str = Field(default="")
    condition: str = Field(default="")
    location_in_set: str = Field(default="")

class SceneExtracted(BaseModel):
    scene_number: str = Field(description="Scene number string, e.g. '1', '2', '2A'")
    title: str = Field(default="")
    location: str = Field(default="")
    time_of_day: str = Field(default="")
    script_summary: str = Field(default="")

class ScreenplayExtraction(BaseModel):
    production_title: str = Field(description="Title of the screenplay or production")
    scenes: list[SceneExtracted]
    character_states: list[CharacterStateExtracted]
    props: list[PropTrackedExtracted]

# ---------------------------------------------------------------------------
# ClickHouse Direct Write Connection
# ---------------------------------------------------------------------------

def get_clickhouse_client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8443")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        secure=os.getenv("CLICKHOUSE_SECURE", "true").lower() == "true",
    )

def _get_max_ids(client) -> tuple[int, int, int]:
    """Retrieve current MAX UInt32 IDs for scenes, character_states, and prop_tracker."""
    res_scenes = client.query("SELECT max(scene_id) FROM scenekeeper.scenes").result_rows
    res_chars = client.query("SELECT max(state_id) FROM scenekeeper.character_states").result_rows
    res_props = client.query("SELECT max(prop_id) FROM scenekeeper.prop_tracker").result_rows

    max_scene_id = res_scenes[0][0] if (res_scenes and res_scenes[0][0] is not None) else 0
    max_state_id = res_chars[0][0] if (res_chars and res_chars[0][0] is not None) else 0
    max_prop_id = res_props[0][0] if (res_props and res_props[0][0] is not None) else 0

    return max_scene_id, max_state_id, max_prop_id

# ---------------------------------------------------------------------------
# Core Ingestion Workflow
# ---------------------------------------------------------------------------

def ingest_txt_screenplay(script_text: str) -> dict:
    """
    1. Calls Gemini via google-genai structured outputs to extract continuity facts.
    2. Allocates safe sequential UInt32 IDs.
    3. Writes extracted data into ClickHouse via clickhouse-connect.
    4. Registers metadata in scenekeeper.productions.
    """
    # Initialize Gemini Client
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is missing.")

    client = genai.Client(api_key=api_key)

    system_instruction = (
        "You are an expert film script supervisor and continuity logger. "
        "Extract structured scenes, character states, and props strictly from the provided screenplay text. "
        "Do not invent facts that are not explicitly in the script. Missing details must be empty strings."
    )

    prompt = f"Analyze the following screenplay text and extract all scenes, character states, and tracked props:\n\n{script_text}"

    # Structured Output Request
    response = generate_content_with_retry(
        client,
        model="gemini-3.6-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=ScreenplayExtraction,
            temperature=0.1,
        ),
    )

    extracted: ScreenplayExtraction = response.parsed

    # Generate a clean production_id
    sanitized_title = re.sub(r"[^a-zA-Z0-9_]", "_", extracted.production_title.lower()).strip("_")
    short_uuid = uuid.uuid4().hex[:6]
    production_id = f"prod_{sanitized_title[:15]}_{short_uuid}"

    # Connect to ClickHouse for Direct Writes
    ch = get_clickhouse_client()
    max_scene_id, max_state_id, max_prop_id = _get_max_ids(ch)

    # 1. Map scene numbers to new sequential UInt32 scene_ids
    scene_number_to_id = {}
    scenes_to_insert = []
    
    current_scene_id = max_scene_id
    for sc in extracted.scenes:
        current_scene_id += 1
        scene_number_to_id[sc.scene_number] = current_scene_id
        scenes_to_insert.append([
            current_scene_id,
            sc.scene_number,
            sc.title,
            sc.location,
            sc.time_of_day,
            sc.script_summary,
            production_id
        ])

    # Fallback mapping if LLM returns a character state or prop referencing an unlisted scene number
    if scenes_to_insert:
        fallback_scene_id = scenes_to_insert[0][0]
    else:
        current_scene_id += 1
        fallback_scene_id = current_scene_id
        scenes_to_insert.append([
            fallback_scene_id,
            "1",
            "General Scene",
            "UNKNOWN",
            "DAY",
            "Auto-generated container scene",
            production_id
        ])
        scene_number_to_id["1"] = fallback_scene_id

    # 2. Prepare character_states rows
    char_states_to_insert = []
    current_state_id = max_state_id
    for cs in extracted.character_states:
        current_state_id += 1
        linked_scene_id = scene_number_to_id.get(cs.scene_number, fallback_scene_id)
        char_states_to_insert.append([
            current_state_id,
            linked_scene_id,
            cs.character_name,
            cs.wardrobe_description,
            cs.physical_condition,
            cs.knowledge_state,
            cs.notes,
            production_id
        ])

    # 3. Prepare prop_tracker rows
    props_to_insert = []
    current_prop_id = max_prop_id
    for pr in extracted.props:
        current_prop_id += 1
        linked_scene_id = scene_number_to_id.get(pr.scene_number, fallback_scene_id)
        props_to_insert.append([
            current_prop_id,
            linked_scene_id,
            pr.prop_name,
            pr.held_by,
            pr.condition,
            pr.location_in_set,
            production_id
        ])

    # 4. Insert records into ClickHouse
    if scenes_to_insert:
        ch.insert(
            "scenekeeper.scenes",
            scenes_to_insert,
            column_names=["scene_id", "scene_number", "title", "location", "time_of_day", "script_summary", "production_id"]
        )

    if char_states_to_insert:
        ch.insert(
            "scenekeeper.character_states",
            char_states_to_insert,
            column_names=["state_id", "scene_id", "character_name", "wardrobe_description", "physical_condition", "knowledge_state", "notes", "production_id"]
        )

    if props_to_insert:
        ch.insert(
            "scenekeeper.prop_tracker",
            props_to_insert,
            column_names=["prop_id", "scene_id", "prop_name", "held_by", "condition", "location_in_set", "production_id"]
        )

    # 5. Record metadata in scenekeeper.productions
    production_title = extracted.production_title or "Uploaded Production"
    ch.insert(
        "scenekeeper.productions",
        [[
            production_id,
            production_title,
            len(scenes_to_insert),
            len(char_states_to_insert),
            len(props_to_insert)
        ]],
        column_names=["production_id", "title", "scene_count", "character_count", "prop_count"]
    )

    return {
        "production_id": production_id,
        "title": production_title,
        "scene_count": len(scenes_to_insert),
        "character_count": len(char_states_to_insert),
        "prop_count": len(props_to_insert)
    }
