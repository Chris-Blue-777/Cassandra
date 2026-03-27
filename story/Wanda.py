from .models import NarrativeMemory, CommittedScene, Character
from .MissPots.characters import build_character_registry, resolve_character_reference
from copy import deepcopy


def _base_context(world, scene_state):
    memories = list(
        NarrativeMemory.objects.filter(world=world)
        .order_by("-created_at")[:10]
    )[::-1]

    recent_scenes = list(
        CommittedScene.objects.filter(world=world)
        .order_by("-created_at")[:20]
    )[::-1]

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
        "recent_N_memories": [m.content for m in memories],
        "recent_scenes": [s.combined_text for s in recent_scenes],
    }


def build_turn_context(world, scene_state, user_input):
    payload = _base_context(world, scene_state)
    payload["user_input"] = user_input
    return payload


def build_revision_context(
    world,
    scene_state,
    original_draft,
    revised_draft,
    revision_feedback,
    revision_mode,
):
    payload = _base_context(world, scene_state)
    payload.update({
        "revision_mode": revision_mode,
        "original_draft": original_draft,
        "revised_draft": revised_draft,
        "revision_feedback": revision_feedback,
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

    if "location" in scene_state_update:
        proposed["location"] = scene_state_update["location"]

    if "cast" in scene_state_update:
        proposed["cast"] = scene_state_update["cast"]

    proposed["pending_intents"] = pending_intents or {}
    return proposed


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

def build_cast_resolution_context(world, scene_state, user_input):

    return {
        "user_input": user_input,
        "current_scene_state": serialize_scene_state(scene_state),
        "character_registry": build_character_registry(world),
        # Optional but VERY helpful
        "recent_scenes": [
            s.combined_text for s in
            CommittedScene.objects.filter(world=world).order_by("-created_at")[:3]
        ][::-1],
    }
