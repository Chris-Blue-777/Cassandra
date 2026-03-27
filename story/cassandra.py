import json
import os
from openai import OpenAI
from .models import NarrativeMemory, CommittedScene
from .Wanda import build_turn_context, build_revision_context
from .MissPots.proposals import _normalize_structured_output

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

You may be given recent narrative memories and recent committed scenes from the active world.
Use them to preserve emotional continuity, relationship evolution, and unresolved tension.
Do not repeat them explicitly unless naturally relevant.
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
                    "type": ["string", "null"]
                },
                "cast": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "slug": {
                                "type": "string"
                            },
                            "presence": {
                                "type": "string",
                                "enum": ["active", "nearby"]
                            },
                            "position": {
                                "type": ["string", "null"]
                            }
                        },
                        "required": ["slug", "presence", "position"]
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
                    "slug": {
                        "type": "string"
                    },
                    "purpose": {
                        "type": "string"
                    },
                    "tone": {
                        "type": "string"
                    },
                    "next": {
                        "type": "string"
                    }
                },
                "required": ["slug", "purpose", "tone", "next"]
            }
        }
    },
    "required": ["draft", "scene_state_update", "pending_intents"]
}

CASSANDRA_REVISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "draft": {
            "type": "string"
        },
        "change_summary": {
            "type": "string"
        },
        "inferred_editorial_intent": {
            "type": "string"
        },
        "editors_craft_memory": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        "scene_state_update": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "location": {
                    "type": ["string", "null"]
                },
                "cast": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "slug": {
                                "type": "string"
                            },
                            "presence": {
                                "type": "string",
                                "enum": ["active", "nearby"]
                            },
                            "position": {
                                "type": ["string", "null"]
                            }
                        },
                        "required": ["slug", "presence", "position"]
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
                    "slug": {
                        "type": "string"
                    },
                    "purpose": {
                        "type": "string"
                    },
                    "tone": {
                        "type": "string"
                    },
                    "next": {
                        "type": "string"
                    }
                },
                "required": ["slug", "purpose", "tone", "next"]
            }
        }
    },
    "required": [
        "draft",
        "change_summary",
        "inferred_editorial_intent",
        "editors_craft_memory",
        "scene_state_update",
        "pending_intents"
    ]
}
# def build_cassandra_input(world, scene_state, user_input):
#     N_memories = NarrativeMemory.objects.filter(world=world).order_by("-created_at")[:5]
#     recent_scenes = CommittedScene.objects.filter(world=world).order_by("-created_at")[:10]
#     payload = {
#         "active_world": {
#             "name": world.name,
#             "description": world.description,
#         },
#         "current_scene_state": {
#             "location": scene_state.location,
#             "cast": scene_state.cast_json,
#             "pending_intents": scene_state.pending_intents_json,
#         },
#         "user_input": user_input,
#         "recent_N_memories": [m.content for m in N_memories],
#         "recent_scenes": [s.combined_text for s in recent_scenes],
#     }
#     return json.dumps(payload, ensure_ascii=False, indent=2)


def call_cassandra(payload):
    print("CONTEXT TYPE:", type(payload))
    print("CONTEXT:", payload)
    response = client.responses.create(
        model="gpt-5.4",
        instructions=CASSANDRA_SYSTEM_PROMPT,
        input=json.dumps(payload, ensure_ascii=False, indent=2),
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
    data = _normalize_structured_output(data)

    return data



def call_cassandra_revision(context):
    print("Revision Context:")
    print(json.dumps(context, ensure_ascii=False, indent=2))
    REVISION_PROMPT = """
You are Cassandra, the narrative orchestrator and editor of a multi-character interactive story.

You are in revision mode.

You will receive a JSON payload containing:
- active world context
- current scene state
- recent narrative memories
- recent committed scenes
- revision_mode
- original_draft
- revised_draft
- revision_feedback

There are two revision modes:

1. interpret_user_edit
Use this mode when the user directly edited the draft text.
In this mode:
- Treat revised_draft as the authoritative candidate draft
- Compare original_draft and revised_draft carefully
- Infer what the user changed intentionally, even if revision_feedback is blank
- Treat the user's textual edits themselves as instructions
- Do NOT overwrite or replace revised_draft
- Still return a draft field, but it should simply match revised_draft
- Evaluate what the revised draft implies for:
  - scene_state_update
  - pending_intents
  - editors_craft_memory (narrative memory implications)
  - change_summary
  - inferred_editorial_intent

2. rewrite_based_on_feedback
Use this mode when the user did not materially edit the prose, but did provide editorial guidance.
In this mode:
- Treat revision_feedback as direct editorial guidance
- Freely revise and improve the prose
- Return the rewritten prose in the draft field
- Then evaluate what that rewritten draft implies for:
  - scene_state_update
  - pending_intents
  - editors_craft_memory (narrative memory implications)
  - change_summary
  - inferred_editorial_intent

Requirements for editors_craft_memory:
- Return 1–3 concise entries
- Capture narrative meaning, emotional shifts, power dynamics, etc.

Global rules:
- Treat revision_feedback as editorial guidance, not in-world dialogue
- Preserve continuity and character integrity
- Keep scene_state_update and pending_intents consistent with the effective draft
- In interpret_user_edit mode, the effective draft is revised_draft
- In rewrite_based_on_feedback mode, the effective draft is the draft you generate
- editors_craft_memory are provisional proposal-level implications only, not canon memories
- Return valid JSON matching the schema exactly
    """

    response = client.responses.create(
        model="gpt-5.4",
        instructions=CASSANDRA_SYSTEM_PROMPT + REVISION_PROMPT,
        input=json.dumps(context, ensure_ascii=False, indent=2),
        text={
            "format": {
                "type": "json_schema",
                "name": "cassandra_scene_revision_response",
                "strict": True,
                "schema": CASSANDRA_REVISION_SCHEMA,
            }
        },
    )

    if not response.output_text:
        raise ValueError("Cassandra returned no output text during revision")

    print("Revision Raw Response:")
    print(response.output_text)

    data = json.loads(response.output_text)
    data = _normalize_structured_output(data)

    return data


def extract_memory_from_scene(draft, user_input=None):
    prompt = f"""
    Extract 1–2 important narrative memories from this scene.

    Focus on:
    - relationship shifts
    - emotional significance
    - tension or power dynamics
    - meaningful decisions

    Keep each memory concise and meaningful. Do not summarize everything - extract only what should persist.

    User contribution:
    {user_input}

    Cassandra final draft:
    {draft}
    """

    response = client.responses.create(
        model="gpt-5.4",
        input=prompt,
    )

    return response.output_text.strip()

import json
from openai import OpenAI

client = OpenAI()


CAST_RESOLUTION_PROMPT = """
You are a structured scene-state resolver.

Your job is to convert user input into an OBJECTIVE scene_state_update.

You do NOT write prose.
You do NOT apply character perspective.
You do NOT invent or reinterpret story meaning.

You ONLY:
- resolve character references
- determine scene state changes
- output structured JSON

---

## CHARACTER RESOLUTION RULES

You are given a character_registry.

Each character has:
- name
- slug
- description

When the user refers to a character using:
- name ("Kara")
- pronouns ("her", "she")
- relational phrases ("my girlfriend", "my ex-husband")
- titles ("Dr. Vale")

You MUST resolve that reference to the correct character using its slug.

IMPORTANT:
- ALWAYS use the slug, NEVER the name
- ONLY use characters from character_registry
- If multiple characters could match, OMIT instead of guessing
- If uncertain, OMIT

---

## TEMPORARY CHARACTER RULES

If the user introduces a person NOT in the character_registry:

- Create a temporary character
- Use a unique key: "tmp_<role>_<number>"

Examples:
- tmp_bartender_1
- tmp_guard_1

Each temporary character MUST include:
- "type": "temporary"
- "label": short role name ("bartender", "guard")
- "description": brief physical or contextual description
- "presence": usually "active"

Rules:
- Temporary characters are scene-scoped
- Do NOT replace or override real characters
- Reuse an existing temporary character if it clearly refers to the same person in the current scene

---

## SCENE STATE RULES

You produce:

scene_state_update:
- "location" (string, if changed or clarified)
- "cast" (object of characters currently present)

Cast rules:
- Keys MUST be character slugs or "tmp_*"
- Values include at minimum:
  - "presence": "active" or "nearby"
  - "position": optional string describing where they are in the scene

Do NOT include characters unless they are present or explicitly affected.

---

## STRICT CONSTRAINTS

- DO NOT output names — only slugs
- DO NOT invent real characters
- DO NOT guess if uncertain
- DO NOT apply perspective or hide information
- DO NOT include explanations
- OUTPUT JSON ONLY

---

## OUTPUT FORMAT

{
  "scene_state_update": {
    "location": "...",
    "cast": {
        "<slug_or_tmp_key>": {
            "presence": {
                "type": "string",
                "enum": ["active", "nearby"]
            },
            "position": {"type": "string"}
        },
        }
    }
  "pending_intents": {}
}
"""


def call_cassandra_cast_resolution(context):
    """
    Calls Cassandra to resolve character ownership and produce a clean scene_state_update.

    context = {
        "user_input": str,
        "current_scene_state": dict,
        "character_registry": list,
        "recent_scenes": list (optional)
    }
    """

    user_prompt = f"""
INPUT:

user_input:
{json.dumps(context.get("user_input", ""), indent=2)}

current_scene_state:
{json.dumps(context.get("current_scene_state", {}), indent=2)}

character_registry:
{json.dumps(context.get("character_registry", []), indent=2)}

recent_scenes:
{json.dumps(context.get("recent_scenes", []), indent=2)}

---

Produce the scene_state_update following all rules.
"""

    response = client.chat.completions.create(
        model="gpt-5.4",
        temperature=0,
        messages=[
            {"role": "system", "content": CAST_RESOLUTION_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    content = response.choices[0].message.content.strip()

    # --- Safe JSON parse ---
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        # fallback: attempt to extract JSON block
        start = content.find("{")
        end = content.rfind("}") + 1
        parsed = json.loads(content[start:end])

    # --- Ensure required structure ---
    return {
        "scene_state_update": parsed.get("scene_state_update", {}),
        "pending_intents": parsed.get("pending_intents", {}),
    }

# def build_character_turn_context(world, scene_state, character, user_input=None):
#     return {
#         "active_world": world,
#         "current_scene_state": scene_state,
#         "character_self": character,
#         "character_state": character.status_json,
#         "character_memories": character.memory_json,
#         "character_perceptions": character.
#         "character_beliefs": ...,
#         "recent_relevant_scenes": ...,
#         "visible_cast": ...,
#         "user_input": user_input or "",
#     }
