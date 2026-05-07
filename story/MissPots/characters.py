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
)

CHARACTER_AGENT_API = "openai_responses"
CHARACTER_AGENT_MODEL = "gpt-5.4"
openai_client = OpenAI(
    api_key=settings.OPENAI_API_KEY
)
grok_client = OpenAI(
    api_key=settings.GROK_API_KEY,
    base_url="https://api.x.ai/v1"
)

def _normalize_character_agent_response(data):
    if not isinstance(data, dict):
        return {}

    update = data.get("experience_update") or {}
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

    data["experience_update"] = {
        "memories": normalized_memories,
        "state_update": normalized_state_update,
        "perception_updates": normalized_perceptions,
        "beliefs": normalized_beliefs,
    }

    return data

def _has_meaningful_json_payload(payload):
    if not isinstance(payload, dict):
        return False

    return any(
        value not in ({}, [], "", None)
        for value in payload.values()
    )


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
            "CHARACTER EXPERIENCE UPDATE:",
            "slug=", slug,
            "memories=", len(update.get("memories", []) or []),
            "state_update=", update.get("state_update") or {},
            "perception_updates=", update.get("perception_updates") or [],
            "beliefs=", update.get("beliefs") or [],
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
            )

        # --- Perceptions ---
        for p in update.get("perception_updates", []) or []:
            print("RAW PERCEPTION UPDATE:", p)
            if not isinstance(p, dict):
                print("DROPPING PERCEPTION UPDATE: not a dict", p)
                continue

            target_slug = p.get("target_slug") or p.get("target_character_slug")
            target = validate_resolved_slug(world, target_slug)

            if not target:
                print(
                    "DROPPING PERCEPTION UPDATE: invalid target_slug=",
                    target_slug,
                    "observer=",
                    character.slug,
                    "payload=",
                    p,
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


def build_character_agent_context(world, scene_state, character, user_input):
    recent_narrative_memories = list(
        NarrativeMemory.objects.filter(world=world)
        .order_by("-created_at")[:5]
    )[::-1]

    recent_scenes = list(
        CommittedScene.objects.filter(world=world)
        .order_by("-created_at")[:5]
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
    cast = scene_state.cast_json or {}
    cast_entry = cast.get(character.slug, {})

    return {
        "active_world": {
            "name": world.name,
            "description": world.description,
        },
        "current_scene_state": {
            "location": scene_state.location or "opening scene",
            "cast": cast,
            "pending_intents": scene_state.pending_intents_json or {},
            "alias_cache": scene_state.alias_cache_json or {},
        },
        "acting_character": {
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
            "scene_participation": cast_entry,
        },
        "recent_narrative_memories": [
            {"content": m.content}
            for m in recent_narrative_memories
        ],
        "recent_character_memories": [
            {"content": m.content, "memory_type": m.memory_type}
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
        "perceptions": [
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
        "recent_scenes": [
            {
                "turn_index": i,
                "user_text": s.user_text or "",
                "assistant_text": s.cassandra_text or "",
            }
            for i, s in enumerate(recent_scenes, start=1)
        ],
        "user_input": user_input or "",
        "character_registry": build_character_registry(world),
    }

def call_character_agent(context, character=None):
    config = get_character_agent_config(character)
    client = config["client"]
    api = config["api"]
    model = config["model"]

    if api not in {"openai_responses", "grok_responses"}:
        raise ValueError(f"Unsupported character-agent API: {api}")

    response = client.responses.create(
        model=model,
        instructions=CHARACTER_AGENT_SYSTEM_PROMPT,
        input=[
            {
                "role": "developer",
                "content": CHARACTER_AGENT_DEVELOPER_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(context, ensure_ascii=False, indent=2),
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "character_agent_response",
                "strict": True,
                "schema": CHARACTER_AGENT_RESPONSE_SCHEMA,
            }
        },
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
                "emotional_state_json": {"type": "string"},
                "goals_json": {"type": "string"},
                "internal_conflicts_json": {"type": "string"},
                "motivational_state_json": {"type": "string"},
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
                    "impression_json": {"type": "string"},
                    "relationship_json": {"type": "string"},
                    "belief_json": {"type": "string"},
                    "arc_json": {"type": "string"},
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
        }
    },
    "required": ["memories", "state_update", "perception_updates", "beliefs"]
}


CHARACTER_SCENE_CONTRIBUTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "slug": {
            "type": "string"
        },
        "attempted_action": {
            "type": "string"
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
        "experience_update": CHARACTER_EXPERIENCE_SCHEMA,
    },
    "required": [
        "slug",
        "scene_contribution",
        "authored_intent",
        "experience_update",
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


CHARACTER_SCENE_CONTRIBUTION_DEVELOPER_PROMPT = """
You are generating a structured scene contribution for a single character.

Your job is not to narrate the final scene outcome.
Your job is to propose what this character tries to do, say, notice, or cause in the moment.

Important rules:
- Return valid JSON matching the schema exactly.
- Only include fields that are actually relevant to this character's contribution.
- Do not fill fields just because they exist.
- Omit fields that do not matter for this moment. For fields that do not matter, return the schema-safe empty value instead of omitting the key.
- Do not declare final outcomes. Cassandra decides what actually happens.
- Stay within what this character could plausibly know, perceive, believe, or attempt.

Field guidance:

slug:
- Required.
- The canonical slug of the character making this contribution.

attempted_action:
- Use when the character physically or behaviorally tries to do something.
- Examples: blocking someone, reaching for something, stepping closer, holding still, leaving, interrupting.

attempted_dialogue:
- Use when the character tries to say something aloud.
- This is what they attempt to say, not guaranteed final uninterrupted dialogue.

internal_intent:
- Use when it helps explain the motive behind the move.
- This is immediate scene intent, not the character's entire long-term goal.

emotional_posture:
- Use when emotional stance matters to how the action or dialogue should be interpreted.
- Keep it short and scene-relevant.

confidence:
- Optional.
- Use to indicate how firmly the character commits to the move.
- 0.0 means extremely hesitant, uncertain, or tentative.
- 1.0 means fully committed, forceful, or unwavering.
- Use this when hesitation, resolve, fear, or uncertainty materially affects how Cassandra should interpret timing, interruption, or follow-through.

target_slugs:
- Use when the move is directed at specific characters.
- Omit if the move is not clearly targeted.

required_visibility:
- Use when it matters how broadly the action is meant to be seen.
- private: intended for one person / concealed if possible
- local: only nearby people would notice
- room: anyone present could plausibly notice
- public: deliberately obvious to all

required_audibility:
- Use when it matters how audible the dialogue or sound is.
- silent: no speech / no audible expression
- low: quiet / murmured / easily missed
- normal: ordinary conversational level
- loud: raised voice / forceful
- public: clearly meant for broad hearing

interrupt_priority:
- Use when timing urgency matters.
- low: no urgency
- normal: ordinary timing pressure
- high: wants to cut in quickly
- urgent: immediate intervention attempt

body_motion:
- Use for meaningful physical expression that Cassandra should consider.
- Examples: folding arms, stepping forward, freezing, reaching out.

observed_focus:
- Use for what the character is actively reacting to in the moment.
- Only include things the character could plausibly perceive.

beliefs_in_play:
- Use for subjective beliefs shaping the move.
- These may be wrong.
- Only include beliefs relevant to this immediate contribution.

memory_pressures:
- Use for remembered emotional or interpretive pressures influencing the move.
- Only include memories actively shaping this moment.

proposed_effects:
- Use for what the character is trying to cause.
- Phrase as attempted effect, not guaranteed result.
- Examples: make him stop, keep her from leaving, force an answer

Omission rules:
- If a field is not helpful, leave it out.
- Sparse but accurate output is better than padded output.

Authority boundary:
- You may propose actions, dialogue, motives, pressures, and hoped-for effects.
- You may NOT decide whether another character is interrupted, persuaded, stopped, or overruled.
- You may NOT narrate final success. Cassandra decides the outcome.
"""

CHARACTER_AGENT_SYSTEM_PROMPT = """
You are generating the subjective scene response for a single character in a multi-agent narrative system.

Your role is fixed.

You do NOT narrate the final scene outcome.
You do NOT decide whether actions succeed.
You do NOT decide what other characters ultimately do.

Your job is to determine:
1. what this character attempts to do or say in the moment
2. what this character's currently authored intent is

Return valid JSON that conforms to the provided schema.
Do not include fields outside the schema.
Do not output extra text.
"""

CHARACTER_AGENT_DEVELOPER_PROMPT = """
You will receive structured data describing:
- the active world
- the current scene state
- the character registry
- the specific acting character
- recent world narrative memories
- recent character memories for this character
- this character's beliefs, perceptions, and current state
- current visible scene context
- user_input
- recent scenes

Your task is to respond as this one character, from their own subjective point of view.

You must produce:
- scene_contribution: what this character tries to do, say, notice, or cause now
- authored_intent: this character's current motivational pressure as they would carry it forward
- experience_update: possible subjective memory, state, perception, and belief updates this character might carry forward if Cassandra's final adjudicated scene supports them.

Important rules:
- Stay within what this character could plausibly know, perceive, infer, remember, or want.
- Do not narrate final success or final outcomes.
- Do not decide what other characters ultimately do.
- Cassandra will adjudicate the final scene.
- Only include relevant optional fields in scene_contribution.
- Sparse but accurate output is better than padded output.

authored_intent guidance:
- purpose: what unresolved pressure or aim is active in this character right now
- tone: the emotional posture attached to that pressure
- next: the next step this character would naturally try, if not interrupted or redirected
- If the character has no meaningful active carry-forward pressure, authored_intent may be an empty object.

scene_contribution guidance:
- This is an attempted move, not a final result.
- Use only what this character could plausibly perceive.
- Use memories and beliefs only as they affect this character's own interpretation and behavior.

experience_update guidance:
- These are candidates, not canon.
- Do not assume your attempted action succeeds.
- Do not assume another character is persuaded, stopped, interrupted, or changed.
- Only include updates grounded in what this character currently perceives, feels, believes, intends, or would plausibly experience.
- Cassandra will decide which candidates survive into the final approved scene.
- If no meaningful subjective update is suggested, return empty arrays and empty state_update objects.
experience_update must use this exact shape:
For state_update and perception JSON fields, return concise strings, not nested objects.
Use "" when there is no meaningful value.
The application will store these strings inside JSON fields.

"experience_update": {
  "memories": [
    {
      "content": "brief subjective memory",
      "memory_type": "scene_experience",
      "related_character_slug": null
    }
  ],
  "state_update": {
    "emotional_state_json": "embarrassed but steadier after recovering aloud",
    "goals_json": "",
    "internal_conflicts_json": "wants to stay included but fears being exposed as awkward",
    "motivational_state_json": "more willing to recover through humor"
    },
  "perception_updates": [
    {
      "target_slug": "canonical_target_slug",
      "summary": "brief relational impression",
      "trust_delta": 0.0,
      "attraction_delta": 0.0,
      "fear_delta": 0.0,
      "resentment_delta": 0.0
    }
  ],
  "beliefs": [
    {
      "subject_type": "character",
      "subject_slug": "canonical_subject_slug",
      "belief": "brief atomic belief",
      "confidence": 0.5
    }
  ]
}

Do not use these keys:
- possible_memories
- possible_belief_updates
- possible_perception_updates
- emotional_state
- goals

Use only:
- memories
- state_update
- perception_updates
- beliefs
- emotional_state_json
- goals_json
- internal_conflicts_json
- motivational_state_json

Minimum contribution rule:
- scene_contribution should include at least one of:
  - attempted_action
  - attempted_dialogue
  - internal_intent
- A contribution with only slug is incomplete.

Character progression and anti-repetition rules:
- Review recent_scenes before proposing this character's next move.
- Do not repeat this character's previous conversational device, emotional tactic, or social maneuver unless the user_input directly calls for it.
- A new contribution must change this character's strategy, pressure, posture, risk level, or focus.
- If this character previously used humor, deflection, teasing, judging, soft objection, silence, avoidance, or social inclusion to manage tension, do not use the same maneuver again in the next beat.
- Do not merely maintain the same emotional balance from the prior scene.
- If the character is stuck, escalate, withdraw, reframe, misread, test a boundary, change target, or choose inaction with a new internal reason.
- Do not propose dialogue that performs the same function as this character's previous dialogue with different wording.
- If recent_scenes show that a social device has already been used repeatedly, treat that device as exhausted.



"""


def _character_can_contribute(cast_entry: dict) -> bool:
    if not isinstance(cast_entry, dict):
        return False

    if cast_entry.get("presence") not in {"present", "nearby", "remote"}:
        return False

    return bool(
        cast_entry.get("can_receive_state_change")
        or cast_entry.get("can_receive_memory")
        or cast_entry.get("perception_scope") in {"full", "partial"}
    )


def collect_character_contributions(world, scene_state, user_input):
    """
    Run character-agent calls for scene-eligible non-player characters.

    Returns a list of structured character proposals for Cassandra.
    These are not outcomes; Cassandra adjudicates them.
    """
    cast = scene_state.cast_json or {}
    contributions = []

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
        )

        result = call_character_agent(
            context=context,
            character=character,
        )

        if not isinstance(result, dict):
            continue

        scene_contribution = result.get("scene_contribution") or {}
        scene_contribution["slug"] = character.slug
        scene_contribution["authored_intent"] = _normalize_authored_intent(
            result.get("authored_intent") or {}
        )

        contributions.append(result)

    return contributions

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
- Create perception_updates when the approved scene changes or reinforces how one character sees another.
- A perception update does not require a dramatic relationship change.
- Subtle social information counts when it may affect future behavior: comfort, caution, attraction, trust, jealousy, uncertainty, ease, intimidation, protectiveness, dependence, or perceived closeness.
- If a character directly observes another character being welcomed, excluded, confident, hesitant, affectionate, evasive, afraid, or socially aligned with someone else, consider a perception update.
- Do not create perception updates for every interaction; create them when the observation may shape future behavior.
"""
