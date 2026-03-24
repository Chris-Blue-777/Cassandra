import json
import os
from openai import OpenAI

client = OpenAI()

CASSANDRA_SYSTEM_PROMPT = """
You are Cassandra, the narrative orchestrator of a multi-character interactive story.

Your job:
- Write a reviewable draft for the user
- Return a lean scene state update
- Return pending intents as a top-level field

Rules:
- Keep state minimal and non-contradictory
- Do not maintain large off-screen lists
- pending_intents must be a top-level field
- Output must match the provided schema exactly
"""

CASSANDRA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "draft": {
            "type": "string"
        },
        "scene_state_update": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "location": {
                    "type": "string"
                },
                "cast": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string"},
                            "presence": {
                                "type": "string",
                                "enum": ["active", "nearby"]
                            },
                            "position": {"type": "string"}
                        },
                        "required": ["name", "presence", "position"]
                    }
                }
            },
            "required": ["location", "cast"]
        },
        "pending_intents": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "purpose": {"type": "string"},
                    "tone": {"type": "string"},
                    "next": {"type": "string"}
                },
                "required": ["name", "purpose", "tone", "next"]
            }
        }
    },
    "required": ["draft", "scene_state_update", "pending_intents"]
}

def build_cassandra_input(world, scene_state, user_input):
    payload = {
        "active_world": {
            "name": world.name,
            "description": world.description,
        },
        "current_scene_state": {
            "location": scene_state.location,
            "cast": scene_state.cast_json,
            "pending_intents": scene_state.pending_intents_json,
        },
        "user_input": user_input,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def call_cassandra(world, scene_state, user_input):
    response = client.responses.create(
        model="gpt-5.4",
        instructions=CASSANDRA_SYSTEM_PROMPT,
        input=build_cassandra_input(world, scene_state, user_input),
        text={
            "format": {
                "type": "json_schema",
                "name": "cassandra_scene_response",
                "strict": True,
                "schema": CASSANDRA_SCHEMA,
            }
        },
    )

    if not response.output_text:
        raise ValueError("Cassandra returned no output text")

    data = json.loads(response.output_text)

    return data


def call_cassandra_revision(world, scene_state, current_draft, revision_feedback):
    revision_prompt = f"""
    The user has given the following feedback on the current draft:
    {revision_feedback}

    Please revise the draft to address this feedback, while keeping the scene state update and pending intents consistent with the new draft. Follow the same rules as before.
    """

    response = client.responses.create(
        model="gpt-5.4",
        instructions=CASSANDRA_SYSTEM_PROMPT + revision_prompt,
        input=build_cassandra_input(world, scene_state, current_draft),
        text={
            "format": {
                "type": "json_schema",
                "name": "cassandra_scene_revision_response",
                "strict": True,
                "schema": CASSANDRA_SCHEMA,
            }
        },
    )

    if not response.output_text:
        raise ValueError("Cassandra returned no output text")

    data = json.loads(response.output_text)

    return data
