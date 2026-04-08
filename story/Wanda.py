import json
from copy import deepcopy
from openai import OpenAI
from .models import NarrativeMemory, CommittedScene
from .MissPots.characters import build_character_registry
from .MissPots.cast_tracker import (
    _clean_presence,
    infer_scene_participants_and_positions
)

client = OpenAI()

def _base_context(world, scene_state):
    memories = list(
        NarrativeMemory.objects.filter(world=world)
        .order_by("-created_at")[:10]
    )[::-1]

    recent_scenes = list(
        CommittedScene.objects.filter(world=world)
        .order_by("-created_at")[:20]
    )[::-1]

    character_registry = build_character_registry(world)

    return {
        "active_world": {
            "name": world.name,
            "description": world.description,
        },
        "current_scene_state": {
            "location": scene_state.location,
            "cast": scene_state.cast_json,
            "pending_intents": scene_state.pending_intents_json,
        },
        "character_registry": character_registry,
        "recent_N_memories": [m.content for m in memories],
        "recent_scenes": [s.combined_text for s in recent_scenes],
    }


def build_turn_context(world, scene_state, user_input, character_authored_intents=None):
    payload = _base_context(world, scene_state)
    payload["user_input"] = user_input
    payload["character_authored_intents"] = character_authored_intents or {}
    return payload


def build_revision_context(
    world,
    scene_state,
    user_input,
    original_draft,
    revised_draft,
    revision_feedback,
    revision_mode,
    character_authored_intents=None,
):
    payload = _base_context(world, scene_state)
    payload.update({
        "user_input": user_input,
        "revision_mode": revision_mode,
        "original_draft": original_draft,
        "revised_draft": revised_draft,
        "revision_feedback": revision_feedback,
        "character_authored_intents": character_authored_intents or {},
    })
    return payload


def serialize_scene_state(scene_state):
    return {
        "location": scene_state.location or "opening scene",
        "cast": scene_state.cast_json or {},
        "pending_intents": scene_state.pending_intents_json or {},
    }


def resolve_proposed_scene_state(current_state, scene_state_update, pending_intents):
    proposed = deepcopy(current_state or {})

    old_location = proposed.get("location")
    new_location = scene_state_update.get("location")
    location_changed = bool(new_location and new_location != old_location)

    if new_location:
        proposed["location"] = new_location

    if "cast" in scene_state_update:
        proposed["cast"] = merge_scene_cast(
            proposed.get("cast", {}),
            scene_state_update.get("cast", {}),
            location_changed=location_changed,
        )

    proposed["pending_intents"] = pending_intents or {}
    return proposed

def merge_scene_cast(current_cast, cast_update, location_changed=False):
    merged = deepcopy(current_cast or {})
    updated_slugs = set((cast_update or {}).keys())

    if location_changed:
        for slug, payload in merged.items():
            if not isinstance(payload, dict):
                continue
            if slug not in updated_slugs:
                payload["presence"] = "mentioned"
                payload["position"] = ""

    for slug, payload in (cast_update or {}).items():
        if not isinstance(payload, dict):
            continue

        existing = merged.get(slug, {})
        merged[slug] = {
            **existing,
            "presence": _clean_presence(
                payload.get("presence", existing.get("presence", "mentioned"))
            ),
            "position": payload.get("position", existing.get("position", "")),
        }

    return merged

def diff_scene_states(old_state, new_state):
    old_state = old_state or {}
    new_state = new_state or {}

    changes = []
    for key in sorted(set(old_state.keys()) | set(new_state.keys())):
        old_val = old_state.get(key)
        new_val = new_state.get(key)
        if old_val != new_val:
            changes.append({
                "field": key,
                "before": old_val,
                "after": new_val,
            })

    return changes

def collect_characterbot_intent_context(
        world,
        scene_state,
        user_input,
        final_draft,
        character_authored_intents,
    ):
    recent_memories = list(
            NarrativeMemory.objects.filter(world=world)
            .order_by("-created_at")[:5]
        )[::-1]

    recent_scenes = list(
            CommittedScene.objects.filter(world=world)
            .order_by("-created_at")[:3]
        )[::-1]

    return {
            "active_world": {
                "name": world.name,
                "description": world.description,
            },
            "current_scene_state": serialize_scene_state(scene_state),
            "character_authored_intents": character_authored_intents or {},
            "recent_narrative_memories": [m.content for m in recent_memories],
            "recent_scenes": [s.combined_text for s in recent_scenes],
            "user_input": user_input or "",
            "final_approved_draft": final_draft or "",
        }

def _normalize_pending_intents_output(data):
    if not isinstance(data, dict):
        return {}

    intents = data.get("pending_intents") or []
    normalized = {}

    if isinstance(intents, dict):
        iterable = [{"slug": slug, **(payload or {})} for slug, payload in intents.items() if isinstance(payload, dict)]
    elif isinstance(intents, list):
        iterable = intents
    else:
        iterable = []

    for entry in iterable:
        if not isinstance(entry, dict):
            continue

        slug = (entry.get("slug") or "").strip()
        if not slug:
            continue

        purpose = (entry.get("purpose") or "").strip()
        tone = (entry.get("tone") or "").strip()
        next_step = (entry.get("next") or "").strip()

        if not purpose and not tone and not next_step:
            continue

        normalized[slug] = {
            "purpose": purpose,
            "tone": tone,
            "next": next_step,
        }

    return normalized


def resolve_intents(
    world,
    scene_state,
    user_input,
    final_draft,
    character_authored_intents,
):


    context = collect_characterbot_intent_context(
        world=world,
        scene_state=scene_state,
        user_input=user_input,
        final_draft=final_draft,
        character_authored_intents=character_authored_intents,
    )

    raw = call_intent_resolver(context)
    normalized = _normalize_pending_intents_output(raw)
    valid_slugs = valid_character_slugs(world)
    authored_slugs = set((character_authored_intents or {}).keys())
    allowed_slugs = authored_slugs & valid_slugs
    return {
        slug: payload
        for slug, payload in normalized.items()
        if slug in allowed_slugs
    }

# def collect_character_authored_intents(world, scene_state, user_input):
#     valid_slugs = valid_character_slugs(world)
#     authored_intents = {
#         slug: payload
#         for slug, payload in authored_intents.items()
#         if slug in valid_slugs
#     }
#     return {}

def collect_character_authored_intents(world, scene_state, user_input):
    """
    v1 transitional collector for character_authored_intents.

    For now, this simply promotes the current canonical pending intents into
    next-turn authored intents. This preserves continuity without inventing
    fresh motivations in Wanda.

    Later, replace the body of this function with real upstream characterbot
    intent authoring.
    """

    valid_slugs = valid_character_slugs(world)
    source_intents = scene_state.pending_intents_json or {}

    if not isinstance(source_intents, dict):
        source_intents = {}

    authored_intents = {}

    for slug, payload in source_intents.items():
        if slug not in valid_slugs:
            continue
        if not isinstance(payload, dict):
            continue

        normalized = {
            "purpose": (payload.get("purpose") or "").strip(),
            "tone": (payload.get("tone") or "").strip(),
            "next": (payload.get("next") or "").strip(),
        }

        if any(normalized.values()):
            authored_intents[slug] = normalized

    return authored_intents

def valid_character_slugs(world):
    return {
        c["slug"]
        for c in build_character_registry(world)
        if c.get("slug")
    }

def resolve_approved_scene_state(
        world,
        scene_state,
        user_input,
        final_draft,
        pending_intents,
        pov_slug=None):
    """
    Authoritative post-approval scene-state resolver.

    Responsibilities:
    - ask Miss Pots to infer scene participants and location from the approved scene
    - resolve those inferred facts against the current canonical scene state
    - return the fully resolved new scene state dict

    This is the scene-state equivalent of resolve_intents().
    """


    scene_text = (
        f"[User]\n{user_input or ''}\n\n"
        f"[Cassandra]\n{final_draft or ''}"
    )

    participant_result = infer_scene_participants_and_positions(
        world=world,
        scene_state=scene_state,
        scene_text=scene_text,
        pov_slug=pov_slug,
    )

    scene_state_update = participant_result.get("scene_state_update", {})

    return resolve_proposed_scene_state(
        current_state=serialize_scene_state(scene_state),
        scene_state_update=scene_state_update,
        pending_intents=pending_intents or {},
    )

INTENT_RESOLUTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pending_intents": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "slug": {"type": "string"},
                    "purpose": {"type": "string"},
                    "tone": {"type": "string"},
                    "next": {"type": "string"},
                },
                "required": ["slug", "purpose", "tone", "next"],
            },
        }
    },
    "required": ["pending_intents"],
}

def call_intent_resolver(context):
    RESOLVER_PROMPT = """
You are resolving carry-forward intents for a narrative system.

You are given:
- the current scene state before approval
- character_authored_intents created upstream before scene composition
- the user's input
- the final approved draft
- recent memories and recent scenes for continuity

Your job is to determine which character_authored_intents remain unresolved after the approved scene and should carry forward as pending_intents.

Important rules:
- Do NOT invent brand-new intents that are not grounded in character_authored_intents.
- You may keep an intent, modify its tone/next-step wording, or drop it.
- Drop intents that were fulfilled, clearly abandoned, contradicted, or no longer supported by the approved scene.
- Keep intents that remain active, unresolved, or partially redirected by the approved scene.
- If an intent survives but the scene changes its emotional posture or immediate next pressure, update tone and next accordingly.
- Prefer omission over speculation.
- Return only intents that should still carry forward into the next scene.

pending_intents are not general summaries.
They are short carry-forward notes about unresolved motivational pressure.

Return valid JSON matching the schema exactly.
"""

    response = client.responses.create(
        model="gpt-5.4",
        instructions=RESOLVER_PROMPT,
        input=json.dumps(context, ensure_ascii=False, indent=2),
        text={
            "format": {
                "type": "json_schema",
                "name": "intent_resolution_response",
                "strict": True,
                "schema": INTENT_RESOLUTION_SCHEMA,
            }
        },
    )

    if not response.output_text:
        raise ValueError("Intent resolver returned no output text")

    return json.loads(response.output_text)
