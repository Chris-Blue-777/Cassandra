#characters.py#

import json
from django.core.exceptions import MultipleObjectsReturned
from django.conf import settings
from django.db import transaction
from openai import OpenAI
from story.models import (
    Character,
    CharacterMemory,
    CharacterBelief,
    CharacterPerception,
    CharacterPerceptionChange,
    SubjectiveRelationshipEdge,
    SubjectiveRelationshipEdgeChange,
    CharacterState,
    CharacterStateChange,
    CommittedScene,
    CharacterScene,
)
from story.arcs import story_arc_lenses_for_character

CHARACTER_AGENT_MODEL = "gpt-5.4"
MEMORY_CONTEXT_HISTORY_LIMIT = 15
MEMORY_CONTEXT_PAST_LIMIT = 5
MEMORY_CONTEXT_RAW_LIMIT = 2
MEMORY_RAW_RECENT_KEEP = 5
MEMORY_RAW_TO_PAST_BATCH_SIZE = 8
MEMORY_PAST_RECENT_KEEP = 2
MEMORY_PAST_TO_HISTORY_BATCH_SIZE = 5
RELATIONSHIP_CONTEXT_HISTORY_LIMIT = 3
RELATIONSHIP_CONTEXT_PAST_LIMIT = 3
RELATIONSHIP_RAW_RECENT_KEEP = 5
RELATIONSHIP_RAW_TO_PAST_BATCH_SIZE = 8
RELATIONSHIP_PAST_RECENT_KEEP = 2
RELATIONSHIP_PAST_TO_HISTORY_BATCH_SIZE = 5
EDGE_CONTEXT_HISTORY_LIMIT = 3
EDGE_CONTEXT_PAST_LIMIT = 3
EDGE_RAW_RECENT_KEEP = 5
EDGE_RAW_TO_PAST_BATCH_SIZE = 8
EDGE_PAST_RECENT_KEEP = 2
EDGE_PAST_TO_HISTORY_BATCH_SIZE = 5
BELIEF_CONTEXT_LIMIT = 50
BELIEF_REDUCER_ACTIVE_LIMIT = 20
PENDING_BELIEF_REDUCTION_SOURCE = "scene_aftermath_pending_reduction"
REDUCED_BELIEF_SOURCE = "belief_reducer"
CONTINUITY_MAINTENANCE_TASK_LIMIT = 5
CHARACTER_AGENT_PROFILE_BELIEF_LIMIT = 10
CHARACTER_AGENT_PROFILE_MEMORY_HISTORY_LIMIT = 15
CHARACTER_AGENT_PROFILE_MEMORY_PAST_LIMIT = 10
CHARACTER_AGENT_IMPRESSION_BELIEF_LIMIT = 5
CHARACTER_AGENT_IMPRESSION_MEMORY_HISTORY_LIMIT = 5
CHARACTER_AGENT_IMPRESSION_MEMORY_PAST_LIMIT = 3
CHARACTER_AGENT_GLOBAL_BELIEF_LIMIT = 6
CHARACTER_AGENT_GLOBAL_MEMORY_LIMIT = 5
openai_client = OpenAI(
    api_key=settings.OPENAI_API_KEY
)
grok_client = OpenAI(
    api_key=settings.GROK_API_KEY,
    base_url="https://api.x.ai/v1"
)

DIRECTIONAL_NOTES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "subject_a_to_b": {"type": "string"},
        "subject_b_to_a": {"type": "string"},
    },
    "required": [
        "summary",
        "subject_a_to_b",
        "subject_b_to_a",
    ],
}

PERSPECTIVE_CONTINUITY_MAINTENANCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "memory_compactions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "task_id": {"type": "string"},
                    "target_layer": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["task_id", "target_layer", "content"],
            },
        },
        "relationship_compactions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "task_id": {"type": "string"},
                    "target_slug": {"type": "string"},
                    "target_layer": {"type": "string"},
                    "summary": {"type": "string"},
                    "revised_summary": {"type": "string"},
                    "knowledge_basis": {"type": "string"},
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "task_id",
                    "target_slug",
                    "target_layer",
                    "summary",
                    "revised_summary",
                    "knowledge_basis",
                    "open_questions",
                ],
            },
        },
        "relationship_edge_compactions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "task_id": {"type": "string"},
                    "subject_a_slug": {"type": "string"},
                    "subject_b_slug": {"type": "string"},
                    "target_layer": {"type": "string"},
                    "relationship_label": {"type": "string"},
                    "summary": {"type": "string"},
                    "revised_summary": {"type": "string"},
                    "knowledge_basis": {"type": "string"},
                    "confidence": {"type": "number"},
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "directional_notes_json": DIRECTIONAL_NOTES_SCHEMA,
                },
                "required": [
                    "task_id",
                    "subject_a_slug",
                    "subject_b_slug",
                    "target_layer",
                    "relationship_label",
                    "summary",
                    "revised_summary",
                    "knowledge_basis",
                    "confidence",
                    "open_questions",
                    "directional_notes_json",
                ],
            },
        },
        "belief_reduction_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "task_id": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": [
                            "create",
                            "reinforce",
                            "revise",
                            "complicate",
                            "promote",
                            "discard",
                            "ignore",
                        ],
                    },
                    "candidate_index": {"type": "integer"},
                    "candidate_belief_id": {"type": "integer"},
                    "target_belief_id": {"type": "integer"},
                    "belief_ids_to_discard": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "subject_type": {"type": "string"},
                    "subject_slug": {"type": "string"},
                    "related_subject_slugs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "belief": {"type": "string"},
                    "confidence": {"type": "number"},
                    "basis": {"type": "string"},
                    "belief_status": {
                        "type": "string",
                        "enum": [
                            CharacterBelief.BELIEF_STATUS_TRANSIENT,
                            CharacterBelief.BELIEF_STATUS_REINFORCED,
                            CharacterBelief.BELIEF_STATUS_PROMOTED,
                            CharacterBelief.BELIEF_STATUS_DISCARDED,
                        ],
                    },
                    "reason": {"type": "string"},
                },
                "required": [
                    "task_id",
                    "action",
                    "candidate_index",
                    "candidate_belief_id",
                    "target_belief_id",
                    "belief_ids_to_discard",
                    "subject_type",
                    "subject_slug",
                    "related_subject_slugs",
                    "belief",
                    "confidence",
                    "basis",
                    "belief_status",
                    "reason",
                ],
            },
        },
    },
    "required": [
        "memory_compactions",
        "relationship_compactions",
        "relationship_edge_compactions",
        "belief_reduction_actions",
    ],
}

PERSPECTIVE_BEAT_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "perspective_beat": {"type": "string"},
        "private_player_material": {"type": "string"},
        "visibility_note": {"type": "string"},
        "continuity_maintenance": PERSPECTIVE_CONTINUITY_MAINTENANCE_SCHEMA,
    },
    "required": [
        "perspective_beat",
        "private_player_material",
        "visibility_note",
        "continuity_maintenance",
    ],
}

PERSPECTIVE_BEAT_SYSTEM_PROMPT = """
You rewrite the user's latest scene beat into the acting character's local, second-person perspective.

Return only valid JSON matching the schema.

Rules:
- Write perspective_beat as immersive prose addressed to the acting character as "you".
- Do not address the acting character by name in the prose.
- Resolve first-person user references such as I, me, my, and mine to the player character(s), not to the acting character unless the acting character is_player=true.
- Treat bracketed OOC/editorial directives such as "[OOC: surprise me]" as authorial instructions, not in-world speech, action, thought, or perception. Do not put the directive itself into perspective_beat.
- Use characterlocal_scene_state as the boundary for what the acting character can directly see, partially see, hear, infer, or not know.
- active_story_arc_lenses are authorial presentation lenses for this character's subjective input. They are not character knowledge and must not be named or explained in perspective_beat.
- Use active_story_arc_lenses to color ambiguous perception, emotional salience, bodily charge, suspicion, temptation, dread, or interpretive pressure when compatible with local access.
- Do not let an arc lens invent physical facts, override explicit scene facts, expose private thoughts, or grant knowledge the acting character could not plausibly perceive or infer.
- Preserve uncertainty when access is partial, inferred, mediated, obstructed, or intermittent.
- Preserve all explicit physical facts, spatial relations, clothing states, object positions, body positions, visibility, and contact exactly unless the acting character truly cannot know them.
- Do not replace a newly introduced physical condition with an earlier or more generic version from memory, recent scene context, or characterlocal_scene_state.
- Do not turn private player thoughts into character knowledge. Put private/internal-only player material in private_player_material if it matters.
- Do not add bespoke warnings or corrections about impossible states. Just write the local perspective cleanly.
- Do not decide the acting character's response, attempted action, dialogue, or intent.
- continuity_maintenance_tasks are archival/reducer chores attached to this existing meta call so they do not create extra model calls.
- They are not the character's lived prose experience and must not leak into perspective_beat.
- For memory_compactions, relationship_compactions, and relationship_edge_compactions, return one compact summary for each task_id provided.
- relationship_edge_compactions summarize the acting character's subjective social-graph edge between two other/self characters; preserve the observer's uncertainty and do not convert it into objective truth.
- For relationship_edge_compactions, preserve the pair slugs, relationship_label, confidence, open questions, and any useful directional asymmetry in directional_notes_json.
- For belief_reduction_tasks, maintain the character's compact set of subjective operating assumptions.
- active_beliefs are already part of the character's operating mental model.
- pending_belief_candidates are newly gated belief candidates from prior scene aftermath. They are not active yet and must not leak into perspective_beat unless accepted by a reduction action.
- For each pending_belief_candidate, return exactly one belief_reduction_action using its candidate_index and candidate_belief_id.
- Use create only when the candidate is a genuinely distinct operating assumption that should affect future interpretation or behavior.
- Use reinforce when the candidate supports an existing active belief without changing its meaning much.
- Use revise when the candidate should replace an existing active belief with a cleaner or more current version.
- Use complicate when the candidate adds meaningful nuance, exception, tension, or uncertainty to an existing active belief.
- Use discard when the candidate directly undermines an existing active belief so that target belief should stop being active.
- Use ignore when the candidate is redundant, merely factual scene recall, too weak, too literal, or not useful as a future interpretive lens.
- For create and ignore, target_belief_id must be 0.
- For reinforce, revise, complicate, promote, or discard, target_belief_id must match an id from active_beliefs.
- For pending candidates, candidate_belief_id must match the candidate's candidate_belief_id. For active-only cleanup, candidate_belief_id must be 0.
- belief_ids_to_discard may list additional active belief ids that become redundant after this action; otherwise return an empty list.
- For active-belief cleanup tasks with no pending candidates, you may use revise/promote/discard/ignore to reduce stale, duplicate, or low-value active beliefs.
- Every belief_reduction_action must include task_id. For active-only cleanup actions without a candidate, use candidate_index -1 and candidate_belief_id 0.
- If no maintenance task is provided, return empty continuity_maintenance arrays.
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
            space_id = event.get("space_id") or ""
            reader_visibility = event.get("reader_visibility") or ""
            cue_summary = event.get("cue_summary") or ""

            lines.append(f"- {event_type}; actor={actor}.")
            if space_id or reader_visibility:
                lines.append(
                    f"  Space/reader visibility: {space_id or 'unknown'}; "
                    f"{reader_visibility or 'unspecified'}."
                )
            if summary:
                lines.append(f"  Summary: {summary}")
            if outcome:
                lines.append(f"  Outcome: {outcome}")
            if cue_summary:
                lines.append(f"  Surface cue: {cue_summary}")
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

    scene_contribution = data.get("scene_contribution") or {}
    if not isinstance(scene_contribution, dict):
        scene_contribution = {}
    scene_contribution["character_pov_prose"] = str(
        scene_contribution.get("character_pov_prose") or ""
    ).strip()
    data["scene_contribution"] = scene_contribution

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
                "related_character_slugs": [],
            })
        elif isinstance(memory, dict):
            related_character_slug = memory.get("related_character_slug")
            normalized_memories.append({
                "content": str(memory.get("content") or "").strip(),
                "memory_type": memory.get("memory_type") or "scene_experience",
                "related_character_slug": related_character_slug,
                "related_character_slugs": _merge_slug_lists(
                    memory.get("related_character_slugs") or [],
                    [related_character_slug] if related_character_slug else [],
                ),
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
                "basis": "",
                "related_subject_slugs": [],
            })
        elif isinstance(belief, dict):
            subject_slug = belief.get("subject_slug") or ""
            normalized_beliefs.append({
                "subject_type": belief.get("subject_type") or "character",
                "subject_slug": subject_slug,
                "related_subject_slugs": _clean_slug_list(
                    belief.get("related_subject_slugs") or []
                ),
                "belief": str(belief.get("belief") or "").strip(),
                "confidence": belief.get("confidence") or 0.5,
                "basis": str(belief.get("basis") or belief.get("source") or "").strip(),
            })

    perception_updates = update.get("perception_updates")
    if perception_updates is None:
        perception_updates = update.get("possible_perception_updates") or []

    normalized_perceptions = []
    for perception in perception_updates or []:
        if not isinstance(perception, dict):
            continue

        change_summary = str(
            perception.get("change_summary")
            or perception.get("summary")
            or ""
        ).strip()
        revised_summary = str(
            perception.get("revised_summary")
            or perception.get("current_summary")
            or perception.get("summary_after")
            or perception.get("summary")
            or ""
        ).strip()
        open_questions = perception.get("open_questions") or []
        if not isinstance(open_questions, list):
            open_questions = []

        normalized_perceptions.append({
            "target_slug": perception.get("target_slug") or perception.get("target_character_slug") or "",
            "summary": change_summary,
            "change_summary": change_summary,
            "revised_summary": revised_summary,
            "knowledge_basis": str(
                perception.get("knowledge_basis")
                or perception.get("basis")
                or perception.get("source")
                or ""
            ).strip(),
            "open_questions": [
                str(item).strip()
                for item in open_questions
                if str(item).strip()
            ],
            "impression_json": perception.get("impression_json") or (
                {"impression": perception.get("impression")}
                if perception.get("impression")
                else {}
            ),
            "relationship_json": perception.get("relationship_json") or {},
            "belief_json": perception.get("belief_json") or {},
            "trust_delta": perception.get("trust_delta") or 0,
            "attraction_delta": perception.get("attraction_delta") or 0,
            "fear_delta": perception.get("fear_delta") or 0,
            "resentment_delta": perception.get("resentment_delta") or 0,
        })

    relationship_edge_updates = update.get("relationship_edge_updates")
    if relationship_edge_updates is None:
        relationship_edge_updates = update.get("subjective_relationship_edge_updates") or []

    normalized_relationship_edges = []
    for edge_update in relationship_edge_updates or []:
        if not isinstance(edge_update, dict):
            continue

        open_questions = edge_update.get("open_questions") or []
        if not isinstance(open_questions, list):
            open_questions = []

        directional_notes = edge_update.get("directional_notes_json") or {}
        if not isinstance(directional_notes, dict):
            directional_notes = {"summary": str(directional_notes or "").strip()}

        change_summary = str(
            edge_update.get("change_summary")
            or edge_update.get("summary")
            or ""
        ).strip()
        revised_summary = str(
            edge_update.get("revised_summary")
            or edge_update.get("current_summary")
            or edge_update.get("summary_after")
            or edge_update.get("summary")
            or ""
        ).strip()

        normalized_relationship_edges.append({
            "subject_a_slug": edge_update.get("subject_a_slug") or "",
            "subject_b_slug": edge_update.get("subject_b_slug") or "",
            "relationship_label": str(
                edge_update.get("relationship_label") or ""
            ).strip(),
            "change_summary": change_summary,
            "revised_summary": revised_summary,
            "knowledge_basis": str(
                edge_update.get("knowledge_basis")
                or edge_update.get("basis")
                or edge_update.get("source")
                or ""
            ).strip(),
            "confidence": _clamp_confidence(edge_update.get("confidence")),
            "open_questions": [
                str(item).strip()
                for item in open_questions
                if str(item).strip()
            ],
            "directional_notes_json": directional_notes,
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
        "relationship_edge_updates": normalized_relationship_edges,
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


MEMORY_COMPACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "content": {"type": "string"},
    },
    "required": ["content"],
}

MEMORY_COMPACTION_SYSTEM_PROMPT = """
You condense a character's subjective memories.

Return valid JSON matching the schema.
Write from the character's limited subjective continuity, not omniscient truth.
Preserve important uncertainty, bias, emotional carryover, and relational pressure.
Do not merely concatenate the source memories.
"""

MEMORY_COMPACTION_DEVELOPER_PROMPT = """
You will receive a JSON payload containing one character and a list of existing memories.

Task:
Create one compact memory summary.

If target_layer is "past":
- Summarize this bounded stretch of experience.
- Emphasize what happened across the period and what pressures are still active.

If target_layer is "history":
- Summarize multiple Past summaries into a broader durable pattern.
- Emphasize what this stretch has come to mean for the character.

Rules:
- Keep the result concise but useful for future character behavior.
- Preserve the character's subjective view, including mistaken beliefs or uncertainty.
- Do not introduce facts not present in the source memories.
- Avoid repeated phrasing from the source memories.
"""


def _clean_string_list(value):
    if not isinstance(value, list):
        return []

    return [
        str(item).strip()
        for item in value
        if str(item).strip()
    ]


def _clean_slug_list(value):
    if value in ("", None):
        return []

    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        return []

    slugs = []

    for item in value:
        slug = str(item or "").strip()
        if slug and slug not in slugs:
            slugs.append(slug)

    return slugs


def _merge_slug_lists(*values):
    merged = []

    for value in values:
        for slug in _clean_slug_list(value):
            if slug and slug not in merged:
                merged.append(slug)

    return merged


def _valid_character_slugs_from_list(world, slugs):
    cleaned = _clean_slug_list(slugs)
    if not cleaned:
        return []

    valid_slug_set = set(
        Character.objects.filter(
            world=world,
            is_active=True,
            slug__in=cleaned,
        ).values_list("slug", flat=True)
    )

    return [slug for slug in cleaned if slug in valid_slug_set]


def _clamp_confidence(value, default=0.5):
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = default

    return max(0.0, min(1.0, confidence))


def active_character_beliefs_for_context(character, limit=BELIEF_CONTEXT_LIMIT):
    return list(
        CharacterBelief.objects.filter(character=character)
        .exclude(belief_status=CharacterBelief.BELIEF_STATUS_DISCARDED)
        .exclude(source=PENDING_BELIEF_REDUCTION_SOURCE)
        .order_by("-updated_at")[:limit]
    )[::-1]


BELIEF_REDUCER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "create",
                            "reinforce",
                            "revise",
                            "complicate",
                            "discard",
                            "ignore",
                        ],
                    },
                    "candidate_index": {"type": "integer"},
                    "target_belief_id": {"type": "integer"},
                    "subject_type": {"type": "string"},
                    "subject_slug": {"type": "string"},
                    "related_subject_slugs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "belief": {"type": "string"},
                    "confidence": {"type": "number"},
                    "basis": {"type": "string"},
                    "belief_status": {
                        "type": "string",
                        "enum": [
                            CharacterBelief.BELIEF_STATUS_TRANSIENT,
                            CharacterBelief.BELIEF_STATUS_REINFORCED,
                            CharacterBelief.BELIEF_STATUS_PROMOTED,
                            CharacterBelief.BELIEF_STATUS_DISCARDED,
                        ],
                    },
                    "reason": {"type": "string"},
                },
                "required": [
                    "action",
                    "candidate_index",
                    "target_belief_id",
                    "subject_type",
                    "subject_slug",
                    "related_subject_slugs",
                    "belief",
                    "confidence",
                    "basis",
                    "belief_status",
                    "reason",
                ],
            },
        },
    },
    "required": ["actions"],
}


BELIEF_REDUCER_SYSTEM_PROMPT = """
You are a belief reducer for one character's subjective continuity.

You do not decide objective truth.
You maintain the character's compact active set of operating assumptions.
Return valid JSON matching the schema.
"""


BELIEF_REDUCER_DEVELOPER_PROMPT = """
You will receive active beliefs for one character and new candidate beliefs from a scene aftermath pass.

Task:
Choose actions that keep the character's beliefs compact, current, and dramatically useful.

Actions:
- create: the candidate is a genuinely distinct operating assumption.
- reinforce: the candidate supports an existing belief without changing its meaning much.
- revise: the candidate replaces an existing belief with a cleaner updated version.
- complicate: the candidate adds meaningful nuance, tension, or exception to an existing belief.
- discard: the candidate directly undermines an existing belief so the character should no longer treat it as active.
- ignore: the candidate is redundant, too weak, too factual, or not useful as an interpretive lens.

Rules:
- Prefer fewer, stronger beliefs.
- Prefer reinforce, revise, or complicate over create when an existing belief covers the same operating assumption.
- Use create only when the belief would shape behavior in a distinct way.
- Use ignore for simple scene recall, neutral observations, duplicate wording, or beliefs without future behavioral weight.
- Use discard only when new support directly contradicts or retires an existing active belief; do not discard merely because a belief was not mentioned.
- Preserve subjective uncertainty and mistaken beliefs when they are still plausible for this character.
- For create/reinforce/revise/complicate, output the full current belief prose the character should hold after reduction.
- related_subject_slugs should include additional character slugs materially involved in the belief beyond the primary subject_slug.
- If the belief compares, triangulates, or explains one character through another, preserve those additional related_subject_slugs.
- Confidence must be between 0 and 1.
- For create and ignore, target_belief_id must be 0.
- For reinforce, revise, complicate, or discard, target_belief_id must match an id from active_beliefs.
- belief_status should be transient for weak/new suspicions, reinforced for strengthened beliefs, promoted for durable operating assumptions, and discarded only for discard actions.
"""


def _belief_payload(belief):
    return {
        "id": belief.id,
        "subject_type": belief.subject_type,
        "subject_slug": belief.subject_slug,
        "related_subject_slugs": belief.related_subject_slugs_json or [],
        "belief": belief.belief,
        "confidence": belief.confidence,
        "basis": belief.basis,
        "belief_status": belief.belief_status,
        "source_turn": (
            belief.source_scene.turn_number
            if belief.source_scene_id
            else None
        ),
    }


def _active_beliefs_for_reducer(character):
    return list(
        CharacterBelief.objects.filter(character=character)
        .exclude(belief_status=CharacterBelief.BELIEF_STATUS_DISCARDED)
        .exclude(source=PENDING_BELIEF_REDUCTION_SOURCE)
        .order_by("-updated_at")[:BELIEF_REDUCER_ACTIVE_LIMIT]
    )


def _pending_belief_candidates_for_reducer(character):
    return list(
        CharacterBelief.objects.filter(
            character=character,
            source=PENDING_BELIEF_REDUCTION_SOURCE,
        )
        .exclude(belief_status=CharacterBelief.BELIEF_STATUS_DISCARDED)
        .order_by("created_at")
    )


def _normalize_belief_candidate(raw_belief):
    if isinstance(raw_belief, str):
        raw_belief = {
            "subject_type": "",
            "subject_slug": "",
            "belief": raw_belief,
            "confidence": 0.5,
            "basis": "",
        }

    if not isinstance(raw_belief, dict):
        return None

    belief_text = str(raw_belief.get("belief") or "").strip()
    if not belief_text:
        return None

    return {
        "subject_type": str(raw_belief.get("subject_type") or "").strip(),
        "subject_slug": str(raw_belief.get("subject_slug") or "").strip(),
        "related_subject_slugs": _clean_slug_list(
            raw_belief.get("related_subject_slugs") or []
        ),
        "belief": belief_text,
        "confidence": _clamp_confidence(raw_belief.get("confidence")),
        "basis": str(
            raw_belief.get("basis")
            or raw_belief.get("source")
            or ""
        ).strip(),
        "source": str(raw_belief.get("source") or "scene_aftermath").strip(),
    }


def _fallback_belief_reducer_actions(candidate_beliefs, active_beliefs):
    actions = []
    active_by_text = {}

    for belief in active_beliefs:
        key = (
            str(belief.subject_type or "").strip().lower(),
            str(belief.subject_slug or "").strip().lower(),
            str(belief.belief or "").strip().lower(),
        )
        active_by_text[key] = belief

    seen_candidate_keys = set()

    for index, candidate in enumerate(candidate_beliefs):
        key = (
            candidate["subject_type"].lower(),
            candidate["subject_slug"].lower(),
            candidate["belief"].lower(),
        )

        if key in seen_candidate_keys:
            actions.append({
                "action": "ignore",
                "candidate_index": index,
                "target_belief_id": 0,
                "subject_type": candidate["subject_type"],
                "subject_slug": candidate["subject_slug"],
                "related_subject_slugs": candidate["related_subject_slugs"],
                "belief": candidate["belief"],
                "confidence": candidate["confidence"],
                "basis": candidate["basis"],
                "belief_status": CharacterBelief.BELIEF_STATUS_TRANSIENT,
                "reason": "Duplicate candidate belief in the same aftermath batch.",
            })
            continue

        seen_candidate_keys.add(key)
        existing = active_by_text.get(key)

        if existing:
            actions.append({
                "action": "reinforce",
                "candidate_index": index,
                "target_belief_id": existing.id,
                "subject_type": existing.subject_type,
                "subject_slug": existing.subject_slug,
                "related_subject_slugs": _merge_slug_lists(
                    existing.related_subject_slugs_json,
                    candidate["related_subject_slugs"],
                ),
                "belief": existing.belief,
                "confidence": max(existing.confidence, candidate["confidence"]),
                "basis": candidate["basis"] or existing.basis,
                "belief_status": CharacterBelief.BELIEF_STATUS_REINFORCED,
                "reason": "Candidate matches an existing active belief.",
            })
            continue

        actions.append({
            "action": "create",
            "candidate_index": index,
            "target_belief_id": 0,
            "subject_type": candidate["subject_type"],
            "subject_slug": candidate["subject_slug"],
            "related_subject_slugs": candidate["related_subject_slugs"],
            "belief": candidate["belief"],
            "confidence": candidate["confidence"],
            "basis": candidate["basis"],
            "belief_status": CharacterBelief.BELIEF_STATUS_TRANSIENT,
            "reason": "No reducer response was available; preserving the gated belief.",
        })

    return actions


def _belief_token_set(text):
    return {
        token
        for token in "".join(
            ch.lower() if ch.isalnum() else " "
            for ch in str(text or "")
        ).split()
        if len(token) > 2
    }


def _belief_similarity(left, right):
    left_tokens = _belief_token_set(left)
    right_tokens = _belief_token_set(right)

    if not left_tokens or not right_tokens:
        return 0.0

    if left_tokens.issubset(right_tokens) or right_tokens.issubset(left_tokens):
        return 1.0

    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _subject_slug_overlap(candidate, belief):
    candidate_slugs = set(_merge_slug_lists(
        [candidate.get("subject_slug")] if candidate.get("subject_slug") else [],
        candidate.get("related_subject_slugs") or [],
    ))
    belief_slugs = set(belief_subject_slugs(belief))

    return bool(candidate_slugs and belief_slugs and candidate_slugs & belief_slugs)


def _deterministic_belief_reducer_actions(candidate_beliefs, active_beliefs):
    actions = []
    seen_candidate_keys = set()
    used_belief_ids = set()

    for index, candidate in enumerate(candidate_beliefs):
        key = (
            candidate["subject_type"].lower(),
            candidate["subject_slug"].lower(),
            tuple(candidate.get("related_subject_slugs") or []),
            candidate["belief"].lower(),
        )

        if key in seen_candidate_keys:
            actions.append({
                "action": "ignore",
                "candidate_index": index,
                "target_belief_id": 0,
                "subject_type": candidate["subject_type"],
                "subject_slug": candidate["subject_slug"],
                "related_subject_slugs": candidate["related_subject_slugs"],
                "belief": candidate["belief"],
                "confidence": candidate["confidence"],
                "basis": candidate["basis"],
                "belief_status": CharacterBelief.BELIEF_STATUS_TRANSIENT,
                "reason": "Duplicate candidate belief in the same aftermath batch.",
            })
            continue

        seen_candidate_keys.add(key)
        best_match = None
        best_similarity = 0.0

        for belief in active_beliefs:
            if belief.id in used_belief_ids:
                continue

            if not _subject_slug_overlap(candidate, belief):
                continue

            similarity = _belief_similarity(candidate["belief"], belief.belief)
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = belief

        if best_match and best_similarity >= 0.58:
            used_belief_ids.add(best_match.id)
            candidate_is_more_specific = (
                len(candidate["belief"]) > len(best_match.belief)
                and best_similarity >= 0.72
            )
            actions.append({
                "action": "revise" if candidate_is_more_specific else "reinforce",
                "candidate_index": index,
                "target_belief_id": best_match.id,
                "subject_type": candidate["subject_type"] or best_match.subject_type,
                "subject_slug": candidate["subject_slug"] or best_match.subject_slug,
                "related_subject_slugs": _merge_slug_lists(
                    best_match.related_subject_slugs_json,
                    candidate["related_subject_slugs"],
                ),
                "belief": (
                    candidate["belief"]
                    if candidate_is_more_specific
                    else best_match.belief
                ),
                "confidence": max(best_match.confidence, candidate["confidence"]),
                "basis": candidate["basis"] or best_match.basis,
                "belief_status": CharacterBelief.BELIEF_STATUS_REINFORCED,
                "reason": (
                    "Deterministic reducer merged a similar belief without "
                    "making an extra model call."
                ),
            })
            continue

        actions.append({
            "action": "create",
            "candidate_index": index,
            "target_belief_id": 0,
            "subject_type": candidate["subject_type"],
            "subject_slug": candidate["subject_slug"],
            "related_subject_slugs": candidate["related_subject_slugs"],
            "belief": candidate["belief"],
            "confidence": candidate["confidence"],
            "basis": candidate["basis"],
            "belief_status": CharacterBelief.BELIEF_STATUS_TRANSIENT,
            "reason": "New gated belief; no similar active belief found deterministically.",
        })

    return actions


def _clean_belief_reducer_action(action, candidate_beliefs, active_by_id):
    if not isinstance(action, dict):
        return None

    allowed_actions = {
        "create",
        "reinforce",
        "revise",
        "complicate",
        "discard",
        "ignore",
    }
    action_name = str(action.get("action") or "").strip().lower()
    if action_name not in allowed_actions:
        return None

    try:
        candidate_index = int(action.get("candidate_index", -1))
    except (TypeError, ValueError):
        candidate_index = -1

    candidate = (
        candidate_beliefs[candidate_index]
        if 0 <= candidate_index < len(candidate_beliefs)
        else {}
    )

    try:
        target_belief_id = int(action.get("target_belief_id") or 0)
    except (TypeError, ValueError):
        target_belief_id = 0

    if action_name in {"reinforce", "revise", "complicate", "discard"}:
        if target_belief_id not in active_by_id:
            return None
        target = active_by_id[target_belief_id]
    else:
        target = None
        target_belief_id = 0

    belief_status = str(action.get("belief_status") or "").strip()
    allowed_statuses = {
        CharacterBelief.BELIEF_STATUS_TRANSIENT,
        CharacterBelief.BELIEF_STATUS_REINFORCED,
        CharacterBelief.BELIEF_STATUS_PROMOTED,
        CharacterBelief.BELIEF_STATUS_DISCARDED,
    }
    if belief_status not in allowed_statuses:
        belief_status = (
            CharacterBelief.BELIEF_STATUS_DISCARDED
            if action_name == "discard"
            else CharacterBelief.BELIEF_STATUS_REINFORCED
            if action_name in {"reinforce", "revise", "complicate"}
            else CharacterBelief.BELIEF_STATUS_TRANSIENT
        )

    if action_name == "discard":
        belief_status = CharacterBelief.BELIEF_STATUS_DISCARDED

    belief_text = str(
        action.get("belief")
        or candidate.get("belief")
        or (target.belief if target else "")
        or ""
    ).strip()
    if action_name not in {"discard", "ignore"} and not belief_text:
        return None

    return {
        "action": action_name,
        "candidate_index": candidate_index,
        "target_belief_id": target_belief_id,
        "subject_type": str(
            action.get("subject_type")
            or candidate.get("subject_type")
            or (target.subject_type if target else "")
            or ""
        ).strip(),
        "subject_slug": str(
            action.get("subject_slug")
            or candidate.get("subject_slug")
            or (target.subject_slug if target else "")
            or ""
        ).strip(),
        "related_subject_slugs": _merge_slug_lists(
            target.related_subject_slugs_json if target else [],
            candidate.get("related_subject_slugs") or [],
            action.get("related_subject_slugs") or [],
        ),
        "belief": belief_text,
        "confidence": _clamp_confidence(
            action.get("confidence"),
            default=candidate.get("confidence", target.confidence if target else 0.5),
        ),
        "basis": str(
            action.get("basis")
            or candidate.get("basis")
            or (target.basis if target else "")
            or ""
        ).strip(),
        "belief_status": belief_status,
        "reason": str(action.get("reason") or "").strip(),
    }


def reduce_belief_candidates(character, candidate_beliefs, active_beliefs):
    if not candidate_beliefs:
        return []

    # Deterministic fallback only. The primary semantic reducer is now folded
    # into the perspective/meta call via belief_reduction_tasks.
    return _deterministic_belief_reducer_actions(candidate_beliefs, active_beliefs)


def apply_belief_reducer_actions(character, actions, source_scene=None):
    if not actions:
        return

    for action in actions:
        action_name = action.get("action")

        if action_name == "ignore":
            print(
                "[story] belief_reducer_ignore",
                "character=",
                character.slug,
                "candidate_index=",
                action.get("candidate_index"),
                "reason=",
                action.get("reason"),
                flush=True,
            )
            continue

        if action_name == "create":
            CharacterBelief.objects.create(
                world=character.world,
                character=character,
                subject_type=action.get("subject_type") or "",
                subject_slug=action.get("subject_slug") or "",
                related_subject_slugs_json=_valid_character_slugs_from_list(
                    character.world,
                    action.get("related_subject_slugs") or [],
                ),
                belief=action.get("belief") or "",
                confidence=action.get("confidence") or 0.5,
                basis=action.get("basis") or "",
                source="belief_reducer",
                source_scene=source_scene,
                belief_status=action.get("belief_status")
                or CharacterBelief.BELIEF_STATUS_TRANSIENT,
            )
            continue

        target_belief = CharacterBelief.objects.filter(
            id=action.get("target_belief_id"),
            character=character,
        ).first()
        if not target_belief:
            continue

        if action_name == "discard":
            target_belief.belief_status = CharacterBelief.BELIEF_STATUS_DISCARDED
            if action.get("basis"):
                target_belief.basis = action.get("basis")
            target_belief.source = "belief_reducer"
            target_belief.source_scene = source_scene
            target_belief.save()
            continue

        if action.get("subject_type"):
            target_belief.subject_type = action.get("subject_type")
        if action.get("subject_slug"):
            target_belief.subject_slug = action.get("subject_slug")
        target_belief.related_subject_slugs_json = _valid_character_slugs_from_list(
            character.world,
            action.get("related_subject_slugs")
            or target_belief.related_subject_slugs_json
            or [],
        )
        if action.get("belief"):
            target_belief.belief = action.get("belief")
        target_belief.confidence = action.get("confidence") or target_belief.confidence
        if action.get("basis"):
            target_belief.basis = action.get("basis")
        target_belief.belief_status = (
            action.get("belief_status")
            or CharacterBelief.BELIEF_STATUS_REINFORCED
        )
        target_belief.source = "belief_reducer"
        target_belief.source_scene = source_scene
        target_belief.save()


def belief_subject_slugs(belief):
    return _merge_slug_lists(
        [belief.subject_slug] if belief.subject_slug else [],
        belief.related_subject_slugs_json or [],
    )


def memory_related_character_slugs(memory):
    return _merge_slug_lists(
        [memory.related_character.slug]
        if memory.related_character_id
        else [],
        memory.related_character_slugs_json or [],
    )


def belief_involves_slug(belief, slug):
    return str(slug or "").strip() in belief_subject_slugs(belief)


def memory_involves_slug(memory, slug):
    return str(slug or "").strip() in memory_related_character_slugs(memory)


def _context_text_key(value):
    return " ".join(str(value or "").strip().lower().split())


def _memory_ids(memories):
    return {
        memory.id
        for memory in memories or []
        if getattr(memory, "id", None)
    }


def _memory_text_keys(memories):
    return {
        key
        for key in (
            _context_text_key(getattr(memory, "content", ""))
            for memory in memories or []
        )
        if key
    }


def _profile_context_keys_by_slug(subjective_profiles, item_type):
    keys_by_slug = {}

    for profile in subjective_profiles or []:
        slug = profile.get("slug")
        mental = profile.get("mental_profile") or {}
        if item_type == "belief":
            items = mental.get("beliefs_involving_character") or []
            limit = CHARACTER_AGENT_PROFILE_BELIEF_LIMIT
            text_key = "belief"
        else:
            items = mental.get("memories_involving_character") or []
            limit = None
            text_key = "content"

        keys = []
        seen = set()
        for item in items:
            key = _context_text_key(item.get(text_key))
            if not key or key in seen:
                continue
            seen.add(key)
            keys.append(key)
            if limit and len(keys) >= limit:
                break

        if slug:
            keys_by_slug[slug] = set(keys)

    return keys_by_slug


def _beliefs_for_slug_text(
    beliefs,
    slug,
    *,
    limit=CHARACTER_AGENT_IMPRESSION_BELIEF_LIMIT,
    exclude_keys=None,
):
    lines = []
    seen = set()
    excluded_count = 0
    exclude_keys = exclude_keys or set()

    for belief in beliefs:
        if not belief_involves_slug(belief, slug):
            continue

        key = _context_text_key(belief.belief)
        if not key or key in seen:
            continue
        seen.add(key)
        if key in exclude_keys:
            excluded_count += 1
            continue

        related = belief_subject_slugs(belief)
        related_text = (
            f" Related: {', '.join(related)}."
            if related
            else ""
        )
        lines.append(
            f"{belief.belief} "
            f"(confidence={belief.confidence:.2f}, status={belief.belief_status})."
            f"{related_text}"
        )

        if limit and len(lines) >= limit:
            break

    if lines:
        return "; ".join(lines)
    if excluded_count:
        return "covered above in the mental profile"
    return "; ".join(lines) or "none stored"


def _memories_for_slug_text(
    memories,
    slug,
    *,
    limit=None,
    exclude_keys=None,
):
    lines = []
    seen = set()
    excluded_count = 0
    exclude_keys = exclude_keys or set()

    for memory in memories:
        if not memory_involves_slug(memory, slug):
            continue

        key = _context_text_key(memory.content)
        if not key or key in seen:
            continue
        seen.add(key)
        if key in exclude_keys:
            excluded_count += 1
            continue

        related = memory_related_character_slugs(memory)
        related_text = (
            f" Related: {', '.join(related)}."
            if related
            else ""
        )
        lines.append(f"{memory.content}{related_text}")

        if limit and len(lines) >= limit:
            break

    if lines:
        return "; ".join(lines)
    if excluded_count:
        return "covered above in the mental profile"
    return "; ".join(lines) or "none stored"


def _global_memory_text(memories, *, exclude_keys=None):
    lines = []
    seen = set()
    excluded_count = 0
    exclude_keys = exclude_keys or set()

    for memory in memories:
        if memory.memory_layer == CharacterMemory.MEMORY_LAYER_RAW:
            continue

        key = _context_text_key(memory.content)
        if not key or key in seen:
            continue
        seen.add(key)
        if key in exclude_keys:
            excluded_count += 1
            continue

        lines.append(
            f"- {memory.content}"
            f" (related: {', '.join(memory_related_character_slugs(memory)) or 'none'})"
        )

        if len(lines) >= CHARACTER_AGENT_GLOBAL_MEMORY_LIMIT:
            break

    if lines:
        return "\n".join(lines)
    if excluded_count:
        return "No additional active memories beyond those already covered elsewhere in your context."
    return "No recent memories are currently pressing on you."


def _global_belief_text(beliefs, *, exclude_keys=None):
    lines = []
    seen = set()
    excluded_count = 0
    exclude_keys = exclude_keys or set()

    for belief in beliefs:
        key = _context_text_key(belief.belief)
        if not key or key in seen:
            continue
        seen.add(key)
        if key in exclude_keys:
            excluded_count += 1
            continue

        lines.append(
            f"- About {belief.subject_slug or belief.subject_type or 'something'}: "
            f"{belief.belief} "
            f"Related: {', '.join(belief_subject_slugs(belief)) or 'none'}. "
            f"You'd say your confidence in that is {belief.confidence}."
        )

        if len(lines) >= CHARACTER_AGENT_GLOBAL_BELIEF_LIMIT:
            break

    if lines:
        return "\n".join(lines)
    if excluded_count:
        return "No additional active beliefs beyond those already summarized in your mental profiles."
    return "No specific stored beliefs are currently pressing on you."


def _cast_entry_for_slug(resolved_scene_state, slug):
    if not isinstance(resolved_scene_state, dict):
        return {}

    cast = resolved_scene_state.get("cast") or {}
    if not isinstance(cast, dict):
        return {}

    entry = cast.get(slug) or {}
    return entry if isinstance(entry, dict) else {}


def _active_character_memories_by_layer(
    character,
    *,
    history_limit,
    past_limit,
    raw_limit=0,
    exclude_ids=None,
    exclude_keys=None,
    related_slug=None,
):
    exclude_ids = exclude_ids or set()
    exclude_keys = exclude_keys or set()
    seen_keys = set()

    def fetch_layer(layer, limit):
        if not limit:
            return []

        queryset = (
            CharacterMemory.objects
            .filter(
                character=character,
                memory_layer=layer,
                is_context_active=True,
            )
            .select_related("related_character")
            .order_by("-created_at")
        )
        if exclude_ids:
            queryset = queryset.exclude(id__in=exclude_ids)

        selected = []
        for memory in queryset:
            if related_slug and not memory_involves_slug(memory, related_slug):
                continue

            key = _context_text_key(memory.content)
            if not key or key in exclude_keys or key in seen_keys:
                continue

            seen_keys.add(key)
            selected.append(memory)
            if len(selected) >= limit:
                break

        return selected[::-1]

    histories = fetch_layer(
        CharacterMemory.MEMORY_LAYER_HISTORY,
        history_limit,
    )
    pasts = fetch_layer(
        CharacterMemory.MEMORY_LAYER_PAST,
        past_limit,
    )
    raw = fetch_layer(
        CharacterMemory.MEMORY_LAYER_RAW,
        raw_limit,
    )

    return histories + pasts + raw


def active_character_memories_for_context(character):
    return _active_character_memories_by_layer(
        character,
        history_limit=MEMORY_CONTEXT_HISTORY_LIMIT,
        past_limit=MEMORY_CONTEXT_PAST_LIMIT,
        raw_limit=MEMORY_CONTEXT_RAW_LIMIT,
    )


def profile_character_memories_for_slug(
    character,
    slug,
    *,
    exclude_ids=None,
    exclude_keys=None,
):
    return _active_character_memories_by_layer(
        character,
        history_limit=CHARACTER_AGENT_PROFILE_MEMORY_HISTORY_LIMIT,
        past_limit=CHARACTER_AGENT_PROFILE_MEMORY_PAST_LIMIT,
        raw_limit=0,
        exclude_ids=exclude_ids,
        exclude_keys=exclude_keys,
        related_slug=slug,
    )


def relationship_pressure_memories_for_slug(
    character,
    slug,
    *,
    exclude_ids=None,
    exclude_keys=None,
):
    return _active_character_memories_by_layer(
        character,
        history_limit=CHARACTER_AGENT_IMPRESSION_MEMORY_HISTORY_LIMIT,
        past_limit=CHARACTER_AGENT_IMPRESSION_MEMORY_PAST_LIMIT,
        raw_limit=0,
        exclude_ids=exclude_ids,
        exclude_keys=exclude_keys,
        related_slug=slug,
    )


def global_extra_character_memories_for_context(
    character,
    *,
    exclude_ids=None,
    exclude_keys=None,
):
    return _active_character_memories_by_layer(
        character,
        history_limit=CHARACTER_AGENT_GLOBAL_MEMORY_LIMIT,
        past_limit=CHARACTER_AGENT_GLOBAL_MEMORY_LIMIT,
        raw_limit=0,
        exclude_ids=exclude_ids,
        exclude_keys=exclude_keys,
    )


def _memory_compaction_fallback(character, source_memories, target_layer):
    label = "History" if target_layer == CharacterMemory.MEMORY_LAYER_HISTORY else "Past"
    snippets = [
        str(memory.content or "").strip()
        for memory in source_memories
        if str(memory.content or "").strip()
    ]
    joined = " ".join(snippets)
    if len(joined) > 1400:
        joined = joined[:1400].rstrip() + "..."
    return f"{label} summary for {character.name}: {joined}"


def _summarize_memories_for_compaction(character, source_memories, target_layer):
    # Compaction must not add an extra model call to scene generation.
    # Character-agent aftermath creates raw subjective memories inside the
    # normal per-character call; this layer only performs deterministic archival
    # folding when thresholds are crossed.
    return _memory_compaction_fallback(character, source_memories, target_layer)


def _shared_related_character(source_memories):
    related_ids = {
        memory.related_character_id
        for memory in source_memories
        if memory.related_character_id
    }

    if len(related_ids) != 1:
        return None

    related_id = next(iter(related_ids))
    return Character.objects.filter(id=related_id).first()


def _merged_memory_related_slugs(source_memories):
    slugs = []

    for memory in source_memories:
        if memory.related_character_id:
            slug = memory.related_character.slug
            if slug and slug not in slugs:
                slugs.append(slug)

        for slug in _clean_slug_list(memory.related_character_slugs_json or []):
            if slug and slug not in slugs:
                slugs.append(slug)

    return slugs


def _latest_source_scene(source_memories):
    scenes = [
        memory.source_scene
        for memory in source_memories
        if memory.source_scene_id
    ]

    if not scenes:
        return None

    return sorted(scenes, key=lambda scene: scene.turn_number)[-1]


def _source_memory_count(source_memories):
    total = 0

    for memory in source_memories:
        total += memory.source_memory_count or 1

    return total


def _compact_memory_batch(
    character,
    source_memories,
    target_layer,
    summary_content=None,
):
    if not source_memories:
        return None

    content = str(summary_content or "").strip()
    if not content:
        content = _summarize_memories_for_compaction(
            character,
            source_memories,
            target_layer,
        )
    source_ids = [memory.id for memory in source_memories]

    with transaction.atomic():
        compacted_memory = CharacterMemory.objects.create(
            world=character.world,
            character=character,
            content=content,
            memory_type=target_layer,
            memory_layer=target_layer,
            is_context_active=True,
            related_character=_shared_related_character(source_memories),
            related_character_slugs_json=_merged_memory_related_slugs(source_memories),
            source_scene=_latest_source_scene(source_memories),
            source_memory_ids_json=source_ids,
            source_memory_count=_source_memory_count(source_memories),
        )

        CharacterMemory.objects.filter(id__in=source_ids).update(
            is_context_active=False,
            compacted_into=compacted_memory,
        )

    print(
        "[story] memory_compacted",
        "character=",
        character.slug,
        "target_layer=",
        target_layer,
        "source_count=",
        len(source_memories),
        "summary_id=",
        compacted_memory.id,
        flush=True,
    )

    return compacted_memory


def compact_character_memories_if_needed(character):
    active_raw = list(
        CharacterMemory.objects.filter(
            character=character,
            memory_layer=CharacterMemory.MEMORY_LAYER_RAW,
            is_context_active=True,
        ).order_by("created_at")
    )

    if len(active_raw) >= MEMORY_RAW_RECENT_KEEP + MEMORY_RAW_TO_PAST_BATCH_SIZE:
        _compact_memory_batch(
            character,
            active_raw[:MEMORY_RAW_TO_PAST_BATCH_SIZE],
            CharacterMemory.MEMORY_LAYER_PAST,
        )

    active_pasts = list(
        CharacterMemory.objects.filter(
            character=character,
            memory_layer=CharacterMemory.MEMORY_LAYER_PAST,
            is_context_active=True,
        ).order_by("created_at")
    )

    if len(active_pasts) >= MEMORY_PAST_RECENT_KEEP + MEMORY_PAST_TO_HISTORY_BATCH_SIZE:
        _compact_memory_batch(
            character,
            active_pasts[:MEMORY_PAST_TO_HISTORY_BATCH_SIZE],
            CharacterMemory.MEMORY_LAYER_HISTORY,
        )


RELATIONSHIP_COMPACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
    },
    "required": ["summary"],
}

RELATIONSHIP_COMPACTION_SYSTEM_PROMPT = """
You condense one character's subjective relationship-change history toward one target.

Return valid JSON matching the schema.
Preserve the observer's subjective view, including bias, uncertainty, attraction, resentment, fear, or trust.
Do not merely concatenate the source changes.
"""

RELATIONSHIP_COMPACTION_DEVELOPER_PROMPT = """
You will receive a JSON payload containing relationship/perception changes for one observer -> target pair.

Task:
Create one compact relationship-history summary.

If target_layer is "past":
- Summarize this bounded stretch of relationship movement.
- Emphasize what changed, what pattern repeated, and what pressure remains active.

If target_layer is "history":
- Summarize multiple Past relationship summaries into a broader durable relationship trajectory.
- Emphasize what the relationship has come to mean to the observer.

Rules:
- Keep the result concise but useful for future relationship-map review.
- Preserve subjective uncertainty and mistaken belief if present.
- Do not introduce facts not present in the source changes.
- Avoid repeated phrasing from the source changes.
"""


def active_relationship_archives_for_context(observer, target):
    histories = list(
        CharacterPerceptionChange.objects.filter(
            observer=observer,
            target=target,
            change_layer=CharacterPerceptionChange.CHANGE_LAYER_HISTORY,
            is_context_active=True,
        ).order_by("-created_at")[:RELATIONSHIP_CONTEXT_HISTORY_LIMIT]
    )[::-1]

    pasts = list(
        CharacterPerceptionChange.objects.filter(
            observer=observer,
            target=target,
            change_layer=CharacterPerceptionChange.CHANGE_LAYER_PAST,
            is_context_active=True,
        ).order_by("-created_at")[:RELATIONSHIP_CONTEXT_PAST_LIMIT]
    )[::-1]

    return histories + pasts


def relationship_archives_payload_for_context(observer, target):
    return [
        {
            "change_layer": archive.change_layer,
            "summary": archive.summary,
            "revised_summary": archive.revised_summary,
            "knowledge_basis": archive.knowledge_basis,
            "open_questions": archive.open_questions_json,
            "source_change_count": archive.source_change_count,
        }
        for archive in active_relationship_archives_for_context(observer, target)
    ]


def _relationship_archive_text(archives):
    if not archives:
        return "none stored"

    lines = []

    for archive in archives:
        summary = str(archive.get("summary") or "").strip()
        if not summary:
            continue

        source_count = archive.get("source_change_count") or 1
        lines.append(
            f"{archive.get('change_layer')}: {summary} "
            f"({source_count} relationship changes)"
        )

    return "; ".join(lines) or "none stored"


def _relationship_compaction_fallback(observer, target, source_changes, target_layer):
    label = "History" if target_layer == CharacterPerceptionChange.CHANGE_LAYER_HISTORY else "Past"
    snippets = [
        str(change.summary or change.revised_summary or "").strip()
        for change in source_changes
        if str(change.summary or change.revised_summary or "").strip()
    ]
    joined = " ".join(snippets)
    if len(joined) > 1400:
        joined = joined[:1400].rstrip() + "..."
    return f"{label} relationship summary for {observer.name} → {target.name}: {joined}"


def _summarize_relationship_changes(observer, target, source_changes, target_layer):
    # Relationship compaction must not add an extra model call to scene
    # generation. The character-agent aftermath call already produces revised
    # relationship dossiers; this layer only archives older deltas.
    return _relationship_compaction_fallback(observer, target, source_changes, target_layer)


def _latest_relationship_source_scene(source_changes):
    scenes = [
        change.source_scene
        for change in source_changes
        if change.source_scene_id
    ]

    if not scenes:
        return None

    return sorted(scenes, key=lambda scene: scene.turn_number)[-1]


def _source_change_count(source_changes):
    total = 0

    for change in source_changes:
        total += change.source_change_count or 1

    return total


def _clean_open_questions_from_changes(source_changes):
    questions = []

    for change in source_changes:
        for question in change.open_questions_json or []:
            question_text = str(question or "").strip()
            if question_text and question_text not in questions:
                questions.append(question_text)

    return questions[:6]


def _compact_relationship_change_batch(
    observer,
    target,
    source_changes,
    target_layer,
    summary_content=None,
    revised_summary=None,
    knowledge_basis=None,
    open_questions=None,
):
    if not source_changes:
        return None

    summary = str(summary_content or "").strip()
    if not summary:
        summary = _summarize_relationship_changes(
            observer,
            target,
            source_changes,
            target_layer,
        )
    revised_summary = str(revised_summary or "").strip()
    knowledge_basis = str(knowledge_basis or "").strip()
    open_questions = _clean_string_list(open_questions or [])
    source_ids = [change.id for change in source_changes]

    with transaction.atomic():
        compacted_change = CharacterPerceptionChange.objects.create(
            world=observer.world,
            observer=observer,
            target=target,
            source_scene=_latest_relationship_source_scene(source_changes),
            change_source="relationship_compaction",
            summary=summary,
            revised_summary=revised_summary or source_changes[-1].revised_summary or "",
            knowledge_basis=(
                knowledge_basis
                or f"Compacted from {len(source_changes)} relationship change records."
            ),
            open_questions_json=(
                open_questions
                or _clean_open_questions_from_changes(source_changes)
            ),
            change_layer=target_layer,
            is_context_active=True,
            source_change_ids_json=source_ids,
            source_change_count=_source_change_count(source_changes),
            trust_delta=sum(change.trust_delta for change in source_changes),
            attraction_delta=sum(change.attraction_delta for change in source_changes),
            fear_delta=sum(change.fear_delta for change in source_changes),
            resentment_delta=sum(change.resentment_delta for change in source_changes),
        )

        CharacterPerceptionChange.objects.filter(id__in=source_ids).update(
            is_context_active=False,
            compacted_into=compacted_change,
        )

    print(
        "[story] relationship_changes_compacted",
        "observer=",
        observer.slug,
        "target=",
        target.slug,
        "target_layer=",
        target_layer,
        "source_count=",
        len(source_changes),
        "summary_id=",
        compacted_change.id,
        flush=True,
    )

    return compacted_change


def compact_relationship_changes_if_needed(observer, target):
    active_raw = list(
        CharacterPerceptionChange.objects.filter(
            observer=observer,
            target=target,
            change_layer=CharacterPerceptionChange.CHANGE_LAYER_RAW,
            is_context_active=True,
        ).order_by("created_at")
    )

    if len(active_raw) >= RELATIONSHIP_RAW_RECENT_KEEP + RELATIONSHIP_RAW_TO_PAST_BATCH_SIZE:
        _compact_relationship_change_batch(
            observer,
            target,
            active_raw[:RELATIONSHIP_RAW_TO_PAST_BATCH_SIZE],
            CharacterPerceptionChange.CHANGE_LAYER_PAST,
        )

    active_pasts = list(
        CharacterPerceptionChange.objects.filter(
            observer=observer,
            target=target,
            change_layer=CharacterPerceptionChange.CHANGE_LAYER_PAST,
            is_context_active=True,
        ).order_by("created_at")
    )

    if len(active_pasts) >= RELATIONSHIP_PAST_RECENT_KEEP + RELATIONSHIP_PAST_TO_HISTORY_BATCH_SIZE:
        _compact_relationship_change_batch(
            observer,
            target,
            active_pasts[:RELATIONSHIP_PAST_TO_HISTORY_BATCH_SIZE],
            CharacterPerceptionChange.CHANGE_LAYER_HISTORY,
        )


def _canonical_edge_subject_pair(subject_a, subject_b):
    if not subject_a or not subject_b:
        return None, None, False

    if subject_a.id == subject_b.id:
        return None, None, False

    swapped = subject_a.id > subject_b.id
    if swapped:
        return subject_b, subject_a, True

    return subject_a, subject_b, False


def _canonical_directional_notes(original_a, original_b, canonical_a, canonical_b, notes):
    if not isinstance(notes, dict):
        notes = {"summary": str(notes or "").strip()}

    if not original_a or not original_b or not canonical_a or not canonical_b:
        return notes

    if original_a.id == canonical_a.id and original_b.id == canonical_b.id:
        return notes

    canonical_notes = dict(notes)
    a_to_b = notes.get("subject_a_to_b")
    b_to_a = notes.get("subject_b_to_a")

    if a_to_b is not None or b_to_a is not None:
        canonical_notes["subject_a_to_b"] = b_to_a or ""
        canonical_notes["subject_b_to_a"] = a_to_b or ""

    return canonical_notes


def _edge_subject_slugs(edge):
    return [
        edge.subject_a.slug,
        edge.subject_b.slug,
    ]


def subjective_relationship_edge_involves_slug(edge, slug):
    slug = str(slug or "").strip()
    if not slug:
        return False

    return slug in _edge_subject_slugs(edge)


def _relationship_edge_archives_for_context(observer, subject_a, subject_b):
    subject_a, subject_b, _ = _canonical_edge_subject_pair(subject_a, subject_b)
    if not subject_a or not subject_b:
        return []

    histories = list(
        SubjectiveRelationshipEdgeChange.objects.filter(
            observer=observer,
            subject_a=subject_a,
            subject_b=subject_b,
            change_layer=SubjectiveRelationshipEdgeChange.CHANGE_LAYER_HISTORY,
            is_context_active=True,
        ).order_by("-created_at")[:EDGE_CONTEXT_HISTORY_LIMIT]
    )[::-1]

    pasts = list(
        SubjectiveRelationshipEdgeChange.objects.filter(
            observer=observer,
            subject_a=subject_a,
            subject_b=subject_b,
            change_layer=SubjectiveRelationshipEdgeChange.CHANGE_LAYER_PAST,
            is_context_active=True,
        ).order_by("-created_at")[:EDGE_CONTEXT_PAST_LIMIT]
    )[::-1]

    return histories + pasts


def _relationship_edge_archives_payload_for_context(observer, subject_a, subject_b):
    return [
        {
            "change_layer": archive.change_layer,
            "relationship_label": archive.relationship_label,
            "summary": archive.summary,
            "revised_summary": archive.revised_summary,
            "knowledge_basis": archive.knowledge_basis,
            "confidence": archive.confidence,
            "open_questions": archive.open_questions_json,
            "directional_notes_json": archive.directional_notes_json,
            "source_change_count": archive.source_change_count,
        }
        for archive in _relationship_edge_archives_for_context(
            observer,
            subject_a,
            subject_b,
        )
    ]


def _relationship_edge_archive_text(archives):
    if not archives:
        return "none stored"

    lines = []

    for archive in archives:
        summary = str(archive.get("summary") or "").strip()
        if not summary:
            continue

        source_count = archive.get("source_change_count") or 1
        lines.append(
            f"{archive.get('change_layer')}: {summary} "
            f"({source_count} edge changes)"
        )

    return "; ".join(lines) or "none stored"


def _relationship_edge_payload(edge):
    archives = _relationship_edge_archives_payload_for_context(
        edge.observer,
        edge.subject_a,
        edge.subject_b,
    )

    return {
        "id": edge.id,
        "subject_a_slug": edge.subject_a.slug,
        "subject_a_name": edge.subject_a.name,
        "subject_b_slug": edge.subject_b.slug,
        "subject_b_name": edge.subject_b.name,
        "relationship_label": edge.relationship_label,
        "summary": edge.summary,
        "knowledge_basis": edge.knowledge_basis,
        "confidence": edge.confidence,
        "open_questions": edge.open_questions_json,
        "directional_notes_json": edge.directional_notes_json,
        "last_change_summary": edge.last_change_summary,
        "relationship_edge_archives": archives,
    }


def _active_subjective_relationship_edges_for_context(
    observer,
    relevant_slugs=None,
    limit=24,
):
    relevant_slug_set = set(_clean_slug_list(relevant_slugs or []))

    edges = list(
        SubjectiveRelationshipEdge.objects.filter(
            observer=observer,
        )
        .select_related("subject_a", "subject_b")
        .order_by("-updated_at")[:limit * 2]
    )

    if relevant_slug_set:
        relevant_edges = [
            edge for edge in edges
            if relevant_slug_set & set(_edge_subject_slugs(edge))
        ]
        if relevant_edges:
            edges = relevant_edges

    return edges[:limit]


def _subjective_relationship_edges_text(edge_payloads):
    if not edge_payloads:
        return "No stored subjective social-graph edges are currently pressing on you."

    lines = []

    for edge in edge_payloads:
        label = edge.get("relationship_label") or "relationship"
        summary = edge.get("summary") or "No developed edge summary stored yet."
        archives = _relationship_edge_archive_text(
            edge.get("relationship_edge_archives") or []
        )
        lines.append(
            f"- {edge.get('subject_a_name')} ↔ {edge.get('subject_b_name')} "
            f"({label}): {summary} "
            f"Basis: {edge.get('knowledge_basis') or 'unknown/unstated'}. "
            f"Recent change: {edge.get('last_change_summary') or 'none stored'}. "
            f"Past/History: {archives}. "
            f"Confidence: {edge.get('confidence')}."
        )

    return "\n".join(lines)


def _relationship_edge_compaction_fallback(observer, subject_a, subject_b, source_changes, target_layer):
    label = (
        "History"
        if target_layer == SubjectiveRelationshipEdgeChange.CHANGE_LAYER_HISTORY
        else "Past"
    )
    snippets = [
        str(change.summary or change.revised_summary or "").strip()
        for change in source_changes
        if str(change.summary or change.revised_summary or "").strip()
    ]
    joined = " ".join(snippets)
    if len(joined) > 1400:
        joined = joined[:1400].rstrip() + "..."
    return (
        f"{label} subjective social-edge summary for {observer.name}'s "
        f"{subject_a.name} ↔ {subject_b.name}: {joined}"
    )


def _summarize_relationship_edge_changes(observer, subject_a, subject_b, source_changes, target_layer):
    # Edge compaction must not add an extra model call to scene generation.
    return _relationship_edge_compaction_fallback(
        observer,
        subject_a,
        subject_b,
        source_changes,
        target_layer,
    )


def _relationship_edge_for_change_batch(observer, subject_a, subject_b):
    subject_a, subject_b, _ = _canonical_edge_subject_pair(subject_a, subject_b)
    if not subject_a or not subject_b:
        return None

    return SubjectiveRelationshipEdge.objects.filter(
        observer=observer,
        subject_a=subject_a,
        subject_b=subject_b,
    ).first()


def _compact_relationship_edge_change_batch(
    observer,
    subject_a,
    subject_b,
    source_changes,
    target_layer,
    summary_content=None,
    revised_summary=None,
    relationship_label=None,
    knowledge_basis=None,
    confidence=None,
    open_questions=None,
    directional_notes_json=None,
):
    if not source_changes:
        return None

    subject_a, subject_b, _ = _canonical_edge_subject_pair(subject_a, subject_b)
    if not subject_a or not subject_b:
        return None

    summary = str(summary_content or "").strip()
    if not summary:
        summary = _summarize_relationship_edge_changes(
            observer,
            subject_a,
            subject_b,
            source_changes,
            target_layer,
        )

    latest_change = source_changes[-1]
    revised_summary = str(revised_summary or "").strip()
    relationship_label = str(relationship_label or latest_change.relationship_label or "").strip()
    knowledge_basis = str(knowledge_basis or "").strip()
    open_questions = _clean_string_list(open_questions or [])
    directional_notes = (
        directional_notes_json
        if isinstance(directional_notes_json, dict)
        else latest_change.directional_notes_json
    )
    source_ids = [change.id for change in source_changes]
    current_edge = _relationship_edge_for_change_batch(observer, subject_a, subject_b)

    with transaction.atomic():
        compacted_change = SubjectiveRelationshipEdgeChange.objects.create(
            world=observer.world,
            observer=observer,
            subject_a=subject_a,
            subject_b=subject_b,
            current_edge=current_edge,
            source_scene=_latest_relationship_source_scene(source_changes),
            change_source="relationship_edge_compaction",
            relationship_label=relationship_label,
            summary=summary,
            revised_summary=revised_summary or latest_change.revised_summary or "",
            knowledge_basis=(
                knowledge_basis
                or f"Compacted from {len(source_changes)} subjective relationship edge records."
            ),
            confidence=_clamp_confidence(confidence, default=latest_change.confidence),
            open_questions_json=(
                open_questions
                or _clean_open_questions_from_changes(source_changes)
            ),
            directional_notes_json=directional_notes or {},
            change_layer=target_layer,
            is_context_active=True,
            source_change_ids_json=source_ids,
            source_change_count=_source_change_count(source_changes),
        )

        SubjectiveRelationshipEdgeChange.objects.filter(id__in=source_ids).update(
            is_context_active=False,
            compacted_into=compacted_change,
        )

    print(
        "[story] relationship_edge_changes_compacted",
        "observer=",
        observer.slug,
        "subject_a=",
        subject_a.slug,
        "subject_b=",
        subject_b.slug,
        "target_layer=",
        target_layer,
        "source_count=",
        len(source_changes),
        "summary_id=",
        compacted_change.id,
        flush=True,
    )

    return compacted_change


def _relationship_edge_change_payload(change):
    return {
        "id": change.id,
        "subject_a_slug": change.subject_a.slug,
        "subject_b_slug": change.subject_b.slug,
        "relationship_label": change.relationship_label,
        "summary": change.summary,
        "revised_summary": change.revised_summary,
        "knowledge_basis": change.knowledge_basis,
        "confidence": change.confidence,
        "open_questions": change.open_questions_json or [],
        "directional_notes_json": change.directional_notes_json or {},
        "change_layer": change.change_layer,
        "source_turn": (
            change.source_scene.turn_number
            if change.source_scene_id
            else None
        ),
        "source_change_count": change.source_change_count,
    }


def _relationship_edge_compaction_task(edge, source_changes, target_layer):
    source_ids = [change.id for change in source_changes]

    return {
        "task_id": (
            f"relationship_edge:{edge.subject_a.slug}:{edge.subject_b.slug}:"
            f"{target_layer}:{'-'.join(str(i) for i in source_ids)}"
        ),
        "subject_a_slug": edge.subject_a.slug,
        "subject_b_slug": edge.subject_b.slug,
        "target_layer": target_layer,
        "source_change_ids": source_ids,
        "source_changes": [
            _relationship_edge_change_payload(change)
            for change in source_changes
        ],
    }


def _character_relationship_edge_compaction_tasks(character):
    tasks = []
    edges = (
        SubjectiveRelationshipEdge.objects
        .filter(observer=character)
        .select_related("subject_a", "subject_b")
        .order_by("subject_a__name", "subject_b__name")
    )

    for edge in edges:
        if len(tasks) >= CONTINUITY_MAINTENANCE_TASK_LIMIT:
            break

        active_raw = list(
            SubjectiveRelationshipEdgeChange.objects.filter(
                observer=character,
                subject_a=edge.subject_a,
                subject_b=edge.subject_b,
                change_layer=SubjectiveRelationshipEdgeChange.CHANGE_LAYER_RAW,
                is_context_active=True,
            ).order_by("created_at")
        )

        if len(active_raw) >= EDGE_RAW_RECENT_KEEP + EDGE_RAW_TO_PAST_BATCH_SIZE:
            tasks.append(_relationship_edge_compaction_task(
                edge,
                active_raw[:EDGE_RAW_TO_PAST_BATCH_SIZE],
                SubjectiveRelationshipEdgeChange.CHANGE_LAYER_PAST,
            ))

        if len(tasks) >= CONTINUITY_MAINTENANCE_TASK_LIMIT:
            break

        active_pasts = list(
            SubjectiveRelationshipEdgeChange.objects.filter(
                observer=character,
                subject_a=edge.subject_a,
                subject_b=edge.subject_b,
                change_layer=SubjectiveRelationshipEdgeChange.CHANGE_LAYER_PAST,
                is_context_active=True,
            ).order_by("created_at")
        )

        if len(active_pasts) >= EDGE_PAST_RECENT_KEEP + EDGE_PAST_TO_HISTORY_BATCH_SIZE:
            tasks.append(_relationship_edge_compaction_task(
                edge,
                active_pasts[:EDGE_PAST_TO_HISTORY_BATCH_SIZE],
                SubjectiveRelationshipEdgeChange.CHANGE_LAYER_HISTORY,
            ))

    return tasks


def _memory_compaction_task(source_memories, target_layer):
    source_ids = [memory.id for memory in source_memories]

    return {
        "task_id": f"memory:{target_layer}:{'-'.join(str(i) for i in source_ids)}",
        "target_layer": target_layer,
        "source_memory_ids": source_ids,
        "source_memories": [
            {
                "id": memory.id,
                "content": memory.content,
                "memory_type": memory.memory_type,
                "memory_layer": memory.memory_layer,
                "related_character_slugs": memory_related_character_slugs(memory),
                "source_turn": (
                    memory.source_scene.turn_number
                    if memory.source_scene_id
                    else None
                ),
                "source_memory_count": memory.source_memory_count,
            }
            for memory in source_memories
        ],
    }


def _character_memory_compaction_tasks(character):
    tasks = []

    active_raw = list(
        CharacterMemory.objects.filter(
            character=character,
            memory_layer=CharacterMemory.MEMORY_LAYER_RAW,
            is_context_active=True,
        ).order_by("created_at")
    )

    if len(active_raw) >= MEMORY_RAW_RECENT_KEEP + MEMORY_RAW_TO_PAST_BATCH_SIZE:
        tasks.append(_memory_compaction_task(
            active_raw[:MEMORY_RAW_TO_PAST_BATCH_SIZE],
            CharacterMemory.MEMORY_LAYER_PAST,
        ))

    active_pasts = list(
        CharacterMemory.objects.filter(
            character=character,
            memory_layer=CharacterMemory.MEMORY_LAYER_PAST,
            is_context_active=True,
        ).order_by("created_at")
    )

    if len(active_pasts) >= MEMORY_PAST_RECENT_KEEP + MEMORY_PAST_TO_HISTORY_BATCH_SIZE:
        tasks.append(_memory_compaction_task(
            active_pasts[:MEMORY_PAST_TO_HISTORY_BATCH_SIZE],
            CharacterMemory.MEMORY_LAYER_HISTORY,
        ))

    return tasks


def _relationship_change_payload(change):
    return {
        "id": change.id,
        "summary": change.summary,
        "revised_summary": change.revised_summary,
        "knowledge_basis": change.knowledge_basis,
        "open_questions": change.open_questions_json or [],
        "change_layer": change.change_layer,
        "source_turn": (
            change.source_scene.turn_number
            if change.source_scene_id
            else None
        ),
        "source_change_count": change.source_change_count,
        "trust_delta": change.trust_delta,
        "attraction_delta": change.attraction_delta,
        "fear_delta": change.fear_delta,
        "resentment_delta": change.resentment_delta,
    }


def _relationship_compaction_task(target, source_changes, target_layer):
    source_ids = [change.id for change in source_changes]

    return {
        "task_id": (
            f"relationship:{target.slug}:{target_layer}:"
            f"{'-'.join(str(i) for i in source_ids)}"
        ),
        "target_slug": target.slug,
        "target_name": target.name,
        "target_layer": target_layer,
        "source_change_ids": source_ids,
        "source_changes": [
            _relationship_change_payload(change)
            for change in source_changes
        ],
    }


def _character_relationship_compaction_tasks(character):
    tasks = []
    perceptions = (
        CharacterPerception.objects
        .filter(observer=character)
        .select_related("target")
        .order_by("target__name")
    )

    for perception in perceptions:
        if len(tasks) >= CONTINUITY_MAINTENANCE_TASK_LIMIT:
            break

        active_raw = list(
            CharacterPerceptionChange.objects.filter(
                observer=character,
                target=perception.target,
                change_layer=CharacterPerceptionChange.CHANGE_LAYER_RAW,
                is_context_active=True,
            ).order_by("created_at")
        )

        if len(active_raw) >= RELATIONSHIP_RAW_RECENT_KEEP + RELATIONSHIP_RAW_TO_PAST_BATCH_SIZE:
            tasks.append(_relationship_compaction_task(
                perception.target,
                active_raw[:RELATIONSHIP_RAW_TO_PAST_BATCH_SIZE],
                CharacterPerceptionChange.CHANGE_LAYER_PAST,
            ))

        if len(tasks) >= CONTINUITY_MAINTENANCE_TASK_LIMIT:
            break

        active_pasts = list(
            CharacterPerceptionChange.objects.filter(
                observer=character,
                target=perception.target,
                change_layer=CharacterPerceptionChange.CHANGE_LAYER_PAST,
                is_context_active=True,
            ).order_by("created_at")
        )

        if len(active_pasts) >= RELATIONSHIP_PAST_RECENT_KEEP + RELATIONSHIP_PAST_TO_HISTORY_BATCH_SIZE:
            tasks.append(_relationship_compaction_task(
                perception.target,
                active_pasts[:RELATIONSHIP_PAST_TO_HISTORY_BATCH_SIZE],
                CharacterPerceptionChange.CHANGE_LAYER_HISTORY,
            ))

    return tasks


def _belief_reduction_is_due(active_beliefs):
    if len(active_beliefs) > BELIEF_REDUCER_ACTIVE_LIMIT:
        return True

    for index, belief in enumerate(active_beliefs):
        belief_slugs = set(belief_subject_slugs(belief))

        if not belief_slugs:
            continue

        for other in active_beliefs[index + 1:]:
            other_slugs = set(belief_subject_slugs(other))

            if not belief_slugs & other_slugs:
                continue

            if _belief_similarity(belief.belief, other.belief) >= 0.58:
                return True

    return False


def _belief_reduction_task(character):
    active_beliefs = list(
        CharacterBelief.objects.filter(character=character)
        .exclude(belief_status=CharacterBelief.BELIEF_STATUS_DISCARDED)
        .exclude(source=PENDING_BELIEF_REDUCTION_SOURCE)
        .order_by("-updated_at")[:BELIEF_REDUCER_ACTIVE_LIMIT + 10]
    )[::-1]
    pending_candidates = _pending_belief_candidates_for_reducer(character)

    if not pending_candidates and not _belief_reduction_is_due(active_beliefs):
        return None

    return {
        "task_id": f"belief_reduction:{character.slug}",
        "active_beliefs": [
            _belief_payload(belief)
            for belief in active_beliefs
        ],
        "pending_belief_candidates": [
            {
                **_belief_payload(belief),
                "candidate_index": index,
                "candidate_belief_id": belief.id,
            }
            for index, belief in enumerate(pending_candidates)
        ],
    }


def character_continuity_maintenance_tasks(character):
    belief_task = _belief_reduction_task(character)

    return {
        "memory_compactions": _character_memory_compaction_tasks(character),
        "relationship_compactions": _character_relationship_compaction_tasks(character),
        "relationship_edge_compactions": _character_relationship_edge_compaction_tasks(character),
        "belief_reduction_tasks": [belief_task] if belief_task else [],
    }


def _maintenance_output_by_task_id(outputs):
    if not isinstance(outputs, list):
        return {}

    output_by_id = {}

    for output in outputs:
        if not isinstance(output, dict):
            continue

        task_id = str(output.get("task_id") or "").strip()
        if task_id:
            output_by_id[task_id] = output

    return output_by_id


def _apply_memory_maintenance(character, tasks, maintenance_output):
    output_by_id = _maintenance_output_by_task_id(
        maintenance_output.get("memory_compactions") or []
    )

    for task in tasks.get("memory_compactions") or []:
        source_ids = task.get("source_memory_ids") or []
        source_memories = list(
            CharacterMemory.objects.filter(
                character=character,
                id__in=source_ids,
                is_context_active=True,
            ).order_by("created_at")
        )

        if not source_memories:
            continue

        output = output_by_id.get(task.get("task_id")) or {}
        _compact_memory_batch(
            character,
            source_memories,
            task.get("target_layer"),
            summary_content=output.get("content"),
        )


def _apply_relationship_maintenance(character, tasks, maintenance_output):
    output_by_id = _maintenance_output_by_task_id(
        maintenance_output.get("relationship_compactions") or []
    )

    for task in tasks.get("relationship_compactions") or []:
        target = validate_resolved_slug(character.world, task.get("target_slug"))
        if not target:
            continue

        source_ids = task.get("source_change_ids") or []
        source_changes = list(
            CharacterPerceptionChange.objects.filter(
                observer=character,
                target=target,
                id__in=source_ids,
                is_context_active=True,
            ).order_by("created_at")
        )

        if not source_changes:
            continue

        output = output_by_id.get(task.get("task_id")) or {}
        _compact_relationship_change_batch(
            character,
            target,
            source_changes,
            task.get("target_layer"),
            summary_content=output.get("summary"),
            revised_summary=output.get("revised_summary"),
            knowledge_basis=output.get("knowledge_basis"),
            open_questions=output.get("open_questions") or [],
        )


def _apply_relationship_edge_maintenance(character, tasks, maintenance_output):
    output_by_id = _maintenance_output_by_task_id(
        maintenance_output.get("relationship_edge_compactions") or []
    )

    for task in tasks.get("relationship_edge_compactions") or []:
        subject_a = validate_resolved_slug(character.world, task.get("subject_a_slug"))
        subject_b = validate_resolved_slug(character.world, task.get("subject_b_slug"))
        subject_a, subject_b, _ = _canonical_edge_subject_pair(subject_a, subject_b)
        if not subject_a or not subject_b:
            continue

        source_ids = task.get("source_change_ids") or []
        source_changes = list(
            SubjectiveRelationshipEdgeChange.objects.filter(
                observer=character,
                subject_a=subject_a,
                subject_b=subject_b,
                id__in=source_ids,
                is_context_active=True,
            ).order_by("created_at")
        )

        if not source_changes:
            continue

        output = output_by_id.get(task.get("task_id")) or {}
        _compact_relationship_edge_change_batch(
            character,
            subject_a,
            subject_b,
            source_changes,
            task.get("target_layer"),
            summary_content=output.get("summary"),
            revised_summary=output.get("revised_summary"),
            relationship_label=output.get("relationship_label"),
            knowledge_basis=output.get("knowledge_basis"),
            confidence=output.get("confidence"),
            open_questions=output.get("open_questions") or [],
            directional_notes_json=output.get("directional_notes_json") or {},
        )


def _belief_reduction_task_allowed_ids(tasks):
    active_ids = set()
    candidate_ids = set()

    if not isinstance(tasks, dict):
        return active_ids, candidate_ids

    for task in tasks.get("belief_reduction_tasks") or []:
        if not isinstance(task, dict):
            continue

        for belief in task.get("active_beliefs") or []:
            if not isinstance(belief, dict):
                continue
            try:
                active_ids.add(int(belief.get("id") or 0))
            except (TypeError, ValueError):
                continue

        for candidate in task.get("pending_belief_candidates") or []:
            if not isinstance(candidate, dict):
                continue
            try:
                candidate_ids.add(int(
                    candidate.get("candidate_belief_id")
                    or candidate.get("id")
                    or 0
                ))
            except (TypeError, ValueError):
                continue

    active_ids.discard(0)
    candidate_ids.discard(0)
    return active_ids, candidate_ids


def _apply_belief_maintenance_action(
    character,
    action,
    allowed_active_ids=None,
    allowed_candidate_ids=None,
):
    if not isinstance(action, dict):
        return

    action_name = str(action.get("action") or "").strip().lower()
    allowed_actions = {
        "create",
        "reinforce",
        "revise",
        "complicate",
        "promote",
        "discard",
        "ignore",
    }
    if action_name not in allowed_actions:
        return

    allowed_active_ids = set(allowed_active_ids or [])
    allowed_candidate_ids = set(allowed_candidate_ids or [])

    try:
        candidate_belief_id = int(action.get("candidate_belief_id") or 0)
    except (TypeError, ValueError):
        candidate_belief_id = 0

    candidate_belief = None
    if candidate_belief_id:
        if allowed_candidate_ids and candidate_belief_id not in allowed_candidate_ids:
            return

        candidate_belief = CharacterBelief.objects.filter(
            id=candidate_belief_id,
            character=character,
            source=PENDING_BELIEF_REDUCTION_SOURCE,
        ).exclude(
            belief_status=CharacterBelief.BELIEF_STATUS_DISCARDED,
        ).first()

        if not candidate_belief:
            return

    try:
        target_belief_id = int(action.get("target_belief_id") or 0)
    except (TypeError, ValueError):
        target_belief_id = 0

    target_belief = None
    if target_belief_id:
        if allowed_active_ids and target_belief_id not in allowed_active_ids:
            return

        target_belief = CharacterBelief.objects.filter(
            id=target_belief_id,
            character=character,
        ).exclude(
            belief_status=CharacterBelief.BELIEF_STATUS_DISCARDED,
        ).exclude(
            source=PENDING_BELIEF_REDUCTION_SOURCE,
        ).first()

        if not target_belief:
            return

    discard_ids = [
        int(belief_id)
        for belief_id in action.get("belief_ids_to_discard") or []
        if str(belief_id).isdigit()
    ]
    discard_ids = [
        belief_id
        for belief_id in discard_ids
        if belief_id != target_belief_id
        and (not allowed_active_ids or belief_id in allowed_active_ids)
    ]

    belief_text = str(
        action.get("belief")
        or (candidate_belief.belief if candidate_belief else "")
        or (target_belief.belief if target_belief else "")
        or ""
    ).strip()
    subject_type = str(
        action.get("subject_type")
        or (candidate_belief.subject_type if candidate_belief else "")
        or (target_belief.subject_type if target_belief else "")
        or ""
    ).strip()
    subject_slug = str(
        action.get("subject_slug")
        or (candidate_belief.subject_slug if candidate_belief else "")
        or (target_belief.subject_slug if target_belief else "")
        or ""
    ).strip()
    related_subject_slugs = _valid_character_slugs_from_list(
        character.world,
        _merge_slug_lists(
            target_belief.related_subject_slugs_json if target_belief else [],
            candidate_belief.related_subject_slugs_json if candidate_belief else [],
            action.get("related_subject_slugs") or [],
        ),
    )
    basis = str(
        action.get("basis")
        or (candidate_belief.basis if candidate_belief else "")
        or (target_belief.basis if target_belief else "")
        or ""
    ).strip()
    confidence = _clamp_confidence(
        action.get("confidence"),
        default=(
            candidate_belief.confidence
            if candidate_belief
            else target_belief.confidence
            if target_belief
            else 0.5
        ),
    )
    belief_status = str(action.get("belief_status") or "").strip()
    allowed_statuses = {
        CharacterBelief.BELIEF_STATUS_TRANSIENT,
        CharacterBelief.BELIEF_STATUS_REINFORCED,
        CharacterBelief.BELIEF_STATUS_PROMOTED,
        CharacterBelief.BELIEF_STATUS_DISCARDED,
    }
    if belief_status not in allowed_statuses:
        if action_name == "create":
            belief_status = CharacterBelief.BELIEF_STATUS_TRANSIENT
        elif action_name == "promote":
            belief_status = CharacterBelief.BELIEF_STATUS_PROMOTED
        elif action_name in {"reinforce", "revise", "complicate"}:
            belief_status = CharacterBelief.BELIEF_STATUS_REINFORCED
        else:
            belief_status = CharacterBelief.BELIEF_STATUS_DISCARDED
    elif action_name == "create" and belief_status == CharacterBelief.BELIEF_STATUS_DISCARDED:
        belief_status = CharacterBelief.BELIEF_STATUS_TRANSIENT
    elif action_name in {"reinforce", "revise", "complicate"} and belief_status == CharacterBelief.BELIEF_STATUS_DISCARDED:
        belief_status = CharacterBelief.BELIEF_STATUS_REINFORCED
    elif action_name == "promote":
        belief_status = CharacterBelief.BELIEF_STATUS_PROMOTED
    elif action_name == "discard":
        belief_status = CharacterBelief.BELIEF_STATUS_DISCARDED

    if action_name == "ignore":
        if candidate_belief:
            candidate_belief.belief_status = CharacterBelief.BELIEF_STATUS_DISCARDED
            candidate_belief.source = "belief_reducer_ignored"
            candidate_belief.save()
        return

    if action_name == "create":
        if not belief_text:
            return

        if candidate_belief:
            candidate_belief.subject_type = subject_type
            candidate_belief.subject_slug = subject_slug
            candidate_belief.related_subject_slugs_json = related_subject_slugs
            candidate_belief.belief = belief_text
            candidate_belief.confidence = confidence
            candidate_belief.basis = basis
            candidate_belief.belief_status = belief_status
            candidate_belief.source = REDUCED_BELIEF_SOURCE
            candidate_belief.save()
        else:
            CharacterBelief.objects.create(
                world=character.world,
                character=character,
                subject_type=subject_type,
                subject_slug=subject_slug,
                related_subject_slugs_json=related_subject_slugs,
                belief=belief_text,
                confidence=confidence,
                basis=basis,
                source=REDUCED_BELIEF_SOURCE,
                belief_status=belief_status,
            )
        return

    if action_name == "discard":
        if target_belief:
            target_belief.belief_status = CharacterBelief.BELIEF_STATUS_DISCARDED
            if basis:
                target_belief.basis = basis
            target_belief.source = REDUCED_BELIEF_SOURCE
            target_belief.source_scene = (
                candidate_belief.source_scene
                if candidate_belief and candidate_belief.source_scene_id
                else target_belief.source_scene
            )
            target_belief.save()

        if candidate_belief:
            candidate_belief.belief_status = CharacterBelief.BELIEF_STATUS_DISCARDED
            candidate_belief.source = "belief_reducer_consumed"
            candidate_belief.save()
        return

    if action_name in {"reinforce", "revise", "complicate", "promote"}:
        if not target_belief or not belief_text:
            return

        target_belief.subject_type = subject_type
        target_belief.subject_slug = subject_slug
        target_belief.related_subject_slugs_json = related_subject_slugs
        target_belief.belief = belief_text
        target_belief.confidence = confidence
        if basis:
            target_belief.basis = basis
        target_belief.belief_status = belief_status
        target_belief.source = REDUCED_BELIEF_SOURCE
        target_belief.source_scene = (
            candidate_belief.source_scene
            if candidate_belief and candidate_belief.source_scene_id
            else target_belief.source_scene
        )
        target_belief.save()

        if candidate_belief:
            candidate_belief.belief_status = CharacterBelief.BELIEF_STATUS_DISCARDED
            candidate_belief.source = "belief_reducer_consumed"
            candidate_belief.save()

    if discard_ids:
        discard_qs = CharacterBelief.objects.filter(
            character=character,
            id__in=discard_ids,
        ).exclude(
            source=PENDING_BELIEF_REDUCTION_SOURCE,
        )
        if target_belief:
            discard_qs = discard_qs.exclude(id=target_belief.id)
        discard_qs.update(
            belief_status=CharacterBelief.BELIEF_STATUS_DISCARDED,
            source=REDUCED_BELIEF_SOURCE,
        )


def _apply_belief_maintenance(character, tasks, maintenance_output):
    if not isinstance(tasks, dict) or not tasks.get("belief_reduction_tasks"):
        return

    allowed_active_ids, allowed_candidate_ids = _belief_reduction_task_allowed_ids(tasks)

    for action in maintenance_output.get("belief_reduction_actions") or []:
        _apply_belief_maintenance_action(
            character,
            action,
            allowed_active_ids=allowed_active_ids,
            allowed_candidate_ids=allowed_candidate_ids,
        )


def apply_character_continuity_maintenance(character, tasks, maintenance_output):
    if not isinstance(tasks, dict):
        return

    if not isinstance(maintenance_output, dict):
        maintenance_output = {}

    _apply_memory_maintenance(character, tasks, maintenance_output)
    _apply_relationship_maintenance(character, tasks, maintenance_output)
    _apply_relationship_edge_maintenance(character, tasks, maintenance_output)
    _apply_belief_maintenance(character, tasks, maintenance_output)


def _event_support_for_character(character, source_scene):
    if not source_scene:
        return {
            "perceived_event_count": 0,
            "perceived_event_summaries": [],
        }

    perceived_summaries = []

    for event in source_scene.scene_events_json or []:
        if not isinstance(event, dict):
            continue

        perceived_by = event.get("perceived_by") or []
        target_slugs = event.get("target_slugs") or []

        if (
            character.slug in perceived_by
            or event.get("actor_slug") == character.slug
            or character.slug in target_slugs
        ):
            perceived_summaries.append(
                event.get("summary")
                or event.get("event_type")
                or "scene event"
            )

    return {
        "perceived_event_count": len(perceived_summaries),
        "perceived_event_summaries": perceived_summaries[:5],
    }


def _subjective_update_access_gate(
    character,
    resolved_scene_state,
    source_scene,
    basis,
    update_kind,
):
    basis = str(basis or "").strip()
    cast_entry = _cast_entry_for_slug(resolved_scene_state, character.slug)
    event_support = _event_support_for_character(character, source_scene)

    can_receive = bool(
        cast_entry.get("can_receive_memory")
        or cast_entry.get("can_receive_state_change")
        or cast_entry.get("can_receive_perception_change")
    )
    presence = str(cast_entry.get("presence") or "").strip()
    has_scene_route = bool(
        source_scene is None
        or event_support["perceived_event_count"]
        or can_receive
        or presence in {"present", "active", "nearby", "offscreen"}
    )

    allowed = bool(basis and has_scene_route)

    return allowed, {
        "allowed": allowed,
        "update_kind": update_kind,
        "observer_slug": character.slug,
        "basis": basis,
        "presence": presence,
        "can_receive_subjective_update": can_receive,
        **event_support,
        "reason": (
            "basis and plausible observer access"
            if allowed
            else "missing basis or no plausible observer access"
        ),
    }


def _persist_subjective_relationship_edge_update(
    *,
    world,
    character,
    resolved_scene_state,
    source_scene,
    edge_update,
):
    if not isinstance(edge_update, dict):
        print("[story] dropping_relationship_edge_update not_a_dict", edge_update, flush=True)
        return None

    original_subject_a = validate_resolved_slug(world, edge_update.get("subject_a_slug"))
    original_subject_b = validate_resolved_slug(world, edge_update.get("subject_b_slug"))

    if not original_subject_a or not original_subject_b:
        print(
            "[story] dropping_relationship_edge_update invalid_subject_slug",
            edge_update.get("subject_a_slug"),
            edge_update.get("subject_b_slug"),
            "observer=",
            character.slug,
            flush=True,
        )
        return None

    subject_a, subject_b, _ = _canonical_edge_subject_pair(
        original_subject_a,
        original_subject_b,
    )
    if not subject_a or not subject_b:
        print(
            "[story] dropping_relationship_edge_update same_subject",
            edge_update.get("subject_a_slug"),
            edge_update.get("subject_b_slug"),
            "observer=",
            character.slug,
            flush=True,
        )
        return None

    relationship_label = str(edge_update.get("relationship_label") or "").strip()
    change_summary = str(
        edge_update.get("change_summary")
        or edge_update.get("summary")
        or ""
    ).strip()
    revised_summary = str(
        edge_update.get("revised_summary")
        or edge_update.get("summary")
        or ""
    ).strip()
    knowledge_basis = str(
        edge_update.get("knowledge_basis")
        or edge_update.get("basis")
        or ""
    ).strip()
    open_questions = _clean_string_list(edge_update.get("open_questions") or [])
    confidence = _clamp_confidence(edge_update.get("confidence"))
    directional_notes = _canonical_directional_notes(
        original_subject_a,
        original_subject_b,
        subject_a,
        subject_b,
        edge_update.get("directional_notes_json") or {},
    )

    has_edge_payload = any([
        relationship_label,
        change_summary,
        revised_summary,
        knowledge_basis,
        open_questions,
        _has_meaningful_json_payload(directional_notes),
    ])

    if not has_edge_payload:
        return None

    allowed, access_gate = _subjective_update_access_gate(
        character=character,
        resolved_scene_state=resolved_scene_state,
        source_scene=source_scene,
        basis=knowledge_basis,
        update_kind="relationship_edge",
    )
    if not allowed:
        print(
            "[story] dropping_relationship_edge_update access_gate_failed",
            access_gate,
            flush=True,
        )
        return None

    edge, _ = SubjectiveRelationshipEdge.objects.get_or_create(
        world=world,
        observer=character,
        subject_a=subject_a,
        subject_b=subject_b,
        defaults={"summary": ""},
    )

    SubjectiveRelationshipEdgeChange.objects.create(
        world=world,
        observer=character,
        subject_a=subject_a,
        subject_b=subject_b,
        current_edge=edge,
        source_scene=source_scene,
        change_source="scene_aftermath",
        relationship_label=relationship_label,
        summary=change_summary,
        revised_summary=revised_summary,
        knowledge_basis=knowledge_basis,
        confidence=confidence,
        open_questions_json=open_questions,
        directional_notes_json=directional_notes or {},
        access_gate_json=access_gate,
        change_layer=SubjectiveRelationshipEdgeChange.CHANGE_LAYER_RAW,
        is_context_active=True,
    )

    edge.relationship_label = relationship_label or edge.relationship_label
    edge.summary = revised_summary or change_summary or edge.summary
    edge.knowledge_basis = knowledge_basis or edge.knowledge_basis
    edge.confidence = confidence
    if open_questions:
        edge.open_questions_json = open_questions
    if _has_meaningful_json_payload(directional_notes):
        edge.directional_notes_json = directional_notes
    edge.last_change_summary = change_summary or edge.last_change_summary
    edge.save()

    return edge


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
            "relationship_edge_updates=",
            len(update.get("relationship_edge_updates") or []),
            "beliefs=",
            len(update.get("beliefs") or []),
            flush=True,
        )

        # --- Memories ---
        created_memory_count = 0
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
            raw_related_character_slugs = _merge_slug_lists(
                memory.get("related_character_slugs") or [],
                [related_character.slug] if related_character else [],
            )
            related_character_slugs = _valid_character_slugs_from_list(
                world,
                raw_related_character_slugs,
            )

            CharacterMemory.objects.create(
                world=world,
                character=character,
                content=content,
                memory_type=memory.get("memory_type") or "scene_experience",
                memory_layer=CharacterMemory.MEMORY_LAYER_RAW,
                is_context_active=True,
                related_character=related_character,
                related_character_slugs_json=related_character_slugs,
                source_scene=source_scene,
            )
            created_memory_count += 1

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
        belief_candidates = []

        for raw_belief in update.get("beliefs", []) or []:
            belief = _normalize_belief_candidate(raw_belief)
            if not belief:
                continue

            allowed, access_gate = _subjective_update_access_gate(
                character=character,
                resolved_scene_state=resolved_scene_state,
                source_scene=source_scene,
                basis=belief["basis"],
                update_kind="belief",
            )
            if not allowed:
                print(
                    "[story] dropping_belief_update access_gate_failed",
                    access_gate,
                    flush=True,
                )
                continue

            belief["access_gate"] = access_gate
            belief_candidates.append(belief)

        for belief in belief_candidates:
            CharacterBelief.objects.create(
                world=character.world,
                character=character,
                subject_type=belief.get("subject_type") or "",
                subject_slug=belief.get("subject_slug") or "",
                related_subject_slugs_json=_valid_character_slugs_from_list(
                    character.world,
                    belief.get("related_subject_slugs") or [],
                ),
                belief=belief.get("belief") or "",
                confidence=belief.get("confidence") or 0.5,
                basis=belief.get("basis") or "",
                source=PENDING_BELIEF_REDUCTION_SOURCE,
                source_scene=source_scene,
                belief_status=CharacterBelief.BELIEF_STATUS_TRANSIENT,
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
            change_summary = str(
                p.get("change_summary")
                or p.get("summary")
                or ""
            ).strip()
            revised_summary = str(
                p.get("revised_summary")
                or p.get("summary")
                or ""
            ).strip()
            knowledge_basis = str(
                p.get("knowledge_basis")
                or p.get("basis")
                or ""
            ).strip()
            open_questions = _clean_string_list(p.get("open_questions") or [])

            has_perception_payload = any([
                change_summary,
                revised_summary,
                knowledge_basis,
                open_questions,
                _has_meaningful_json_payload(p.get("impression_json") or {}),
                _has_meaningful_json_payload(p.get("relationship_json") or {}),
                _has_meaningful_json_payload(p.get("belief_json") or {}),
                trust_delta,
                attraction_delta,
                fear_delta,
                resentment_delta,
            ])

            if not has_perception_payload:
                continue

            allowed, access_gate = _subjective_update_access_gate(
                character=character,
                resolved_scene_state=resolved_scene_state,
                source_scene=source_scene,
                basis=knowledge_basis,
                update_kind="perception",
            )
            if not allowed:
                print(
                    "[story] dropping_perception_update access_gate_failed",
                    access_gate,
                    flush=True,
                )
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
                summary=change_summary,
                revised_summary=revised_summary,
                knowledge_basis=knowledge_basis,
                open_questions_json=open_questions,
                access_gate_json=access_gate,
                change_layer=CharacterPerceptionChange.CHANGE_LAYER_RAW,
                is_context_active=True,
                impression_json=_jsonish(p.get("impression_json")),
                relationship_json=_jsonish(p.get("relationship_json")),
                belief_json=_jsonish(p.get("belief_json")),
                trust_delta=trust_delta,
                attraction_delta=attraction_delta,
                fear_delta=fear_delta,
                resentment_delta=resentment_delta,
            )

            perception.summary = (
                revised_summary
                or change_summary
                or perception.summary
            )
            perception.last_change_summary = change_summary or perception.last_change_summary
            perception.knowledge_basis = knowledge_basis or perception.knowledge_basis
            if open_questions:
                perception.open_questions_json = open_questions
            perception.impression_json = _jsonish(
                p.get("impression_json")
            ) or perception.impression_json

            perception.relationship_json = _jsonish(
                p.get("relationship_json")
            ) or perception.relationship_json

            perception.belief_json = _jsonish(
                p.get("belief_json")
            ) or perception.belief_json

            perception.trust += trust_delta
            perception.attraction += attraction_delta
            perception.fear += fear_delta
            perception.resentment += resentment_delta

            perception.save()

        # --- Subjective relationship edges ---
        for edge_update in update.get("relationship_edge_updates", []) or []:
            _persist_subjective_relationship_edge_update(
                world=world,
                character=character,
                resolved_scene_state=resolved_scene_state,
                source_scene=source_scene,
                edge_update=edge_update,
            )


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


def build_character_identity_registry(world):
    return [
        {
            "slug": item.get("slug"),
            "name": item.get("name"),
            "is_player": item.get("is_player", False),
        }
        for item in build_character_registry(world)
    ]


def _belief_payload_for_character_context(belief):
    return {
        "subject_type": belief.subject_type,
        "subject_slug": belief.subject_slug,
        "related_subject_slugs": belief_subject_slugs(belief),
        "belief": belief.belief,
        "confidence": belief.confidence,
        "basis": belief.basis,
        "belief_status": belief.belief_status,
    }


def _memory_payload_for_character_context(memory):
    return {
        "content": memory.content,
        "memory_type": memory.memory_type,
        "memory_layer": memory.memory_layer,
        "related_character_slug": (
            memory.related_character.slug
            if memory.related_character_id
            else None
        ),
        "related_character_slugs": memory_related_character_slugs(memory),
        "source_memory_count": memory.source_memory_count,
    }


def _relationship_archive_payloads_for_target(
    relationship_archives_by_target,
    target_id,
):
    return relationship_archives_by_target.get(target_id, [])


def build_subjective_character_profiles(
    observer,
    registry,
    local_cast,
    perceptions,
    beliefs,
    memories,
    relationship_archives_by_target,
    profile_memories_by_slug=None,
):
    profile_memories_by_slug = profile_memories_by_slug or {}
    registry_by_slug = {
        item.get("slug"): item
        for item in registry or []
        if item.get("slug")
    }
    perceptions_by_slug = {
        perception.target.slug: perception
        for perception in perceptions
    }

    relevant_slugs = []

    def add_slug(slug):
        slug = str(slug or "").strip()
        if (
            slug
            and slug != observer.slug
            and slug in registry_by_slug
            and slug not in relevant_slugs
        ):
            relevant_slugs.append(slug)

    for slug in (local_cast or {}).keys():
        add_slug(slug)

    for perception in perceptions:
        add_slug(perception.target.slug)

    for belief in beliefs:
        for slug in belief_subject_slugs(belief):
            add_slug(slug)

    for memory in memories:
        for slug in memory_related_character_slugs(memory):
            add_slug(slug)

    profiles = []

    for slug in relevant_slugs:
        registry_item = registry_by_slug.get(slug) or {}
        perception = perceptions_by_slug.get(slug)
        local_entry = local_cast.get(slug) if isinstance(local_cast, dict) else {}
        if not isinstance(local_entry, dict):
            local_entry = {}

        related_beliefs = [
            _belief_payload_for_character_context(belief)
            for belief in beliefs
            if belief_involves_slug(belief, slug)
        ][:CHARACTER_AGENT_PROFILE_BELIEF_LIMIT]
        profile_memories = profile_memories_by_slug.get(slug)
        if profile_memories is None:
            profile_memories = [
                memory
                for memory in memories
                if (
                    memory.memory_layer != CharacterMemory.MEMORY_LAYER_RAW
                    and memory_involves_slug(memory, slug)
                )
            ]
        related_memories = [
            _memory_payload_for_character_context(memory)
            for memory in profile_memories
        ]

        profiles.append({
            "slug": slug,
            "name": registry_item.get("name") or slug,
            "is_player": registry_item.get("is_player", False),
            "scene_access": {
                "local_presence": local_entry.get("local_presence", ""),
                "access": local_entry.get("access", ""),
                "perception_scope": local_entry.get("perception_scope", ""),
                "perception_reason": local_entry.get("perception_reason", ""),
                "known_position": local_entry.get("known_position", ""),
                "known_space_label": local_entry.get("known_space_label", ""),
            },
            "mental_profile": {
                "source": (
                    "stored_subjective_perception"
                    if perception
                    else "no_stored_subjective_perception"
                ),
                "summary": perception.summary if perception else "",
                "knowledge_basis": perception.knowledge_basis if perception else "",
                "recent_change": perception.last_change_summary if perception else "",
                "relationship_archives": (
                    _relationship_archive_payloads_for_target(
                        relationship_archives_by_target,
                        perception.target_id,
                    )
                    if perception
                    else []
                ),
                "open_questions": perception.open_questions_json if perception else [],
                "beliefs_involving_character": related_beliefs,
                "memories_involving_character": related_memories,
                "scores": (
                    {
                        "trust": perception.trust,
                        "attraction": perception.attraction,
                        "fear": perception.fear,
                        "resentment": perception.resentment,
                    }
                    if perception
                    else {}
                ),
            },
        })

    return profiles


def _subjective_character_profiles_text(subjective_profiles):
    if not subjective_profiles:
        return (
            "No stored mental profiles of other characters are currently pressing on you. "
            "Use only your local scene access and current perception."
        )

    lines = []

    for profile in subjective_profiles:
        mental = profile.get("mental_profile") or {}
        summary = (
            mental.get("summary")
            or "No developed mental profile stored yet."
        )
        beliefs = mental.get("beliefs_involving_character") or []
        memories = mental.get("memories_involving_character") or []
        belief_lines = []
        seen_beliefs = set()
        for belief in beliefs:
            key = _context_text_key(belief.get("belief"))
            if not key or key in seen_beliefs:
                continue
            seen_beliefs.add(key)
            belief_lines.append(belief.get("belief", ""))
            if len(belief_lines) >= CHARACTER_AGENT_PROFILE_BELIEF_LIMIT:
                break
        belief_text = "; ".join(belief_lines) or "none stored"

        memory_lines = []
        seen_memories = set()
        for memory in memories:
            key = _context_text_key(memory.get("content"))
            if not key or key in seen_memories:
                continue
            seen_memories.add(key)
            memory_lines.append(memory.get("content", ""))
        memory_text = "; ".join(memory_lines) or "none stored"
        archive_text = _relationship_archive_text(
            mental.get("relationship_archives") or []
        )

        lines.append(
            f"- {profile.get('name')} ({profile.get('slug')}): "
            f"mental profile: {summary} "
            f"Basis: {mental.get('knowledge_basis') or 'unknown/unstated'}. "
            f"Recent change: {mental.get('recent_change') or 'none stored'}. "
            f"Past/History: {archive_text}. "
            f"Beliefs involving them: {belief_text}. "
            f"Memories involving them: {memory_text}."
        )

    return "\n".join(lines)


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
            "continuity_maintenance": {
                "memory_compactions": [],
                "relationship_compactions": [],
                "relationship_edge_compactions": [],
                "belief_reduction_actions": [],
            },
        }

    config = get_character_agent_config(character)
    client = config["client"]
    model = config["model"]
    continuity_maintenance_tasks = character_continuity_maintenance_tasks(character)
    active_story_arc_lenses = story_arc_lenses_for_character(world, character)

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
        "active_story_arc_lenses": active_story_arc_lenses,
        "continuity_maintenance_tasks": continuity_maintenance_tasks,
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

    apply_character_continuity_maintenance(
        character,
        continuity_maintenance_tasks,
        data.get("continuity_maintenance") or {},
    )

    return {
        "perspective_beat": str(data.get("perspective_beat") or "").strip(),
        "private_player_material": str(
            data.get("private_player_material") or ""
        ).strip(),
        "visibility_note": str(data.get("visibility_note") or "").strip(),
        "active_story_arc_lenses": active_story_arc_lenses,
        "continuity_maintenance": data.get("continuity_maintenance") or {
            "memory_compactions": [],
            "relationship_compactions": [],
            "relationship_edge_compactions": [],
            "belief_reduction_actions": [],
        },
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

    profile = getattr(character, "profile", None)
    state = getattr(character, "state", None)
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

    # The perspective-rewriter is the per-character meta lane. It may have just
    # compacted memories/relationships or reduced beliefs, so fetch context after
    # it runs rather than before.
    recent_character_memories = active_character_memories_for_context(character)

    beliefs = active_character_beliefs_for_context(character)

    perceptions = list(
        CharacterPerception.objects.filter(observer=character)
        .select_related("target")
    )
    relationship_archives_by_target = {
        p.target_id: relationship_archives_payload_for_context(character, p.target)
        for p in perceptions
    }
    character_memory_ids = _memory_ids(recent_character_memories)
    character_memory_keys = _memory_text_keys(recent_character_memories)
    profile_memory_relevant_slugs = _merge_slug_lists(
        [character.slug],
        list(local_cast.keys()) if isinstance(local_cast, dict) else [],
        [p.target.slug for p in perceptions],
        [
            slug
            for belief in beliefs
            for slug in belief_subject_slugs(belief)
        ],
        [
            slug
            for memory in recent_character_memories
            for slug in memory_related_character_slugs(memory)
        ],
    )
    profile_memories_by_slug = {
        slug: profile_character_memories_for_slug(
            character,
            slug,
            exclude_ids=character_memory_ids,
            exclude_keys=character_memory_keys,
        )
        for slug in profile_memory_relevant_slugs
        if slug != character.slug
    }
    other_characters = build_subjective_character_profiles(
        observer=character,
        registry=registry,
        local_cast=local_cast,
        perceptions=perceptions,
        beliefs=beliefs,
        memories=recent_character_memories,
        relationship_archives_by_target=relationship_archives_by_target,
        profile_memories_by_slug=profile_memories_by_slug,
    )
    other_characters_text = _subjective_character_profiles_text(other_characters)
    profile_belief_keys_by_slug = _profile_context_keys_by_slug(
        other_characters,
        "belief",
    )
    profile_memory_keys_by_slug = _profile_context_keys_by_slug(
        other_characters,
        "memory",
    )
    profile_belief_keys = (
        set().union(*profile_belief_keys_by_slug.values())
        if profile_belief_keys_by_slug
        else set()
    )
    profile_memory_keys = (
        set().union(*profile_memory_keys_by_slug.values())
        if profile_memory_keys_by_slug
        else set()
    )
    profile_memory_ids = set().union(
        *[
            _memory_ids(memories)
            for memories in profile_memories_by_slug.values()
        ]
    ) if profile_memories_by_slug else set()
    relationship_pressure_memories_by_slug = {
        slug: relationship_pressure_memories_for_slug(
            character,
            slug,
            exclude_ids=(
                character_memory_ids
                | _memory_ids(profile_memories_by_slug.get(slug, []))
            ),
            exclude_keys=(
                character_memory_keys
                | profile_memory_keys_by_slug.get(slug, set())
            ),
        )
        for slug in profile_memory_relevant_slugs
        if slug != character.slug
    }
    global_extra_memories = global_extra_character_memories_for_context(
        character,
        exclude_ids=character_memory_ids | profile_memory_ids,
        exclude_keys=character_memory_keys | profile_memory_keys,
    )
    social_edge_relevant_slugs = _merge_slug_lists(
        [character.slug],
        list(local_cast.keys()) if isinstance(local_cast, dict) else [],
        [p.target.slug for p in perceptions],
        [
            slug
            for belief in beliefs
            for slug in belief_subject_slugs(belief)
        ],
        [
            slug
            for memory in recent_character_memories
            for slug in memory_related_character_slugs(memory)
        ],
    )
    subjective_relationship_edges = _active_subjective_relationship_edges_for_context(
        character,
        relevant_slugs=social_edge_relevant_slugs,
    )
    subjective_relationship_edge_payloads = [
        _relationship_edge_payload(edge)
        for edge in subjective_relationship_edges
    ]
    subjective_relationship_edges_text = _subjective_relationship_edges_text(
        subjective_relationship_edge_payloads
    )

    local_access_text = "\n".join(
        (
            f"- {slug}: {entry.get('known_position', '')}. "
            f"{entry.get('perception_reason', '')} "
            f"scope={entry.get('perception_scope', '')}."
        ).strip()
        for slug, entry in local_cast.items()
        if slug != character.slug and isinstance(entry, dict)
    ) or "You do not meaningfully perceive any other character right now."

    impression_lines = []
    for perception in perceptions:
        additional_beliefs = _beliefs_for_slug_text(
            beliefs,
            perception.target.slug,
            exclude_keys=profile_belief_keys_by_slug.get(
                perception.target.slug,
                set(),
            ),
        )
        additional_memories = _memories_for_slug_text(
            relationship_pressure_memories_by_slug.get(
                perception.target.slug,
                [],
            ),
            perception.target.slug,
            exclude_keys=profile_memory_keys_by_slug.get(
                perception.target.slug,
                set(),
            ),
        )
        open_questions = json.dumps(
            perception.open_questions_json or [],
            ensure_ascii=False,
        )
        impression_lines.append(
            f"- {perception.target.name}: "
            f"Additional beliefs not covered above: {additional_beliefs}. "
            f"Additional memories not covered above: {additional_memories}. "
            f"Open questions: {open_questions}. "
            f"trust={perception.trust}, attraction={perception.attraction}, "
            f"fear={perception.fear}, resentment={perception.resentment}."
        )

    impression_text = (
        "\n".join(impression_lines)
        or "No additional stored impressions of other characters are currently pressing on you."
    )

    recent_memory_text = _global_memory_text(
        global_extra_memories,
        exclude_keys=character_memory_keys | profile_memory_keys,
    )

    current_belief_text = _global_belief_text(
        beliefs,
        exclude_keys=profile_belief_keys,
    )

    recent_subjective_scene_text = "\n\n".join(
        f"{s.subjective_scene_text}"
        for s in recent_subjective_scenes
        if s.subjective_scene_text
    ) or "No recent subjective scene history is available."

    pending_aftermath_text = (
        f"""
Turn: {pending_aftermath_scene.turn_number}
Your participation: {pending_aftermath_scene.participation}

What you experienced:
{pending_aftermath_scene.event_record_text}
""".strip()
    if pending_aftermath_scene
    else "No previous approved scene is waiting to become part of your subjective continuity.")

    pending_subjective_scene_text = (
        f"{pending_aftermath_scene.subjective_scene_text}"
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

    You have a few core beliefs that can affect your actions, such as: {profile_permabeliefsnotes}

    Your current mental profiles of other characters are built from your accumulated experience, beliefs, memories, and relationship perceptions. Do not treat these as objective author profiles:
    {other_characters_text}

    Your subjective social map of how characters relate to each other. This may be incomplete, biased, or wrong; use it as your understanding, not narrator truth:
    {subjective_relationship_edges_text}


    Your current local access:
    {local_access_text}

    You're in {space.get("description")}.

    Perspective rewrite visibility note:
    {visibility_note or "Use the local scene state above as your boundary."}

    Current relationship pressures and emotional axes:
    {impression_text}

    Active memories not already covered in the mental profiles above:
    {recent_memory_text}

    Your recent subjective scene records, character-scoped and not omniscient narration:
    {recent_subjective_scene_text}

    Waiting subjective scene aftermath, if any:
    {pending_subjective_scene_text}

    Pending prior scene event record for archival digestion, if any:
    {pending_aftermath_text}

    You're feeling {emotional_statesummary}

    Active beliefs not already covered in the mental profiles above:
    {current_belief_text}

    The latest beat, adjusted for your perspective, is provided as the user message.

    Private player material not directly available to you:
    {private_player_material or "None."}

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
            "These are your active subjective memories: durable History summaries, recent Past summaries, and a small number of fresh raw memories. They are not new events. "
            "related_character_slugs lists every character materially involved in the memory. "
            "Use them for emotional carryover, grudges, attachments, fears, unresolved pressures, interpretation, and motivation. "
            "They do not supply the next maneuver, tactic, offer, line of dialogue, or physical action unless the current scene beat specifically calls for that return.",
            [
                {
                    "content": m.content,
                    "memory_type": m.memory_type,
                    "memory_layer": m.memory_layer,
                    "related_character_slug": (
                        m.related_character.slug
                        if m.related_character_id
                        else None
                    ),
                    "related_character_slugs": memory_related_character_slugs(m),
                    "source_memory_count": m.source_memory_count,
                }
                for m in recent_character_memories
            ],
        ),
        "beliefs": _payload_section(
            "These are things you currently believe or suspect. They may be incomplete, biased, or wrong. "
            "subject_slug is the primary anchor; related_subject_slugs are other characters materially involved in that belief. "
            "Use them to mold your interpretation of the scene, not as guaranteed narrator truth.",
            [
                {
                    "subject_type": b.subject_type,
                    "subject_slug": b.subject_slug,
                    "related_subject_slugs": belief_subject_slugs(b),
                    "belief": b.belief,
                    "confidence": b.confidence,
                    "basis": b.basis,
                    "belief_status": b.belief_status,
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
                    "knowledge_basis": p.knowledge_basis,
                    "open_questions": p.open_questions_json,
                    "last_change_summary": p.last_change_summary,
                    "relationship_archives": relationship_archives_by_target.get(p.target_id, []),
                    "impression": p.impression_json,
                    "relationship": p.relationship_json,
                    "belief": p.belief_json,
                    "trust": p.trust,
                    "attraction": p.attraction,
                    "fear": p.fear,
                    "resentment": p.resentment,
                }
                for p in perceptions
            ],
        ),
        "subjective_relationship_edges": _payload_section(
            "These are your subjective social-graph edges: how you currently understand relationships between pairs of characters. "
            "They may include your own relationship edge with someone else, or a relationship between two other people. "
            "They are not objective narrator truth.",
            subjective_relationship_edge_payloads,
        ),
        "pending_previous_scene_aftermath": _payload_section(
            "If present, this is an approved prior scene that has not yet been converted into your character's subjective scene history, memory, state, beliefs, and perceptions. "
            "Process this internally as archival digestion, not as a template for your current maneuver. "
            "Use only the resulting emotional/subjective continuity when writing your current scene contribution, then move forward instead of restaging the prior scene's tactic, gesture, dialogue pattern, or physical arrangement. ",
            {
                "character_scene_id": pending_aftermath_scene.id,
                "source_turn_number": pending_aftermath_scene.turn_number,
                "participation": pending_aftermath_scene.participation,
                "event_record_json": pending_aftermath_scene.event_record_json,
            } if pending_aftermath_scene else None,
        ),
        "recent_scene_events": _payload_section(
            "Recent event records for approved scenes that do not yet have subjective scene text for you. These are filtered to your character record, not omniscient narration:",
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
            "Your recent subjective scene records. These are character-scoped memories of approved scenes, not Cassandra's omniscient scene text:",
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
        "active_story_arc_lenses": _payload_section(
            "Authorial presentation lenses used by the perspective rewriter before the character-agent call. These are shown for debugging; they are not directly sent as character knowledge.",
            perspective_beat.get("active_story_arc_lenses") or [],
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
        "character_identity_registry": build_character_identity_registry(world),
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

RELATIONSHIP_PERCEPTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "relationship_label": {"type": "string"},
    },
    "required": ["summary", "relationship_label"],
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
                    "related_character_slug": {"type": ["string", "null"]},
                    "related_character_slugs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "content",
                    "memory_type",
                    "related_character_slug",
                    "related_character_slugs",
                ],
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
                    "change_summary": {"type": "string"},
                    "revised_summary": {"type": "string"},
                    "knowledge_basis": {"type": "string"},
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "impression_json": JSON_SUMMARY_SCHEMA,
                    "relationship_json": RELATIONSHIP_PERCEPTION_SCHEMA,
                    "belief_json": JSON_SUMMARY_SCHEMA,
                    "trust_delta": {"type": "number"},
                    "attraction_delta": {"type": "number"},
                    "fear_delta": {"type": "number"},
                    "resentment_delta": {"type": "number"}
                },
                "required": [
                    "target_slug", "change_summary", "revised_summary",
                    "knowledge_basis", "open_questions",
                    "impression_json", "relationship_json",
                    "belief_json",
                    "trust_delta", "attraction_delta", "fear_delta", "resentment_delta"
                ]
            }
        },
        "relationship_edge_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "subject_a_slug": {"type": "string"},
                    "subject_b_slug": {"type": "string"},
                    "relationship_label": {"type": "string"},
                    "change_summary": {"type": "string"},
                    "revised_summary": {"type": "string"},
                    "knowledge_basis": {"type": "string"},
                    "confidence": {"type": "number"},
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "directional_notes_json": DIRECTIONAL_NOTES_SCHEMA,
                },
                "required": [
                    "subject_a_slug",
                    "subject_b_slug",
                    "relationship_label",
                    "change_summary",
                    "revised_summary",
                    "knowledge_basis",
                    "confidence",
                    "open_questions",
                    "directional_notes_json",
                ],
            },
        },
        "beliefs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "subject_type": {"type": "string"},
                    "subject_slug": {"type": "string"},
                    "related_subject_slugs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "belief": {"type": "string"},
                    "confidence": {"type": "number"},
                    "basis": {"type": "string"}
                },
                "required": [
                    "subject_type",
                    "subject_slug",
                    "related_subject_slugs",
                    "belief",
                    "confidence",
                    "basis",
                ],
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
    "required": [
        "subjective_scene_text",
        "memories",
        "state_update",
        "perception_updates",
        "relationship_edge_updates",
        "beliefs",
    ]
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
        "character_pov_prose": {
            "type": "string",
            "description": (
                "A first-person, present-tense current-beat scenelet from this character's limited perspective. "
                "Write it like the character's visible contribution to the group scene: what I perceive, feel, attempt, say, withhold, want, and expect right now. "
                "Do not decide final success, narrate other characters' ultimate reactions, or claim narrator-only knowledge."
            ),
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
        "emotional_strategy": {
            "type": "string"
        },
        "unspoken_avoidance": {
            "type": "string"
        },
        "desired_next_moment": {
            "type": "string"
        },
        "uninterrupted_followthrough": {
            "type": "string"
        },
        "resistance_response": {
            "type": "string"
        },
        "pressure_channel": {
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
        "character_pov_prose",
        "attempted_action",
        "attempted_dialogue",
        "internal_intent",
        "emotional_strategy",
        "unspoken_avoidance",
        "desired_next_moment",
        "uninterrupted_followthrough",
        "resistance_response",
        "pressure_channel",
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

You write this character's current subjective contribution to the scene.
You do NOT narrate the final resolved scene outcome.
You do NOT decide whether actions succeed.
You do NOT decide what other characters ultimately do.

Your job is to determine:
1. the first-person current-beat prose this character contributes
2. what this character attempts to do or say in the moment
3. what this character's currently authored intent is

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
- scene_contribution: this character's current subjective scene contribution. It begins with first-person character_pov_prose, then gives Cassandra structured support fields: what they attempt now, the emotional strategy behind it, what they are avoiding saying, what they want the next few seconds to become, what they would do if not stopped, how they would adapt to resistance, and which channel should carry the pressure.
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
- private_player_material may include the player character's private thoughts, motives, desires, interpretations, or intentions.
- Do not let this character directly know, quote, answer, or react to private user/player thoughts.
- If private_player_material describes internal player thought, react only to outward behavior described in perspective_adjusted_beat.
- If private thought changes visible behavior, infer only a plausible visible consequence, not the exact thought.
- Subtle outward cues do not reveal exact internal meaning.
- Prefer biased, partial, uncertain, or socially motivated interpretation over accurate mind-reading.
- Do not use phrases implying direct access to thought, such as "I can tell what you're thinking," "saw you thinking," or "you realized."

scene_contribution rules:
- character_pov_prose is the first thing the user will see from this character before Cassandra's final scene draft. Make it read like a vivid subjective message or scenelet from the character's own present-tense point of view.
- The overall scene_contribution is a current-beat proposal, not a final outcome.
- Cassandra decides success, failure, interruption, timing, and final narration.
- Include one concise but substantial character_pov_prose passage and at least one meaningful attempted_action, attempted_dialogue, or internal_intent.
- Give Cassandra enough usable pressure to render more than a reflexive gesture or throwaway acknowledgement when your character has a richer strategy available.
- Stay within what this character could plausibly know, perceive, infer, remember, want, or attempt.
- Do not decide what other characters ultimately do.
- Do not narrate final success.
- Do not fill the richer beat fields by restating the same sentence in different words. Each field should add a distinct kind of guidance.
- Use memories, beliefs, and perceptions only as they affect this character's own interpretation and behavior.
- other_characters contains this character's subjective mental profiles of other characters, not omniscient author profiles.
- If another character has no stored mental profile yet, do not invent one from genre assumptions; rely on current local access, memories, beliefs, and visible behavior.
- Sparse but accurate output is better than padded output.

Field guidance:
- slug: the canonical slug of the acting character.
- character_pov_prose: first-person, current-beat prose from this character's limited perspective. Use "I" naturally. Include what this character notices, how it lands, what they attempt, what they say or almost say, what they hide, and what they want the next seconds to become. Do not write Cassandra-style final narration.
- attempted_action: a concrete physical or behavioral action the character attempts.
- attempted_dialogue: what the character attempts to say aloud; not guaranteed to land uninterrupted.
- internal_intent: the immediate motive behind the move.
- emotional_strategy: how this character is trying to shape the emotional/social pressure of the beat.
- unspoken_avoidance: what this character is avoiding saying, admitting, naming, asking, or making explicit.
- desired_next_moment: what this character wants the next few seconds to become if the scene keeps bending their way.
- uninterrupted_followthrough: what this character would naturally attempt next if nobody stops, interrupts, resists, or redirects them.
- resistance_response: how this character would adapt if the main target resists, challenges, withdraws, misunderstands, or refuses the move.
- pressure_channel: the main channel carrying this beat, such as speech, silence, touch, distance, misdirection, display, withdrawal, or mixed channels.
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
- Review your subjective recent_scenes before proposing the next move.
- Do not repeat this character's previous conversational device, emotional tactic, prose rhythm, or social maneuver unless perspective_adjusted_beat directly calls for it.
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
- If it is present, process it before writing your current scene contribution as archival digestion, not as a template for your current maneuver.
- Let it shape emotional carryover, interpretation, memory, beliefs, and perception changes; do not restage its tactic, gesture, dialogue pattern, or physical arrangement unless the current scene beat specifically calls for that return.
- Return previous_scene_aftermath as the subjective consequences of that prior approved scene.
- previous_scene_aftermath.subjective_scene_text should be a concise prose scene written from your limited perspective.
- previous_scene_aftermath must not include Cassandra narrator prose.
- Use only the event_record_json, local participation, memories, beliefs, perceptions, and character state provided, as well as those your character naturally knows.
- For memories, related_character_slugs should include every character slug materially involved in that memory, not just the primary related_character_slug.
- Every belief must include basis: what you perceived, were told, inferred, remembered, or felt that caused you to believe it. Do not create a belief if you cannot state such a basis.
- For beliefs, subject_slug is the primary anchor; related_subject_slugs should include additional character slugs materially involved in the belief.
- If a belief compares or triangulates two characters, link both with subject_slug plus related_subject_slugs.
- Do not reduce or archive the broader belief set here; return only scene-grounded belief candidates that create, reinforce, revise, or meaningfully complicate an active operating assumption.
- If that belief also changes how you see more than one character, emit separate perception_updates for each changed relationship target.
- For each perception update, change_summary is what changed in this scene, while revised_summary is your compact current understanding of that target after merging the new evidence with current_perceptions.
- For perception_updates.relationship_json, summary should describe the direct observer-target relationship, and relationship_label should be a compact meaningful label such as flirtation, dependence, rivalry, trust, fear, caretaking, antagonism, uncertainty, control, or alliance. Do not use the generic label "relationship" unless nothing more specific is possible.
- revised_summary is a living subjective dossier, not an append-only log.
- knowledge_basis must explain why you now think or feel this, using only information available to you.
- open_questions should list uncertainties about the target that may shape future behavior; return an empty list if none matter.
- Return relationship_edge_updates when you learn, revise, or meaningfully complicate how two characters relate to each other, including your own relationship edge with another character.
- A relationship edge is your subjective social map, not objective narrator truth. Use it for pair dynamics such as siblings, rivalry, dating, friendship, alliance, resentment, intimacy, obligation, or uncertainty.
- If you return a belief whose subject_slug and related_subject_slugs connect two characters through a socially meaningful dynamic, also return a relationship_edge_update for that same pair.
- Do not create edge updates for every co-presence; create them only when the pair relationship would matter to future interpretation or behavior.
- For relationship_edge_updates, knowledge_basis must explain how you could know, infer, remember, or feel the connection.
- Then write your current scene_contribution as this character now, beginning with character_pov_prose and carrying forward the subjective effect of that prior scene.
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
                "relationship_edge_updates=",
                len(previous_scene_aftermath.get("relationship_edge_updates") or []),
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
        "relationship_edge_updates": [],
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

    recent_character_memories = active_character_memories_for_context(character)

    beliefs = active_character_beliefs_for_context(character)

    perceptions = list(
        CharacterPerception.objects.filter(observer=character)
        .select_related("target")
    )
    relationship_archives_by_target = {
        p.target_id: relationship_archives_payload_for_context(character, p.target)
        for p in perceptions
    }
    social_edge_relevant_slugs = _merge_slug_lists(
        [character.slug],
        list(cast.keys()) if isinstance(cast, dict) else [],
        [p.target.slug for p in perceptions],
        [
            slug
            for belief in beliefs
            for slug in belief_subject_slugs(belief)
        ],
        [
            slug
            for memory in recent_character_memories
            for slug in memory_related_character_slugs(memory)
        ],
    )
    subjective_relationship_edges = _active_subjective_relationship_edges_for_context(
        character,
        relevant_slugs=social_edge_relevant_slugs,
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
                "memory_layer": m.memory_layer,
                "related_character_slug": (
                    m.related_character.slug
                    if m.related_character_id
                    else None
                ),
                "related_character_slugs": memory_related_character_slugs(m),
                "source_memory_count": m.source_memory_count,
            }
            for m in recent_character_memories
        ],
        "beliefs": [
            {
                "subject_type": b.subject_type,
                "subject_slug": b.subject_slug,
                "related_subject_slugs": belief_subject_slugs(b),
                "belief": b.belief,
                "confidence": b.confidence,
                "basis": b.basis,
                "belief_status": b.belief_status,
            }
            for b in beliefs
        ],
        "current_perceptions": [
            {
                "target_slug": p.target.slug,
                "summary": p.summary,
                "knowledge_basis": p.knowledge_basis,
                "open_questions": p.open_questions_json,
                "last_change_summary": p.last_change_summary,
                "relationship_archives": relationship_archives_by_target.get(p.target_id, []),
                "impression": p.impression_json,
                "relationship": p.relationship_json,
                "belief": p.belief_json,
                "trust": p.trust,
                "attraction": p.attraction,
                "fear": p.fear,
                "resentment": p.resentment,
            }
            for p in perceptions
        ],
        "current_subjective_relationship_edges": [
            _relationship_edge_payload(edge)
            for edge in subjective_relationship_edges
        ],
        "character_registry": build_character_identity_registry(world),
    }


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
- relationship_edge_updates: changes in this character's understanding of how two characters relate to each other
- beliefs: beliefs this character now holds or reinforces

Rules:
- Respect observer_cast_entry, perception_scope, sensory_access, and presence.
- Do not include facts this character could not perceive or plausibly infer.
- Prefer sparse, meaningful updates over padded output.
- If nothing changed, return empty memories, perception_updates, beliefs, and empty objects inside state_update.
- Memories should be subjective, not omniscient.
- For memories, related_character_slugs should include every character slug materially involved in that memory, not just the primary related_character_slug.
- Perception updates should target valid character slugs only.
- Relationship edge updates should use two valid character slugs and describe the observer's subjective understanding of that pair's relationship, dynamic, status, alliance, kinship, rivalry, intimacy, obligation, or tension.
- Beliefs may be wrong; they are the character's beliefs, not objective truth.
- Every belief must include a concrete basis: what the character perceived, was told, inferred, remembered, or felt that caused the belief.
- For beliefs, subject_slug is the primary anchor; related_subject_slugs should include additional character slugs materially involved in the belief.
- If a belief compares or triangulates two characters, link both with subject_slug plus related_subject_slugs.
- Do not reduce or archive the broader belief set here; return only scene-grounded belief candidates that create, reinforce, revise, or meaningfully complicate an active operating assumption.
- Do not create a belief or perception update if you cannot state a plausible basis available to this character.

Perception update guidance:
- Create perception_updates when the approved scene changes or reinforces how this observer sees another character.
- The perception target does not need to be physically present in the scene.
- A scene interaction with one character may change the observer's perception of an absent third party through comparison, guilt, attraction, jealousy, resentment, memory, longing, relief, fear, or contrast.
- A cross-character belief or memory does not automatically change every linked relationship; create separate perception_updates only for the targets whose relationship map actually changed.
- The observer must have perceived, felt, inferred, remembered, or internally associated the change during the scene.
- The target only needs to be a valid known character.
- Subtle social information counts when it may affect future behavior: comfort, caution, attraction, trust, jealousy, uncertainty, ease, intimidation, protectiveness, dependence, resentment, or perceived closeness.
- Do not create perception updates for every interaction; create them when the observation, comparison, or internal reaction may shape future behavior.
- For each perception update, write change_summary as what changed in this scene, and revised_summary as the observer's compact current understanding of the target after merging this change with existing current_perceptions.
- For perception_updates.relationship_json, summary should describe the direct observer-target relationship, and relationship_label should be a compact meaningful label such as flirtation, dependence, rivalry, trust, fear, caretaking, antagonism, uncertainty, control, or alliance. Do not use the generic label "relationship" unless nothing more specific is possible.
- revised_summary should not be an append-only log. It should be a concise living dossier that preserves important prior understanding while incorporating the new evidence.
- knowledge_basis must explain why this observer now thinks or feels this, using only information available to the observer.
- open_questions should list important uncertainties that may shape future behavior. Return an empty list if there are none.

Relationship edge update guidance:
- Create relationship_edge_updates when this observer learns, revises, or meaningfully complicates how two characters relate to one another.
- This can include the observer's own edge with another character, e.g. Mallory ↔ Donnie, or an edge between two other characters, e.g. Donnie ↔ Byrne as Mallory understands it.
- If you return a belief whose subject_slug and related_subject_slugs connect two characters through a socially meaningful dynamic, also return a relationship_edge_update for that same pair.
- Do not create an edge update for every co-presence; create one only when the relationship between the pair would matter to future interpretation or behavior.
- The edge is subjective and may be incomplete, biased, or wrong.
- knowledge_basis must explain how this observer could know, infer, remember, or feel the connection.
- directional_notes_json may capture asymmetry, such as how subject_a seems to treat subject_b versus how subject_b seems to treat subject_a.
"""
