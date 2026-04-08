import json
import re
from openai import OpenAI
from .models import NarrativeMemory, CommittedScene

client = OpenAI()

CASSANDRA_SYSTEM_PROMPT = """
You are Cassandra, the narrative orchestrator of a multi-character interactive story.

Your job:
- Write a reviewable draft for the user

Rules:
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
    },
    "required": ["draft"]
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
    },
    "required": [
        "draft",
        "change_summary",
        "inferred_editorial_intent",
        "editors_craft_memory",
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

    return data



def call_cassandra_revision(context):
    REVISION_PROMPT = """
You are Cassandra, the narrative orchestrator and editor of a multi-character interactive story.

You are in revision mode.

You will receive a JSON payload containing:
- active world context
- current scene state
- recent narrative memories
- recent committed scenes
- user_input
- revision_mode
- original_draft
- revised_draft
- revision_feedback

There are three revision modes:

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
  - editors_craft_memory (narrative memory implications)
  - change_summary
  - inferred_editorial_intent

3. rewrite_from_scratch
Use this mode when the user wants a substantially different attempt.
If revision_mode is rewrite_from_scratch:
- Treat revised_draft as a reference only, not something to preserve.
- Treat original_draft as a rejected prior attempt, not as prose to preserve

In this mode:
- Do not preserve the wording, paragraph structure, or sequencing of the original draft.
- Treat the original draft only as evidence of scene facts and prior interpretation.
- Rebuild the prose from the ground up.
- Preserve continuity and character integrity, but aim for a clearly distinct execution.
- The new draft should feel like an alternate valid response to the same scene prompt, not a lightly edited variant.

Requirements for editors_craft_memory:
- Return 1 to 3 concise entries
- Capture narrative meaning, emotional shifts, power dynamics, or interpretive continuity

Global rules:
- Treat revision_feedback as editorial guidance, not in-world dialogue
- Preserve continuity and character integrity
- In interpret_user_edit mode, the effective draft is revised_draft
- In rewrite_based_on_feedback mode, the effective draft is the draft you generate
- In rewrite_from_scratch mode, the effective draft is the new draft you generate
- editors_craft_memory are provisional proposal-level implications only, not canon memories
- Return valid JSON matching the schema exactly
When revision_mode is rewrite_from_scratch:
- maximize meaningful variation in structure, emphasis, pacing, and line choices

The effective revised draft is:
- revised_draft in interpret_user_edit mode
- the new draft you generate in rewrite_based_on_feedback mode
- the new draft you generate in rewrite_from_scratch mode

Important:
- user_input may establish scene facts, emotional pressure, addressees, or continuity constraints that the revised prose assumes without restating explicitly
- If original_draft and revised prose differ, prefer the revised prose as the final editorial intent unless revision_feedback clearly indicates otherwise

Additional rewrite_from_scratch guidance:
- Do not perform a sentence-level edit pass over original_draft
- Do not preserve the same paragraph count unless it happens naturally
- Do not keep the same opening line pattern or closing beat by default
- Prefer fresh construction over substitution
- Write as though the user asked for "another real attempt at this scene"

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


    data = json.loads(response.output_text)
    data = _normalize_revision_output(data)

    return data


def extract_memory_from_scene(world, draft, user_input=None):
    recent_memories = list(
        NarrativeMemory.objects.filter(world=world)
        .order_by("-created_at")[:5]
    )[::-1]

    recent_scenes = list(
        CommittedScene.objects.filter(world=world)
        .order_by("-created_at")[:3]
    )[::-1]

    payload = {
        "user_input": user_input or "",
        "final_draft": draft or "",
        "recent_memories": [m.content for m in recent_memories],
        "recent_scenes": [s.combined_text for s in recent_scenes],
    }

    prompt = f"""
Extract 1–2 narrative memories from this scene.

Narrative memories are short continuity-assist notes.
They are not action trackers and not long-term lore storage.

Their purpose is to preserve the scene's implied meaning for upcoming scenes:
- emotional carryover
- inferred motives or feelings
- relationship implications
- power shifts or tension dynamics
- why a moment mattered
- what subtext should remain active

Focus on:
- implications and emotional meaning
- causes, pressures, and inferred significance
- shifts in how characters now relate to or understand each other

Do not focus on:
- unfinished physical actions
- logistical next steps
- explicit future intentions
- surface recap of what literally happened
- details already covered by pending intents

Avoid creating a memory that merely repeats an existing recent memory unless this scene materially deepens or changes it.

Recent narrative memories:
{chr(10).join(f"- {m.content}" for m in recent_memories) if recent_memories else "- None"}

Recent scenes:
{chr(10).join(f"- {s.combined_text}" for s in recent_scenes) if recent_scenes else "- None"}

User contribution:
{user_input or ""}

Cassandra final draft:
{draft or ""}

Return 1–2 concise narrative memories.
Each one should capture interpretive continuity, not action continuity.
"""
    response = client.responses.create(
        model="gpt-5.4",
        instructions=prompt,
        input=json.dumps(payload, ensure_ascii=False, indent=2),
    )

    return response.output_text.strip()


# - do not reuse sentences from original_draft unless truly necessary for continuity
# When revision_mode is rewrite_from_scratch:
# - avoid reusing sentences from original_draft
# - avoid preserving the same paragraph order unless necessary
# - prefer meaningful variation in structure, emphasis, and delivery

def normalize_for_revision_compare(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def materially_changed(original_draft: str, revised_draft: str) -> bool:
    return normalize_for_revision_compare(original_draft) != normalize_for_revision_compare(revised_draft)


def choose_revision_mode(
    original_draft: str,
    revised_draft: str,
    revision_feedback: str | None,
    rewrite_from_scratch: bool = False,
) -> str:
    if rewrite_from_scratch:
        return "rewrite_from_scratch"

    has_material_edit = materially_changed(original_draft, revised_draft)
    has_feedback = bool(revision_feedback and revision_feedback.strip())

    if has_material_edit:
        return "interpret_user_edit"

    if has_feedback:
        return "rewrite_based_on_feedback"

    return "rewrite_from_scratch"


def _normalize_revision_output(data):
    if not isinstance(data, dict):
        return {
            "draft": "",
            "change_summary": "",
            "inferred_editorial_intent": "",
            "editors_craft_memory": [],
        }

    memories = data.get("editors_craft_memory") or []
    if not isinstance(memories, list):
        memories = []

    return {
        "draft": (data.get("draft") or "").strip(),
        "change_summary": (data.get("change_summary") or "").strip(),
        "inferred_editorial_intent": (data.get("inferred_editorial_intent") or "").strip(),
        "editors_craft_memory": [
            str(item).strip()
            for item in memories
            if str(item).strip()
        ],
    }
