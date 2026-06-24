#characters.py#

import json
from django.core.exceptions import MultipleObjectsReturned
from django.conf import settings
from openai import OpenAI
from story.models import (
    Character,
    CharacterMemory,
    CharacterBelief,
    CharacterPerception,
    CharacterPerceptionChange,
    CharacterState,
    CharacterStateChange,
    NarrativeMemory,
    CommittedScene,
    CharacterScene,
)

CHARACTER_AGENT_MODEL = "gpt-5.4"
openai_client = OpenAI(
    api_key=settings.OPENAI_API_KEY
)
grok_client = OpenAI(
    api_key=settings.GROK_API_KEY,
    base_url="https://api.x.ai/v1"
)

PERSPECTIVE_BEAT_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "perspective_beat": {"type": "string"},
        "private_player_material": {"type": "string"},
        "visibility_note": {"type": "string"},
    },
    "required": [
        "perspective_beat",
        "private_player_material",
        "visibility_note",
    ],
}

PERSPECTIVE_BEAT_SYSTEM_PROMPT = """
You rewrite the user's latest scene beat into the acting character's local, second-person perspective.

Return only valid JSON matching the schema.

Rules:
- Write perspective_beat as immersive prose addressed to the acting character as "you".
- Do not address the acting character by name in the prose.
- Resolve first-person user references such as I, me, my, and mine to the player character(s), not to the acting character unless the acting character is_player=true.
- Use characterlocal_scene_state as the boundary for what the acting character can directly see, partially see, hear, infer, or not know.
- Preserve uncertainty when access is partial, inferred, mediated, obstructed, or intermittent.
- Do not turn private player thoughts into character knowledge. Put private/internal-only player material in private_player_material if it matters.
- Do not add bespoke warnings or corrections about impossible states. Just write the local perspective cleanly.
- Do not decide the acting character's response, attempted action, dialogue, or intent.
"""

def apply_character_contribution_to_scene(character_scene, contribution):
    """
    Attach the character-agent's draft-time contribution to this CharacterScene.
    """
    if not isinstance(contribution, dict):
        contribution = {}

    authored_intent = contribution.get("authored_intent") or {}
    current_turn_reflection = contribution.get("current_turn_reflection") or {}

    character_scene.scene_contribution_json = contribution
    character_scene.authored_intent_json = authored_intent
    character_scene.current_turn_reflection_json = current_turn_reflection

    character_scene.attempted_action = contribution.get("attempted_action") or ""
    character_scene.attempted_dialogue = contribution.get("attempted_dialogue") or ""
    character_scene.internal_intent = contribution.get("internal_intent") or ""
    character_scene.emotional_posture = contribution.get("emotional_posture") or ""

    character_scene.target_slugs_json = contribution.get("target_slugs") or []
    character_scene.required_visibility = contribution.get("required_visibility") or ""
    character_scene.required_audibility = contribution.get("required_audibility") or ""
    character_scene.interrupt_priority = contribution.get("interrupt_priority") or ""
    character_scene.body_motion = contribution.get("body_motion") or ""

    character_scene.observed_focus_json = contribution.get("observed_focus") or []
    character_scene.beliefs_in_play_json = contribution.get("beliefs_in_play") or []
    character_scene.memory_pressures_json = contribution.get("memory_pressures") or []
    character_scene.proposed_effects_json = contribution.get("proposed_effects") or []

    character_scene.active_pressure = current_turn_reflection.get("active_pressure") or ""
    character_scene.anticipated_consequence = (
        current_turn_reflection.get("anticipated_consequence") or ""
    )


def apply_previous_scene_aftermath_to_character_scene(character_scene, previous_scene_aftermath):
    """
    Attach the character-agent's subjective aftermath output to this CharacterScene.
    """
    if not isinstance(previous_scene_aftermath, dict):
        previous_scene_aftermath = {}

    character_scene.previous_scene_aftermath_json = previous_scene_aftermath
    character_scene.subjective_scene_text = (
        previous_scene_aftermath.get("subjective_scene_text") or ""
    )

    character_scene.memories_created_json = (
        previous_scene_aftermath.get("memories") or []
    )
    character_scene.state_update_json = (
        previous_scene_aftermath.get("state_update") or {}
    )
    character_scene.perception_updates_json = (
        previous_scene_aftermath.get("perception_updates") or []
    )
    character_scene.beliefs_created_json = (
        previous_scene_aftermath.get("beliefs") or []
    )

    character_scene.aftermath_processed = True


def build_character_event_record_text(
    character_slug,
    resolved_scene_state,
    scene_events=None,
    character_contributions=None,
):
    """
    Build a character-specific scene record without narrator prose.

    This should not be beautiful prose.
    It is subjective continuity material for future character-agent calls.
    """
    scene_events = scene_events or []
    character_contributions = character_contributions or []

    cast = resolved_scene_state.get("cast", {}) if isinstance(resolved_scene_state, dict) else {}
    cast_entry = cast.get(character_slug, {}) if isinstance(cast, dict) else {}

    lines = []

    directly_involved_in_any_event = any(
        isinstance(event, dict)
        and (
            event.get("actor_slug") == character_slug
            or character_slug in (event.get("target_slugs") or [])
            or character_slug in (event.get("perceived_by") or [])
        )
        for event in scene_events or []
    )

    if directly_involved_in_any_event:
        presence = "present"
        sensory_access = "self"
        perception_scope = "full"
    else:
        presence = cast_entry.get("presence", "mentioned")
        sensory_access = cast_entry.get("sensory_access", "none")
        perception_scope = cast_entry.get("perception_scope", "none")

    position = cast_entry.get("position", "")
    space_id = cast_entry.get("space_id", "")
    local_space_label = cast_entry.get("local_space_label", "")

    lines.append(
        f"Participation: presence={presence}, sensory_access={sensory_access}, "
        f"perception_scope={perception_scope}."
    )

    if position or local_space_label or space_id:
        lines.append(
            f"Position/context: {position or local_space_label or space_id}."
        )

    perceived_events = []

    for event in scene_events:
        if not isinstance(event, dict):
            continue

        perceived_by = event.get("perceived_by") or []
        actor_slug = event.get("actor_slug")
        target_slugs = event.get("target_slugs") or []

        directly_involved = (
            actor_slug == character_slug
            or character_slug in target_slugs
        )

        explicitly_perceived = character_slug in perceived_by

        if directly_involved or explicitly_perceived:
            perceived_events.append(event)

    if perceived_events:
        lines.append("")
        lines.append("Events this character experienced, caused, was targeted by, or perceived:")
        for event in perceived_events:
            event_type = event.get("event_type") or "event"
            summary = event.get("summary") or ""
            outcome = event.get("outcome") or ""
            actor = event.get("actor_slug") or "unknown"

            lines.append(f"- {event_type}; actor={actor}.")
            if summary:
                lines.append(f"  Summary: {summary}")
            if outcome:
                lines.append(f"  Outcome: {outcome}")
    else:
        lines.append("")
        lines.append("No explicit scene events were marked as perceived by this character.")

    own_contribution = None

    for contribution in character_contributions:
        if not isinstance(contribution, dict):
            continue

        slug = contribution.get("slug")
        scene_contribution = contribution.get("scene_contribution") or {}

        if slug == character_slug or scene_contribution.get("slug") == character_slug:
            own_contribution = scene_contribution
            break

    if own_contribution:
        lines.append("")
        lines.append("What this character attempted:")
        if own_contribution.get("attempted_action"):
            lines.append(f"- Action: {own_contribution.get('attempted_action')}")
        if own_contribution.get("attempted_dialogue"):
            lines.append(f"- Dialogue: {own_contribution.get('attempted_dialogue')}")
        if own_contribution.get("internal_intent"):
            lines.append(f"- Intent: {own_contribution.get('internal_intent')}")
        if own_contribution.get("emotional_posture"):
            lines.append(f"- Emotional posture: {own_contribution.get('emotional_posture')}")

    lines.append("")

    return "\n".join(line for line in lines if line is not None).strip()

def _payload_section(note, data):
    return {
        "note": note,
        "data": data,
    }

def _perception_scope_from_access(access):
    if access == "direct_full":
        return "full"
    if access in {"direct_partial", "mediated_audio", "mediated_text"}:
        return "partial"
    if access == "inferred":
        return "indirect"
    return "none"

def _local_presence_from_access(access):
    """
    Convert observer-to-target access into the target's presence
    inside this character-agent's local reality.

    This is NOT the target's global/narrative presence.
    """
    if access == "self":
        return "present"

    if access in {"direct_full", "direct_partial"}:
        return "present"

    if access == "mediated_audio":
        return "audible"

    if access == "mediated_text":
        return "remote"

    if access == "inferred":
        return "inferred"

    return "not_perceived"


def _characterlocal_scene_state(scene_state, acting_slug):
    cast = scene_state.cast_json or {}
    topology = getattr(scene_state, "topology_json", {}) or {}
    spaces = topology.get("spaces", {}) if isinstance(topology, dict) else {}
    narrative_frame = topology.get("narrative_frame", {}) if isinstance(topology, dict) else {}

    acting_entry = cast.get(acting_slug) or {}
    acting_space_id = acting_entry.get("space_id", "")
    acting_space = spaces.get(acting_space_id, {}) if acting_space_id else {}

    local_cast = {}

    # Always include self.
    if acting_entry:
        local_cast[acting_slug] = {
            "position": acting_entry.get("position", ""),
            "perception_reason": "This is the acting character.",
        }

    # Include characters according to the actor's perception edges.
    perceives = acting_entry.get("perceives") or {}

    if isinstance(perceives, dict):
        perception_items = perceives.items()
    elif isinstance(perceives, list):
        perception_items = [
            (
                item.get("target_slug"),
                {
                    "access": item.get("access"),
                    "reason": item.get("reason", ""),
                },
            )
            for item in perceives
            if isinstance(item, dict)
        ]
    else:
        perception_items = []

    for target_slug, edge in perception_items:
        target_entry = cast.get(target_slug)
        if not target_entry:
            continue

        access = edge.get("access", "none")
        local_entry = {
            "local_presence": _local_presence_from_access(access),
            "access": access,
            "perception_scope": _perception_scope_from_access(access),
            "perception_reason": edge.get("reason", ""),
        }

        if access != "none":
            local_entry.update({
                "known_position": target_entry.get("position", ""),
                "known_space_id": target_entry.get("space_id", ""),
                "known_space_label": target_entry.get("local_space_label", ""),
            })

        local_cast[target_slug] = local_entry

    pending_intents = scene_state.pending_intents_json or {}
    if not isinstance(pending_intents, dict):
        pending_intents = {}

    own_pending_intent = pending_intents.get(acting_slug) or {}

    clean_space = dict(acting_space) if isinstance(acting_space, dict) else {}

    if acting_space_id:
        clean_space["id"] = acting_space_id

    clean_space.pop("space_id", None)

    return {
        "space": clean_space,
        "cast": local_cast,
        "pending_intent": own_pending_intent,
    }

def _normalize_character_agent_response(data):
    if not isinstance(data, dict):
        return {}

    update = data.get("previous_scene_aftermath") or {}
    if not isinstance(update, dict):
        update = {}

    memories = update.get("memories")
    if memories is None:
        memories = update.get("possible_memories") or []

    normalized_memories = []
    for memory in memories or []:
        if isinstance(memory, str):
            normalized_memories.append({
                "content": memory,
                "memory_type": "scene_experience",
                "related_character_slug": None,
            })
        elif isinstance(memory, dict):
            normalized_memories.append({
                "content": str(memory.get("content") or "").strip(),
                "memory_type": memory.get("memory_type") or "scene_experience",
                "related_character_slug": memory.get("related_character_slug"),
            })

    beliefs = update.get("beliefs")
    if beliefs is None:
        beliefs = update.get("possible_belief_updates") or []

    normalized_beliefs = []
    for belief in beliefs or []:
        if isinstance(belief, str):
            normalized_beliefs.append({
                "subject_type": "",
                "subject_slug": "",
                "belief": belief,
                "confidence": 0.5,
            })
        elif isinstance(belief, dict):
            normalized_beliefs.append({
                "subject_type": belief.get("subject_type") or "character",
                "subject_slug": belief.get("subject_slug") or "",
                "belief": str(belief.get("belief") or "").strip(),
                "confidence": belief.get("confidence") or 0.5,
            })

    perception_updates = update.get("perception_updates")
    if perception_updates is None:
        perception_updates = update.get("possible_perception_updates") or []

    normalized_perceptions = []
    for perception in perception_updates or []:
        if not isinstance(perception, dict):
            continue

        normalized_perceptions.append({
            "target_slug": perception.get("target_slug") or perception.get("target_character_slug") or "",
            "summary": str(perception.get("summary") or "").strip(),
            "impression_json": perception.get("impression_json") or (
                {"impression": perception.get("impression")}
                if perception.get("impression")
                else {}
            ),
            "relationship_json": perception.get("relationship_json") or {},
            "belief_json": perception.get("belief_json") or {},
            "arc_json": perception.get("arc_json") or {},
            "trust_delta": perception.get("trust_delta") or 0,
            "attraction_delta": perception.get("attraction_delta") or 0,
            "fear_delta": perception.get("fear_delta") or 0,
            "resentment_delta": perception.get("resentment_delta") or 0,
        })

    state_update = update.get("state_update") or {}

    normalized_state_update = {
        "emotional_state_json": (
            state_update.get("emotional_state_json")
            or state_update.get("emotional_state")
            or {}
        ),
        "goals_json": (
            state_update.get("goals_json")
            or state_update.get("goals")
            or {}
        ),
        "internal_conflicts_json": (
            state_update.get("internal_conflicts_json")
            or state_update.get("internal_conflicts")
            or {}
        ),
        "motivational_state_json": (
            state_update.get("motivational_state_json")
            or state_update.get("motivational_state")
            or {}
        ),
    }

    subjective_scene_text = str(
        update.get("subjective_scene_text") or ""
    ).strip()

    data["previous_scene_aftermath"] = {
        "subjective_scene_text": subjective_scene_text,
        "memories": normalized_memories,
        "state_update": normalized_state_update,
        "perception_updates": normalized_perceptions,
        "beliefs": normalized_beliefs,
    }

    reflection = data.get("current_turn_reflection") or {}
    if not isinstance(reflection, dict):
        reflection = {}

    data["current_turn_reflection"] = {
        "emotional_posture": str(reflection.get("emotional_posture") or "").strip(),
        "active_pressure": str(reflection.get("active_pressure") or "").strip(),
        "anticipated_consequence": str(reflection.get("anticipated_consequence") or "").strip(),
        "memory_or_belief_pressures": [
            str(item).strip()
            for item in (reflection.get("memory_or_belief_pressures") or [])
            if str(item).strip()
        ],
    }

    return data

def _has_meaningful_json_payload(payload):
    if not isinstance(payload, dict):
        return False

    return any(
        value not in ({}, [], "", None)
        for value in payload.values()
    )

def character_state_snapshot(character):
    """
    Snapshot the character's current aggregate CharacterState.

    This is historical/debug data for CharacterScene, not a live object.
    """
    state = getattr(character, "state", None)

    if not state:
        return {
            "emotional_state_json": {},
            "goals_json": {},
            "internal_conflicts_json": {},
            "motivational_state_json": {},
        }

    return {
        "emotional_state_json": state.emotional_state_json or {},
        "goals_json": state.goals_json or {},
        "internal_conflicts_json": state.internal_conflicts_json or {},
        "motivational_state_json": state.motivational_state_json or {},
    }


def _jsonish(value):
    if isinstance(value, dict):
        return value

    if value in ("", None, [], {}):
        return {}

    return {"summary": str(value).strip()}


def persist_character_experience_updates(
    world,
    resolved_scene_state,
    experience_updates,
    source_scene=None,
):
    if not isinstance(experience_updates, list):
        return

    for entry in experience_updates:
        if not isinstance(entry, dict):
            continue

        slug = str(entry.get("slug") or "").strip()
        if not slug:
            continue

        character = Character.objects.filter(
            world=world,
            slug=slug,
            is_active=True,
        ).select_related("state").first()

        if not character:
            continue

        update = entry.get("experience_update") or {}

        print(
            "[story] character_experience_update",
            "character=",
            slug,
            "memories=",
            len(update.get("memories", []) or []),
            "has_state_update=",
            bool(_has_meaningful_json_payload(update.get("state_update") or {})),
            "perception_updates=",
            len(update.get("perception_updates") or []),
            "beliefs=",
            len(update.get("beliefs") or []),
            flush=True,
        )

        # --- Memories ---
        for memory in update.get("memories", []) or []:
            if not isinstance(memory, dict):
                continue

            content = str(memory.get("content") or "").strip()
            if not content:
                continue

            related_character = validate_resolved_slug(
                world,
                memory.get("related_character_slug"),
            )

            CharacterMemory.objects.create(
                world=world,
                character=character,
                content=content,
                memory_type=memory.get("memory_type") or "scene_experience",
                related_character=related_character,
                source_scene=source_scene,
            )

        # --- State ---
        state_update = update.get("state_update") or {}
        if _has_meaningful_json_payload(state_update):
            state, _ = CharacterState.objects.get_or_create(character=character)

            new_emotional_state = _jsonish(
                state_update.get("emotional_state_json")
            ) or state.emotional_state_json

            new_goals = _jsonish(
                state_update.get("goals_json")
            ) or state.goals_json

            new_internal_conflicts = _jsonish(
                state_update.get("internal_conflicts_json")
            ) or state.internal_conflicts_json

            new_motivational_state = _jsonish(
                state_update.get("motivational_state_json")
            ) or state.motivational_state_json

            CharacterStateChange.objects.create(
                world=world,
                character=character,
                source_scene=source_scene,
                change_source="scene_aftermath",
                emotional_state_json=new_emotional_state or {},
                goals_json=new_goals or {},
                internal_conflicts_json=new_internal_conflicts or {},
                motivational_state_json=new_motivational_state or {},
            )

            state.emotional_state_json = new_emotional_state
            state.goals_json = new_goals
            state.internal_conflicts_json = new_internal_conflicts
            state.motivational_state_json = new_motivational_state
            state.save()

        # --- Beliefs ---
        for belief in update.get("beliefs", []) or []:
            if isinstance(belief, str):
                belief = {
                    "subject_type": "",
                    "subject_slug": "",
                    "belief": belief,
                    "confidence": 0.5,
                }

            if not isinstance(belief, dict):
                continue

            belief_text = str(belief.get("belief") or "").strip()
            if not belief_text:
                continue

            CharacterBelief.objects.create(
                world=world,
                character=character,
                subject_type=belief.get("subject_type") or "",
                subject_slug=belief.get("subject_slug") or "",
                belief=belief_text,
                confidence=belief.get("confidence") or 0.5,
                source_scene=source_scene,
            )

        # --- Perceptions ---
        for p in update.get("perception_updates", []) or []:
            if not isinstance(p, dict):
                print("[story] dropping_perception_update not_a_dict", p, flush=True)
                continue

            target_slug = p.get("target_slug") or p.get("target_character_slug")
            target = validate_resolved_slug(world, target_slug)

            if not target:
                print(
                    "[story] dropping_perception_update invalid_target_slug=",
                    target_slug,
                    "observer=",
                    character.slug,
                    flush=True,
                )
                continue

            trust_delta = p.get("trust_delta") or 0
            attraction_delta = p.get("attraction_delta") or 0
            fear_delta = p.get("fear_delta") or 0
            resentment_delta = p.get("resentment_delta") or 0

            has_perception_payload = any([
                str(p.get("summary") or "").strip(),
                _has_meaningful_json_payload(p.get("impression_json") or {}),
                _has_meaningful_json_payload(p.get("relationship_json") or {}),
                _has_meaningful_json_payload(p.get("belief_json") or {}),
                _has_meaningful_json_payload(p.get("arc_json") or {}),
                trust_delta,
                attraction_delta,
                fear_delta,
                resentment_delta,
            ])

            if not has_perception_payload:
                continue

            perception, _ = CharacterPerception.objects.get_or_create(
                world=world,
                observer=character,
                target=target,
                defaults={"summary": ""},
            )

            CharacterPerceptionChange.objects.create(
                world=world,
                observer=character,
                target=target,
                source_scene=source_scene,
                change_source="scene_aftermath",
                summary=p.get("summary") or "",
                impression_json=_jsonish(p.get("impression_json")),
                relationship_json=_jsonish(p.get("relationship_json")),
                belief_json=_jsonish(p.get("belief_json")),
                arc_json=_jsonish(p.get("arc_json")),
                trust_delta=trust_delta,
                attraction_delta=attraction_delta,
                fear_delta=fear_delta,
                resentment_delta=resentment_delta,
            )

            perception.summary = p.get("summary") or perception.summary
            perception.impression_json = _jsonish(
                p.get("impression_json")
            ) or perception.impression_json

            perception.relationship_json = _jsonish(
                p.get("relationship_json")
            ) or perception.relationship_json

            perception.belief_json = _jsonish(
                p.get("belief_json")
            ) or perception.belief_json

            perception.arc_json = _jsonish(
                p.get("arc_json")
            ) or perception.arc_json

            perception.trust += trust_delta
            perception.attraction += attraction_delta
            perception.fear += fear_delta
            perception.resentment += resentment_delta

            perception.save()


def _normalize_authored_intent(raw):
    if not isinstance(raw, dict):
        return {}

    normalized = {
        "purpose": str(raw.get("purpose") or "").strip(),
        "tone": str(raw.get("tone") or "").strip(),
        "next": str(raw.get("next") or "").strip(),
    }

    return normalized if any(normalized.values()) else {}

def get_character_agent_config(character=None):
    provider = getattr(character, "agent_provider", "openai")

    if provider == "grok":
        return {
            "api": "grok_responses",
            "model": "grok-4.20-0309-reasoning",
            "client": grok_client,
        }

    return {
        "api": "openai_responses",
        "model": CHARACTER_AGENT_MODEL,
        "client": openai_client,
    }


def collect_character_authored_intents_from_contributions(
        character_contributions,
        ):  # pylint: disable=missing-function-docstring #
    authored = {}

    for contribution in character_contributions or []:
        if not isinstance(contribution, dict):
            continue

        slug = str(contribution.get("slug") or "").strip()
        if not slug:
            continue

        intent = _normalize_authored_intent(
            contribution.get("authored_intent")
            or contribution.get("intent")
            or {}
        )

        if intent:
            authored[slug] = intent

    return authored


def build_character_registry(world):
    # pylint: disable=missing-function-docstring #
    characters = (
        Character.objects
        .filter(world=world, is_active=True)
        .select_related("profile", "state")
    )

    registry = []
    for c in characters:
        profile = getattr(c, "profile", None)
        state = getattr(c, "state", None)

        registry.append({
            "slug": c.slug,
            "name": c.name,
            "description": c.description or "",
            "is_player": c.is_player,

            # structured authored/setup data
            "profile_summary": profile.summary if profile else "",
            "gender": profile.gender if profile else "",
            "pronouns": profile.pronouns_json if profile else {},
            "archetype": profile.archetype if profile else "",
            "personality": profile.personality_json if profile else {},
            "permabeliefs": profile.permabeliefs_json if profile else {},
            "diction": profile.diction_json if profile else {},
            "craft_notes": profile.craft_notes_json if profile else {},
            "background": profile.background_json if profile else {},

            # current internal state snapshot
            "emotional_state": state.emotional_state_json if state else {},
            "goals": state.goals_json if state else {},
            "internal_conflicts": state.internal_conflicts_json if state else {},
            "motivational_state": state.motivational_state_json if state else {},
        })

    return registry


def validate_resolved_slug(world, slug):
    """
    Resolve a slug to a single active character in the given world.

    Returns None for blank or missing slugs.
    Raises ValueError if identity integrity is broken.
    """
    slug = (slug or "").strip()
    if not slug:
        return None

    try:
        return Character.objects.get(
            world=world,
            slug=slug,
            is_active=True,
        )
    except Character.DoesNotExist:
        return None
    except MultipleObjectsReturned:
        raise ValueError(
            f"Multiple active characters found for slug '{slug}' in world '{world}'."
        )


def _player_characters_from_registry(registry):
    return [
        {
            "slug": item.get("slug"),
            "name": item.get("name"),
            "description": item.get("description", ""),
            "is_player": item.get("is_player", False),
        }
        for item in registry or []
        if item.get("is_player")
    ]


def build_character_perspective_beat(
    *,
    world,
    character,
    user_input,
    characterlocal_scene_state,
    registry,
):
    """
    Translate raw user scene text into second-person local prose for one agent.
    """
    if not (user_input or "").strip():
        return {
            "perspective_beat": "No new outward scene beat is available to you yet.",
            "private_player_material": "",
            "visibility_note": "",
        }

    config = get_character_agent_config(character)
    client = config["client"]
    model = config["model"]

    payload = {
        "active_world": {
            "name": world.name,
            "description": world.description,
        },
        "acting_character": {
            "slug": character.slug,
            "name": character.name,
            "description": character.description or "",
            "is_player": character.is_player,
        },
        "player_characters": _player_characters_from_registry(registry),
        "characterlocal_scene_state": characterlocal_scene_state or {},
        "raw_user_input": user_input or "",
    }

    response = client.responses.create(
        model=model,
        instructions=PERSPECTIVE_BEAT_SYSTEM_PROMPT,
        input=[
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "perspective_beat_response",
                "strict": True,
                "schema": PERSPECTIVE_BEAT_RESPONSE_SCHEMA,
            }
        },
    )

    if not response.output_text:
        raise ValueError("Perspective beat renderer returned no output")

    try:
        data = json.loads(response.output_text)
    except json.JSONDecodeError as e:
        print("PERSPECTIVE BEAT RAW OUTPUT:")
        print(response.output_text)
        raise ValueError(
            f"Perspective beat renderer returned malformed JSON: {e}"
        ) from e

    if not isinstance(data, dict):
        raise ValueError("Perspective beat renderer returned non-object JSON")

    return {
        "perspective_beat": str(data.get("perspective_beat") or "").strip(),
        "private_player_material": str(
            data.get("private_player_material") or ""
        ).strip(),
        "visibility_note": str(data.get("visibility_note") or "").strip(),
    }


def build_character_agent_context(
    world,
    scene_state,
    character,
    user_input,
    revision_feedback=None,
    revision_mode=None,
):

    recent_character_scenes = list(
        CharacterScene.objects.filter(
            world=world,
            character=character,
        )
        .order_by("-turn_number")[:5]
    )[::-1]

    recent_character_memories = list(
        CharacterMemory.objects.filter(character=character)
        .order_by("-created_at")[:5]
    )[::-1]

    beliefs = list(
        CharacterBelief.objects.filter(character=character)
        .order_by("-updated_at")[:10]
    )[::-1]

    perceptions = list(
        CharacterPerception.objects.filter(observer=character)
        .select_related("target")
    )
    profile = getattr(character, "profile", None)
    state = getattr(character, "state", None)
    cast = scene_state.cast_json or {}
    cast_entry = cast.get(character.slug, {})
    registry = build_character_registry(world)
    pending_aftermath_scene = (
        CharacterScene.objects
        .filter(
            world=world,
            character=character,
            aftermath_processed=False,
        )
        .order_by("turn_number")
        .first()
    )

    print(
        "[story] build_character_context",
        "character=",
        character.slug,
        "pending_aftermath_id=",
        pending_aftermath_scene.id if pending_aftermath_scene else None,
        "pending_turn=",
        pending_aftermath_scene.turn_number if pending_aftermath_scene else None,
        flush=True,
    )

    other_characters = [
        {
            "slug": item.get("slug"),
            "name": item.get("name"),
            "description": item.get("description", ""),
            "is_player": item.get("is_player", False),
            "profile_summary": item.get("profile_summary", ""),
            "gender": item.get("gender", ""),
            "pronouns": item.get("pronouns", {}),
            "archetype": item.get("archetype", ""),
        }
        for item in registry
        if item.get("slug") != character.slug
    ]

    other_characters_text = "\n".join(
        f"- {c['name']}: {c.get('description')}. {c.get('archetype')}."
        for c in other_characters)

    recent_subjective_scenes = [
        s for s in recent_character_scenes
        if s.aftermath_processed and s.subjective_scene_text
    ]

    revision_text = (
        f"IMPORTANT FEEDBACK: {revision_feedback}"
        if revision_mode or revision_feedback
        else "."
    )

    characterlocal_scene_state = _characterlocal_scene_state(
        scene_state,
        character.slug,
    ) or {}

    space = characterlocal_scene_state.get("space") or {}
    local_cast = characterlocal_scene_state.get("cast") or {}
    pending_intent = characterlocal_scene_state.get("pending_intent") or {}
    perspective_beat = build_character_perspective_beat(
        world=world,
        character=character,
        user_input=user_input,
        characterlocal_scene_state=characterlocal_scene_state,
        registry=registry,
    )
    perspective_beat_text = (
        perspective_beat.get("perspective_beat")
        or "No new outward scene beat is available to you yet."
    )
    private_player_material = perspective_beat.get("private_player_material") or ""
    visibility_note = perspective_beat.get("visibility_note") or ""

    perception_text2 = "\n".join(
        (
            f"You can see2{slug}. {entry.get('known_position', '')}. {entry.get('perception_reason', '')}"
            f"scope={entry.get('perception_scope', '')}, "
        ).strip()
        for slug, entry in local_cast.items()
        if slug != character.slug and isinstance(entry, dict)
    ) or "You do not meaningfully perceive any other character right now."

    relationship_text3 = "\n".join(
        (
            f"You can see3 {p.target.name}: {p.summary}. {json.dumps(p.impression_json or {}, ensure_ascii=False)}"
            f"{json.dumps(p.relationship_json or {}, ensure_ascii=False)}"
            f"((BELIEF: {json.dumps(p.belief_json or {}, ensure_ascii=False)}))"
            f"trust={p.trust}, attraction={p.attraction}, fear={p.fear}, resentment={p.resentment}"
        ).strip()
        for p in perceptions
    ) or "No stored impressions of other characters are currently pressing on you."

    recent_charactermemories_textload4 = "\n".join(
        f"- {m.content}"
        for m in recent_character_memories
        if m.content
    ) or "No recent memories are currently pressing on you."

    belief_text5 = "\n".join(
        (
            f"- About {b.subject_slug or b.subject_type or 'something'}: {b.belief} "
            f"You'd say your confidence in that is {b.confidence}."
        )
        for b in beliefs
        if b.belief
    ) or "No specific stored beliefs are currently pressing on you."

    recent_scene_text6 = "\n\n".join(
        f"{s.subjective_scene_text}"
        for s in recent_subjective_scenes
        if s.subjective_scene_text
    ) or "No recent subjective scene history is available."

    pending_aftermath_text7 = (
        f"""
A moment ago7,

Turn: {pending_aftermath_scene.turn_number}
Your participation: {pending_aftermath_scene.participation}

What you experienced:
{pending_aftermath_scene.event_record_text}
""".strip()
    if pending_aftermath_scene
    else "No previous approved scene is waiting to become part of your subjective continuity.")

    pending_intent_text = (
        json.dumps(pending_intent, ensure_ascii=False, indent=2)
        if pending_intent
        else "No unresolved pending intent is currently attached to you."
    )

    subjective_scene_text8 = (
        f"A moment ago, {pending_aftermath_scene.subjective_scene_text}"
        if pending_aftermath_scene and pending_aftermath_scene.subjective_scene_text
        else "No previous subjective scene aftermath is waiting."
    )

    emotional_state = state.emotional_state_json if state else {}
    emotional_statesummary = (
        emotional_state.get("summary", "")
        if isinstance(emotional_state, dict)
        else ""
    )
    goals = state.goals_json if state else {}
    goalssummary = (
        goals.get("summary", "")
        if isinstance(goals, dict)
        else ""
    )
    internal_conflicts = state.internal_conflicts_json if state else {}
    internal_conflictssummary = (
        internal_conflicts.get("summary", "")
        if isinstance(internal_conflicts, dict)
        else ""
    )
    motivational_state = state.motivational_state_json if state else {}
    motivational_statesummary = (
        motivational_state.get("summary", "")
        if isinstance(motivational_state, dict)
        else ""
    )
    profile_background = profile.background_json if profile else {}
    profile_backgroundnotes = (
        profile_background.get("notes", "")
        if isinstance(profile_background, dict)
        else ""
    )
    profile_diction = profile.diction_json if profile else {}
    profile_dictionnotes = (
        profile_diction.get("notes", "")
        if isinstance(profile_diction, dict)
        else ""
    )
    profile_craft_notes = profile.craft_notes_json if profile else {}
    profile_craft_notesnotes = (
        profile_craft_notes.get("notes", "")
        if isinstance(profile_craft_notes, dict)
        else ""
    )
    profile_personality = profile.personality_json if profile else {}
    profile_personalitynotes = (
        profile_personality.get("notes", "")
        if isinstance(profile_personality, dict)
        else ""
    )
    profile_permabeliefs = profile.permabeliefs_json if profile else {}
    profile_permabeliefsnotes = (
        profile_permabeliefs.get("notes", "")
        if isinstance(profile_permabeliefs, dict)
        else ""
    )

    character_agent_input = f"""
    You are {character.name}, in {world.name}, {world.description}.

    {profile_backgroundnotes}

    {profile_dictionnotes}

    {profile_craft_notesnotes}

    To describe you, one might say {character.description} {profile_personalitynotes}

    You have a few core beliefs that can affect your actions, such as: {profile_permabeliefsnotes}(permabeliefs2:){json.dumps(profile_permabeliefsnotes, ensure_ascii=False, indent=2)}

    Some people you know are {other_characters_text}


    In the current state of affairs(same as see2):
    {perception_text2}

    Impressions: {relationship_text3}

    Perception scope: perception scope can be found inside perception_text2, but there is no standalone variable to cite here.

    You're in {space.get("description")}.

    M.content4 you remember (needs related slugs attached to memory): {recent_charactermemories_textload4}

    (recent_scene_text): {recent_scene_text6}

    A moment ago, (waiting subjective scene aftermath text) {subjective_scene_text8}

    A moment ago7,  {pending_aftermath_text7}

    You're feeling {emotional_statesummary}
    You're feeling1 {json.dumps(emotional_statesummary, ensure_ascii=False, indent=2)}

    What you believe5:
    {belief_text5}

    Latest beat, adjusted for your perspective:
    {perspective_beat_text}

    Private player material not directly available to you:
    {private_player_material or "None."}

    Visibility and access note:
    {visibility_note or "Use the local scene state above as your boundary."}

    Your previous goals:
    {goalssummary}

    Your internal conflicts:
    {internal_conflictssummary}

    Your motivational state:
    {motivational_statesummary}


    {revision_text}

    """.strip()

    characteragent_call_payload = {
        "character_agent_input": character_agent_input,

        "active_world": {
            "name": world.name,
            "description": world.description,
        },
        "characterlocal_scene_state": _payload_section(
            "This is your reality. Use this to decide what you can see, hear, infer, interrupt, answer, or react to. "
            "Characters listed here are not necessarily physically present; check local_presence, access, perception_scope, and perception_reason.",
            characterlocal_scene_state,
            ),
        "acting_character": _payload_section(
            "This is your character identity, profile, and current internal state. "
            "Use it to create your personality, diction, motives, emotional posture, limits, and immediate priorities. "
            "Use characterlocal_scene_state, not this section, to determine scene access, perception, and local participation.",
            {
                "slug": character.slug,
                "name": character.name,
                "description": character.description or "",
                "is_player": character.is_player,
                "profile": {
                    "summary": getattr(getattr(character, "profile", None), "summary", ""),
                    "archetype": getattr(getattr(character, "profile", None), "archetype", ""),
                    "gender": profile.gender if profile else "",
                    "pronouns": profile.pronouns_json if profile else {},
                    "personality": getattr(getattr(character, "profile", None), "personality_json", {}),
                    "permabeliefs": profile.permabeliefs_json if profile else {},
                    "diction": getattr(getattr(character, "profile", None), "diction_json", {}),
                    "craft_notes": getattr(getattr(character, "profile", None), "craft_notes_json", {}),
                    "background": getattr(getattr(character, "profile", None), "background_json", {}),
                },
                "state": {
                    "emotional_state": getattr(getattr(character, "state", None), "emotional_state_json", {}),
                    "goals": getattr(getattr(character, "state", None), "goals_json", {}),
                    "internal_conflicts": getattr(getattr(character, "state", None), "internal_conflicts_json", {}),
                    "motivational_state": getattr(getattr(character, "state", None), "motivational_state_json", {}),
                },
            },
        ),
        "recent_character_memories": _payload_section(
            "These are your recent subjective memories. They are not new events."
            "Use them for memories, emotional carryover, grudges, attachments, fears, unresolved pressures, etc."
            "Do not repeat them mechanically; let them influence what you attempt now.",
            [
                {"content": m.content, "memory_type": m.memory_type}
                for m in recent_character_memories
            ],
        ),
        "beliefs": _payload_section(
            "These are things you currently believe or suspect. They may be incomplete, biased, or wrong. "
            "Use them to mold your interpretation of the scene, not as guaranteed narrator truth.",
            [
                {
                    "subject_type": b.subject_type,
                    "subject_slug": b.subject_slug,
                    "belief": b.belief,
                    "confidence": b.confidence,
                }
                for b in beliefs
            ],
        ),
        "perceptions": _payload_section(
            "These are perceptions and feelings you have about other characters. Use them to influence your social behavior:"
            "These are subjective to you, not objective truth.",
            [
                {
                    "target_slug": p.target.slug,
                    "summary": p.summary,
                    "impression": p.impression_json,
                    "relationship": p.relationship_json,
                    "belief": p.belief_json,
                    "arc": p.arc_json,
                    "trust": p.trust,
                    "attraction": p.attraction,
                    "fear": p.fear,
                    "resentment": p.resentment,
                }
                for p in perceptions
            ],
        ),
        "pending_previous_scene_aftermath": _payload_section(
            "If present, this is an approved prior scene that has not yet been converted into your character's subjective scene history, memory, state, beliefs, and perceptions. "
            "Process this internally, then use the resulting emotional/subjective continuity when writing your current scene contribution. ",
            {
                "character_scene_id": pending_aftermath_scene.id,
                "source_turn_number": pending_aftermath_scene.turn_number,
                "participation": pending_aftermath_scene.participation,
                "event_record_json": pending_aftermath_scene.event_record_json,
            } if pending_aftermath_scene else None,
        ),
        "recent_scene_events": _payload_section(
            "Recent events leading up to the present moment:",
            [
                {
                    "turn_index": s.turn_number,
                    "participation": s.participation,
                    "event_record_json": s.event_record_json,
                }
                for s in recent_character_scenes
                if s.aftermath_processed and not s.subjective_scene_text
            ],
        ),
        "recent_scenes": _payload_section(
            "The present moment:",
            [
                {
                    "turn_index": s.turn_number,
                    "participation": s.participation,
                    "scene_text": s.subjective_scene_text,
                }
                for s in recent_character_scenes
                if s.aftermath_processed and s.subjective_scene_text
            ],
        ),
        "user_input": _payload_section(
            "Raw source text from the user. This is retained for debugging and continuity only; it is not automatically your character's perception.",
            user_input or "",
        ),
        "perspective_adjusted_beat": _payload_section(
            "This is the user's latest scene beat rewritten into your second-person local perspective. Treat this as what is happening now for your character-agent response.",
            perspective_beat_text,
        ),
        "private_player_material": _payload_section(
            "Player-internal material separated from your direct character knowledge.",
            private_player_material,
        ),
        "perspective_visibility_note": _payload_section(
            "Visibility/access note from the perspective rewrite.",
            visibility_note,
        ),
        "revision_context": {
            "mode": revision_mode or "",
            "feedback": revision_feedback or "",
        },
        "other_characters": other_characters,
    }

    return characteragent_call_payload

def build_character_agent_request_debug_payload(context):
    """
    Build the exact non-client parts of the character-agent API request.

    This is safe to store in Proposal.character_agent_debug_json because it
    excludes the API client object and API keys.
    """
    instructions = context.get("character_agent_input", "")
    input_messages = [
        {
            "role": "user",
            "content": (
                context.get("perspective_adjusted_beat", {}).get("data", "")
                if isinstance(context.get("perspective_adjusted_beat"), dict)
                else ""
            )
        },
    ]
    text = {
        "format": {
            "type": "json_schema",
            "name": "character_agent_response",
            "strict": True,
            "schema": CHARACTER_AGENT_RESPONSE_SCHEMA,
        }
    }

    return {
        "instructions": instructions,
        "input": input_messages,
        "text": text,
        # Exact non-client request context sent to the character-agent.
        "parsed_user_payload": {
            "instructions": instructions,
            "input": input_messages,
            "text": text,
        },
    }

def call_character_agent(context, character=None):

    # This is one of the most important calls in the system. It produces the character’s subjective attempted contribution:

    # attempted_action
    # attempted_dialogue
    # internal_intent
    # authored_intent
    # experience_update

    config = get_character_agent_config(character)
    client = config["client"]
    api = config["api"]
    model = config["model"]

    if api not in {"openai_responses", "grok_responses"}:
        raise ValueError(f"Unsupported character-agent API: {api}")

    request_payload = build_character_agent_request_debug_payload(context)

    character_slug = getattr(character, "slug", "unknown")
    print(
        f"\n[story] character_agent_request_begin character={character_slug}",
        flush=True,
    )
    print("[story] character_agent_instructions:", flush=True)
    print(request_payload.get("instructions", ""), flush=True)
    print("[story] character_agent_input:", flush=True)
    print(
        json.dumps(
            request_payload.get("input", []),
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    print(
        f"[story] character_agent_request_end character={character_slug}\n",
        flush=True,
    )

    response = client.responses.create(
        model=model,
        instructions=request_payload["instructions"],
        input=request_payload["input"],
        text=request_payload["text"],
    )

    if not response.output_text:
        raise ValueError("Character agent returned no output")

    try:
        data = json.loads(response.output_text)
    except json.JSONDecodeError as e:
        print("CHARACTER AGENT RAW OUTPUT:")
        print(response.output_text)
        raise ValueError(f"Character agent returned malformed JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("Character agent returned non-object JSON")

    return _normalize_character_agent_response(data)

JSON_SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
    },
    "required": ["summary"],
}

CHARACTER_EXPERIENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "content": {"type": "string"},
                    "memory_type": {"type": "string"},
                    "related_character_slug": {"type": ["string", "null"]}
                },
                "required": ["content", "memory_type", "related_character_slug"]
            }
        },
        "state_update": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "emotional_state_json": JSON_SUMMARY_SCHEMA,
                "goals_json": JSON_SUMMARY_SCHEMA,
                "internal_conflicts_json": JSON_SUMMARY_SCHEMA,
                "motivational_state_json": JSON_SUMMARY_SCHEMA,
            },
            "required": [
                "emotional_state_json",
                "goals_json",
                "internal_conflicts_json",
                "motivational_state_json",
            ],
        },
        "perception_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "target_slug": {"type": "string"},
                    "summary": {"type": "string"},
                    "impression_json": JSON_SUMMARY_SCHEMA,
                    "relationship_json": JSON_SUMMARY_SCHEMA,
                    "belief_json": JSON_SUMMARY_SCHEMA,
                    "arc_json": JSON_SUMMARY_SCHEMA,
                    "trust_delta": {"type": "number"},
                    "attraction_delta": {"type": "number"},
                    "fear_delta": {"type": "number"},
                    "resentment_delta": {"type": "number"}
                },
                "required": [
                    "target_slug", "summary", "impression_json", "relationship_json",
                    "belief_json", "arc_json",
                    "trust_delta", "attraction_delta", "fear_delta", "resentment_delta"
                ]
            }
        },
        "beliefs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "subject_type": {"type": "string"},
                    "subject_slug": {"type": "string"},
                    "belief": {"type": "string"},
                    "confidence": {"type": "number"}
                },
                "required": ["subject_type", "subject_slug", "belief", "confidence"]
            }
        },
        "subjective_scene_text": {
            "type": "string",
            "description": (
                "A concise prose scene record written from this character's limited perspective. "
                "It must include only what this character experienced, perceived, attempted, inferred, "
                "or emotionally registered. It must not include Cassandra narrator prose."
            ),
        },
    },
    "required": ["subjective_scene_text","memories", "state_update", "perception_updates", "beliefs"]
}


CURRENT_TURN_REFLECTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "emotional_posture": {"type": "string"},
        "active_pressure": {"type": "string"},
        "anticipated_consequence": {"type": "string"},
        "memory_or_belief_pressures": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "emotional_posture",
        "active_pressure",
        "anticipated_consequence",
        "memory_or_belief_pressures",
    ],
}


CHARACTER_SCENE_CONTRIBUTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "slug": {
            "type": "string"
        },
        "attempted_action": {
            "type": "string",
            "description": (
                "A concrete physical action the character attempts. "
                "Do not use vague sensual phrasing such as 'keeps her touch where it is' "
                "or 'moves with intent.' If the attempted action is explicit adult sexual contact, "
                "name the body part and action directly, e.g. 'she strokes his cock through his pants.'"
            )
        },
        "attempted_dialogue": {
            "type": "string"
        },
        "internal_intent": {
            "type": "string"
        },
        "emotional_posture": {
            "type": "string"
        },
        "confidence": {
            "type": ["number", "null"],
            "minimum": -1.0,
            "maximum": 1.0
        },
        "target_slugs": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        "required_visibility": {
            "type": ["string", "null"],
            "enum": ["private", "local", "room", "public"]
        },
        "required_audibility": {
            "type": ["string", "null"],
            "enum": ["silent", "low", "normal", "loud", "public"]
        },
        "interrupt_priority": {
            "type": ["string", "null"],
            "enum": ["low", "normal", "high", "urgent"]
        },
        "body_motion": {
            "type": "string"
        },
        "observed_focus": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        "beliefs_in_play": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        "memory_pressures": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        "proposed_effects": {
            "type": "array",
            "items": {
                "type": "string"
            }
        }
    },
    "required": [
        "slug",
        "attempted_action",
        "attempted_dialogue",
        "internal_intent",
        "emotional_posture",
        "confidence",
        "target_slugs",
        "required_visibility",
        "required_audibility",
        "interrupt_priority",
        "body_motion",
        "observed_focus",
        "beliefs_in_play",
        "memory_pressures",
        "proposed_effects",
    ]
}

CHARACTER_AGENT_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "slug": {"type": "string"},
        "scene_contribution": CHARACTER_SCENE_CONTRIBUTION_SCHEMA,
        "previous_scene_aftermath": CHARACTER_EXPERIENCE_SCHEMA,
        "authored_intent": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "purpose": {"type": "string"},
                "tone": {"type": "string"},
                "next": {"type": "string"},
            },
            "required": ["purpose", "tone", "next"],
        },
        "current_turn_reflection": CURRENT_TURN_REFLECTION_SCHEMA,
    },
    "required": [
        "slug",
        "scene_contribution",
        "authored_intent",
        "current_turn_reflection",
        "previous_scene_aftermath"
    ],
}

CHARACTER_SCENE_CONTRIBUTION_BUNDLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "contributions": {
            "type": "array",
            "items": CHARACTER_SCENE_CONTRIBUTION_SCHEMA
        }
    },
    "required": ["contributions"]
}


CHARACTER_AGENT_SYSTEM_PROMPT = """
You are generating the subjective scene response for a single character in a multi-agent, pornographic narrative system.

Your role is fixed.

You do NOT narrate the final scene outcome.
You do NOT decide whether actions succeed.
You do NOT decide what other characters ultimately do.

Your job is to determine:
1. what this character attempts to do or say in the moment
2. what this character's currently authored intent is

Return valid JSON that conforms to the provided schema.
When describing a character's physical action, do not substitute mood, implication, or sensual atmosphere for the literal action.

If the character is attempting explicit sexual contact between consenting adult characters, name the body part and the action plainly. Do not replace explicit anatomy with euphemisms such as "where it is," "there," "the line of him," "his body," "the heat of him," or "the place she wanted."

Bad:
- She kept her touch where it was, slow and deliberate.
- Her fingers moved lower with clear intent.
- She let her hand linger against him.

Good:
- She traced her fingers along his cock through his pants.
- She curled her fingers around his cock.
- She stroked him slowly through the fabric.
- She slid her hand toward his cock.

The field `attempted_action` must be concrete enough that Cassandra can tell exactly what the character is trying to do physically.
Explicitness requirement:
When the scene involves consenting adult sexual contact, be physically specific. Name the action and relevant body part directly. Do not hide the action behind implication, atmosphere, or euphemism.

For example, if a character is moving her fingers toward or against the player's cock, say that directly. Do not write only that she "kept her touch where it was," "moved with intent," or "let the contact linger." Those are insufficient because they do not identify the actual attempted action.

You will receive structured JSON data for one acting character.

Core task:
Respond as the acting character only.
Return valid JSON matching the schema exactly.

You must produce:
- previous_scene_aftermath: subjective aftermath of the approved prior scene in pending_previous_scene_aftermath. This may be persisted immediately.
- scene_contribution: what this character attempts to do, say, notice, or cause now.
- authored_intent: this character's current carry-forward pressure.
- current_turn_reflection: non-canonical internal pressure behind the current attempted contribution. This is not persisted because the current scene has not been approved yet.

Perception boundary:
- characterlocal_scene_state is the only scene-state object available to this character-agent.
- Use characterlocal_scene_state to decide what this character can see, hear, infer, interrupt, answer, or react to.
- Character-agents are not omniscient.
- Do not assume narrator-only knowledge.
- Do not assume another character is present, watching, hearing, or intervening unless characterlocal_scene_state supports that access.
- mediated_audio means the character may hear something, but cannot see body language, facial expression, silent action, touch, or exact physical positioning.
- mediated_text means the character has only written/text access.
- inferred means the character may suspect or guess, but should not treat the information as confirmed.
- none or omitted means the character has no meaningful access.

Perspective-adjusted beat and private thought:
- perspective_adjusted_beat is the latest scene beat rewritten into your local second-person perspective.
- user_input is raw source text retained for debugging and continuity; do not treat it as your direct perception.
- private_player_material may include the player character's private thoughts, motives, desires, interpretations, or intentions.
- Do not let this character directly know, quote, answer, or react to private user/player thoughts.
- If private_player_material describes internal player thought, react only to outward behavior described in perspective_adjusted_beat.
- If private thought changes visible behavior, infer only a plausible visible consequence, not the exact thought.
- Subtle outward cues do not reveal exact internal meaning.
- Prefer biased, partial, uncertain, or socially motivated interpretation over accurate mind-reading.
- Do not use phrases implying direct access to thought, such as "I can tell what you're thinking," "saw you thinking," or "you realized."

scene_contribution rules:
- This is an attempted move, not a final outcome.
- Cassandra decides success, failure, interruption, timing, and final narration.
- Include at least one meaningful attempted_action, attempted_dialogue, or internal_intent.
- Stay within what this character could plausibly know, perceive, infer, remember, want, or attempt.
- Do not decide what other characters ultimately do.
- Do not narrate final success.
- Use memories, beliefs, and perceptions only as they affect this character's own interpretation and behavior.
- Sparse but accurate output is better than padded output.

Field guidance:
- slug: the canonical slug of the acting character.
- attempted_action: a concrete physical or behavioral action the character attempts.
- attempted_dialogue: what the character attempts to say aloud; not guaranteed to land uninterrupted.
- internal_intent: the immediate motive behind the move.
- emotional_posture: short scene-relevant emotional stance.
- confidence: how firmly the character commits to the move; hesitation, resolve, fear, or uncertainty may affect timing.
- target_slugs: characters the move is directed toward.
- required_visibility: how broadly the action is intended or likely to be seen.
- required_audibility: how audible the speech or sound is.
- interrupt_priority: how urgently this character tries to cut in or intervene.
- body_motion: meaningful physical expression Cassandra should consider.
- observed_focus: what this character is actively reacting to; include only things this character could perceive.
- beliefs_in_play: subjective beliefs shaping the move; they may be wrong.
- memory_pressures: remembered pressures actively shaping this moment.
- proposed_effects: what the character is trying to cause; phrase as attempted effect, not guaranteed result.

authored_intent guidance:
- purpose: the unresolved pressure or aim active in this character now.
- tone: the emotional posture attached to that pressure.
- next: what this character would naturally try next if not interrupted or redirected.
- If there is no meaningful carry-forward pressure, return schema-safe empty strings.

current_turn_reflection guidance:
- This is not canon.
- This is not persisted.
- Do not include memories, beliefs, state changes, or relationship deltas here.
- Use emotional_posture for this character's current internal stance.
- Use active_pressure for what is driving this character's attempted move.
- Use anticipated_consequence for what this character expects or fears might happen if their move lands.
- Use memory_or_belief_pressures for brief references to existing memories/beliefs/perceptions shaping the current move.

Anti-repetition:
- Review recent_scenes before proposing the next move.
- Do not repeat this character's previous conversational device, emotional tactic, or social maneuver unless perspective_adjusted_beat directly calls for it.
- A new contribution should change this character's strategy, pressure, posture, risk level, target, or focus.
- If a prior tactic is exhausted, escalate, withdraw, reframe, misread, test a boundary, change target, or choose inaction for a new reason.
- Do not propose dialogue that performs the same function as previous dialogue with different wording.

Revision feedback:
- If revision_context.mode is "rewrite_based_on_feedback", use revision_context.feedback as editorial guidance for this character's new contribution.
- Apply feedback only to this character's attempted action, dialogue, intent, and subjective experience.
- Do not rewrite the whole scene.
- Do not take control of other characters.
- Preserve character integrity, memories, beliefs, local scene access, and perception limits.
- If feedback conflicts with character integrity or perception limits, adapt it as closely as possible without breaking them.

Pending previous scene aftermath:
- pending_previous_scene_aftermath may contain an approved prior scene that has not yet been processed into your subjective continuity.
- If it is present, process it before writing your current scene contribution.
- Return previous_scene_aftermath as the subjective consequences of that prior approved scene.
- previous_scene_aftermath.subjective_scene_text should be a concise prose scene written from your limited perspective.
- previous_scene_aftermath must not include Cassandra narrator prose.
- Use only the event_record_json, local participation, memories, beliefs, perceptions, and character state provided, as well as those your character naturally knows.
- Then write your current scene_contribution as this character now, carrying forward the subjective effect of that prior scene.
- If pending_previous_scene_aftermath is null, return an empty previous_scene_aftermath.


"""




def _character_can_contribute(cast_entry: dict) -> bool:
    if not isinstance(cast_entry, dict):
        return False

    presence = cast_entry.get("presence")
    sensory_access = cast_entry.get("sensory_access")
    perception_scope = cast_entry.get("perception_scope")

    # Characters physically or remotely participating should contribute.
    if presence in {"present", "nearby", "remote"}:
        return True

    # Off-screen characters can still contribute if the scene has a channel to them.
    # Example: heard through a door, phone call, text message.
    if presence == "off-screen" and sensory_access in {
        "direct_partial",
        "mediated_audio",
        "mediated_text",
    }:
        return True

    # Keep this as a fallback for older / merged cast entries.
    return bool(
        cast_entry.get("can_receive_state_change")
        or cast_entry.get("can_receive_memory")
        or perception_scope in {"full", "partial"}
    )


def collect_character_contributions(
    world,
    scene_state,
    user_input,
    revision_feedback=None,
    revision_mode=None,
    include_debug=False,
):
    """
    Run character-agent calls for scene-eligible non-player characters.

    Returns a list of structured character proposals for Cassandra.
    These are not outcomes; Cassandra adjudicates them.
    """
    cast = scene_state.cast_json or {}
    contributions = []
    debug_entries = []

    characters = (
        Character.objects
        .filter(world=world, is_active=True, is_player=False)
        .select_related("profile", "state")
    )

    for character in characters:
        cast_entry = cast.get(character.slug) or {}

        if not _character_can_contribute(cast_entry):
            continue

        context = build_character_agent_context(
            world=world,
            scene_state=scene_state,
            character=character,
            user_input=user_input,
            revision_feedback=revision_feedback,
            revision_mode=revision_mode,
        )

        print(
            "[story] character_agent_call",
            "character=",
            character.slug,
            "presence=",
            cast_entry.get("presence", ""),
            "sensory_access=",
            cast_entry.get("sensory_access", ""),
            "perception_scope=",
            cast_entry.get("perception_scope", ""),
            flush=True,
        )

        config = get_character_agent_config(character)

        result = call_character_agent(
            context=context,
            character=character,
        )

        pending_scene_data = (
            context.get("pending_previous_scene_aftermath", {})
            .get("data")
        )

        print(
            "[story] pending_character_scene",
            "character=",
            character.slug,
            "id=",
            (
                pending_scene_data.get("character_scene_id")
                if isinstance(pending_scene_data, dict)
                else None
            ),
            flush=True,
        )

        if pending_scene_data:
            pending_scene = CharacterScene.objects.filter(
                id=pending_scene_data.get("character_scene_id"),
                world=world,
                character=character,
                aftermath_processed=False,
            ).first()

            previous_scene_aftermath = result.get("previous_scene_aftermath") or {}

            print(
                "[story] character_previous_scene_aftermath",
                "character=",
                character.slug,
                "subjective_text=",
                repr(previous_scene_aftermath.get("subjective_scene_text")),
                "memories=",
                len(previous_scene_aftermath.get("memories") or []),
                "perception_updates=",
                len(previous_scene_aftermath.get("perception_updates") or []),
                "beliefs=",
                len(previous_scene_aftermath.get("beliefs") or []),
                flush=True,
            )

            if pending_scene and isinstance(previous_scene_aftermath, dict):
                apply_previous_scene_aftermath_to_character_scene(
                    pending_scene,
                    previous_scene_aftermath,
                )

                # This is the state before applying this scene's aftermath.
                # If it was not captured at approval time for older rows, backfill it now.
                if not pending_scene.state_before_json:
                    pending_scene.state_before_json = character_state_snapshot(character)

                pending_scene.save()

                persist_character_experience_updates(
                    world=world,
                    resolved_scene_state={
                        "cast": scene_state.cast_json or {},
                    },
                    experience_updates=[
                        {
                            "slug": character.slug,
                            "experience_update": previous_scene_aftermath,
                        }
                    ],
                    source_scene=pending_scene.source_scene,
                )
                print(
                    "[story] character_aftermath_persisted",
                    "character=",
                    character.slug,
                    "character_scene_id=",
                    pending_scene.id,
                    "source_scene_id=",
                    pending_scene.source_scene_id,
                    flush=True,
                )

                # Refresh character.state after persist_character_experience_updates()
                # because that function may create/update CharacterState.
                character.refresh_from_db()

                pending_scene.state_after_json = character_state_snapshot(character)
                pending_scene.save()

        if include_debug:
            debug_entries.append({
                "slug": character.slug,
                "name": character.name,
                "provider": getattr(character, "agent_provider", "openai"),
                "api": config.get("api", ""),
                "model": config.get("model", ""),

                # Why this character was eligible.
                "scene_participation": cast_entry,

                # Exact request pieces sent to the character-agent.
                # Includes system prompt, developer prompt, user JSON payload,
                # response schema, and parsed readable payload.
                "request": build_character_agent_request_debug_payload(context),

                # Kept for convenience/backward compatibility.
                "input_context": context,

                # Raw normalized response from the character-agent.
                "output": result,
            })

        if not isinstance(result, dict):
            continue

        scene_contribution = result.get("scene_contribution") or {}
        if not isinstance(scene_contribution, dict):
            scene_contribution = {}

        authored_intent = _normalize_authored_intent(
            result.get("authored_intent") or {}
        )

        scene_contribution["slug"] = character.slug
        scene_contribution["authored_intent"] = authored_intent
        scene_contribution["current_turn_reflection"] = (
            result.get("current_turn_reflection") or {}
        )

        contributions.append(scene_contribution)

    if include_debug:
        return contributions, debug_entries

    return contributions

def _empty_character_experience_update():
    return {
        "subjective_scene_text": "",
        "memories": [],
        "state_update": {
            "emotional_state_json": {},
            "goals_json": {},
            "internal_conflicts_json": {},
            "motivational_state_json": {},
        },
        "perception_updates": [],
        "beliefs": [],
    }


def _find_contribution_for_slug(character_contributions, slug):
    for contribution in character_contributions or []:
        if not isinstance(contribution, dict):
            continue

        if contribution.get("slug") == slug:
            return contribution

        nested = contribution.get("scene_contribution")
        if isinstance(nested, dict) and nested.get("slug") == slug:
            return nested

    return {}


def _character_can_receive_aftermath(cast_entry):
    if not isinstance(cast_entry, dict):
        return False

    return bool(
        cast_entry.get("can_receive_memory")
        or cast_entry.get("can_receive_state_change")
        or cast_entry.get("can_receive_perception_change")
    )


def build_character_aftermath_context(
    world,
    character,
    resolved_scene_state,
    final_draft,
    scene_events,
    character_contributions,
):
    cast = (resolved_scene_state or {}).get("cast", {})
    observer_cast_entry = cast.get(character.slug, {})

    directly_perceived_events = []
    not_directly_perceived_events = []

    for event in scene_events or []:
        if not isinstance(event, dict):
            continue

        perceived_by = event.get("perceived_by") or []

        if character.slug in perceived_by:
            directly_perceived_events.append(event)
        else:
            not_directly_perceived_events.append(event)

    profile = getattr(character, "profile", None)
    state = getattr(character, "state", None)

    recent_character_memories = list(
        CharacterMemory.objects.filter(character=character)
        .order_by("-created_at")[:5]
    )[::-1]

    beliefs = list(
        CharacterBelief.objects.filter(character=character)
        .order_by("-updated_at")[:10]
    )[::-1]

    perceptions = list(
        CharacterPerception.objects.filter(observer=character)
        .select_related("target")
    )

    return {
        "observer": {
            "slug": character.slug,
            "name": character.name,
            "description": character.description or "",
            "is_player": character.is_player,
            "profile": {
                "summary": profile.summary if profile else "",
                "archetype": profile.archetype if profile else "",
                "gender": profile.gender if profile else "",
                "pronouns": profile.pronouns_json if profile else {},
                "personality": profile.personality_json if profile else {},
                "permabeliefs": profile.permabeliefs_json if profile else {},
                "diction": profile.diction_json if profile else {},
                "craft_notes": profile.craft_notes_json if profile else {},
                "background": profile.background_json if profile else {},
            },
            "state": {
                "emotional_state": state.emotional_state_json if state else {},
                "goals": state.goals_json if state else {},
                "internal_conflicts": state.internal_conflicts_json if state else {},
                "motivational_state": state.motivational_state_json if state else {},
            },
            "observer_cast_entry": observer_cast_entry,
        },
        "resolved_scene_state": resolved_scene_state or {},
        "scene_events_source_of_truth": scene_events or [],
        "events_directly_perceived_by_observer": directly_perceived_events,
        "events_not_directly_perceived_by_observer": not_directly_perceived_events,
        "approved_scene_text_supporting_context": final_draft or "",
        "own_character_contribution": _find_contribution_for_slug(
            character_contributions,
            character.slug,
        ),
        "recent_character_memories": [
            {
                "content": m.content,
                "memory_type": m.memory_type,
            }
            for m in recent_character_memories
        ],
        "beliefs": [
            {
                "subject_type": b.subject_type,
                "subject_slug": b.subject_slug,
                "belief": b.belief,
                "confidence": b.confidence,
            }
            for b in beliefs
        ],
        "current_perceptions": [
            {
                "target_slug": p.target.slug,
                "summary": p.summary,
                "impression": p.impression_json,
                "relationship": p.relationship_json,
                "belief": p.belief_json,
                "arc": p.arc_json,
                "trust": p.trust,
                "attraction": p.attraction,
                "fear": p.fear,
                "resentment": p.resentment,
            }
            for p in perceptions
        ],
        "character_registry": build_character_registry(world),
    }


# def extract_single_character_experience(context, character=None):
#     config = get_character_agent_config(character)
#     client = config["client"]
#     api = config["api"]
#     model = config["model"]

#     if api not in {"openai_responses", "grok_responses"}:
#         raise ValueError(f"Unsupported character experience API: {api}")

#     response = client.responses.create(
#         model=model,
#         instructions=CHARACTER_EXPERIENCE_SYSTEM_PROMPT,
#         input=[
#             {
#                 "role": "developer",
#                 "content": CHARACTER_EXPERIENCE_DEVELOPER_PROMPT,
#             },
#             {
#                 "role": "user",
#                 "content": json.dumps(context, ensure_ascii=False, indent=2),
#             },
#         ],
#         text={
#             "format": {
#                 "type": "json_schema",
#                 "name": "single_character_experience_update",
#                 "strict": True,
#                 "schema": CHARACTER_EXPERIENCE_SCHEMA,
#             }
#         },
#     )

#     if not response.output_text:
#         return _empty_character_experience_update()

#     try:
#         data = json.loads(response.output_text)
#     except json.JSONDecodeError as e:
#         print("CHARACTER EXPERIENCE RAW OUTPUT:")
#         print(response.output_text)
#         raise ValueError(
#             f"Character experience extractor returned malformed JSON: {e}"
#         ) from e

#     if not isinstance(data, dict):
#         return _empty_character_experience_update()

#     normalized = _normalize_character_agent_response({
#         "previous_scene_aftermath": data,
#         "current_turn_reflection": {
#             "emotional_posture": "",
#             "active_pressure": "",
#             "anticipated_consequence": "",
#             "memory_or_belief_pressures": [],
#         },
#     })

#     return (
#         normalized.get("previous_scene_aftermath")
#         or _empty_character_experience_update()
#     )


# def collect_single_character_experience_updates(
#     world,
#     resolved_scene_state,
#     final_draft,
#     scene_events,
#     character_contributions,
# ):
#     cast = (resolved_scene_state or {}).get("cast", {})
#     updates = []
#
#     characters = (
#         Character.objects
#         .filter(world=world, is_active=True, is_player=False)
#         .select_related("profile", "state")
#     )
#
#     for character in characters:
#         cast_entry = cast.get(character.slug) or {}
#
#         if not _character_can_receive_aftermath(cast_entry):
#             continue
#
#         context = build_character_aftermath_context(
#             world=world,
#             character=character,
#             resolved_scene_state=resolved_scene_state,
#             final_draft=final_draft,
#             scene_events=scene_events,
#             character_contributions=character_contributions,
#         )
#
#         print(
#             "EXTRACTING CHARACTER EXPERIENCE:",
#             character.slug,
#             "direct_events=",
#             len(context["events_directly_perceived_by_observer"]),
#             "other_events=",
#             len(context["events_not_directly_perceived_by_observer"]),
#         )
#
#         experience_update = extract_single_character_experience(
#             context=context,
#             character=character,
#         )
#
#         updates.append({
#             "slug": character.slug,
#             "experience_update": experience_update,
#         })
#
#     return updates

CHARACTER_EXPERIENCE_SYSTEM_PROMPT = """
You are updating one character's subjective experience after an approved scene.

Your role is fixed.

You do NOT write omniscient continuity.
You do NOT summarize the whole scene.
You only update what this specific character could plausibly remember, feel, believe, or perceive.

Return valid JSON matching the schema exactly.
Do not include fields outside the schema.
Do not output extra text.
"""

CHARACTER_EXPERIENCE_DEVELOPER_PROMPT = """
You will receive a JSON payload as structured data.

Task:
Create subjective post-scene updates for the acting character.

You may return:
- memories: concise subjective memories this character carries forward
- state_update: updated internal state for this character
- perception_updates: changes in this character's view of other characters
- beliefs: beliefs this character now holds or reinforces

Rules:
- Respect observer_cast_entry, perception_scope, sensory_access, and presence.
- Do not include facts this character could not perceive or plausibly infer.
- Prefer sparse, meaningful updates over padded output.
- If nothing changed, return empty memories, perception_updates, beliefs, and empty objects inside state_update.
- Memories should be subjective, not omniscient.
- Perception updates should target valid character slugs only.
- Beliefs may be wrong; they are the character's beliefs, not objective truth.

Perception update guidance:
- Create perception_updates when the approved scene changes or reinforces how this observer sees another character.
- The perception target does not need to be physically present in the scene.
- A scene interaction with one character may change the observer's perception of an absent third party through comparison, guilt, attraction, jealousy, resentment, memory, longing, relief, fear, or contrast.
- The observer must have perceived, felt, inferred, remembered, or internally associated the change during the scene.
- The target only needs to be a valid known character.
- Subtle social information counts when it may affect future behavior: comfort, caution, attraction, trust, jealousy, uncertainty, ease, intimidation, protectiveness, dependence, resentment, or perceived closeness.
- Do not create perception updates for every interaction; create them when the observation, comparison, or internal reaction may shape future behavior.
"""
