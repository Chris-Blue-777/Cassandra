import json
from openai import OpenAI
from story.models import CommittedScene
from .characters import build_character_registry

client = OpenAI()

VALID_PRESENCE = {"present", "remote", "mentioned", "nearby", "off-screen"}

def _serialize_scene_state(scene_state):
    return {
        "location": scene_state.location or "opening scene",
        "cast": scene_state.cast_json or {},
        "pending_intents": scene_state.pending_intents_json or {},
    }

def _clean_presence(value):
    if not isinstance(value, str):
        return "mentioned"

    value = value.strip().lower()

    if value in VALID_PRESENCE:
        return value

    return "mentioned"

def build_scene_participant_context(world, scene_state, scene_text, pov_slug=None):
    recent_scenes = list(
        CommittedScene.objects.filter(world=world)
        .order_by("-created_at")[:3]
    )[::-1]

    return {
        "scene_text": scene_text,
        "pov_slug": pov_slug,
        "current_scene_state": _serialize_scene_state(scene_state),
        "character_registry": build_character_registry(world),
        "recent_scenes": [s.combined_text for s in recent_scenes],
    }

def _normalize_scene_participant_output(data):
    if not isinstance(data, dict):
        return {
            "scene_state_update": {
                "location": None,
                "cast": {},
            },
            "resolution_notes": [],
        }

    scene_state_update = data.get("scene_state_update") or {}
    cast_data = scene_state_update.get("cast") or []
    notes = data.get("resolution_notes") or []

    normalized_cast = {}

    if isinstance(cast_data, dict):
        # Fallback support if cast is already dict-shaped
        for slug, payload in cast_data.items():
            if not slug or not isinstance(payload, dict):
                continue

            normalized_cast[slug] = {
                "presence": _clean_presence(payload.get("presence")),
                "position": (payload.get("position") or "").strip(),
            }

    elif isinstance(cast_data, list):
        # Normal path for schema-backed LLM output
        for entry in cast_data:
            if not isinstance(entry, dict):
                continue

            slug = entry.get("slug")
            if not slug:
                continue

            normalized_cast[slug] = {
                "presence": _clean_presence(entry.get("presence")),
                "position": (entry.get("position") or "").strip(),
            }

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

    return {
        "scene_state_update": {
            "location": scene_state_update.get("location"),
            "cast": normalized_cast,
        },
        "resolution_notes": normalized_notes,
    }

def _valid_character_slugs(registry):
    return {c["slug"] for c in registry if c.get("slug")}

def _filter_scene_participant_output(data, registry):
    valid_slugs = _valid_character_slugs(registry)
    cast = data["scene_state_update"]["cast"]

    filtered_cast = {}
    dropped_slugs = []

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

        merged_cast[slug] = {
            "presence": _clean_presence(payload.get("presence")),
            "position": (payload.get("position") or "").strip(),
        }

    for slug, payload in (secondary.get("cast") or {}).items():
        if not slug_allowed(slug) or not isinstance(payload, dict):
            continue

        existing = merged_cast.get(slug, {})
        merged_cast[slug] = {
            "presence": _clean_presence(payload.get("presence", existing.get("presence", "mentioned"))),
            "position": (payload.get("position") or existing.get("position", "")).strip(),
        }

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
- position (REQUIRED)

Allowed presence values:
- present      → physically in the active scene
- nearby       → physically close but not fully inside the scene
- remote       → participating via phone/text/etc
- mentioned    → referenced but not actively participating
- off-screen   → relevant but not part of immediate action

Guidance:
- Only include characters who are meaningfully involved
- Not all mentioned characters belong in the active cast

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

FINAL RULES

- Only output valid JSON matching the schema
- Do not include commentary or explanation outside the JSON
- Do not output invalid slugs
- Do not invent canonical identities
- Always map references onto character_registry whenever possible"""

    response = client.responses.create(
        model="gpt-5.4",
        instructions=system_prompt,
        input=json.dumps(context, ensure_ascii=False, indent=2),
        text={
            "format": {
                "type": "json_schema",
                "name": "scene_participant_response",
                "strict": True,
                "schema": SCENE_PARTICIPANT_SCHEMA,
            }
        },
    )

    if not response.output_text:
        raise ValueError("Scene participant inference returned no output")

    data = json.loads(response.output_text)
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
                            }
                        },
                        "required": ["slug", "presence", "position"]
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
        }
    },
    "required": ["scene_state_update", "resolution_notes"]
}
