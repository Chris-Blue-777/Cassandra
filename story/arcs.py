import re

from .models import StoryArc


ACTIVE_ARC_CONTEXT_LIMIT = 8
CHARACTER_ARC_LENS_CONTEXT_LIMIT = 5
OOC_TAG_PATTERN = re.compile(r"^\[OOC:\s*.+\]$", re.IGNORECASE | re.DOTALL)


def normalize_story_arc_ooc_tag(value):
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""

    if OOC_TAG_PATTERN.match(text):
        return text

    if not text.endswith((".", "!", "?")):
        text = f"{text}."

    return f"[OOC: {text}]"


def _clean_slug_list(value):
    if value in ("", None):
        return []

    if isinstance(value, str):
        value = [
            item.strip()
            for item in value.replace("\n", ",").split(",")
            if item.strip()
        ]

    if not isinstance(value, list):
        return []

    slugs = []
    for item in value:
        slug = str(item or "").strip()
        if slug and slug not in slugs:
            slugs.append(slug)

    return slugs


def _clean_lens_payload(value):
    if isinstance(value, str):
        value = {"presentation_bias": value}

    if not isinstance(value, dict):
        return None

    presentation_bias = str(
        value.get("presentation_bias")
        or value.get("lens")
        or value.get("guidance")
        or value.get("summary")
        or ""
    ).strip()
    limits = str(value.get("limits") or value.get("constraints") or "").strip()
    emotional_pressure = str(value.get("emotional_pressure") or "").strip()
    perceptual_bias = str(value.get("perceptual_bias") or "").strip()

    if not any([presentation_bias, limits, emotional_pressure, perceptual_bias]):
        return None

    return {
        "presentation_bias": presentation_bias,
        "emotional_pressure": emotional_pressure,
        "perceptual_bias": perceptual_bias,
        "limits": limits,
    }


def _lens_for_character(arc, character_slug):
    lenses = arc.character_lenses_json or {}
    if not isinstance(lenses, dict):
        return None

    for key in (character_slug, "_default", "default", "*"):
        if key in lenses:
            cleaned = _clean_lens_payload(lenses.get(key))
            if cleaned:
                return cleaned

    return None


def _story_arc_queryset(world):
    return (
        StoryArc.objects
        .filter(world=world, status=StoryArc.STATUS_ACTIVE)
        .order_by("-priority", "-updated_at", "title")
    )


def story_arc_payload(arc):
    subject_slugs = _clean_slug_list(arc.subject_slugs_json)

    return {
        "id": arc.id,
        "slug": arc.slug,
        "title": arc.title,
        "status": arc.status,
        "scope": arc.scope,
        "subject_slugs": subject_slugs,
        "priority": arc.priority,
        "current_phase": arc.current_phase,
        "horizon": arc.horizon,
        "summary": arc.summary,
        "narrator_guidance": arc.narrator_guidance,
        "constraints": arc.constraints,
    }


def active_story_arcs_for_context(world, limit=ACTIVE_ARC_CONTEXT_LIMIT):
    if not world:
        return []

    return [
        story_arc_payload(arc)
        for arc in _story_arc_queryset(world)[:limit]
    ]


def active_story_arc_records(world, limit=ACTIVE_ARC_CONTEXT_LIMIT):
    if not world:
        return []

    return list(_story_arc_queryset(world)[:limit])


def active_story_arc_ooc_tags(world):
    if not world:
        return []

    tags = []
    for arc in _story_arc_queryset(world):
        tag = normalize_story_arc_ooc_tag(getattr(arc, "ooc_tag", ""))
        if tag and tag not in tags:
            tags.append(tag)

    return tags


def story_arc_lenses_for_character(
    world,
    character,
    limit=CHARACTER_ARC_LENS_CONTEXT_LIMIT,
):
    if not world or not character:
        return []

    character_slug = character.slug
    lenses = []

    for arc in _story_arc_queryset(world):
        lens = _lens_for_character(arc, character_slug)
        subject_slugs = _clean_slug_list(arc.subject_slugs_json)

        if not lens:
            continue

        if arc.scope in {StoryArc.SCOPE_CHARACTER, StoryArc.SCOPE_RELATIONSHIP}:
            if character_slug not in subject_slugs and not (
                isinstance(arc.character_lenses_json, dict)
                and character_slug in arc.character_lenses_json
            ):
                continue

        lenses.append({
            "arc_slug": arc.slug,
            "title": arc.title,
            "scope": arc.scope,
            "subject_slugs": subject_slugs,
            "priority": arc.priority,
            "current_phase": arc.current_phase,
            "horizon": arc.horizon,
            "summary": arc.summary,
            "presentation_lens": lens,
        })

        if len(lenses) >= limit:
            break

    return lenses
