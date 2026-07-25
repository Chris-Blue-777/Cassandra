#cast_tracker.py#
import json
from openai import OpenAI
from story.models import CommittedScene
from story.coverage import normalize_narrative_frame
from .characters import build_character_registry

client = OpenAI()

VALID_PERCEPTION_ACCESS = {
    "direct_full",
    "direct_partial",
    "mediated_audio",
    "mediated_text",
    "inferred",
    "none",
}


def _clean_space_id(value):
    if not isinstance(value, str):
        return "unknown_space"

    value = value.strip().lower()
    value = value.replace("'", "")
    value = value.replace('"', "")
    value = "_".join(value.split())

    cleaned = "".join(ch for ch in value if ch.isalnum() or ch == "_")
    return cleaned or "unknown_space"


def _clean_perception_access(value):
    if not isinstance(value, str):
        return "none"

    value = value.strip().lower()
    return value if value in VALID_PERCEPTION_ACCESS else "none"


def _normalize_spaces(raw_spaces):
    spaces = {}

    if not isinstance(raw_spaces, list):
        return spaces

    for item in raw_spaces:
        if not isinstance(item, dict):
            continue

        space_id = _clean_space_id(item.get("space_id"))
        spaces[space_id] = {
            "space_id": space_id,
            "label": str(item.get("label") or "").strip(),
            "kind": str(item.get("kind") or "unknown").strip(),
            "description": str(item.get("description") or "").strip(),
            "occupants": [
                str(slug).strip()
                for slug in item.get("occupants", [])
                if str(slug).strip()
            ],
            "adjacent_space_ids": [
                _clean_space_id(space_id)
                for space_id in item.get("adjacent_space_ids", [])
            ],
            "visible_space_ids": [
                _clean_space_id(space_id)
                for space_id in item.get("visible_space_ids", [])
            ],
            "audible_space_ids": [
                _clean_space_id(space_id)
                for space_id in item.get("audible_space_ids", [])
            ],
        }

    return spaces


def _normalize_perception_edges(raw_edges, valid_slugs):
    edges = {}

    if isinstance(raw_edges, dict):
        iterable = [
            {
                "target_slug": target_slug,
                "access": edge.get("access") if isinstance(edge, dict) else None,
                "reason": edge.get("reason", "") if isinstance(edge, dict) else "",
            }
            for target_slug, edge in raw_edges.items()
        ]
    elif isinstance(raw_edges, list):
        iterable = raw_edges
    else:
        return edges

    for edge in iterable:
        if not isinstance(edge, dict):
            continue

        target_slug = str(edge.get("target_slug") or "").strip()
        if target_slug not in valid_slugs:
            continue

        edges[target_slug] = {
            "access": _clean_perception_access(edge.get("access")),
            "reason": str(edge.get("reason") or "").strip(),
        }

    return edges

SPACE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "space_id": {"type": "string"},
        "label": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": ["room", "hall", "outdoor_area", "vehicle", "threshold", "remote", "unknown"],
        },
        "description": {"type": "string"},
        "occupants": {
            "type": "array",
            "items": {"type": "string"},
        },
        "adjacent_space_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "visible_space_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "audible_space_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "space_id",
        "label",
        "kind",
        "description",
        "occupants",
        "adjacent_space_ids",
        "visible_space_ids",
        "audible_space_ids",
    ],
}

PERCEPTION_EDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target_slug": {"type": "string"},
        "access": {
            "type": "string",
            "enum": [
                "direct_full",
                "direct_partial",
                "mediated_audio",
                "mediated_text",
                "inferred",
                "none",
            ],
        },
        "reason": {"type": "string"},
    },
    "required": ["target_slug", "access", "reason"],
}

CUE_CHANNEL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "from_space_id": {"type": "string"},
        "to_space_id": {"type": "string"},
        "access": {
            "type": "string",
            "enum": [
                "direct_partial",
                "mediated_audio",
                "mediated_text",
                "inferred",
                "none",
            ],
        },
        "description": {"type": "string"},
    },
    "required": ["from_space_id", "to_space_id", "access", "description"],
}

VALID_PRESENCE = {"present", "remote", "mentioned", "nearby", "off-screen"}
VALID_SPATIAL_RELATIONS = {"inside_scene", "adjacent", "distant", "absent"}
VALID_SENSORY_ACCESS = {
    "direct_full",
    "direct_partial",
    "mediated_audio",
    "mediated_text",
    "indirect",
    "none",
}

PRESENCE_DEFAULTS = {
    "present": {"spatial_relation": "inside_scene", "sensory_access": "direct_full"},
    "nearby": {"spatial_relation": "adjacent", "sensory_access": "direct_partial"},
    "remote": {"spatial_relation": "distant", "sensory_access": "mediated_audio"},
    "off-screen": {"spatial_relation": "absent", "sensory_access": "indirect"},
    "mentioned": {"spatial_relation": "absent", "sensory_access": "none"},
}

NON_DURABLE_ALIAS_KEYS = {
    "i", "me", "my", "mine",
    "you", "your", "yours",
    "he", "him", "his", "he's",
    "she", "her", "hers",
    "they", "them", "their", "theirs",
    "we", "us", "our", "ours",
    "someone", "something", "everything",
    "the question",
}

NON_CHARACTER_NOUNS = {
    "doorway", "hall", "the hall", "open doorway",
    "kitchen", "fridge", "living room", "bedroom",
    "front door",
}

BODY_PART_TERMS = {
    "hand", "hands", "my hand", "her hands",
    "fingertips", "my fingertips",
    "cock", "my cock", "his cock",
    "back", "mallory's back",
    "inner thigh", "mallory's inner thigh",
}

def _alias_is_durable(alias_key, slug):
    if not alias_key or not slug:
        return False

    if alias_key in NON_DURABLE_ALIAS_KEYS:
        return False

    if alias_key in NON_CHARACTER_NOUNS:
        return False

    if alias_key in BODY_PART_TERMS:
        return False

    # Do not persist temporary scene-object aliases.
    if str(slug).startswith("tmp_"):
        return False

    # Avoid possessive body/object phrases.
    if "'s " in alias_key:
        return False

    return True

def _clean_spatial_relation(value):
    if not isinstance(value, str):
        return "absent"
    value = value.strip().lower()
    return value if value in VALID_SPATIAL_RELATIONS else "absent"

def _clean_sensory_access(value):
    if not isinstance(value, str):
        return "none"
    value = value.strip().lower()
    return value if value in VALID_SENSORY_ACCESS else "none"

def _resolve_participation_axes(presence, spatial_relation=None, sensory_access=None):
    clean_presence = _clean_presence(presence)
    defaults = PRESENCE_DEFAULTS.get(clean_presence, PRESENCE_DEFAULTS["mentioned"])

    return {
        "presence": clean_presence,
        "spatial_relation": _clean_spatial_relation(
            spatial_relation or defaults["spatial_relation"]
        ),
        "sensory_access": _clean_sensory_access(
            sensory_access or defaults["sensory_access"]
        ),
    }


def _derive_participation_flags(spatial_relation: str, sensory_access: str) -> dict:
    if sensory_access == "direct_full":
        scope = "full"
        enabled = True

    elif sensory_access in {"direct_partial", "mediated_audio"}:
        scope = "partial"
        enabled = True

    elif sensory_access in {"mediated_text", "indirect"}:
        scope = "indirect"
        enabled = False

    else:
        scope = "none"
        enabled = False

    return {
        "perception_scope": scope,
        "can_receive_memory": enabled,
        "can_receive_state_change": enabled,
        "can_receive_perception_change": enabled,
    }


def _build_cast_entry(
    presence: str,
    position: str = "",
    spatial_relation: str | None = None,
    sensory_access: str | None = None,
    space_id=None,
    local_space_label="",
    perceives=None,
) -> dict:
    axes = _resolve_participation_axes(
        presence=presence,
        spatial_relation=spatial_relation,
        sensory_access=sensory_access,
    )
    flags = _derive_participation_flags(
        spatial_relation=axes["spatial_relation"],
        sensory_access=axes["sensory_access"],
    )
    return {
        "presence": axes["presence"],
        "spatial_relation": axes["spatial_relation"],
        "sensory_access": axes["sensory_access"],
        "position": (position or "").strip(),
        "space_id": _clean_space_id(space_id),
        "local_space_label": local_space_label or "",
        "perceives": perceives or {},
        **flags,
    }


def _serialize_scene_state(scene_state):
    topology = getattr(scene_state, "topology_json", {}) or {}
    if not isinstance(topology, dict):
        topology = {}

    return {
        "location": scene_state.location or "opening scene",
        "narrative_frame": topology.get("narrative_frame", {}),
        "spaces": topology.get("spaces", {}),
        "cast": scene_state.cast_json or {},
        "pending_intents": scene_state.pending_intents_json or {},
        "alias_cache": scene_state.alias_cache_json or {},
    }


def _clean_presence(value):
    if not isinstance(value, str):
        return "mentioned"
    value = value.strip().lower()
    if value in VALID_PRESENCE:
        return value
    return "mentioned"


def build_scene_participant_context(
        world,
        scene_state,
        scene_text,
        pov_slug=None):
    # pylint: disable=missing-function-docstring
    recent_scenes = list(
        CommittedScene.objects.filter(world=world)
        .order_by("-created_at")[:3]
    )[::-1]

    return {
        "scene_text": scene_text,
        "pov_slug": pov_slug,
        "current_scene_state": _serialize_scene_state(scene_state),
        "character_registry": build_character_registry(world),
        "recent_scenes": [
            {
                "user_text": s.user_text or "",
                "assistant_text": s.cassandra_text or "",
            }
            for s in recent_scenes
        ],
    }

def _normalize_scene_participant_output(data, registry=None):
    registry_slugs = set(_valid_character_slugs(registry or []))

    if not isinstance(data, dict):
        return {
            "scene_state_update": {
                "location": None,
                "narrative_frame": {
                    "summary_location": None,
                    "camera_scope": "single_space",
                    "active_space_ids": [],
                    "coverage_mode": "split_screen",
                    "resolved_space_ids": [],
                    "reader_visible_space_ids": [],
                    "cue_channels": [],
                    "reveal_policy": "show_resolved_spaces_now",
                },
                "spaces": {},
                "cast": {},
            },
            "resolution_notes": [],
            "alias_cache_update": {},
        }

    scene_state_update = data.get("scene_state_update") or {}
    if not isinstance(scene_state_update, dict):
        scene_state_update = {}

    cast_data = scene_state_update.get("cast") or []
    notes = data.get("resolution_notes") or []
    alias_cache_update = data.get("alias_cache_update") or {}

    raw_spaces = scene_state_update.get("spaces") or []
    spaces = _normalize_spaces(raw_spaces)

    raw_narrative_frame = scene_state_update.get("narrative_frame") or {}
    if not isinstance(raw_narrative_frame, dict):
        raw_narrative_frame = {}

    narrative_frame = {
        **normalize_narrative_frame(raw_narrative_frame, spaces=spaces),
    }

    # Build the set of allowed perception targets.
    # This should include known registry characters AND any temporary slugs
    # that MissPots actually included in this turn's cast.
    cast_slugs = set()

    if isinstance(cast_data, dict):
        cast_slugs.update(
            str(slug).strip()
            for slug in cast_data.keys()
            if str(slug).strip()
        )

    elif isinstance(cast_data, list):
        for entry in cast_data:
            if not isinstance(entry, dict):
                continue
            slug = str(entry.get("slug") or "").strip()
            if slug:
                cast_slugs.add(slug)

    valid_perception_targets = registry_slugs | cast_slugs

    normalized_cast = {}

    if isinstance(cast_data, dict):
        # Fallback support if cast is already dict-shaped.
        for slug, payload in cast_data.items():
            slug = str(slug or "").strip()
            if not slug or not isinstance(payload, dict):
                continue

            normalized_cast[slug] = _build_cast_entry(
                presence=payload.get("presence"),
                position=payload.get("position", ""),
                spatial_relation=payload.get("spatial_relation"),
                sensory_access=payload.get("sensory_access"),
                space_id=payload.get("space_id"),
                local_space_label=payload.get("local_space_label", ""),
                perceives=_normalize_perception_edges(
                    payload.get("perceives") or [],
                    valid_slugs=valid_perception_targets,
                ),
            )

    elif isinstance(cast_data, list):
        # Normal path for schema-backed LLM output.
        for entry in cast_data:
            if not isinstance(entry, dict):
                continue

            slug = str(entry.get("slug") or "").strip()
            if not slug:
                continue

            normalized_cast[slug] = _build_cast_entry(
                presence=entry.get("presence"),
                position=entry.get("position", ""),
                spatial_relation=entry.get("spatial_relation"),
                sensory_access=entry.get("sensory_access"),
                space_id=entry.get("space_id"),
                local_space_label=entry.get("local_space_label", ""),
                perceives=_normalize_perception_edges(
                    entry.get("perceives") or [],
                    valid_slugs=valid_perception_targets,
                ),
            )

    normalized_notes = []
    if isinstance(notes, list):
        for note in notes:
            if not isinstance(note, dict):
                continue

            normalized_notes.append({
                "text": str(note.get("text") or "").strip(),
                "resolved_slug": note.get("resolved_slug"),
                "reason": str(note.get("reason") or "").strip(),
            })

    alias_cache_update = _normalize_alias_cache_update(alias_cache_update)

    return {
        "scene_state_update": {
            "location": scene_state_update.get("location"),
            "narrative_frame": narrative_frame,
            "spaces": spaces,
            "cast": normalized_cast,
        },
        "resolution_notes": normalized_notes,
        "alias_cache_update": alias_cache_update,
    }

def _normalize_alias_key(value):
    if not isinstance(value, str):
        return ""

    value = value.strip().lower()

    # strip simple punctuation edges
    value = value.strip(",.?!:;\"'")

    return " ".join(value.split())

def _merge_alias_cache(existing, update, valid_slugs=None, allow_tmp=True):
    existing = existing or {}
    update = update or {}
    valid_slugs = set(valid_slugs or [])

    def slug_allowed(slug):
        if not slug:
            return False
        if slug in valid_slugs:
            return True
        return allow_tmp and str(slug).startswith("tmp_")

    merged = {
        _normalize_alias_key(alias): slug
        for alias, slug in existing.items()
        if (
            _normalize_alias_key(alias)
            and slug_allowed(slug)
            and _alias_is_durable(_normalize_alias_key(alias), slug)
        )
    }

    for alias, slug in update.items():
        alias_key = _normalize_alias_key(alias)

        if not _alias_is_durable(alias_key, slug):
            continue

        if not alias_key or not slug_allowed(slug):
            continue

        existing_slug = merged.get(alias_key)
        if existing_slug and existing_slug != slug:
            if str(existing_slug).startswith("tmp_") and slug in valid_slugs:
                merged[alias_key] = slug
            else:
                print(f"Alias conflict for {alias_key}: keeping {existing_slug}, ignoring {slug}")
                continue
        else:
            merged[alias_key] = slug

    return merged

def _normalize_alias_cache_update(raw):
    normalized = {}

    if isinstance(raw, dict):
        iterable = [
            {"alias": alias, "slug": slug}
            for alias, slug in raw.items()
        ]
    elif isinstance(raw, list):
        iterable = raw
    else:
        return normalized

    for item in iterable:
        if not isinstance(item, dict):
            continue

        alias_key = _normalize_alias_key(item.get("alias"))
        slug = str(item.get("slug") or "").strip()

        if not alias_key or not slug:
            continue

        normalized[alias_key] = slug

    return normalized

def _valid_character_slugs(registry):
    return {c["slug"] for c in registry if c.get("slug")}

def _filter_scene_participant_output(data, registry):
    valid_slugs = _valid_character_slugs(registry)
    scene_state_update = data.get("scene_state_update") or {}
    raw_spaces = scene_state_update.get("spaces") or []
    spaces = _normalize_spaces(raw_spaces)
    cast = data["scene_state_update"]["cast"]

    filtered_cast = {}
    dropped_slugs = []
    filtered_alias = {}
    for alias, slug in data.get("alias_cache_update", {}).items():
        if slug in valid_slugs or str(slug).startswith("tmp_"):
            filtered_alias[alias] = slug
        else:
            print(f"Dropping invalid alias mapping: {alias} -> {slug}")

    data["alias_cache_update"] = filtered_alias

    for slug, payload in cast.items():
        if slug in valid_slugs or slug.startswith("tmp_"):
            filtered_cast[slug] = payload
        else:
            print(f"Dropping invalid inferred slug: {slug}")
            dropped_slugs.append(slug)

    if dropped_slugs:
        print(f"Dropped invalid slugs: {dropped_slugs}")

    data["scene_state_update"]["cast"] = filtered_cast

    filtered_notes = []
    dropped_note_slugs = []
    for note in data.get("resolution_notes", []):
        resolved_slug = note.get("resolved_slug")
        if resolved_slug is None or resolved_slug in valid_slugs or str(resolved_slug).startswith("tmp_"):
            filtered_notes.append(note)
        else:
            print(f"Dropping note with invalid resolved slug: {resolved_slug}")
            dropped_note_slugs.append(resolved_slug)
    if dropped_note_slugs:
        print(f"Dropped invalid note slugs: {dropped_note_slugs}")

    data["resolution_notes"] = filtered_notes
    print(f"resolution notes: {data['resolution_notes']}")
    return data

def infer_scene_participants_and_positions(world, scene_state, scene_text, pov_slug=None):
    context = build_scene_participant_context(
        world=world,
        scene_state=scene_state,
        scene_text=scene_text,
        pov_slug=pov_slug,
    )

    raw = call_scene_participant_inference(context)
    normalized = _normalize_scene_participant_output(raw, registry=context["character_registry"])
    filtered = _filter_scene_participant_output(
        normalized,
        registry=context["character_registry"],
    )

    return filtered

def _merge_scene_state_updates(primary, secondary, valid_slugs=None, allow_tmp=True):
    primary = primary or {}
    secondary = secondary or {}
    valid_slugs = set(valid_slugs or [])

    def slug_allowed(slug):
        if not slug:
            return False
        if slug in valid_slugs:
            return True
        return allow_tmp and str(slug).startswith("tmp_")

    merged_cast = {}

    for slug, payload in (primary.get("cast") or {}).items():
        if not slug_allowed(slug) or not isinstance(payload, dict):
            continue

        existing = merged_cast.get(slug, {})
        presence = payload.get("presence", existing.get("presence", "mentioned"))
        position = payload.get("position", existing.get("position", ""))
        spatial_relation = payload.get("spatial_relation", existing.get("spatial_relation"))
        sensory_access = payload.get("sensory_access", existing.get("sensory_access"))
        space_id = payload.get("space_id", existing.get("space_id", ""))
        local_space_label = payload.get(
            "local_space_label",
            existing.get("local_space_label", ""),
        )
        perceives = payload.get("perceives", existing.get("perceives", {}))

        merged_cast[slug] = _build_cast_entry(
            presence=presence,
            position=position,
            spatial_relation=spatial_relation,
            sensory_access=sensory_access,
            space_id=space_id,
            local_space_label=local_space_label,
            perceives=perceives,
        )

    for slug, payload in (secondary.get("cast") or {}).items():
        if not slug_allowed(slug) or not isinstance(payload, dict):
            continue

        existing = merged_cast.get(slug, {})
        presence = payload.get("presence", existing.get("presence", "mentioned"))
        position = payload.get("position", existing.get("position", ""))
        spatial_relation = payload.get("spatial_relation", existing.get("spatial_relation"))
        sensory_access = payload.get("sensory_access", existing.get("sensory_access"))
        space_id = payload.get("space_id", existing.get("space_id", ""))
        local_space_label = payload.get(
            "local_space_label",
            existing.get("local_space_label", ""),
        )
        perceives = payload.get("perceives", existing.get("perceives", {}))

        merged_cast[slug] = _build_cast_entry(
            presence=presence,
            position=position,
            spatial_relation=spatial_relation,
            sensory_access=sensory_access,
            space_id=space_id,
            local_space_label=local_space_label,
            perceives=perceives,
        )

    location = secondary.get("location") or primary.get("location")

    return {
        "location": location,
        "cast": merged_cast,
    }

def call_scene_participant_inference(context):

    # turn user input + previous scene state into draft-time scene topology/cast/perception state.

    # Cassandra and character-agents depend on this because they need to know:

    # who is present
    # who is nearby
    # who can perceive what
    # where people are
    # what space/location is active

    system_prompt = """You are MissPots, a scene-state inference engine for a narrative system.

Your job is to infer structured scene-state updates from the provided scene text.

You are given:
- scene_text
- current_scene_state
- character_registry (the authoritative list of valid characters)
- recent_scenes
- an optional POV character slug

Return valid JSON matching the schema exactly. Do not include any extra text.

---

CORE TASK

Interpret scene_text and map all relevant character references onto canonical identities.

OOC / EDITORIAL DIRECTIVES

scene_text may contain bracketed out-of-character instructions such as:
- [OOC: surprise me]
- [OOC: let Cassandra choose]

These are reader/editorial instructions, not in-world speech, narration, action, perception, or character thought.
Do not treat OOC directives as something any character said, heard, saw, remembered, or did.
Use them only as a hint that the next scene-state update may need to allow a plausible new arrival, interruption, message, reveal, or event.

You are NOT generating identities.
You are SELECTING identities from a CLOSED SET defined in character_registry.

---

CHARACTER RESOLUTION (CRITICAL)

character_registry is the source of truth for all valid characters.

Each entry contains a "slug".
You MUST use these slugs EXACTLY as written.

1. Only use slugs from character_registry for known characters.
   - Do NOT modify slugs
   - Do NOT recreate slugs from names
   - Do NOT invent new canonical slugs

2. Never output names where slugs are required.
   - ❌ "Kara"
   - ❌ "Dr. Kara Voss"
   - ❌ "kara_voss"
   - ✅ "kara"

3. Treat character_registry as a selection list.
   - Your task is to MATCH references in scene_text to these entries

4. Prefer known characters whenever plausible.
   - If a reference could reasonably match a known character, you MUST use that slug
   - Do NOT create a new character if an existing one is a reasonable match
   - If a reference matches a known character, you MUST use that character's slug even if the reference uses a different name, title, or phrasing.

5. Resolve indirect references using context:
   - pronouns ("he", "she", "her")
   - relational phrases ("my girl", "my ex-husband")
   - titles ("Dr. Voss", "the bartender")
   - dialogue context
   - POV perspective
   - recent scenes

6. If uncertain between multiple known characters:
   - choose the MOST likely character
   - do NOT create a new slug
   - include a resolution_note explaining the ambiguity

7. Only create a temporary character if NO known character plausibly fits.
   - Temporary slugs MUST begin with "tmp_"
   - Examples: "tmp_bartender", "tmp_waitress"

8. Temporary character rules:
   - keep slugs simple and consistent
   - reuse the same slug if referring to the same entity
   - do NOT create multiple temp slugs for the same character in one scene

9. If a reference is too ambiguous to safely resolve:
   - omit it rather than guessing incorrectly

---

SCENE PARTICIPATION

For each relevant character, assign:

- slug (REQUIRED)
- presence (REQUIRED)
- spatial_relation (REQUIRED)
- sensory_access (REQUIRED)
- position (REQUIRED)

SPATIAL RELATION:
- inside_scene → physically in the active scene
- adjacent     → nearby (doorway, next room, outside)
- distant      → elsewhere
- absent       → not physically present

SENSORY ACCESS:
- direct_full     → fully sees/hears the scene
- direct_partial  → partially perceives (muffled, obstructed, edge of scene)
- mediated_audio  → phone, radio, voice transmission
- mediated_text   → text messages or delayed written communication
- indirect        → learns about events secondhand
- none            → no meaningful perception

Examples:
- doorway observer → adjacent + direct_partial
- phone call       → distant + mediated_audio
- texting          → distant + mediated_text
- off-screen aware → absent + indirect
- mentioned only   → absent + none

Allowed presence values:
- present      → physically in the active scene
- nearby       → physically close but not fully inside the scene
- remote       → participating via phone/text/etc
- mentioned    → referenced but not actively participating
- off-screen   → relevant but not part of immediate action

SCENE PARTICIPATION (CRITICAL)

Include any character who is explicitly present in the scene, even if the action is subtle, quiet, or minimal.

A character MUST be included in cast if they:
- are physically present in the scene
- perform any action (even small or slow actions)
- are the subject of narration
- are being approached, observed, or interacted with
- are resolved from a pronoun that participates in the scene

Do NOT exclude characters simply because:
- the scene is quiet or low-action
- the character is passive or still
- the action is internal, emotional, or minimal

Only exclude characters who are:
- purely mentioned without participating
- not part of the immediate scene space

Resolving a character (via name, alias, or pronoun) AND identifying them as part of the scene implies they belong in cast.

If a resolution_note identifies a character who is acting or present in the scene, that character must also appear in cast.

---

SPATIAL TOPOLOGY INFERENCE

You are not limited to one physical location.

The story may use an omniscient or multi-space narrative camera. A scene can include:
- one character in one room
- other characters in another room
- characters hearing each other through a wall, door, hall, phone, text, or other mediated channel
- characters who are narratively visible to Cassandra but not perceptible to each other

Return:
1. narrative_frame
2. spaces
3. cast entries with space_id and perception edges

Definitions:

narrative_frame:
- summary_location: a human-readable summary of the whole narrative frame
- camera_scope:
  - single_space: all active characters are in the same immediate space
  - multi_space: more than one space matters
  - omniscient_multi_space: the narration can show multiple spaces even when characters cannot perceive each other
  - remote_or_unclear: spatial relation is unclear or mediated
- active_space_ids: every space currently relevant to the scene
- coverage_mode:
  - hidden_objective: Cassandra should resolve objective events in multiple spaces, but the user-facing draft should withhold some resolved spaces and surface only allowed cues from them.
  - split_screen: Cassandra should resolve objective events in multiple spaces and may narrate those spaces directly to the reader.
- resolved_space_ids: every space Cassandra should objectively resolve this turn, including hidden/off-screen spaces whose events matter.
- reader_visible_space_ids: the spaces Cassandra is allowed to directly narrate to the reader in the draft.
- cue_channels: ways information can travel from a hidden or separate space into a reader-visible space, such as muffled audio through a wall, a text, a phone call, or inference from timing.
- reveal_policy:
  - show_resolved_spaces_now: use with split_screen.
  - withhold_until_user_or_arc_changes: use when hidden objective events should remain hidden unless later story direction changes.
  - withhold_until_explicit_reveal: use when the user clearly wants delayed revelation.

Coverage inference:
- If the user asks to cut to, show, meanwhile, follow, or directly narrate another room/space, prefer split_screen.
- If the user stays with one character waiting/listening outside, says not to show what happens yet, or frames the action as sounds/cues through a wall/door/hall, prefer hidden_objective.
- If existing current_scene_state.narrative_frame.coverage_mode is hidden_objective, preserve it unless scene_text clearly opens the camera or requests a reveal.
- In hidden_objective, resolved_space_ids may include spaces not listed in reader_visible_space_ids.
- In split_screen, reader_visible_space_ids normally equals resolved_space_ids.

spaces:
- Each physical or communicative area relevant to the scene.
- Examples:
  - byrne_bedroom
  - donnie_room
  - hall
  - phone_call
  - outside_car
- Use stable snake_case ids.
- occupants should include characters physically in that space.
- audible_space_ids should include spaces that can be heard from this space.
- visible_space_ids should include spaces that can be seen from this space.
- adjacent_space_ids should include nearby spaces that are physically connected.

cast:
- Each cast entry must include the character's own space_id.
- A character is present in their own space even if the narrative camera is centered elsewhere.
- Do not mark a character as present/full in another character's room unless they are physically there.
- Use perceives to describe what this character can perceive of other characters.

Perception edge rules:
- direct_full: same room/space, directly visible/audible
- direct_partial: same threshold or partially obstructed
- mediated_audio: heard through door, hall, wall, phone, recording, etc.
- mediated_text: text/chat/written communication
- inferred: not directly perceived, but reasonably inferred
- none: not perceived

Closed doors, walls, separate rooms, and opaque barriers block direct visual access.

If two characters are in different spaces and the connection is audible but not visible, use:
access = "mediated_audio"

Do not use "direct_partial" for a character on the other side of a closed door unless the scene establishes a visual opening, line of sight, crack in the door, window, mirror, camera, or other direct visual channel.

"direct_partial" means the observer can directly see some part of the target, the target's body language, movement, facial expression, or physical position.

"mediated_audio" means the observer may hear voice, tone, volume, impact sounds, movement sounds, breathing, or other audible cues, but cannot see silent body language, exact touch, facial expression, or precise physical positioning.

Important:
- Cassandra may be omniscient.
- Character-agents are not.
- The topology must distinguish what the narrator can show from what each character can perceive.

---

SCENE CONSISTENCY

- Use recent_scenes for continuity when needed

---

RESOLUTION NOTES

Include resolution_notes ONLY when useful.

Use them when:
- resolving indirect references ("my girl" → kara)
- resolving titles ("Dr. Voss" → kara)
- resolving pronouns
- choosing between multiple plausible characters

Each note must include:
- text
- resolved_slug
- reason

Keep notes concise and focused.

---

Also return alias_cache_update when you resolve reusable references like titles or descriptors (e.g. "Dr. Voss", "the bartender") to specific characters.

alias_cache_update should map those references to slugs.

---

FINAL RULES

- Only output valid JSON matching the schema
- Do not include commentary or explanation outside the JSON
- Do not output invalid slugs
- Do not invent canonical identities
- Always map references onto character_registry whenever possible"""

    response = client.responses.create(
        model="gpt-5.4",
        instructions=system_prompt,
        input=[
            {
                "role": "user",
                "content": json.dumps(context, ensure_ascii=False, indent=2),
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "scene_participant_output",
                "strict": True,
                "schema": SCENE_PARTICIPANT_SCHEMA,
            }
        },
    )

    if not response.output_text:
        raise ValueError("Scene participant inference returned no output")

    try:
        data = json.loads(response.output_text)
    except json.JSONDecodeError as e:
        print("MISSPOTS RAW OUTPUT:")
        print(response.output_text)
        raise ValueError(f"MissPots returned malformed JSON: {e}") from e
    return data


SCENE_PARTICIPANT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scene_state_update": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "location": {
                    "type": ["string", "null"]
                },
                "cast": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "slug": {
                                "type": "string"
                            },
                            "presence": {
                                "type": "string",
                                "enum": ["present", "remote", "mentioned", "nearby", "off-screen"]
                            },
                            "space_id": {"type": "string"},
                            "local_space_label": {"type": "string"},
                            "position": {
                                "type": "string"
                            },
                            "spatial_relation": {
                                "type": "string",
                                "enum": ["inside_scene", "adjacent", "distant", "absent"]
                            },
                            "sensory_access": {
                                "type": "string",
                                "enum": [
                                    "direct_full",
                                    "direct_partial",
                                    "mediated_audio",
                                    "mediated_text",
                                    "indirect",
                                    "none"
                                ]
                            },
                            "perceives": {
                                "type": "array",
                                "items": PERCEPTION_EDGE_SCHEMA,
                            },
                        },
                        "required": [
                            "slug",
                            "presence",
                            "space_id",
                            "local_space_label",
                            "position",
                            "spatial_relation",
                            "sensory_access",
                            "perceives",
                        ]
                    }
                },
                "narrative_frame": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "summary_location": {"type": ["string", "null"]},
                        "camera_scope": {
                            "type": "string",
                            "enum": [
                                "single_space",
                                "multi_space",
                                "omniscient_multi_space",
                                "remote_or_unclear",
                            ],
                        },
                        "active_space_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "coverage_mode": {
                            "type": "string",
                            "enum": ["hidden_objective", "split_screen"],
                        },
                        "resolved_space_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "reader_visible_space_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "cue_channels": {
                            "type": "array",
                            "items": CUE_CHANNEL_SCHEMA,
                        },
                        "reveal_policy": {
                            "type": "string",
                            "enum": [
                                "show_resolved_spaces_now",
                                "withhold_until_user_or_arc_changes",
                                "withhold_until_explicit_reveal",
                            ],
                        },
                    },
                    "required": [
                        "summary_location",
                        "camera_scope",
                        "active_space_ids",
                        "coverage_mode",
                        "resolved_space_ids",
                        "reader_visible_space_ids",
                        "cue_channels",
                        "reveal_policy",
                    ],
                },
                "spaces": {
                    "type": "array",
                    "items": SPACE_SCHEMA,
                },
            },
            "required": [
                "location",
                "narrative_frame",
                "spaces",
                "cast",
            ],
        },
        "resolution_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {
                        "type": "string"
                    },
                    "resolved_slug": {
                        "type": ["string", "null"]
                    },
                    "reason": {
                        "type": "string"
                    }
                },
                "required": ["text", "resolved_slug", "reason"]
            }
        },
        "alias_cache_update": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "alias": {"type": "string"},
                    "slug": {"type": "string"},
                },
                "required": ["alias", "slug"],
            },
        }
    },
    "required": ["scene_state_update", "resolution_notes", "alias_cache_update"],
}
