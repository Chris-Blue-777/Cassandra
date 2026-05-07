#cast_tracker.py#
import json
from openai import OpenAI
from story.models import CommittedScene
from .characters import build_character_registry

client = OpenAI()

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
        **flags,
    }


def _serialize_scene_state(scene_state):
    return {
        "location": scene_state.location or "opening scene",
        "cast": scene_state.cast_json or {},
        "pending_intents": scene_state.pending_intents_json or {},
        "alias_cache": scene_state.alias_cache_json or {}
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

def _normalize_scene_participant_output(data):
    if not isinstance(data, dict):
        return {
            "scene_state_update": {
                "location": None,
                "cast": {},
            },
            "resolution_notes": [],
            "alias_cache_update": {},
        }

    scene_state_update = data.get("scene_state_update") or {}
    cast_data = scene_state_update.get("cast") or []
    notes = data.get("resolution_notes") or []
    alias_cache_update = data.get("alias_cache_update") or {}

    normalized_cast = {}

    if isinstance(cast_data, dict):
        # Fallback support if cast is already dict-shaped
        for slug, payload in cast_data.items():
            if not slug or not isinstance(payload, dict):
                continue

            normalized_cast[slug] = _build_cast_entry(
                presence=payload.get("presence"),
                position=payload.get("position", ""),
                spatial_relation=payload.get("spatial_relation"),
                sensory_access=payload.get("sensory_access"),
            )

    elif isinstance(cast_data, list):
        # Normal path for schema-backed LLM output
        for entry in cast_data:
            if not isinstance(entry, dict):
                continue

            slug = entry.get("slug")
            if not slug:
                continue

            normalized_cast[slug] = _build_cast_entry(
                presence=entry.get("presence"),
                position=entry.get("position", ""),
                spatial_relation=entry.get("spatial_relation"),
                sensory_access=entry.get("sensory_access"),
            )

    normalized_notes = []
    if isinstance(notes, list):
        for note in notes:
            if not isinstance(note, dict):
                continue

            normalized_notes.append({
                "text": note.get("text", ""),
                "resolved_slug": note.get("resolved_slug"),
                "reason": note.get("reason", ""),
            })

    alias_cache_update = _normalize_alias_cache_update(alias_cache_update)

    return {
        "scene_state_update": {
            "location": scene_state_update.get("location"),
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
        if _normalize_alias_key(alias) and slug_allowed(slug)
    }

    for alias, slug in update.items():
        alias_key = _normalize_alias_key(alias)

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

    if not isinstance(raw, dict):
        return normalized

    for alias, slug in raw.items():
        alias_key = _normalize_alias_key(alias)
        slug = str(slug or "").strip()

        if not alias_key or not slug:
            continue

        normalized[alias_key] = slug

    return normalized

def _valid_character_slugs(registry):
    return {c["slug"] for c in registry if c.get("slug")}

def _filter_scene_participant_output(data, registry):
    valid_slugs = _valid_character_slugs(registry)
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
    normalized = _normalize_scene_participant_output(raw)
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

        merged_cast[slug] = _build_cast_entry(
            presence=presence,
            position=position,
            spatial_relation=spatial_relation,
            sensory_access=sensory_access,
        )

    for slug, payload in (secondary.get("cast") or {}).items():
        if not slug_allowed(slug) or not isinstance(payload, dict):
            continue

        existing = merged_cast.get(slug, {})
        presence = payload.get("presence", existing.get("presence", "mentioned"))
        position = payload.get("position", existing.get("position", ""))
        spatial_relation = payload.get("spatial_relation", existing.get("spatial_relation"))
        sensory_access = payload.get("sensory_access", existing.get("sensory_access"))

        merged_cast[slug] = _build_cast_entry(
            presence=presence,
            position=position,
            spatial_relation=spatial_relation,
            sensory_access=sensory_access,
        )

    location = secondary.get("location") or primary.get("location")

    return {
        "location": location,
        "cast": merged_cast,
    }

def call_scene_participant_inference(context):
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

LOCATION INFERENCE

- Return a location ONLY if the scene clearly establishes or changes it
- Otherwise return null
- If a new location is established, do not preserve incompatible prior positions

---

POSITION RULES

- Keep positions short, concrete, and relative to the scene
- Examples:
  - "inside the bar"
  - "car outside"
  - "near the doorway"
  - "on the phone"

---

SCENE CONSISTENCY

- Respect current_scene_state unless the new scene clearly overrides it
- Use recent_scenes for continuity when needed
- Do not hallucinate large changes to cast or location

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
                        },
                        "required": ["slug", "presence", "position", "spatial_relation", "sensory_access"]
                    }
                }
            },
            "required": ["location", "cast"]
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
            "type": "object",
            "additionalProperties": {
                "type": "string"
            }
        }
    },
    "required": ["scene_state_update", "resolution_notes"]
}
