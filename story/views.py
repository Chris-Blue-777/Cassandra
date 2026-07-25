#views.py#
import math
import json

from django.contrib import messages
from django.core.files import File
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.text import slugify
from .models import (
    World,
    SceneState,
    Proposal,
    CommittedScene,
    Character,
    CharacterProfile,
    CharacterVisualIdentity,
    CharacterVisualReference,
    GeneratedMediaAsset,
    GeneratedMediaJob,
    GeneratedMediaJobSubject,
    NarrativeMemory,
    CharacterBelief,
    CharacterMemory,
    CharacterPerception,
    CharacterPerceptionChange,
    SubjectiveRelationshipEdge,
    SubjectiveRelationshipEdgeChange,
    CharacterState,
    CharacterStateChange,
    TempSceneState,
    CharacterScene,
    StoryArc,
    StoryArcUpdateProposal,
)
from .Cassandra import (
    call_cassandra,
    call_cassandra_revision,
    materially_changed,
    choose_revision_mode,
)
from .continuity import (
    active_narrative_memories_for_context,
    apply_narrative_continuity_maintenance,
)
from .arcs import active_story_arc_ooc_tags, active_story_arc_records
from .coverage import normalize_narrative_frame
from .Wanda import (
    GOOGLE_NANO_BANANA_2_PROVIDER_ID,
    RUNWAY_GEN45_VIDEO_PROVIDER_ID,
    MEDIA_STYLE_MATCH_REFERENCE,
    build_general_image_media_prompt_packet,
    build_asset_video_media_prompt_packet,
    build_portrait_media_prompt_packet,
    build_scene_image_media_prompt_packet,
    build_video_media_prompt_packet,
    build_visual_generation_prompt_packet,
    build_visual_identity_registry,
    build_turn_context,
    check_runway_media_job_status,
    character_visual_identity_packet,
    clean_scene_image_prompt_for_editor,
    default_wanda_media_provider_id,
    copy_visual_version_to_identity,
    copy_media_job_for_retry,
    create_asset_video_media_job,
    create_general_image_media_job,
    create_portrait_media_job,
    create_scene_image_media_job,
    create_video_media_job,
    create_visual_identity_version_snapshot,
    current_visual_identity_version,
    enqueue_media_job_with_provider,
    get_or_create_character_visual_identity,
    media_job_can_restart_background_generation,
    media_job_local_runner_state,
    media_job_provider_task_id,
    media_provider_actions_for_job,
    save_visual_identity_version_from_identity,
    update_general_image_media_job,
    update_scene_image_media_job,
    visual_identity_version_by_number,
    build_revision_context,
    resolve_proposed_scene_state,
    serialize_scene_state,
    wanda_media_provider,
    wanda_media_providers,
)
from .MissPots.cast_tracker import (
    infer_scene_participants_and_positions,
    _build_cast_entry,
    _merge_alias_cache,
    _normalize_perception_edges,
    _valid_character_slugs
)
from .MissPots.characters import (
    build_character_registry,
    belief_involves_slug,
    belief_subject_slugs,
    memory_related_character_slugs,
    collect_character_contributions,
    collect_character_authored_intents_from_contributions,
    build_character_event_record_text,
    apply_character_contribution_to_scene,
    character_state_snapshot,
    PENDING_BELIEF_REDUCTION_SOURCE,
)
from .forms import (
    CharacterForm,
    GeneratedMediaAssetForm,
    GeneratedGeneralImageMediaJobReviewForm,
    GeneratedMediaJobReviewForm,
    GeneratedSceneImageMediaJobReviewForm,
    GeneratedVideoMediaJobReviewForm,
    CharacterVisualIdentityForm,
    CharacterVisualReferenceForm,
    StoryArcForm,
    WorldForm,
)

SURPRISE_ME_DIRECTIVE = "[OOC: surprise me]"
RELATIONSHIP_MAP_WIDTH = 760
RELATIONSHIP_MAP_HEIGHT = 520
RELATIONSHIP_MAP_CENTER_X = RELATIONSHIP_MAP_WIDTH / 2
RELATIONSHIP_MAP_CENTER_Y = RELATIONSHIP_MAP_HEIGHT / 2
RELATIONSHIP_MAP_RADIUS = 190


def _append_missing_ooc_tags(user_input, ooc_tags):
    text = str(user_input or "").strip()
    parts = [text] if text else []
    existing_text = text

    for tag in ooc_tags or []:
        clean_tag = str(tag or "").strip()
        if not clean_tag:
            continue
        if clean_tag in existing_text:
            continue
        parts.append(clean_tag)
        existing_text = "\n\n".join(parts)

    return "\n\n".join(parts).strip()


def _draft_user_input_from_post(post_data, world=None):
    user_input = post_data.get("user_input", "").strip()
    surprise_me = post_data.get("surprise_me") == "true"

    if surprise_me:
        if user_input:
            user_input = f"{user_input}\n\n{SURPRISE_ME_DIRECTIVE}"
        else:
            user_input = SURPRISE_ME_DIRECTIVE

    return _append_missing_ooc_tags(
        user_input,
        active_story_arc_ooc_tags(world),
    )


def _log_story_heading(title):
    print(f"\n=== STORY {title} ===", flush=True)


def _log_story_text(label, text):
    print(f"[story] {label}:", flush=True)
    print(text or "", flush=True)


def _log_story_json(label, data):
    print(f"[story] {label}:", flush=True)
    try:
        print(json.dumps(data, indent=2, ensure_ascii=False), flush=True)
    except TypeError:
        print(repr(data), flush=True)


def _clean_slug(slug):
    return str(slug or "").strip()


def _active_world_or_first():
    worlds = World.objects.all().order_by("name")
    active_world = World.objects.filter(is_active=True).first()

    if not active_world and worlds.exists():
        active_world = worlds.first()
        active_world.is_active = True
        active_world.save()

    return worlds, active_world


def _edge_archives_for_map(edge):
    histories = list(
        SubjectiveRelationshipEdgeChange.objects
        .filter(
            world=edge.world,
            observer=edge.observer,
            subject_a=edge.subject_a,
            subject_b=edge.subject_b,
            is_context_active=True,
            change_layer=SubjectiveRelationshipEdgeChange.CHANGE_LAYER_HISTORY,
        )
        .order_by("-created_at")[:3]
    )[::-1]

    pasts = list(
        SubjectiveRelationshipEdgeChange.objects
        .filter(
            world=edge.world,
            observer=edge.observer,
            subject_a=edge.subject_a,
            subject_b=edge.subject_b,
            is_context_active=True,
            change_layer=SubjectiveRelationshipEdgeChange.CHANGE_LAYER_PAST,
        )
        .order_by("-created_at")[:3]
    )[::-1]

    return histories + pasts


def _relationship_map_node_positions(known_characters, observer):
    other_characters = [
        character
        for character in known_characters
        if character.id != observer.id
    ]
    positions = {
        observer.slug: {
            "x": RELATIONSHIP_MAP_CENTER_X,
            "y": RELATIONSHIP_MAP_CENTER_Y,
        }
    }

    if not other_characters:
        return positions

    if len(other_characters) == 2:
        angles = [(-3 * math.pi / 4), (-math.pi / 4)]
    else:
        angles = [
            (2 * math.pi * index / len(other_characters)) - (math.pi / 2)
            for index in range(len(other_characters))
        ]

    for character, angle in zip(other_characters, angles):
        positions[character.slug] = {
            "x": RELATIONSHIP_MAP_CENTER_X + RELATIONSHIP_MAP_RADIUS * math.cos(angle),
            "y": RELATIONSHIP_MAP_CENTER_Y + RELATIONSHIP_MAP_RADIUS * math.sin(angle),
        }

    return positions


def _relationship_map_label_position(start, end):
    return {
        "x": (start["x"] + end["x"]) / 2,
        "y": (start["y"] + end["y"]) / 2,
    }


def _relationship_map_pair_key(slug_a, slug_b):
    return tuple(sorted([
        _clean_slug(slug_a),
        _clean_slug(slug_b),
    ]))


def _relationship_character_anchor(slug):
    return f"relationship-character-{_clean_slug(slug)}"


def _relationship_edge_anchor(edge):
    return f"relationship-edge-{edge.id}"


def _relationship_belief_edge_anchor(belief_id, slug_a, slug_b):
    first_slug, second_slug = _relationship_map_pair_key(slug_a, slug_b)
    return f"relationship-belief-edge-{belief_id}-{first_slug}-{second_slug}"


def _relationship_pending_belief_edge_anchor(belief_id, slug_a, slug_b):
    first_slug, second_slug = _relationship_map_pair_key(slug_a, slug_b)
    return f"relationship-pending-belief-edge-{belief_id}-{first_slug}-{second_slug}"


def _perception_relationship_summary(perception):
    if not perception:
        return ""

    relationship = perception.relationship_json or {}
    if not isinstance(relationship, dict):
        return str(relationship or "").strip()

    for key in ("summary", "relationship", "dynamic", "status"):
        value = str(relationship.get(key) or "").strip()
        if value:
            return value

    return ""


def _relationship_label_from_summary(summary):
    text = str(summary or "").strip().lower()

    if not text:
        return ""

    label_keywords = [
        ("flirtation", ["flirt", "charged", "attraction", "desire", "interested"]),
        ("dating", ["dating", "girlfriend", "boyfriend", "partner", "couple"]),
        ("family", ["brother", "sister", "sibling", "family"]),
        ("friendship", ["friend", "friendly", "ally", "alliance"]),
        ("rivalry", ["rival", "competition", "competitive"]),
        ("antagonism", ["needle", "jab", "dismiss", "threat", "resent", "contempt"]),
        ("caretaking / dependence", ["depend", "coax", "caretaking", "comfort", "protect"]),
        ("control", ["control", "shape", "manage", "contain", "entitled"]),
        ("uncertain tension", ["uncertain", "ambiguous", "unsure", "question"]),
    ]

    for label, keywords in label_keywords:
        if any(keyword in text for keyword in keywords):
            return label

    return ""


def _perception_relationship_label(perception):
    if not perception:
        return "direct tie"

    relationship = perception.relationship_json or {}

    if isinstance(relationship, dict):
        for key in ("relationship_label", "label", "type", "dynamic", "status"):
            value = str(relationship.get(key) or "").strip()
            if value and value.lower() not in {"summary", "relationship"}:
                return value

    label = _relationship_label_from_summary(
        _perception_relationship_summary(perception)
    )
    if label:
        return label

    return "direct tie"


def _belief_graph_edges_for_map(
    active_beliefs,
    positions,
    explicit_social_pairs,
    characters_by_slug,
    *,
    kind="belief",
    label="belief",
    card_label="inferred from belief",
    anchor_builder=_relationship_belief_edge_anchor,
    initial_seen_pairs=None,
):
    graph_edges = []
    edge_cards = []
    seen_pairs = set(initial_seen_pairs or [])

    for belief in active_beliefs:
        slugs = [
            slug for slug in belief_subject_slugs(belief)
            if slug in positions
        ]

        for index, from_slug in enumerate(slugs):
            for to_slug in slugs[index + 1:]:
                if from_slug == to_slug:
                    continue

                pair_key = _relationship_map_pair_key(from_slug, to_slug)
                if pair_key in explicit_social_pairs or pair_key in seen_pairs:
                    continue

                start = positions[from_slug]
                end = positions[to_slug]
                label_position = _relationship_map_label_position(start, end)
                target_anchor = anchor_builder(
                    belief.id,
                    from_slug,
                    to_slug,
                )
                graph_edges.append({
                    "kind": kind,
                    "label": label,
                    "from_slug": from_slug,
                    "to_slug": to_slug,
                    "title": (
                        f"{characters_by_slug[from_slug].name} ↔ "
                        f"{characters_by_slug[to_slug].name}"
                    ),
                    "summary": belief.belief,
                    "target_anchor": target_anchor,
                    "aria_label": (
                        f"Belief link between {characters_by_slug[from_slug].name} "
                        f"and {characters_by_slug[to_slug].name}: {belief.belief}"
                    ),
                    "x1": start["x"],
                    "y1": start["y"],
                    "x2": end["x"],
                    "y2": end["y"],
                    "label_x": label_position["x"],
                    "label_y": label_position["y"],
                    "source_belief_id": belief.id,
                })
                edge_cards.append({
                    "anchor": target_anchor,
                    "subject_a": characters_by_slug[from_slug],
                    "subject_b": characters_by_slug[to_slug],
                    "belief": belief,
                    "card_label": card_label,
                })
                seen_pairs.add(pair_key)

    return graph_edges, edge_cards, seen_pairs


def _build_observer_relationship_map(active_world, observer):
    characters_by_slug = {
        character.slug: character
        for character in Character.objects.filter(
            world=active_world,
            is_active=True,
        ).order_by("name")
        if character.slug
    }

    perceptions = list(
        CharacterPerception.objects
        .filter(world=active_world, observer=observer)
        .select_related("target")
        .order_by("target__name")
    )
    perceptions_by_slug = {
        perception.target.slug: perception
        for perception in perceptions
    }

    subjective_edges = list(
        SubjectiveRelationshipEdge.objects
        .filter(world=active_world, observer=observer)
        .select_related("subject_a", "subject_b")
        .order_by("subject_a__name", "subject_b__name")
    )

    active_beliefs = list(
        CharacterBelief.objects
        .filter(world=active_world, character=observer)
        .exclude(belief_status=CharacterBelief.BELIEF_STATUS_DISCARDED)
        .exclude(source=PENDING_BELIEF_REDUCTION_SOURCE)
        .order_by("-updated_at")[:80]
    )

    pending_beliefs = list(
        CharacterBelief.objects
        .filter(
            world=active_world,
            character=observer,
            source=PENDING_BELIEF_REDUCTION_SOURCE,
        )
        .exclude(belief_status=CharacterBelief.BELIEF_STATUS_DISCARDED)
        .order_by("-updated_at")[:40]
    )

    active_memories = list(
        CharacterMemory.objects
        .filter(
            world=active_world,
            character=observer,
            is_context_active=True,
        )
        .select_related("related_character")
        .order_by("-created_at")[:80]
    )

    known_slugs = {_clean_slug(observer.slug)}

    for perception in perceptions:
        known_slugs.add(_clean_slug(perception.target.slug))

    for edge in subjective_edges:
        known_slugs.add(_clean_slug(edge.subject_a.slug))
        known_slugs.add(_clean_slug(edge.subject_b.slug))

    for belief in active_beliefs:
        known_slugs.update(belief_subject_slugs(belief))

    for belief in pending_beliefs:
        known_slugs.update(belief_subject_slugs(belief))

    for memory in active_memories:
        known_slugs.update(memory_related_character_slugs(memory))

    known_characters = [
        character
        for character in characters_by_slug.values()
        if character.slug in known_slugs
    ]
    known_characters.sort(
        key=lambda character: (
            0 if character.id == observer.id else 1,
            character.name.lower(),
        )
    )

    positions = _relationship_map_node_positions(known_characters, observer)

    graph_nodes = [
        {
            "slug": character.slug,
            "name": character.name,
            "x": positions[character.slug]["x"],
            "y": positions[character.slug]["y"],
            "is_observer": character.id == observer.id,
            "has_perception": character.slug in perceptions_by_slug,
            "target_anchor": _relationship_character_anchor(character.slug),
            "title": character.name,
            "summary": (
                perceptions_by_slug[character.slug].summary
                if character.slug in perceptions_by_slug
                else (
                    f"{character.name} is the selected observer."
                    if character.id == observer.id
                    else "No personal synopsis stored yet."
                )
            ),
        }
        for character in known_characters
        if character.slug in positions
    ]

    graph_edges = []
    explicit_social_pairs = {
        _relationship_map_pair_key(edge.subject_a.slug, edge.subject_b.slug)
        for edge in subjective_edges
    }

    for perception in perceptions:
        if perception.target.slug not in positions:
            continue

        pair_key = _relationship_map_pair_key(observer.slug, perception.target.slug)
        if pair_key in explicit_social_pairs:
            continue

        start = positions[observer.slug]
        end = positions[perception.target.slug]
        label_position = _relationship_map_label_position(start, end)
        relationship_summary = (
            _perception_relationship_summary(perception)
            or "No relationship summary stored yet."
        )
        relationship_label = _perception_relationship_label(perception)
        graph_edges.append({
            "kind": "direct",
            "label": relationship_label,
            "from_slug": observer.slug,
            "to_slug": perception.target.slug,
            "title": f"{observer.name} ↔ {perception.target.name}",
            "summary": relationship_summary,
            "target_anchor": f"relationship-direct-{observer.slug}-{perception.target.slug}",
            "aria_label": (
                f"{observer.name}'s relationship with {perception.target.name}: "
                f"{relationship_summary}"
            ),
            "x1": start["x"],
            "y1": start["y"],
            "x2": end["x"],
            "y2": end["y"],
            "label_x": label_position["x"],
            "label_y": label_position["y"],
        })

    direct_relationship_cards = []
    for perception in perceptions:
        pair_key = _relationship_map_pair_key(observer.slug, perception.target.slug)
        if pair_key in explicit_social_pairs:
            continue

        direct_relationship_cards.append({
            "anchor": f"relationship-direct-{observer.slug}-{perception.target.slug}",
            "observer": observer,
            "target": perception.target,
            "perception": perception,
            "relationship_label": _perception_relationship_label(perception),
            "relationship_summary": (
                _perception_relationship_summary(perception)
                or "No relationship summary stored yet."
            ),
        })

    edge_cards = []
    for edge in subjective_edges:
        if edge.subject_a.slug not in positions or edge.subject_b.slug not in positions:
            continue

        start = positions[edge.subject_a.slug]
        end = positions[edge.subject_b.slug]
        label_position = _relationship_map_label_position(start, end)
        label = edge.relationship_label or "relationship"
        graph_edges.append({
            "kind": "social",
            "label": label,
            "from_slug": edge.subject_a.slug,
            "to_slug": edge.subject_b.slug,
            "title": f"{edge.subject_a.name} ↔ {edge.subject_b.name}",
            "summary": (
                edge.last_change_summary
                or edge.summary
                or edge.knowledge_basis
                or "Stored subjective social edge."
            ),
            "target_anchor": _relationship_edge_anchor(edge),
            "aria_label": (
                f"{edge.subject_a.name} and {edge.subject_b.name}: "
                f"{edge.summary or edge.relationship_label or 'relationship'}"
            ),
            "x1": start["x"],
            "y1": start["y"],
            "x2": end["x"],
            "y2": end["y"],
            "label_x": label_position["x"],
            "label_y": label_position["y"],
        })
        edge_cards.append({
            "anchor": _relationship_edge_anchor(edge),
            "edge": edge,
            "archives": _edge_archives_for_map(edge),
        })

    belief_graph_edges, belief_edge_cards, belief_edge_pairs = _belief_graph_edges_for_map(
        active_beliefs,
        positions,
        explicit_social_pairs,
        characters_by_slug,
    )
    graph_edges.extend(belief_graph_edges)

    (
        pending_belief_graph_edges,
        pending_belief_edge_cards,
        _pending_belief_edge_pairs,
    ) = _belief_graph_edges_for_map(
        pending_beliefs,
        positions,
        explicit_social_pairs,
        characters_by_slug,
        kind="pending-belief",
        label="pending",
        card_label="pending belief candidate",
        anchor_builder=_relationship_pending_belief_edge_anchor,
        initial_seen_pairs=belief_edge_pairs,
    )
    graph_edges.extend(pending_belief_graph_edges)

    character_cards = []
    for character in known_characters:
        perception = perceptions_by_slug.get(character.slug)
        related_beliefs = [
            belief for belief in active_beliefs
            if character.slug in belief_subject_slugs(belief)
        ][:6]
        related_memories = [
            memory for memory in active_memories
            if character.slug in memory_related_character_slugs(memory)
        ][:6]
        character_cards.append({
            "anchor": _relationship_character_anchor(character.slug),
            "character": character,
            "is_observer": character.id == observer.id,
            "perception": perception,
            "beliefs": related_beliefs,
            "memories": related_memories,
            "scores": (
                [
                    {"label": "Trust", "value": perception.trust},
                    {"label": "Attraction", "value": perception.attraction},
                    {"label": "Fear", "value": perception.fear},
                    {"label": "Resentment", "value": perception.resentment},
                ]
                if perception
                else []
            ),
        })

    return {
        "known_characters": known_characters,
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
        "character_cards": character_cards,
        "direct_relationship_cards": direct_relationship_cards,
        "edge_cards": edge_cards,
        "belief_edge_cards": belief_edge_cards,
        "pending_belief_edge_cards": pending_belief_edge_cards,
        "has_known_map": len(known_characters) > 1 or bool(edge_cards),
        "svg_width": RELATIONSHIP_MAP_WIDTH,
        "svg_height": RELATIONSHIP_MAP_HEIGHT,
    }


def _character_contribution_log_rows(character_contributions):
    rows = []

    for contribution in character_contributions or []:
        if not isinstance(contribution, dict):
            continue

        rows.append({
            "slug": contribution.get("slug", ""),
            "character_pov_prose": contribution.get("character_pov_prose", ""),
            "attempted_action": contribution.get("attempted_action", ""),
            "attempted_dialogue": contribution.get("attempted_dialogue", ""),
            "internal_intent": contribution.get("internal_intent", ""),
            "emotional_strategy": contribution.get("emotional_strategy", ""),
            "unspoken_avoidance": contribution.get("unspoken_avoidance", ""),
            "desired_next_moment": contribution.get("desired_next_moment", ""),
            "uninterrupted_followthrough": contribution.get(
                "uninterrupted_followthrough",
                "",
            ),
            "resistance_response": contribution.get("resistance_response", ""),
            "pressure_channel": contribution.get("pressure_channel", ""),
            "authored_intent": contribution.get("authored_intent", {}),
        })

    return rows


def _scene_state_log_summary(scene_state_data):
    if not isinstance(scene_state_data, dict):
        return {}

    cast = scene_state_data.get("cast", {}) or {}
    pending_intents = scene_state_data.get("pending_intents", {}) or {}
    spaces = scene_state_data.get("spaces", {}) or {}
    alias_cache = scene_state_data.get("alias_cache", {}) or {}

    return {
        "location": scene_state_data.get("location", ""),
        "cast_slugs": list(cast.keys()) if isinstance(cast, dict) else [],
        "pending_intent_slugs": (
            list(pending_intents.keys())
            if isinstance(pending_intents, dict)
            else []
        ),
        "space_ids": list(spaces.keys()) if isinstance(spaces, dict) else [],
        "alias_count": len(alias_cache) if isinstance(alias_cache, dict) else 0,
    }


def _event_involved_character_slugs(scene_events):
    involved = set()

    for event in scene_events or []:
        if not isinstance(event, dict):
            continue

        actor_slug = str(event.get("actor_slug") or "").strip()
        if actor_slug:
            involved.add(actor_slug)

        for key in ("target_slugs", "perceived_by"):
            for slug in event.get(key) or []:
                clean_slug = str(slug or "").strip()
                if clean_slug:
                    involved.add(clean_slug)

    return involved


def _normalize_cassandra_scene_state_update(raw_update, active_slugs, scene_events):
    if not isinstance(raw_update, dict):
        raw_update = {}

    active_slugs = set(active_slugs or [])
    event_slugs = _event_involved_character_slugs(scene_events)
    allowed_update_slugs = active_slugs.intersection(event_slugs)

    location = raw_update.get("location")
    if location is not None:
        location = str(location).strip() or None

    normalized_cast = {}
    raw_cast = raw_update.get("cast") or []

    if isinstance(raw_cast, dict):
        cast_entries = [
            {"slug": slug, **payload}
            for slug, payload in raw_cast.items()
            if isinstance(payload, dict)
        ]
    elif isinstance(raw_cast, list):
        cast_entries = raw_cast
    else:
        cast_entries = []

    for entry in cast_entries:
        if not isinstance(entry, dict):
            continue

        slug = str(entry.get("slug") or "").strip()
        if slug not in allowed_update_slugs:
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
                valid_slugs=active_slugs,
            ),
        )

    return {
        "location": location,
        "cast": normalized_cast,
    }


def _merge_cassandra_scene_state_consequences(
    pre_draft_scene_state,
    cassandra_scene_state_update,
    pending_intents,
    alias_cache,
):
    base_state = {
        **(pre_draft_scene_state or {}),
        "alias_cache": alias_cache or {},
    }

    return resolve_proposed_scene_state(
        current_state=base_state,
        scene_state_update=cassandra_scene_state_update or {},
        pending_intents=pending_intents or {},
    )


def _auto_skip_stale_story_arc_update_proposals(world, latest_source_scene):
    if not world or not latest_source_scene:
        return 0

    stale_proposals = (
        StoryArcUpdateProposal.objects
        .filter(
            world=world,
            status=StoryArcUpdateProposal.STATUS_PENDING,
            source_scene__turn_number__lt=latest_source_scene.turn_number,
        )
        .select_related("story_arc", "source_scene")
    )

    skipped_count = 0
    for proposal in stale_proposals:
        proposal.skip()
        skipped_count += 1

    return skipped_count


def _find_proposal_for_committed_scene(scene):
    return (
        Proposal.objects
        .filter(
            world=scene.world,
            is_approved=True,
            user_input=scene.user_text,
            draft=scene.cassandra_text,
        )
        .order_by("-created_at")
        .first()
    )


def _delete_legacy_null_source_beliefs(character_scenes):
    for character_scene in character_scenes:
        for belief in character_scene.beliefs_created_json or []:
            if not isinstance(belief, dict):
                continue

            belief_text = str(belief.get("belief") or "").strip()
            if not belief_text:
                continue

            CharacterBelief.objects.filter(
                world=character_scene.world,
                character=character_scene.character,
                source_scene__isnull=True,
                subject_type=belief.get("subject_type") or "",
                subject_slug=belief.get("subject_slug") or "",
                belief=belief_text,
            ).delete()


def _rebuild_character_state_for_rewind(world, character_ids):
    for character in Character.objects.filter(world=world, id__in=character_ids):
        latest_change = (
            CharacterStateChange.objects
            .filter(world=world, character=character)
            .order_by("-created_at", "-id")
            .first()
        )

        if not latest_change:
            CharacterState.objects.filter(character=character).delete()
            continue

        state, _ = CharacterState.objects.get_or_create(character=character)
        state.emotional_state_json = latest_change.emotional_state_json or {}
        state.goals_json = latest_change.goals_json or {}
        state.internal_conflicts_json = latest_change.internal_conflicts_json or {}
        state.motivational_state_json = latest_change.motivational_state_json or {}
        state.save()


def _rebuild_perception_for_pair(world, observer_id, target_id):
    changes = (
        CharacterPerceptionChange.objects
        .filter(world=world, observer_id=observer_id, target_id=target_id)
        .order_by("created_at", "id")
    )

    if not changes.exists():
        CharacterPerception.objects.filter(
            world=world,
            observer_id=observer_id,
            target_id=target_id,
        ).delete()
        return

    perception, _ = CharacterPerception.objects.get_or_create(
        world=world,
        observer_id=observer_id,
        target_id=target_id,
        defaults={"summary": ""},
    )

    summary = ""
    impression = {}
    relationship = {}
    belief = {}
    trust = 0.0
    attraction = 0.0
    fear = 0.0
    resentment = 0.0

    for change in changes:
        summary = change.summary or summary
        impression = change.impression_json or impression
        relationship = change.relationship_json or relationship
        belief = change.belief_json or belief
        trust += change.trust_delta or 0.0
        attraction += change.attraction_delta or 0.0
        fear += change.fear_delta or 0.0
        resentment += change.resentment_delta or 0.0

    perception.summary = summary
    perception.impression_json = impression
    perception.relationship_json = relationship
    perception.belief_json = belief
    perception.trust = trust
    perception.attraction = attraction
    perception.fear = fear
    perception.resentment = resentment
    perception.save()


def _rebuild_perceptions_for_rewind(world, perception_pairs):
    for observer_id, target_id in perception_pairs:
        _rebuild_perception_for_pair(world, observer_id, target_id)


def _rebuild_subjective_relationship_edge_for_pair(
    world,
    observer_id,
    subject_a_id,
    subject_b_id,
):
    changes = (
        SubjectiveRelationshipEdgeChange.objects
        .filter(
            world=world,
            observer_id=observer_id,
            subject_a_id=subject_a_id,
            subject_b_id=subject_b_id,
        )
        .order_by("created_at", "id")
    )

    if not changes.exists():
        SubjectiveRelationshipEdge.objects.filter(
            world=world,
            observer_id=observer_id,
            subject_a_id=subject_a_id,
            subject_b_id=subject_b_id,
        ).delete()
        return

    edge, _ = SubjectiveRelationshipEdge.objects.get_or_create(
        world=world,
        observer_id=observer_id,
        subject_a_id=subject_a_id,
        subject_b_id=subject_b_id,
        defaults={"summary": ""},
    )

    relationship_label = ""
    summary = ""
    knowledge_basis = ""
    confidence = 0.5
    open_questions = []
    directional_notes = {}
    last_change_summary = ""

    for change in changes:
        relationship_label = change.relationship_label or relationship_label
        summary = change.revised_summary or change.summary or summary
        knowledge_basis = change.knowledge_basis or knowledge_basis
        confidence = change.confidence
        open_questions = change.open_questions_json or open_questions
        directional_notes = change.directional_notes_json or directional_notes
        last_change_summary = change.summary or last_change_summary

    edge.relationship_label = relationship_label
    edge.summary = summary
    edge.knowledge_basis = knowledge_basis
    edge.confidence = confidence
    edge.open_questions_json = open_questions
    edge.directional_notes_json = directional_notes
    edge.last_change_summary = last_change_summary
    edge.save()


def _rebuild_subjective_relationship_edges_for_rewind(world, edge_pairs):
    for observer_id, subject_a_id, subject_b_id in edge_pairs:
        _rebuild_subjective_relationship_edge_for_pair(
            world,
            observer_id,
            subject_a_id,
            subject_b_id,
        )


def _fallback_scene_state_from_latest_scene(scene):
    cast = {}

    character_scenes = (
        CharacterScene.objects
        .filter(world=scene.world, source_scene=scene)
        .select_related("character")
    )

    for character_scene in character_scenes:
        if character_scene.local_scene_state_json:
            cast[character_scene.character.slug] = (
                character_scene.local_scene_state_json
            )

    return {
        "location": "opening scene",
        "cast": cast,
        "pending_intents": {},
        "alias_cache": {},
        "narrative_frame": {},
        "spaces": {},
    }


def _rewind_scene_state(world):
    scene_state, _ = SceneState.objects.get_or_create(
        world=world,
        defaults={
            "location": "opening scene",
            "cast_json": {},
            "pending_intents_json": {},
            "alias_cache_json": {},
            "topology_json": {},
        }
    )

    latest_scene = (
        CommittedScene.objects
        .filter(world=world)
        .order_by("-turn_number")
        .first()
    )

    if not latest_scene:
        scene_state.location = "opening scene"
        scene_state.cast_json = {}
        scene_state.pending_intents_json = {}
        scene_state.alias_cache_json = {}
        scene_state.topology_json = {}
        scene_state.save()
        return

    proposal = _find_proposal_for_committed_scene(latest_scene)
    if proposal:
        rewind_state = {
            **(proposal.proposed_scene_state_json or {}),
            "pending_intents": proposal.resolved_pending_intents_json or {},
        }
    else:
        rewind_state = _fallback_scene_state_from_latest_scene(latest_scene)

    scene_state.location = rewind_state.get("location", scene_state.location)
    scene_state.cast_json = rewind_state.get("cast", {})
    scene_state.pending_intents_json = rewind_state.get("pending_intents", {})
    scene_state.alias_cache_json = rewind_state.get("alias_cache", {})
    scene_state.topology_json = {
        "narrative_frame": rewind_state.get("narrative_frame", {}),
        "spaces": rewind_state.get("spaces", {}),
    }
    scene_state.save()


def normalize_intents(intents):
    normalized = {}

    if isinstance(intents, dict):
        iterable = [
            {"slug": slug, **payload}
            for slug, payload in intents.items()
            if isinstance(payload, dict)
        ]
    elif isinstance(intents, list):
        iterable = intents
    else:
        iterable = []

    for entry in iterable:
        if not isinstance(entry, dict):
            continue

        slug = str(entry.get("slug") or "").strip()
        if not slug:
            continue

        normalized[slug] = {
            "purpose": str(entry.get("purpose") or "").strip(),
            "tone": str(entry.get("tone") or "").strip(),
            "next": str(entry.get("next") or "").strip(),
        }

    return normalized


def find_character_contribution(character_contributions, slug):
    for contribution in character_contributions or []:
        if not isinstance(contribution, dict):
            continue

        if contribution.get("slug") == slug:
            return contribution

        if contribution.get("scene_contribution", {}).get("slug") == slug:
            scene_contribution = contribution.get("scene_contribution") or {}
            scene_contribution["authored_intent"] = (
                contribution.get("authored_intent") or {}
            )
            scene_contribution["current_turn_reflection"] = (
                contribution.get("current_turn_reflection") or {}
            )
            return scene_contribution

    return {}


def relationship_map_page(request):
    worlds, active_world = _active_world_or_first()

    if not active_world:
        return render(request, "story/relationship_map_page.html", {
            "worlds": worlds,
            "active_world": None,
            "characters": [],
            "observer": None,
            "map_data": None,
            "error": "No worlds exist yet. Create one from the New World page.",
        })

    characters = list(
        Character.objects
        .filter(world=active_world, is_active=True)
        .order_by("name")
    )
    requested_observer_slug = _clean_slug(request.GET.get("observer"))
    observer = None

    if requested_observer_slug:
        observer = next(
            (
                character for character in characters
                if character.slug == requested_observer_slug
            ),
            None,
        )

    if observer is None and characters:
        observer = characters[0]

    map_data = (
        _build_observer_relationship_map(active_world, observer)
        if observer
        else None
    )

    return render(request, "story/relationship_map_page.html", {
        "worlds": worlds,
        "active_world": active_world,
        "characters": characters,
        "observer": observer,
        "map_data": map_data,
    })


def _wanda_visual_identity_redirect(character, version_number=None):
    url = reverse("wanda_visual_identities")
    if character and character.slug:
        query = f"?character={character.slug}"
        if version_number:
            query += f"&version={version_number}"
        return redirect(f"{url}{query}")
    return redirect(url)


def _wanda_media_jobs_url(job=None):
    url = reverse("wanda_media_jobs")
    if job:
        return f"{url}?job={job.id}"
    return url


def _wanda_media_asset_url(asset=None):
    url = reverse("wanda_media_jobs")
    if asset:
        return f"{url}?asset={asset.id}"
    return url


def _generated_asset_reference(asset):
    if not asset or not asset.target_character_id:
        return None

    references = CharacterVisualReference.objects.filter(
        character=asset.target_character,
        kind=CharacterVisualReference.KIND_GENERATED_REFERENCE,
    )
    if asset.visual_identity_version_id:
        references = references.filter(
            identity_version=asset.visual_identity_version,
        )

    for reference in references:
        metadata = reference.metadata_json or {}
        if metadata.get("source_generated_media_asset_id") == asset.id:
            return reference

    return None


def _generated_asset_reference_filename(asset):
    source_name = (
        asset.file.name.split("/")[-1]
        if asset and asset.file
        else "generated-media"
    )
    return f"generated-asset-{asset.id}-{source_name}"


def _valid_media_job_source(source):
    if source in {
        GeneratedMediaJob.SOURCE_WANDA_IDENTITY,
        GeneratedMediaJob.SOURCE_APPROVED_SCENE,
    }:
        return source
    return GeneratedMediaJob.SOURCE_WANDA_IDENTITY


def _visual_identity_version_for_character(character, version_number=None):
    visual_identity = (
        CharacterVisualIdentity.objects
        .filter(character=character)
        .first()
    )
    if not visual_identity:
        return None, None

    version = (
        visual_identity_version_by_number(visual_identity, version_number)
        if version_number
        else None
    )
    if not version:
        version = current_visual_identity_version(visual_identity)

    return visual_identity, version


def _mallory_visual_identity_template_for_character(active_world, selected_character):
    if (
        not active_world
        or not selected_character
        or selected_character.slug == "mallory"
    ):
        return None

    mallory = (
        Character.objects
        .filter(
            world=active_world,
            slug="mallory",
            is_active=True,
        )
        .first()
    )
    if not mallory:
        return None

    mallory_identity = (
        CharacterVisualIdentity.objects
        .filter(character=mallory)
        .first()
    )
    if not mallory_identity:
        return None

    mallory_version = current_visual_identity_version(mallory_identity)
    template = CharacterVisualIdentity(
        world=active_world,
        character=selected_character,
        status=CharacterVisualIdentity.STATUS_DRAFT,
    )

    if mallory_version:
        template.is_locked = mallory_version.is_locked
        template.appearance_summary = mallory_version.appearance_summary
        template.canonical_identity_prompt = mallory_version.canonical_identity_prompt
        template.negative_identity_prompt = mallory_version.negative_identity_prompt
        template.traits_json = mallory_version.traits_json or {}
        template.allowed_variations_json = (
            mallory_version.allowed_variations_json or {}
        )
        template.provider_notes_json = mallory_version.provider_notes_json or {}
        return template

    template.is_locked = mallory_identity.is_locked
    template.appearance_summary = mallory_identity.appearance_summary
    template.canonical_identity_prompt = mallory_identity.canonical_identity_prompt
    template.negative_identity_prompt = mallory_identity.negative_identity_prompt
    template.traits_json = mallory_identity.traits_json or {}
    template.allowed_variations_json = mallory_identity.allowed_variations_json or {}
    template.provider_notes_json = mallory_identity.provider_notes_json or {}
    return template


def _media_portrait_character_options(world):
    options = []
    characters = (
        Character.objects
        .filter(world=world, is_active=True)
        .order_by("name")
    )

    for character in characters:
        visual_identity, version = _visual_identity_version_for_character(
            character
        )
        if not visual_identity or not version:
            continue

        options.append({
            "slug": character.slug,
            "name": character.name,
            "version_number": version.version_number,
            "version_status": version.status,
        })

    return options


def _stringify_scene_event_bits(value):
    if isinstance(value, dict):
        bits = []
        for nested_value in value.values():
            nested_text = _stringify_scene_event_bits(nested_value)
            if nested_text:
                bits.append(nested_text)
        return " ".join(bits)
    if isinstance(value, list):
        return " ".join(
            bit for bit in (
                _stringify_scene_event_bits(item)
                for item in value
            )
            if bit
        )
    return str(value or "")


def _detect_scene_subject_slugs(world, scene, explicit_slugs=""):
    explicit = []
    for raw_slug in str(explicit_slugs or "").replace("\n", ",").split(","):
        slug = raw_slug.strip()
        if slug and slug not in explicit:
            explicit.append(slug)
    if explicit:
        return explicit

    if not world or not scene:
        return []

    scene_text = " ".join([
        scene.user_text or "",
        scene.cassandra_text or "",
        _stringify_scene_event_bits(scene.scene_events_json or []),
    ]).lower()
    detected = []

    for character in Character.objects.filter(
        world=world,
        is_active=True,
    ).order_by("name"):
        name = (character.name or "").lower()
        slug = (character.slug or "").lower()
        if (
            (name and name in scene_text)
            or (slug and slug in scene_text)
        ) and character.slug not in detected:
            visual_identity, version = _visual_identity_version_for_character(
                character
            )
            if visual_identity and version:
                detected.append(character.slug)

    return detected


def _scene_subject_options(world, selected_slugs=None):
    selected_slugs = set(selected_slugs or [])
    options = []
    for option in _media_portrait_character_options(world):
        option = dict(option)
        option["is_selected"] = option["slug"] in selected_slugs
        options.append(option)
    return options


def _scene_image_reference_selection_from_request(request, subject_slugs):
    selected_by_slug = {}
    primary_by_slug = {}

    for slug in _detect_scene_subject_slugs(
        None,
        None,
        subject_slugs or "",
    ):
        selected_by_slug[slug] = request.POST.getlist(
            f"subject_reference_ids__{slug}"
        )
        primary_id = request.POST.get(f"subject_primary_reference_id__{slug}") or ""
        if primary_id:
            primary_by_slug[slug] = primary_id

    return selected_by_slug, primary_by_slug


def _scene_image_selected_reference_count(selected_by_slug):
    def parse_ids(value):
        if isinstance(value, (list, tuple)):
            value = ",".join(str(item) for item in value)
        return _parse_reference_id_csv(value)

    return sum(
        len(parse_ids(reference_ids))
        for reference_ids in (selected_by_slug or {}).values()
    )


def _scene_image_prompt_packet_preview(prompt_packet):
    preview = json.loads(json.dumps(prompt_packet or {}))
    selected_ids = {
        int(reference["id"])
        for reference in preview.get("selected_references", [])
        if isinstance(reference, dict) and reference.get("id") is not None
    }

    for subject in preview.get("visual_subjects", []):
        subject.pop("reference_options", None)
        identity_packet = subject.get("identity_packet") or {}
        selected_subject_references = [
            reference
            for reference in subject.get("selected_references", [])
            if isinstance(reference, dict)
        ]
        selected_subject_ids = {
            int(reference["id"])
            for reference in selected_subject_references
            if reference.get("id") is not None
        }
        identity_packet["reference_assets"] = [
            reference
            for reference in identity_packet.get("reference_assets", [])
            if isinstance(reference, dict)
            and reference.get("id") is not None
            and int(reference["id"]) in selected_subject_ids
        ]
        identity_packet["primary_reference_asset"] = (
            selected_subject_references[0]
            if selected_subject_references
            else None
        )

    preview["selected_reference_ids"] = sorted(selected_ids)
    return preview


def _empty_scene_image_reference_selection(subject_slugs):
    return {
        slug: []
        for slug in _detect_scene_subject_slugs(
            None,
            None,
            subject_slugs or "",
        )
    }


def _portrait_media_selection_from_request(request, active_world):
    data = request.POST if request.method == "POST" else request.GET
    source = _valid_media_job_source(data.get("source"))
    source_scene = None
    scene_id = data.get("scene_id") or data.get("source_scene_id")

    if scene_id:
        source_scene = get_object_or_404(
            CommittedScene,
            id=scene_id,
            world=active_world,
        )
        source = GeneratedMediaJob.SOURCE_APPROVED_SCENE

    character_slug = _clean_slug(
        data.get("character_slug")
        or data.get("character")
    )
    selected_character = None

    if character_slug:
        selected_character = get_object_or_404(
            Character,
            world=active_world,
            slug=character_slug,
            is_active=True,
        )
    else:
        selected_character = (
            Character.objects
            .filter(world=active_world, is_active=True)
            .order_by("name")
            .first()
        )

    version_number = data.get("version_number") or data.get("version")
    visual_identity = None
    selected_version = None

    if selected_character:
        visual_identity, selected_version = _visual_identity_version_for_character(
            selected_character,
            version_number,
        )

    return {
        "source": source,
        "source_scene": source_scene,
        "selected_character": selected_character,
        "visual_identity": visual_identity,
        "selected_version": selected_version,
        "version_number": (
            selected_version.version_number
            if selected_version
            else version_number
        ),
        "provider": data.get("provider") or "",
        "style_mode": data.get("style_mode") or MEDIA_STYLE_MATCH_REFERENCE,
        "custom_style_prompt": data.get("custom_style_prompt") or "",
        "user_prompt_override": data.get("user_prompt_override") or "",
    }


def _parse_reference_id_csv(value):
    reference_ids = []

    for raw_id in str(value or "").replace("\n", ",").split(","):
        try:
            reference_id = int(raw_id.strip())
        except (TypeError, ValueError):
            continue

        if reference_id not in reference_ids:
            reference_ids.append(reference_id)

    return reference_ids


def _normalize_prompt_edit_comparison(value):
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def _prompt_text_was_manually_edited(posted_text, original_text):
    return (
        _normalize_prompt_edit_comparison(posted_text)
        != _normalize_prompt_edit_comparison(original_text)
    )


def _prompt_with_user_override(prompt_text, user_prompt_override):
    prompt = str(prompt_text or "").strip()
    override = str(user_prompt_override or "").strip()

    if not override:
        return prompt

    normalized_prompt = _normalize_prompt_edit_comparison(prompt)
    normalized_override = _normalize_prompt_edit_comparison(override)

    if normalized_override in normalized_prompt:
        return prompt

    return (
        f"{prompt}\n\n"
        "User prompt override / extra direction:\n"
        f"{override}"
    ).strip()


def _reference_options_from_prompt_packet(prompt_packet, selected_reference_ids=None):
    identity_packet = (
        prompt_packet.get("identity_packet", {})
        if isinstance(prompt_packet, dict)
        else {}
    )
    available_references = identity_packet.get("reference_assets") or []
    selected_ids = _parse_reference_id_csv(selected_reference_ids)
    if selected_reference_ids is None:
        selected_ids = [
            int(reference.get("id"))
            for reference in prompt_packet.get("selected_references", [])
            if reference.get("id") is not None
        ]
    primary_id = None
    selected_references = prompt_packet.get("selected_references") or []
    if selected_references and selected_references[0].get("id") is not None:
        primary_id = int(selected_references[0]["id"])

    options = []
    for reference in available_references:
        reference_id = reference.get("id")
        if reference_id is None:
            continue
        reference_id = int(reference_id)
        options.append({
            "reference": reference,
            "is_selected": reference_id in selected_ids,
            "is_job_primary": reference_id == primary_id,
        })

    return options


def _selected_reference_csv_from_packet(prompt_packet):
    return ",".join(
        str(reference.get("id"))
        for reference in prompt_packet.get("selected_references", [])
        if reference.get("id") is not None
    )


def _primary_reference_id_from_packet(prompt_packet):
    selected_references = prompt_packet.get("selected_references") or []
    if selected_references and selected_references[0].get("id") is not None:
        return str(selected_references[0]["id"])
    return ""


def _available_reference_ids(prompt_packet):
    identity_packet = prompt_packet.get("identity_packet", {}) or {}
    return {
        int(reference["id"])
        for reference in identity_packet.get("reference_assets", [])
        if reference.get("id") is not None
    }


def _review_provider_id_from_request(request, selection):
    data = request.POST if request.method == "POST" else request.GET
    provider_id = (
        data.get("provider")
        or selection.get("provider")
        or GOOGLE_NANO_BANANA_2_PROVIDER_ID
    )
    if not wanda_media_provider(provider_id):
        return ""
    return provider_id


def _review_video_provider_id_from_request(request, selection, generation_mode=None):
    data = request.POST if request.method == "POST" else request.GET
    default_provider_id = (
        default_wanda_media_provider_id(
            media_type=GeneratedMediaJob.MEDIA_TYPE_VIDEO,
            generation_mode=generation_mode,
        )
        or RUNWAY_GEN45_VIDEO_PROVIDER_ID
    )
    provider_id = (
        data.get("provider")
        or selection.get("provider")
        or default_provider_id
    )
    provider = wanda_media_provider(provider_id)
    if not provider or provider.get("media_type") != GeneratedMediaJob.MEDIA_TYPE_VIDEO:
        return default_provider_id
    modes = provider.get("modes") or []
    if generation_mode and modes and generation_mode not in modes:
        return default_provider_id
    return provider_id


def _video_mode_from_request(request):
    data = request.POST if request.method == "POST" else request.GET
    video_mode = data.get("video_mode") or GeneratedMediaJob.MODE_VIDEO_IMAGE
    if video_mode not in {
        GeneratedMediaJob.MODE_VIDEO_IMAGE,
        GeneratedMediaJob.MODE_VIDEO_TEXT,
    }:
        return GeneratedMediaJob.MODE_VIDEO_IMAGE
    return video_mode


def _effective_reference_limit(provider_id, requested_limit=None):
    provider = wanda_media_provider(provider_id)
    provider_cap = (
        provider.get("max_reference_assets")
        if provider
        else None
    )

    if requested_limit:
        try:
            requested_limit = int(requested_limit)
        except (TypeError, ValueError):
            requested_limit = None

    if provider_cap and not requested_limit:
        return provider_cap

    if provider_cap and requested_limit:
        return min(requested_limit, provider_cap)

    return requested_limit


def _committed_scene_rows_with_media_jobs(committed_scenes):
    scenes = list(committed_scenes)
    jobs_by_scene_id = {scene.id: [] for scene in scenes}
    contributions_by_scene_id = {scene.id: [] for scene in scenes}

    if scenes:
        scene_ids = [scene.id for scene in scenes]
        media_jobs = (
            GeneratedMediaJob.objects
            .filter(source_scene_id__in=scene_ids)
            .select_related("target_character", "visual_identity_version")
            .prefetch_related(
                "subjects__character",
                "subjects__visual_identity_version",
            )
            .order_by("-created_at")
        )

        for job in media_jobs:
            jobs_by_scene_id.setdefault(job.source_scene_id, []).append(job)

        character_scenes = (
            CharacterScene.objects
            .filter(world=scenes[0].world, source_scene_id__in=scene_ids)
            .select_related("character")
            .order_by("source_scene__turn_number", "character__name")
        )

        for character_scene in character_scenes:
            contribution = character_scene.scene_contribution_json or {}
            if not _has_character_contribution_content(contribution):
                continue

            contributions_by_scene_id.setdefault(
                character_scene.source_scene_id,
                [],
            ).append({
                "character": character_scene.character,
                "slug": character_scene.character.slug,
                "name": character_scene.character.name,
                "contribution": contribution,
            })

    return [
        {
            "scene": scene,
            "media_jobs": jobs_by_scene_id.get(scene.id, []),
            "character_contributions": contributions_by_scene_id.get(
                scene.id,
                [],
            ),
        }
        for scene in scenes
    ]


def _has_character_contribution_content(contribution):
    if not isinstance(contribution, dict):
        return False

    nested_contribution = contribution.get("scene_contribution")
    if (
        isinstance(nested_contribution, dict)
        and _has_character_contribution_content(nested_contribution)
    ):
        return True

    content_fields = {
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
        "body_motion",
        "observed_focus",
        "beliefs_in_play",
        "memory_pressures",
        "proposed_effects",
        "authored_intent",
        "current_turn_reflection",
    }

    for field in content_fields:
        value = contribution.get(field)
        if isinstance(value, (list, dict)) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
        if value and not isinstance(value, (list, dict, str)):
            return True

    return False


def wanda_visual_identity_page(request):
    worlds, active_world = _active_world_or_first()

    if not active_world:
        return render(request, "story/wanda_visual_identity_page.html", {
            "worlds": [],
            "active_world": None,
            "characters": [],
            "selected_character": None,
            "visual_identity": None,
            "identity_form": None,
            "reference_form": None,
            "references": [],
            "versions": [],
            "visual_packet": {},
            "visual_registry": {},
            "visual_prompt_packet": {},
            "visual_template_source": None,
            "error": "No worlds exist yet. Create one from the New World page.",
        })

    characters = list(
        Character.objects
        .filter(world=active_world, is_active=True)
        .select_related("visual_identity")
        .order_by("name")
    )
    selected_slug = _clean_slug(
        request.POST.get("character_slug")
        or request.GET.get("character")
    )
    selected_character = None

    if selected_slug:
        selected_character = next(
            (
                character for character in characters
                if character.slug == selected_slug
            ),
            None,
        )

    if selected_character is None and characters:
        selected_character = characters[0]

    visual_identity = None
    identity_form = None
    reference_form = CharacterVisualReferenceForm()
    selected_version = None
    requested_version_number = (
        request.POST.get("version_number")
        or request.GET.get("version")
    )
    versions = []
    visual_template_source = None

    if selected_character:
        visual_identity = (
            CharacterVisualIdentity.objects
            .filter(character=selected_character)
            .first()
        )

        if visual_identity:
            selected_version = (
                visual_identity_version_by_number(
                    visual_identity,
                    requested_version_number,
                )
                or current_visual_identity_version(visual_identity)
            )

    if request.method == "POST" and selected_character:
        form_action = request.POST.get("form_action", "")

        if form_action == "save_identity":
            visual_identity = get_or_create_character_visual_identity(
                selected_character
            )
            selected_version = (
                visual_identity_version_by_number(
                    visual_identity,
                    request.POST.get("version_number"),
                )
                or current_visual_identity_version(visual_identity)
            )
            identity_form = CharacterVisualIdentityForm(
                request.POST,
                instance=visual_identity,
            )

            if identity_form.is_valid():
                visual_identity = identity_form.save(commit=False)
                visual_identity.world = active_world
                visual_identity.character = selected_character
                if selected_version:
                    visual_identity.is_locked = selected_version.is_locked
                visual_identity.save()

                change_reason = (
                    identity_form.cleaned_data.get("change_reason")
                    or "Manual visual identity edit."
                )
                if selected_version:
                    version = save_visual_identity_version_from_identity(
                        visual_identity,
                        selected_version,
                        change_reason=change_reason,
                    )
                else:
                    version = create_visual_identity_version_snapshot(
                        visual_identity,
                        change_reason=change_reason,
                    )
                messages.success(
                    request,
                    (
                        f"Saved {selected_character.name}'s visual identity "
                        f"version {version.version_number}."
                        if version
                        else f"Saved {selected_character.name}'s visual identity."
                    ),
                )
                return _wanda_visual_identity_redirect(
                    selected_character,
                    version.version_number if version else None,
                )

        elif form_action == "create_version":
            visual_identity = get_or_create_character_visual_identity(
                selected_character
            )
            selected_version = (
                visual_identity_version_by_number(
                    visual_identity,
                    request.POST.get("version_number"),
                )
                or current_visual_identity_version(visual_identity)
            )

            if selected_version:
                copy_visual_version_to_identity(visual_identity, selected_version)
                visual_identity.save()

            version = create_visual_identity_version_snapshot(
                visual_identity,
                change_reason=(
                    f"Created from version {selected_version.version_number}."
                    if selected_version
                    else "Initial visual identity version."
                ),
            )
            messages.success(
                request,
                (
                    f"Created {selected_character.name}'s visual identity "
                    f"version {version.version_number}."
                ),
            )
            return _wanda_visual_identity_redirect(
                selected_character,
                version.version_number,
            )

        elif form_action == "upload_reference":
            visual_identity = get_or_create_character_visual_identity(
                selected_character
            )
            selected_version = (
                visual_identity_version_by_number(
                    visual_identity,
                    request.POST.get("version_number"),
                )
                or current_visual_identity_version(visual_identity)
            )
            reference_form = CharacterVisualReferenceForm(
                request.POST,
                request.FILES,
            )

            if reference_form.is_valid():
                if not selected_version:
                    selected_version = create_visual_identity_version_snapshot(
                        visual_identity,
                        change_reason=(
                            "Initial visual identity snapshot for reference upload."
                        ),
                    )

                reference = reference_form.save(commit=False)
                reference.world = active_world
                reference.character = selected_character
                reference.visual_identity = visual_identity
                reference.identity_version = selected_version

                if reference.is_primary:
                    CharacterVisualReference.objects.filter(
                        character=selected_character,
                        identity_version=selected_version,
                        is_primary=True,
                    ).update(is_primary=False)

                reference.save()
                messages.success(
                    request,
                    (
                        f"Added a visual reference for {selected_character.name} "
                        f"version {selected_version.version_number}."
                    ),
                )
                return _wanda_visual_identity_redirect(
                    selected_character,
                    selected_version.version_number if selected_version else None,
                )

        elif form_action == "delete_reference":
            reference_id = request.POST.get("reference_id")
            visual_identity = get_or_create_character_visual_identity(
                selected_character
            )
            selected_version = (
                visual_identity_version_by_number(
                    visual_identity,
                    request.POST.get("version_number"),
                )
                or current_visual_identity_version(visual_identity)
            )
            reference = get_object_or_404(
                CharacterVisualReference,
                id=reference_id,
                character=selected_character,
                visual_identity=visual_identity,
            )
            redirect_version = (
                reference.identity_version.version_number
                if reference.identity_version_id
                else selected_version.version_number if selected_version else None
            )

            if reference.file:
                reference.file.delete(save=False)
            reference.delete()
            messages.success(
                request,
                f"Deleted a visual reference for {selected_character.name}.",
            )
            return _wanda_visual_identity_redirect(
                selected_character,
                redirect_version,
            )

        else:
            messages.error(request, "That Wanda visual identity action is not valid.")
            return _wanda_visual_identity_redirect(
                selected_character,
                selected_version.version_number if selected_version else None,
            )

    if selected_character and identity_form is None:
        visual_identity_for_form = visual_identity
        if visual_identity and selected_version:
            visual_identity_for_form = copy_visual_version_to_identity(
                visual_identity,
                selected_version,
            )
        if not visual_identity_for_form:
            visual_identity_for_form = (
                _mallory_visual_identity_template_for_character(
                    active_world,
                    selected_character,
                )
            )
            if visual_identity_for_form:
                visual_template_source = "Mallory"
        if not visual_identity_for_form:
            visual_identity_for_form = CharacterVisualIdentity(
                world=active_world,
                character=selected_character,
            )
        identity_form = CharacterVisualIdentityForm(
            instance=visual_identity_for_form,
        )

    references = []
    if visual_identity and visual_identity.pk:
        versions = list(
            visual_identity.versions
            .order_by("-version_number")[:24]
        )
        if not selected_version:
            selected_version = current_visual_identity_version(visual_identity)

        references = list(
            visual_identity.references
            .filter(identity_version=selected_version)
            .select_related("identity_version")
            .order_by("-is_primary", "-created_at")
        )

    selected_slug_list = (
        [selected_character.slug]
        if selected_character and selected_character.slug
        else []
    )
    selected_version_numbers_by_slug = (
        {
            selected_character.slug: selected_version.version_number,
        }
        if selected_character and selected_version
        else {}
    )
    visual_registry = build_visual_identity_registry(
        active_world,
        slugs=selected_slug_list or None,
        version_numbers_by_slug=selected_version_numbers_by_slug,
    )
    visual_packet = (
        character_visual_identity_packet(
            selected_character,
            version_number=(
                selected_version.version_number if selected_version else None
            ),
        )
        if selected_character
        else {}
    )
    visual_prompt_packet = build_visual_generation_prompt_packet(
        active_world,
        selected_slug_list,
        scene_description=(
            "Future scene image/video prompt preview. No provider call is made."
        ),
        version_numbers_by_slug=selected_version_numbers_by_slug,
    )

    return render(request, "story/wanda_visual_identity_page.html", {
        "worlds": worlds,
        "active_world": active_world,
        "characters": characters,
        "selected_character": selected_character,
        "visual_identity": visual_identity,
        "selected_version": selected_version,
        "identity_form": identity_form,
        "reference_form": reference_form,
        "references": references,
        "versions": versions,
        "visual_packet": visual_packet,
        "visual_registry": visual_registry,
        "visual_prompt_packet": visual_prompt_packet,
        "visual_template_source": visual_template_source,
    })


def _uploaded_image_files_from_request(request):
    uploaded_files = list(request.FILES.getlist("reference_files"))
    valid_files = []
    invalid_names = []

    for uploaded_file in uploaded_files:
        content_type = str(getattr(uploaded_file, "content_type", "") or "")
        if content_type and not content_type.startswith("image/"):
            invalid_names.append(getattr(uploaded_file, "name", "uploaded file"))
            continue
        valid_files.append(uploaded_file)

    return valid_files, invalid_names


def _editable_general_image_media_job_or_redirect(request, active_world, job_id):
    if not job_id:
        return None

    edit_job = get_object_or_404(
        GeneratedMediaJob.objects
        .filter(
            world=active_world,
            generation_mode=GeneratedMediaJob.MODE_GENERAL_IMAGE,
        )
        .prefetch_related("reference_uploads"),
        id=job_id,
    )
    if edit_job.status in {
        GeneratedMediaJob.STATUS_COMPLETED,
        GeneratedMediaJob.STATUS_QUEUED,
    }:
        messages.error(
            request,
            "Only failed, canceled, draft, or ready general-image jobs can be edited.",
        )
        return None

    return edit_job


def _general_image_negative_prompt_for_editor(job):
    prompt_packet = job.prompt_packet_json or {}
    if "freeform_negative_prompt" in prompt_packet:
        return prompt_packet.get("freeform_negative_prompt") or ""

    negative_prompt = job.negative_prompt or ""
    visual_style = prompt_packet.get("visual_style") or {}
    style_negative = (visual_style.get("negative_instruction") or "").strip()
    if style_negative and negative_prompt.strip().endswith(style_negative):
        return negative_prompt.strip()[:-len(style_negative)].strip()
    return negative_prompt


def _general_image_job_edit_values(job):
    prompt_packet = job.prompt_packet_json or {}
    visual_style = prompt_packet.get("visual_style") or {}

    return {
        "provider": job.provider or prompt_packet.get("provider") or "",
        "style_mode": (
            visual_style.get("mode")
            or prompt_packet.get("style_mode")
            or MEDIA_STYLE_MATCH_REFERENCE
        ),
        "custom_style_prompt": visual_style.get("custom_style_prompt") or "",
        "reference_asset_limit": prompt_packet.get("reference_asset_limit"),
        "prompt": prompt_packet.get("freeform_prompt") or job.prompt or "",
        "negative_prompt": _general_image_negative_prompt_for_editor(job),
        "user_prompt_override": job.user_prompt_override or "",
        "title": job.title or "",
    }


def review_general_image_media_job(request, job_id=None):
    worlds, active_world = _active_world_or_first()

    if not active_world:
        return render(request, "story/wanda_general_image_job_review.html", {
            "worlds": [],
            "active_world": None,
            "review_form": None,
            "prompt_packet": {},
            "provider_reference_cap": None,
            "uploaded_reference_count": 0,
            "error": "No worlds exist yet. Create one from the New World page.",
        })

    edit_job = _editable_general_image_media_job_or_redirect(
        request,
        active_world,
        job_id,
    )
    if job_id and not edit_job:
        return redirect("wanda_media_jobs")

    edit_values = _general_image_job_edit_values(edit_job) if edit_job else {}
    existing_references = (
        list(edit_job.reference_uploads.order_by("ordering", "id"))
        if edit_job
        else []
    )
    existing_reference_count = len(existing_references)

    selection = {
        "provider": (
            edit_values.get("provider")
            or
            default_wanda_media_provider_id(
                media_type=GeneratedMediaJob.MEDIA_TYPE_PHOTO,
            )
            or GOOGLE_NANO_BANANA_2_PROVIDER_ID
        ),
        "style_mode": (
            edit_values.get("style_mode")
            or MEDIA_STYLE_MATCH_REFERENCE
        ),
        "custom_style_prompt": edit_values.get("custom_style_prompt") or "",
        "user_prompt_override": edit_values.get("user_prompt_override") or "",
    }
    provider_id = _review_provider_id_from_request(request, selection)
    provider_reference_cap = _effective_reference_limit(provider_id)
    uploaded_reference_count = 0

    if request.method == "POST":
        review_form = GeneratedGeneralImageMediaJobReviewForm(request.POST)
        uploaded_reference_files, invalid_file_names = (
            _uploaded_image_files_from_request(request)
        )
        uploaded_reference_count = len(uploaded_reference_files)
        total_reference_count = existing_reference_count + uploaded_reference_count
        form_is_valid = review_form.is_valid()
        reference_asset_limit = provider_reference_cap

        if form_is_valid:
            provider_id = review_form.cleaned_data.get("provider") or provider_id
            provider = wanda_media_provider(provider_id)
            provider_reference_cap = _effective_reference_limit(provider_id)
            reference_asset_limit = (
                review_form.cleaned_data.get("reference_asset_limit")
                or provider_reference_cap
            )
            provider_cap = provider.get("max_reference_assets") if provider else None

            if (
                provider_cap
                and reference_asset_limit
                and reference_asset_limit > provider_cap
            ):
                review_form.add_error(
                    "reference_asset_limit",
                    (
                        f"{provider['label']} can use up to {provider_cap} "
                        "reference asset(s)."
                    ),
                )
                reference_asset_limit = provider_cap

            if (
                reference_asset_limit
                and total_reference_count > reference_asset_limit
            ):
                review_form.add_error(
                    "reference_asset_limit",
                    (
                        f"This job is limited to {reference_asset_limit} "
                        f"reference image(s), but it would have "
                        f"{total_reference_count} attached reference image(s)."
                    ),
                )

            if invalid_file_names:
                review_form.add_error(
                    None,
                    (
                        "Only image reference files are supported. Skipped: "
                        + ", ".join(invalid_file_names)
                    ),
                )

        prompt_packet = build_general_image_media_prompt_packet(
            active_world,
            provider=review_form.data.get("provider") or provider_id,
            user_prompt_override=request.POST.get("user_prompt_override") or "",
            reference_asset_limit=(
                reference_asset_limit if form_is_valid else provider_reference_cap
            ),
            style_mode=(
                request.POST.get("style_mode")
                or MEDIA_STYLE_MATCH_REFERENCE
            ),
            custom_style_prompt=request.POST.get("custom_style_prompt") or "",
            title=request.POST.get("title") or "",
            prompt=request.POST.get("prompt") or "",
            negative_prompt=request.POST.get("negative_prompt") or "",
            reference_count=total_reference_count,
        )

        if form_is_valid and not review_form.errors:
            if edit_job:
                job = update_general_image_media_job(
                    edit_job,
                    provider=review_form.cleaned_data.get("provider") or "",
                    user_prompt_override=(
                        review_form.cleaned_data.get("user_prompt_override") or ""
                    ),
                    reference_asset_limit=reference_asset_limit,
                    style_mode=(
                        review_form.cleaned_data.get("style_mode")
                        or MEDIA_STYLE_MATCH_REFERENCE
                    ),
                    custom_style_prompt=(
                        review_form.cleaned_data.get("custom_style_prompt") or ""
                    ),
                    title=review_form.cleaned_data.get("title") or "",
                    prompt=review_form.cleaned_data.get("prompt") or "",
                    negative_prompt=(
                        review_form.cleaned_data.get("negative_prompt") or ""
                    ),
                    uploaded_reference_files=uploaded_reference_files,
                    status=GeneratedMediaJob.STATUS_READY_FOR_PROVIDER,
                )
                messages.success(
                    request,
                    f"Updated general image media job #{job.id}.",
                )
            else:
                job = create_general_image_media_job(
                    active_world,
                    provider=review_form.cleaned_data.get("provider") or "",
                    user_prompt_override=(
                        review_form.cleaned_data.get("user_prompt_override") or ""
                    ),
                    reference_asset_limit=reference_asset_limit,
                    style_mode=(
                        review_form.cleaned_data.get("style_mode")
                        or MEDIA_STYLE_MATCH_REFERENCE
                    ),
                    custom_style_prompt=(
                        review_form.cleaned_data.get("custom_style_prompt") or ""
                    ),
                    title=review_form.cleaned_data.get("title") or "",
                    prompt=review_form.cleaned_data.get("prompt") or "",
                    negative_prompt=(
                        review_form.cleaned_data.get("negative_prompt") or ""
                    ),
                    uploaded_reference_files=uploaded_reference_files,
                    status=GeneratedMediaJob.STATUS_READY_FOR_PROVIDER,
                )
                messages.success(
                    request,
                    f"Created general image media job #{job.id}.",
                )
            return redirect(_wanda_media_jobs_url(job))
    else:
        prompt_packet = build_general_image_media_prompt_packet(
            active_world,
            provider=provider_id,
            reference_asset_limit=(
                edit_values.get("reference_asset_limit")
                or provider_reference_cap
            ),
            style_mode=edit_values.get("style_mode") or MEDIA_STYLE_MATCH_REFERENCE,
            custom_style_prompt=edit_values.get("custom_style_prompt") or "",
            title=edit_values.get("title") or "",
            prompt=edit_values.get("prompt") or "",
            negative_prompt=edit_values.get("negative_prompt") or "",
            user_prompt_override=edit_values.get("user_prompt_override") or "",
            reference_count=existing_reference_count,
        )
        review_form = GeneratedGeneralImageMediaJobReviewForm(initial={
            "title": edit_values.get("title") or prompt_packet["title"],
            "provider": provider_id,
            "style_mode": edit_values.get("style_mode") or MEDIA_STYLE_MATCH_REFERENCE,
            "custom_style_prompt": edit_values.get("custom_style_prompt") or "",
            "prompt": edit_values.get("prompt") or "",
            "negative_prompt": edit_values.get("negative_prompt") or "",
            "user_prompt_override": edit_values.get("user_prompt_override") or "",
            "reference_asset_limit": (
                edit_values.get("reference_asset_limit")
                or provider_reference_cap
            ),
            "original_prompt": prompt_packet["prompt"],
            "original_negative_prompt": prompt_packet["negative_prompt"],
        })

    return render(request, "story/wanda_general_image_job_review.html", {
        "worlds": worlds,
        "active_world": active_world,
        "review_form": review_form,
        "prompt_packet": prompt_packet.get("prompt_packet", {}),
        "provider_reference_cap": provider_reference_cap,
        "uploaded_reference_count": uploaded_reference_count,
        "existing_references": existing_references,
        "existing_reference_count": existing_reference_count,
        "edit_job": edit_job,
        "error": None,
    })


def review_portrait_media_job(request):
    worlds, active_world = _active_world_or_first()

    if not active_world:
        return render(request, "story/wanda_media_job_review.html", {
            "worlds": [],
            "active_world": None,
            "selection": {},
            "review_form": None,
            "prompt_packet": {},
            "error": "No worlds exist yet. Create one from the New World page.",
        })

    selection = _portrait_media_selection_from_request(request, active_world)
    selected_character = selection["selected_character"]
    selected_version = selection["selected_version"]
    source_scene = selection["source_scene"]
    provider_id = _review_provider_id_from_request(request, selection)
    provider_reference_cap = _effective_reference_limit(provider_id)
    selection["provider"] = provider_id

    if not selected_character or not selected_version:
        messages.error(
            request,
            "Choose a character with a saved visual identity version first.",
        )
        return redirect("wanda_visual_identities")

    if (
        selection["source"] == GeneratedMediaJob.SOURCE_APPROVED_SCENE
        and not source_scene
    ):
        messages.error(
            request,
            "Choose an approved scene before creating a scene mood portrait.",
        )
        return redirect("scene_page")

    selected_reference_ids = None
    primary_reference_id = None
    reference_asset_limit = None
    style_mode = selection["style_mode"]
    custom_style_prompt = selection["custom_style_prompt"]

    if request.method == "POST":
        review_form = GeneratedMediaJobReviewForm(request.POST)
        selected_reference_ids = request.POST.get("selected_reference_ids", "")
        primary_reference_id = request.POST.get("primary_reference_id", "")

        form_is_valid = review_form.is_valid()
        if form_is_valid:
            reference_asset_limit = (
                review_form.cleaned_data.get("reference_asset_limit")
            )
            style_mode = (
                review_form.cleaned_data.get("style_mode")
                or MEDIA_STYLE_MATCH_REFERENCE
            )
            custom_style_prompt = (
                review_form.cleaned_data.get("custom_style_prompt")
                or ""
            )
            provider = wanda_media_provider(
                review_form.cleaned_data.get("provider") or ""
            )
            provider_cap = (
                provider.get("max_reference_assets")
                if provider
                else None
            )
            if provider_cap and not reference_asset_limit:
                reference_asset_limit = provider_cap
            if (
                provider_cap
                and reference_asset_limit
                and reference_asset_limit > provider_cap
            ):
                review_form.add_error(
                    "reference_asset_limit",
                    (
                        f"{provider['label']} can use up to {provider_cap} "
                        "reference asset(s)."
                    ),
                )
                reference_asset_limit = provider_cap

        prompt_packet = build_portrait_media_prompt_packet(
            active_world,
            selected_character,
            visual_identity_version=selected_version,
            source=selection["source"],
            source_scene=source_scene,
            provider=review_form.data.get("provider") or "",
            style_mode=style_mode,
            custom_style_prompt=custom_style_prompt,
            user_prompt_override=request.POST.get("user_prompt_override") or "",
            selected_reference_ids=selected_reference_ids,
            primary_reference_id=primary_reference_id,
            reference_asset_limit=(
                reference_asset_limit if form_is_valid else provider_reference_cap
            ),
        )

        if form_is_valid and not review_form.errors:
            valid_reference_ids = _available_reference_ids(
                prompt_packet.get("prompt_packet", {})
            )
            selected_ids = [
                reference_id
                for reference_id in _parse_reference_id_csv(selected_reference_ids)
                if reference_id in valid_reference_ids
            ]

            if reference_asset_limit and len(selected_ids) > reference_asset_limit:
                review_form.add_error(
                    "reference_asset_limit",
                    (
                        f"This job is limited to {reference_asset_limit} "
                        f"reference asset(s), but {len(selected_ids)} are selected."
                    ),
                )

        if form_is_valid and not review_form.errors:
            posted_prompt = review_form.cleaned_data.get("prompt") or ""
            posted_negative_prompt = (
                review_form.cleaned_data.get("negative_prompt") or ""
            )
            original_prompt = (
                review_form.cleaned_data.get("original_prompt") or ""
            )
            original_negative_prompt = (
                review_form.cleaned_data.get("original_negative_prompt") or ""
            )
            final_prompt = (
                posted_prompt
                if _prompt_text_was_manually_edited(
                    posted_prompt,
                    original_prompt,
                )
                else prompt_packet["prompt"]
            )
            final_negative_prompt = (
                posted_negative_prompt
                if _prompt_text_was_manually_edited(
                    posted_negative_prompt,
                    original_negative_prompt,
                )
                else prompt_packet["negative_prompt"]
            )
            user_prompt_override = (
                review_form.cleaned_data.get("user_prompt_override")
                or ""
            )
            final_prompt = _prompt_with_user_override(
                final_prompt,
                user_prompt_override,
            )
            job = create_portrait_media_job(
                active_world,
                selected_character,
                visual_identity_version=selected_version,
                source=selection["source"],
                source_scene=source_scene,
                provider=review_form.cleaned_data.get("provider") or "",
                user_prompt_override=user_prompt_override,
                style_mode=style_mode,
                custom_style_prompt=custom_style_prompt,
                title=review_form.cleaned_data.get("title") or "",
                prompt=final_prompt,
                negative_prompt=final_negative_prompt,
                selected_reference_ids=selected_reference_ids,
                primary_reference_id=primary_reference_id,
                reference_asset_limit=reference_asset_limit,
                status=GeneratedMediaJob.STATUS_READY_FOR_PROVIDER,
            )
            messages.success(
                request,
                (
                    f"Created portrait media job #{job.id} for "
                    f"{selected_character.name}."
                ),
            )
            return redirect(_wanda_media_jobs_url(job))
    else:
        prompt_packet = build_portrait_media_prompt_packet(
            active_world,
            selected_character,
            visual_identity_version=selected_version,
            source=selection["source"],
            source_scene=source_scene,
            provider=provider_id,
            style_mode=style_mode,
            custom_style_prompt=custom_style_prompt,
            user_prompt_override=selection["user_prompt_override"],
            reference_asset_limit=provider_reference_cap,
        )
        selected_reference_ids = _selected_reference_csv_from_packet(
            prompt_packet["prompt_packet"]
        )
        primary_reference_id = _primary_reference_id_from_packet(
            prompt_packet["prompt_packet"]
        )
        review_form = GeneratedMediaJobReviewForm(initial={
            "title": prompt_packet["title"],
            "provider": provider_id,
            "style_mode": style_mode,
            "custom_style_prompt": custom_style_prompt,
            "prompt": prompt_packet["prompt"],
            "negative_prompt": prompt_packet["negative_prompt"],
            "user_prompt_override": selection["user_prompt_override"],
            "selected_reference_ids": selected_reference_ids,
            "primary_reference_id": primary_reference_id,
            "reference_asset_limit": provider_reference_cap,
            "original_prompt": prompt_packet["prompt"],
            "original_negative_prompt": prompt_packet["negative_prompt"],
        })

    reference_options = _reference_options_from_prompt_packet(
        prompt_packet.get("prompt_packet", {}),
        selected_reference_ids=selected_reference_ids,
    )

    return render(request, "story/wanda_media_job_review.html", {
        "worlds": worlds,
        "active_world": active_world,
        "selection": selection,
        "review_form": review_form,
        "prompt_packet": prompt_packet.get("prompt_packet", {}),
        "reference_options": reference_options,
        "selected_reference_ids": selected_reference_ids or "",
        "primary_reference_id": primary_reference_id or "",
        "error": None,
    })


def review_scene_image_media_job(request, job_id=None):
    worlds, active_world = _active_world_or_first()

    if not active_world:
        return render(request, "story/wanda_scene_image_job_review.html", {
            "worlds": [],
            "active_world": None,
            "scene": None,
            "review_form": None,
            "prompt_packet": {},
            "subject_options": [],
            "edit_job": None,
            "error": "No worlds exist yet. Create one from the New World page.",
        })

    edit_job = None
    edit_values = {}
    if job_id:
        edit_job = get_object_or_404(
            GeneratedMediaJob.objects
            .select_related("source_scene")
            .prefetch_related("subjects__character"),
            id=job_id,
            world=active_world,
            generation_mode=GeneratedMediaJob.MODE_SCENE_IMAGE,
        )
        if edit_job.status in {
            GeneratedMediaJob.STATUS_COMPLETED,
            GeneratedMediaJob.STATUS_QUEUED,
        }:
            messages.error(
                request,
                "Only failed, canceled, draft, or ready scene-image jobs can be edited.",
            )
            return redirect(_wanda_media_jobs_url(edit_job))
        edit_values = _scene_image_job_edit_values(edit_job)

    data = request.POST if request.method == "POST" else request.GET
    scene_id = (
        data.get("scene_id")
        or data.get("source_scene_id")
        or (edit_job.source_scene_id if edit_job else None)
    )
    scene = get_object_or_404(
        CommittedScene,
        id=scene_id,
        world=active_world,
    )
    provider_id = (
        data.get("provider")
        or edit_values.get("provider")
        or GOOGLE_NANO_BANANA_2_PROVIDER_ID
    )
    if not wanda_media_provider(provider_id):
        provider_id = GOOGLE_NANO_BANANA_2_PROVIDER_ID
    provider_reference_cap = _effective_reference_limit(provider_id)
    style_mode = (
        data.get("style_mode")
        or edit_values.get("style_mode")
        or MEDIA_STYLE_MATCH_REFERENCE
    )
    custom_style_prompt = (
        data.get("custom_style_prompt")
        or edit_values.get("custom_style_prompt")
        or ""
    )
    subject_slugs = ",".join(
        _detect_scene_subject_slugs(
            active_world,
            scene,
            data.get("subject_slugs")
            or edit_values.get("subject_slugs")
            or "",
        )
    )
    scene_excerpt = (
        data.get("scene_excerpt")
        or edit_values.get("scene_excerpt")
        or ""
    )
    reference_asset_limit = (
        data.get("reference_asset_limit")
        or edit_values.get("reference_asset_limit")
        or provider_reference_cap
    )
    selected_reference_ids_by_slug = (
        edit_values.get("selected_reference_ids_by_slug")
        if request.method != "POST"
        else None
    )
    primary_reference_ids_by_slug = (
        edit_values.get("primary_reference_ids_by_slug")
        if request.method != "POST"
        else None
    )
    if request.method != "POST" and not edit_job:
        selected_reference_ids_by_slug = _empty_scene_image_reference_selection(
            subject_slugs
        )
        primary_reference_ids_by_slug = {}

    if request.method == "POST":
        review_form = GeneratedSceneImageMediaJobReviewForm(request.POST)
        form_is_valid = review_form.is_valid()
        if form_is_valid:
            provider_id = review_form.cleaned_data.get("provider") or provider_id
            provider = wanda_media_provider(provider_id)
            style_mode = (
                review_form.cleaned_data.get("style_mode")
                or MEDIA_STYLE_MATCH_REFERENCE
            )
            custom_style_prompt = (
                review_form.cleaned_data.get("custom_style_prompt")
                or ""
            )
            scene_excerpt = review_form.cleaned_data.get("scene_excerpt") or ""
            subject_slugs = review_form.cleaned_data.get("subject_slugs") or ""
            (
                selected_reference_ids_by_slug,
                primary_reference_ids_by_slug,
            ) = _scene_image_reference_selection_from_request(
                request,
                subject_slugs,
            )
            reference_asset_limit = (
                review_form.cleaned_data.get("reference_asset_limit")
                or _effective_reference_limit(provider_id)
            )
            provider_cap = (
                provider.get("max_reference_assets")
                if provider
                else None
            )
            if (
                provider_cap
                and reference_asset_limit
                and reference_asset_limit > provider_cap
            ):
                review_form.add_error(
                    "reference_asset_limit",
                    (
                        f"{provider['label']} can use up to {provider_cap} "
                        "reference asset(s)."
                    ),
                )
                reference_asset_limit = provider_cap
            selected_reference_count = _scene_image_selected_reference_count(
                selected_reference_ids_by_slug
            )
            if (
                reference_asset_limit
                and selected_reference_count > reference_asset_limit
            ):
                review_form.add_error(
                    "reference_asset_limit",
                    (
                        f"This job is limited to {reference_asset_limit} "
                        f"reference asset(s), but {selected_reference_count} "
                        "are selected."
                    ),
                )

        packet_reference_asset_limit = (
            None
            if review_form.errors.get("reference_asset_limit")
            else reference_asset_limit
        )

        prompt_packet = build_scene_image_media_prompt_packet(
            active_world,
            scene,
            subject_slugs=subject_slugs,
            scene_excerpt=scene_excerpt,
            provider=provider_id,
            style_mode=style_mode,
            custom_style_prompt=custom_style_prompt,
            user_prompt_override=request.POST.get("user_prompt_override") or "",
            reference_asset_limit=packet_reference_asset_limit,
            selected_reference_ids_by_slug=selected_reference_ids_by_slug,
            primary_reference_ids_by_slug=primary_reference_ids_by_slug,
        )

        if form_is_valid and not review_form.errors:
            if not prompt_packet.get("visual_subjects"):
                review_form.add_error(
                    "subject_slugs",
                    "Choose at least one scene character with a saved visual identity version.",
                )

        if form_is_valid and not review_form.errors:
            posted_prompt = review_form.cleaned_data.get("prompt") or ""
            posted_negative_prompt = (
                review_form.cleaned_data.get("negative_prompt") or ""
            )
            original_prompt = review_form.cleaned_data.get("original_prompt") or ""
            original_negative_prompt = (
                review_form.cleaned_data.get("original_negative_prompt") or ""
            )
            final_prompt = (
                posted_prompt
                if _prompt_text_was_manually_edited(
                    posted_prompt,
                    original_prompt,
                )
                else prompt_packet["prompt"]
            )
            final_negative_prompt = (
                posted_negative_prompt
                if _prompt_text_was_manually_edited(
                    posted_negative_prompt,
                    original_negative_prompt,
                )
                else prompt_packet["negative_prompt"]
            )
            user_prompt_override = (
                review_form.cleaned_data.get("user_prompt_override") or ""
            )
            final_prompt = _prompt_with_user_override(
                final_prompt,
                user_prompt_override,
            )
            if edit_job:
                job = update_scene_image_media_job(
                    edit_job,
                    active_world,
                    scene,
                    subject_slugs=subject_slugs,
                    scene_excerpt=scene_excerpt,
                    provider=provider_id,
                    user_prompt_override=user_prompt_override,
                    reference_asset_limit=reference_asset_limit,
                    selected_reference_ids_by_slug=selected_reference_ids_by_slug,
                    primary_reference_ids_by_slug=primary_reference_ids_by_slug,
                    style_mode=style_mode,
                    custom_style_prompt=custom_style_prompt,
                    title=review_form.cleaned_data.get("title") or "",
                    prompt=final_prompt,
                    negative_prompt=final_negative_prompt,
                    status=GeneratedMediaJob.STATUS_READY_FOR_PROVIDER,
                )
                messages.success(
                    request,
                    f"Updated scene image media job #{job.id}.",
                )
            else:
                job = create_scene_image_media_job(
                    active_world,
                    scene,
                    subject_slugs=subject_slugs,
                    scene_excerpt=scene_excerpt,
                    provider=provider_id,
                    user_prompt_override=user_prompt_override,
                    reference_asset_limit=reference_asset_limit,
                    selected_reference_ids_by_slug=selected_reference_ids_by_slug,
                    primary_reference_ids_by_slug=primary_reference_ids_by_slug,
                    style_mode=style_mode,
                    custom_style_prompt=custom_style_prompt,
                    title=review_form.cleaned_data.get("title") or "",
                    prompt=final_prompt,
                    negative_prompt=final_negative_prompt,
                    status=GeneratedMediaJob.STATUS_READY_FOR_PROVIDER,
                )
                messages.success(
                    request,
                    f"Created scene image media job #{job.id}.",
                )
            return redirect(_wanda_media_jobs_url(job))
    else:
        edit_prompt = None
        if edit_job:
            edit_prompt = clean_scene_image_prompt_for_editor(edit_job.prompt)

        prompt_packet = build_scene_image_media_prompt_packet(
            active_world,
            scene,
            subject_slugs=subject_slugs,
            scene_excerpt=scene_excerpt,
            provider=provider_id,
            style_mode=style_mode,
            custom_style_prompt=custom_style_prompt,
            user_prompt_override=edit_values.get("user_prompt_override") or "",
            reference_asset_limit=reference_asset_limit,
            selected_reference_ids_by_slug=selected_reference_ids_by_slug,
            primary_reference_ids_by_slug=primary_reference_ids_by_slug,
            title=edit_job.title if edit_job else "",
            prompt=edit_prompt if edit_prompt else None,
            negative_prompt=edit_job.negative_prompt if edit_job else None,
        )
        subject_slugs = ",".join(
            subject.get("slug")
            for subject in prompt_packet.get("visual_subjects", [])
            if subject.get("slug")
        ) or subject_slugs
        review_form = GeneratedSceneImageMediaJobReviewForm(initial={
            "title": prompt_packet["title"],
            "provider": provider_id,
            "style_mode": style_mode,
            "custom_style_prompt": custom_style_prompt,
            "scene_excerpt": prompt_packet["prompt_packet"].get("scene_excerpt", ""),
            "subject_slugs": subject_slugs,
            "reference_asset_limit": reference_asset_limit,
            "prompt": prompt_packet["prompt"],
            "negative_prompt": prompt_packet["negative_prompt"],
            "user_prompt_override": edit_values.get("user_prompt_override") or "",
            "original_prompt": prompt_packet["prompt"],
            "original_negative_prompt": prompt_packet["negative_prompt"],
        })

    selected_subject_slugs = [
        subject.get("slug")
        for subject in prompt_packet.get("visual_subjects", [])
        if subject.get("slug")
    ]

    return render(request, "story/wanda_scene_image_job_review.html", {
        "worlds": worlds,
        "active_world": active_world,
        "scene": scene,
        "review_form": review_form,
        "prompt_packet": prompt_packet.get("prompt_packet", {}),
        "prompt_packet_preview": _scene_image_prompt_packet_preview(
            prompt_packet.get("prompt_packet", {})
        ),
        "subject_options": _scene_subject_options(
            active_world,
            selected_subject_slugs,
        ),
        "edit_job": edit_job,
        "error": None,
    })


def review_video_media_job(request):
    worlds, active_world = _active_world_or_first()

    if not active_world:
        return render(request, "story/wanda_video_job_review.html", {
            "worlds": [],
            "active_world": None,
            "selection": {},
            "review_form": None,
            "prompt_packet": {},
            "reference_options": [],
            "error": "No worlds exist yet. Create one from the New World page.",
        })

    selection = _portrait_media_selection_from_request(request, active_world)
    selection["source"] = GeneratedMediaJob.SOURCE_WANDA_IDENTITY
    selection["source_scene"] = None
    selected_character = selection["selected_character"]
    selected_version = selection["selected_version"]
    video_mode = _video_mode_from_request(request)
    provider_id = _review_video_provider_id_from_request(
        request,
        selection,
        generation_mode=video_mode,
    )
    provider_reference_limit = _effective_reference_limit(provider_id)
    selection["provider"] = provider_id
    selection["video_mode"] = video_mode

    if not selected_character or not selected_version:
        messages.error(
            request,
            "Choose a character with a saved visual identity version first.",
        )
        return redirect("wanda_visual_identities")

    selected_reference_ids = None
    primary_reference_id = None

    if request.method == "POST":
        review_form = GeneratedVideoMediaJobReviewForm(request.POST)
        selected_reference_ids = request.POST.get("selected_reference_ids", "")
        primary_reference_id = request.POST.get("primary_reference_id", "")
        form_is_valid = review_form.is_valid()
        if form_is_valid:
            provider_id = review_form.cleaned_data.get("provider") or provider_id
            video_mode = review_form.cleaned_data.get("video_mode") or video_mode
            provider_reference_limit = _effective_reference_limit(provider_id)
            selection["provider"] = provider_id
            selection["video_mode"] = video_mode

        prompt_packet = build_video_media_prompt_packet(
            active_world,
            selected_character,
            visual_identity_version=selected_version,
            provider=provider_id,
            video_mode=video_mode,
            user_prompt_override=request.POST.get("user_prompt_override") or "",
            selected_reference_ids=selected_reference_ids,
            primary_reference_id=primary_reference_id,
            reference_asset_limit=provider_reference_limit,
        )

        if form_is_valid and not review_form.errors:
            valid_reference_ids = _available_reference_ids(
                prompt_packet.get("prompt_packet", {})
            )
            selected_ids = [
                reference_id
                for reference_id in _parse_reference_id_csv(selected_reference_ids)
                if reference_id in valid_reference_ids
            ]
            if (
                video_mode == GeneratedMediaJob.MODE_VIDEO_IMAGE
                and not selected_ids
            ):
                review_form.add_error(
                    None,
                    "Image-to-video requires one selected first-frame reference.",
                )

        if form_is_valid and not review_form.errors:
            posted_prompt = review_form.cleaned_data.get("prompt") or ""
            posted_negative_prompt = (
                review_form.cleaned_data.get("negative_prompt") or ""
            )
            original_prompt = (
                review_form.cleaned_data.get("original_prompt") or ""
            )
            original_negative_prompt = (
                review_form.cleaned_data.get("original_negative_prompt") or ""
            )
            final_prompt = (
                posted_prompt
                if _prompt_text_was_manually_edited(
                    posted_prompt,
                    original_prompt,
                )
                else prompt_packet["prompt"]
            )
            final_negative_prompt = (
                posted_negative_prompt
                if _prompt_text_was_manually_edited(
                    posted_negative_prompt,
                    original_negative_prompt,
                )
                else prompt_packet["negative_prompt"]
            )
            user_prompt_override = (
                review_form.cleaned_data.get("user_prompt_override")
                or ""
            )
            final_prompt = _prompt_with_user_override(
                final_prompt,
                user_prompt_override,
            )
            job = create_video_media_job(
                active_world,
                selected_character,
                visual_identity_version=selected_version,
                provider=provider_id,
                video_mode=video_mode,
                user_prompt_override=user_prompt_override,
                title=review_form.cleaned_data.get("title") or "",
                prompt=final_prompt,
                negative_prompt=final_negative_prompt,
                selected_reference_ids=selected_reference_ids,
                primary_reference_id=primary_reference_id,
                reference_asset_limit=provider_reference_limit,
                status=GeneratedMediaJob.STATUS_READY_FOR_PROVIDER,
            )
            messages.success(
                request,
                f"Created video media job #{job.id} for {selected_character.name}.",
            )
            return redirect(_wanda_media_jobs_url(job))
    else:
        prompt_packet = build_video_media_prompt_packet(
            active_world,
            selected_character,
            visual_identity_version=selected_version,
            provider=provider_id,
            video_mode=video_mode,
            user_prompt_override=selection["user_prompt_override"],
            reference_asset_limit=provider_reference_limit,
        )
        selected_reference_ids = _selected_reference_csv_from_packet(
            prompt_packet["prompt_packet"]
        )
        primary_reference_id = _primary_reference_id_from_packet(
            prompt_packet["prompt_packet"]
        )
        review_form = GeneratedVideoMediaJobReviewForm(initial={
            "title": prompt_packet["title"],
            "provider": provider_id,
            "video_mode": video_mode,
            "prompt": prompt_packet["prompt"],
            "negative_prompt": prompt_packet["negative_prompt"],
            "user_prompt_override": selection["user_prompt_override"],
            "selected_reference_ids": selected_reference_ids,
            "primary_reference_id": primary_reference_id,
            "original_prompt": prompt_packet["prompt"],
            "original_negative_prompt": prompt_packet["negative_prompt"],
        })

    reference_options = _reference_options_from_prompt_packet(
        prompt_packet.get("prompt_packet", {}),
        selected_reference_ids=selected_reference_ids,
    )

    return render(request, "story/wanda_video_job_review.html", {
        "worlds": worlds,
        "active_world": active_world,
        "selection": selection,
        "review_form": review_form,
        "prompt_packet": prompt_packet.get("prompt_packet", {}),
        "reference_options": reference_options,
        "selected_reference_ids": selected_reference_ids or "",
        "primary_reference_id": primary_reference_id or "",
        "provider_reference_limit": provider_reference_limit,
        "video_providers": wanda_media_providers(
            media_type=GeneratedMediaJob.MEDIA_TYPE_VIDEO,
            generation_mode=video_mode,
        ),
        "error": None,
    })


def review_asset_video_media_job(request, asset_id):
    worlds, active_world = _active_world_or_first()

    if not active_world:
        return render(request, "story/wanda_asset_video_job_review.html", {
            "worlds": [],
            "active_world": None,
            "asset": None,
            "review_form": None,
            "prompt_packet": {},
            "error": "No worlds exist yet. Create one from the New World page.",
        })

    asset = get_object_or_404(
        GeneratedMediaAsset.objects.select_related(
            "job",
            "source_scene",
            "target_character",
            "visual_identity_version",
        ),
        id=asset_id,
        world=active_world,
        media_type=GeneratedMediaJob.MEDIA_TYPE_PHOTO,
    )
    image_to_video_providers = wanda_media_providers(
        media_type=GeneratedMediaJob.MEDIA_TYPE_VIDEO,
        generation_mode=GeneratedMediaJob.MODE_VIDEO_IMAGE,
    )
    image_to_video_provider_choices = [
        (provider["id"], provider["label"])
        for provider in image_to_video_providers
    ]
    provider_id = _review_video_provider_id_from_request(
        request,
        {},
        generation_mode=GeneratedMediaJob.MODE_VIDEO_IMAGE,
    )

    if request.method == "POST":
        review_form = GeneratedVideoMediaJobReviewForm(
            request.POST,
            provider_choices=image_to_video_provider_choices,
        )
        form_is_valid = review_form.is_valid()
        if form_is_valid:
            provider_id = review_form.cleaned_data.get("provider") or provider_id
        user_prompt_override = request.POST.get("user_prompt_override") or ""
        prompt_packet = build_asset_video_media_prompt_packet(
            active_world,
            asset,
            provider=provider_id,
            user_prompt_override=user_prompt_override,
        )

        if form_is_valid and not review_form.errors:
            posted_prompt = review_form.cleaned_data.get("prompt") or ""
            posted_negative_prompt = (
                review_form.cleaned_data.get("negative_prompt") or ""
            )
            original_prompt = review_form.cleaned_data.get("original_prompt") or ""
            original_negative_prompt = (
                review_form.cleaned_data.get("original_negative_prompt") or ""
            )
            final_prompt = (
                posted_prompt
                if _prompt_text_was_manually_edited(
                    posted_prompt,
                    original_prompt,
                )
                else prompt_packet["prompt"]
            )
            final_negative_prompt = (
                posted_negative_prompt
                if _prompt_text_was_manually_edited(
                    posted_negative_prompt,
                    original_negative_prompt,
                )
                else prompt_packet["negative_prompt"]
            )
            user_prompt_override = (
                review_form.cleaned_data.get("user_prompt_override") or ""
            )
            final_prompt = _prompt_with_user_override(
                final_prompt,
                user_prompt_override,
            )
            job = create_asset_video_media_job(
                active_world,
                asset,
                provider=provider_id,
                user_prompt_override=user_prompt_override,
                title=review_form.cleaned_data.get("title") or "",
                prompt=final_prompt,
                negative_prompt=final_negative_prompt,
                status=GeneratedMediaJob.STATUS_READY_FOR_PROVIDER,
            )
            provider_label = (
                (wanda_media_provider(provider_id) or {}).get("label")
                or "selected provider"
            )
            messages.success(
                request,
                f"Created {provider_label} video job #{job.id} from asset #{asset.id}.",
            )
            return redirect(_wanda_media_jobs_url(job))
    else:
        prompt_packet = build_asset_video_media_prompt_packet(
            active_world,
            asset,
            provider=provider_id,
        )
        review_form = GeneratedVideoMediaJobReviewForm(
            provider_choices=image_to_video_provider_choices,
            initial={
                "title": prompt_packet["title"],
                "provider": provider_id,
                "video_mode": GeneratedMediaJob.MODE_VIDEO_IMAGE,
                "prompt": prompt_packet["prompt"],
                "negative_prompt": prompt_packet["negative_prompt"],
                "selected_reference_ids": "",
                "primary_reference_id": "",
                "original_prompt": prompt_packet["prompt"],
                "original_negative_prompt": prompt_packet["negative_prompt"],
            },
        )

    return render(request, "story/wanda_asset_video_job_review.html", {
        "worlds": worlds,
        "active_world": active_world,
        "asset": asset,
        "review_form": review_form,
        "prompt_packet": prompt_packet.get("prompt_packet", {}),
        "video_providers": image_to_video_providers,
        "selected_provider": wanda_media_provider(provider_id),
        "error": None,
    })


def quick_create_portrait_media_job(request):
    if request.method != "POST":
        return redirect("wanda_visual_identities")

    _, active_world = _active_world_or_first()
    if not active_world:
        messages.error(request, "No active world is available.")
        return redirect("scene_page")

    selection = _portrait_media_selection_from_request(request, active_world)
    selected_character = selection["selected_character"]
    selected_version = selection["selected_version"]
    source_scene = selection["source_scene"]

    if not selected_character or not selected_version:
        messages.error(
            request,
            "Choose a character with a saved visual identity version first.",
        )
        return redirect("wanda_visual_identities")

    if (
        selection["source"] == GeneratedMediaJob.SOURCE_APPROVED_SCENE
        and not source_scene
    ):
        messages.error(
            request,
            "Choose an approved scene before creating a scene mood portrait.",
        )
        return redirect("scene_page")

    job = create_portrait_media_job(
        active_world,
        selected_character,
        visual_identity_version=selected_version,
        source=selection["source"],
        source_scene=source_scene,
        provider=selection["provider"],
        style_mode=selection["style_mode"],
        custom_style_prompt=selection["custom_style_prompt"],
        user_prompt_override=selection["user_prompt_override"],
        status=GeneratedMediaJob.STATUS_READY_FOR_PROVIDER,
    )
    messages.success(
        request,
        f"Quick-created portrait media job #{job.id} for {selected_character.name}.",
    )
    return redirect(_wanda_media_jobs_url(job))


def _scene_image_subject_slugs_from_job(job):
    slugs = [
        subject.character.slug
        for subject in job.subjects.select_related("character").all()
        if subject.character_id and subject.character.slug
    ]
    if slugs:
        return ",".join(slugs)

    return ",".join(
        subject.get("slug")
        for subject in (job.prompt_packet_json or {}).get("visual_subjects", [])
        if subject.get("slug")
    )


def _scene_image_reference_maps_from_job(job):
    selected_by_slug = {}
    primary_by_slug = {}

    for subject in job.subjects.select_related("character").all():
        if not subject.character_id or not subject.character.slug:
            continue
        slug = subject.character.slug
        selected_by_slug[slug] = [
            str(reference_id)
            for reference_id in subject.selected_reference_ids_json or []
            if reference_id is not None
        ]
        if subject.primary_reference_id:
            primary_by_slug[slug] = str(subject.primary_reference_id)

    if selected_by_slug:
        return selected_by_slug, primary_by_slug

    for subject in (job.prompt_packet_json or {}).get("visual_subjects", []):
        slug = subject.get("slug")
        if not slug:
            continue
        selected_references = subject.get("selected_references") or []
        selected_by_slug[slug] = [
            str(reference.get("id"))
            for reference in selected_references
            if reference.get("id") is not None
        ]
        primary = next(
            (
                reference
                for reference in selected_references
                if reference.get("is_subject_primary")
            ),
            selected_references[0] if selected_references else None,
        )
        if primary and primary.get("id") is not None:
            primary_by_slug[slug] = str(primary["id"])

    return selected_by_slug, primary_by_slug


def _scene_image_job_edit_values(job):
    prompt_packet = job.prompt_packet_json or {}
    visual_style = prompt_packet.get("visual_style") or {}
    selected_by_slug, primary_by_slug = _scene_image_reference_maps_from_job(job)

    return {
        "provider": job.provider or prompt_packet.get("provider") or "",
        "style_mode": (
            visual_style.get("mode")
            or prompt_packet.get("style_mode")
            or MEDIA_STYLE_MATCH_REFERENCE
        ),
        "custom_style_prompt": visual_style.get("custom_style_prompt") or "",
        "scene_excerpt": prompt_packet.get("scene_excerpt") or "",
        "subject_slugs": _scene_image_subject_slugs_from_job(job),
        "reference_asset_limit": prompt_packet.get("reference_asset_limit"),
        "selected_reference_ids_by_slug": selected_by_slug,
        "primary_reference_ids_by_slug": primary_by_slug,
        "user_prompt_override": job.user_prompt_override or "",
    }


def wanda_media_jobs_page(request):
    worlds, active_world = _active_world_or_first()

    if not active_world:
        return render(request, "story/wanda_media_jobs_page.html", {
            "worlds": [],
            "active_world": None,
            "jobs": [],
            "assets": [],
            "asset_form": None,
            "focused_job_id": None,
            "focused_asset_id": None,
            "error": "No worlds exist yet. Create one from the New World page.",
        })

    focused_job_id = request.GET.get("job")
    focused_asset_id = request.GET.get("asset")
    assets = list(
        GeneratedMediaAsset.objects
        .filter(world=active_world)
        .select_related(
            "job",
            "source_scene",
            "target_character",
            "visual_identity_version",
        )
        .prefetch_related("job__subjects__character")
        .order_by("-created_at")[:80]
    )
    for asset in assets:
        asset.saved_visual_reference = _generated_asset_reference(asset)

    jobs = list(
        GeneratedMediaJob.objects
        .filter(world=active_world)
        .exclude(status=GeneratedMediaJob.STATUS_COMPLETED)
        .select_related(
            "source_scene",
            "target_character",
            "visual_identity_version",
        )
        .prefetch_related(
            "assets",
            "reference_uploads",
            "subjects__character",
            "subjects__visual_identity_version",
        )
        .order_by("-created_at")[:80]
    )
    for job in jobs:
        job.provider_actions = media_provider_actions_for_job(job)
        job.local_runner_state = media_job_local_runner_state(job)
        job.local_runner_phase = job.local_runner_state.get("phase", "")
        job.provider_task_id = media_job_provider_task_id(job)
        job.can_check_provider_status = (
            job.provider == RUNWAY_GEN45_VIDEO_PROVIDER_ID
            and job.status == GeneratedMediaJob.STATUS_QUEUED
            and bool(job.provider_task_id)
        )
        job.can_restart_background_generation = (
            media_job_can_restart_background_generation(job)
        )
        job.is_local_background_generation = (
            job.status == GeneratedMediaJob.STATUS_QUEUED
            and bool(job.local_runner_state)
            and not job.provider_task_id
        )
        job.subject_names = [
            subject.character.name
            for subject in job.subjects.all()
            if subject.character_id
        ]

    return render(request, "story/wanda_media_jobs_page.html", {
        "worlds": worlds,
        "active_world": active_world,
        "jobs": jobs,
        "assets": assets,
        "asset_form": GeneratedMediaAssetForm(),
        "focused_job_id": int(focused_job_id) if str(focused_job_id).isdigit() else None,
        "focused_asset_id": int(focused_asset_id) if str(focused_asset_id).isdigit() else None,
        "error": None,
    })


def confirm_media_provider_generation(request, job_id):
    _, active_world = _active_world_or_first()

    if not active_world:
        messages.error(request, "No active world is available.")
        return redirect("scene_page")

    provider_id = (
        request.POST.get("provider_id")
        or request.GET.get("provider_id")
        or GOOGLE_NANO_BANANA_2_PROVIDER_ID
    )
    provider = wanda_media_provider(provider_id)
    job = get_object_or_404(
        GeneratedMediaJob.objects.select_related(
            "source_scene",
            "target_character",
            "visual_identity_version",
        ),
        id=job_id,
        world=active_world,
    )

    if request.method != "POST":
        messages.info(
            request,
            "Use the Generate button on the Media Jobs page to send a job.",
        )
        return redirect(_wanda_media_jobs_url(job))

    restart_stale = request.POST.get("restart_background") == "1"
    result = enqueue_media_job_with_provider(
        job,
        provider_id,
        restart_stale=restart_stale,
    )
    if result.get("ok"):
        messages.success(
            request,
            (
                f"Queued job #{job.id} for background generation with "
                f"{provider['label']}. You can return to the scene while Wanda works."
            ),
        )
        return redirect(_wanda_media_jobs_url(job))

    messages.error(
        request,
        "Generation could not be queued: "
        + "; ".join(result.get("blockers") or []),
    )
    return redirect(_wanda_media_jobs_url(job))


def check_media_provider_generation_status(request, job_id):
    if request.method != "POST":
        return redirect("wanda_media_jobs")

    _, active_world = _active_world_or_first()
    if not active_world:
        messages.error(request, "No active world is available.")
        return redirect("scene_page")

    job = get_object_or_404(
        GeneratedMediaJob.objects.select_related(
            "source_scene",
            "target_character",
            "visual_identity_version",
        ),
        id=job_id,
        world=active_world,
    )
    result = check_runway_media_job_status(job)

    if result.get("ok") and result.get("asset"):
        asset = result["asset"]
        messages.success(
            request,
            f"Downloaded Runway video asset #{asset.id} for job #{job.id}.",
        )
        return redirect(_wanda_media_asset_url(asset))

    if result.get("ok"):
        messages.info(
            request,
            f"Runway job #{job.id} is still processing.",
        )
        return redirect(_wanda_media_jobs_url(job))

    messages.error(
        request,
        "Runway status check failed: " + "; ".join(result.get("blockers") or []),
    )
    return redirect(_wanda_media_jobs_url(job))


def copy_media_job(request, job_id):
    if request.method != "POST":
        return redirect("wanda_media_jobs")

    _, active_world = _active_world_or_first()
    if not active_world:
        messages.error(request, "No active world is available.")
        return redirect("scene_page")

    job = get_object_or_404(
        GeneratedMediaJob.objects
        .select_related(
            "world",
            "source_scene",
            "target_character",
            "visual_identity",
            "visual_identity_version",
        )
        .prefetch_related(
            "subjects__character",
            "subjects__visual_identity_version",
        ),
        id=job_id,
        world=active_world,
    )

    copied_job = copy_media_job_for_retry(job)
    messages.success(
        request,
        f"Copied job #{job.id} into new ready job #{copied_job.id}.",
    )
    return redirect(_wanda_media_jobs_url(copied_job))


def delete_media_job(request, job_id):
    if request.method != "POST":
        return redirect("wanda_media_jobs")

    _, active_world = _active_world_or_first()
    if not active_world:
        messages.error(request, "No active world is available.")
        return redirect("scene_page")

    job = get_object_or_404(
        GeneratedMediaJob,
        id=job_id,
        world=active_world,
    )

    if job.status == GeneratedMediaJob.STATUS_QUEUED:
        messages.error(
            request,
            "Queued provider jobs cannot be deleted while they may still be processing.",
        )
        return redirect(_wanda_media_jobs_url(job))

    if job.status == GeneratedMediaJob.STATUS_COMPLETED:
        messages.error(
            request,
            "Completed jobs are represented by generated assets and cannot be deleted here.",
        )
        return redirect(_wanda_media_jobs_url(job))

    job_id_for_message = job.id
    job.delete()
    messages.success(request, f"Deleted media job #{job_id_for_message}.")
    return redirect("wanda_media_jobs")


def upload_generated_media_asset(request, job_id):
    if request.method != "POST":
        return redirect("wanda_media_jobs")

    _, active_world = _active_world_or_first()
    job = get_object_or_404(
        GeneratedMediaJob,
        id=job_id,
        world=active_world,
    )
    form = GeneratedMediaAssetForm(request.POST, request.FILES)

    if not form.is_valid():
        messages.error(
            request,
            "The media asset was not attached. Check the upload fields.",
        )
        return redirect(_wanda_media_jobs_url(job))

    asset = form.save(commit=False)
    asset.world = job.world
    asset.source_scene = job.source_scene
    asset.job = job
    asset.target_character = job.target_character
    asset.visual_identity_version = job.visual_identity_version
    asset.media_type = job.media_type
    asset.save()

    if job.status not in {
        GeneratedMediaJob.STATUS_CANCELED,
        GeneratedMediaJob.STATUS_FAILED,
    }:
        job.status = GeneratedMediaJob.STATUS_COMPLETED
        job.save(update_fields=["status", "updated_at"])

    messages.success(request, f"Attached media asset #{asset.id} to job #{job.id}.")
    return redirect(_wanda_media_asset_url(asset))


def save_generated_media_asset_as_reference(request, asset_id):
    if request.method != "POST":
        return redirect("wanda_media_jobs")

    _, active_world = _active_world_or_first()
    asset = get_object_or_404(
        GeneratedMediaAsset.objects.select_related(
            "job",
            "target_character",
            "visual_identity_version",
        ),
        id=asset_id,
        world=active_world,
    )

    if not asset.target_character:
        messages.error(
            request,
            "This media asset has no target character, so it cannot become a character reference.",
        )
        return redirect(_wanda_media_asset_url(asset))

    if not asset.file:
        messages.error(
            request,
            "This media asset has no file attached, so it cannot become a character reference.",
        )
        return redirect(_wanda_media_asset_url(asset))

    if asset.media_type != GeneratedMediaJob.MEDIA_TYPE_PHOTO:
        messages.error(
            request,
            "Only generated photo assets can be saved as character references right now.",
        )
        return redirect(_wanda_media_asset_url(asset))

    existing_reference = _generated_asset_reference(asset)
    if existing_reference:
        messages.info(
            request,
            f"Asset #{asset.id} is already saved as reference #{existing_reference.id}.",
        )
        return redirect(_wanda_media_asset_url(asset))

    version = (
        asset.visual_identity_version
        or (asset.job.visual_identity_version if asset.job else None)
    )
    visual_identity = (
        version.visual_identity
        if version
        else get_or_create_character_visual_identity(asset.target_character)
    )

    if not version:
        version = current_visual_identity_version(visual_identity)

    reference = CharacterVisualReference(
        world=asset.world,
        character=asset.target_character,
        visual_identity=visual_identity,
        identity_version=version,
        kind=CharacterVisualReference.KIND_GENERATED_REFERENCE,
        is_primary=False,
        caption=(
            f"Generated media asset #{asset.id}"
            + (f" from job #{asset.job_id}." if asset.job_id else ".")
        ),
        generation_prompt=asset.job.prompt if asset.job else "",
        provider=asset.provider or "",
        provider_asset_id=asset.provider_asset_id or "",
        metadata_json={
            "source": "generated_media_asset",
            "source_generated_media_asset_id": asset.id,
            "source_generated_media_job_id": asset.job_id,
            "source_media_type": asset.media_type,
            "source_scene_id": asset.source_scene_id,
            "visual_identity_version_id": version.id if version else None,
            "visual_identity_version_number": (
                version.version_number if version else None
            ),
            "asset_metadata": asset.metadata_json or {},
            "job_visual_style": (
                (asset.job.prompt_packet_json or {}).get("visual_style", {})
                if asset.job
                else {}
            ),
        },
    )
    with asset.file.open("rb") as handle:
        reference.file.save(
            _generated_asset_reference_filename(asset),
            File(handle),
            save=True,
        )

    messages.success(
        request,
        (
            f"Saved asset #{asset.id} as {asset.target_character.name} "
            f"visual reference #{reference.id}."
        ),
    )
    return redirect(_wanda_media_asset_url(asset))


def delete_generated_media_asset(request, asset_id):
    if request.method != "POST":
        return redirect("wanda_media_jobs")

    _, active_world = _active_world_or_first()
    if not active_world:
        messages.error(request, "No active world is available.")
        return redirect("scene_page")

    asset = get_object_or_404(
        GeneratedMediaAsset.objects.select_related(
            "job",
            "target_character",
        ),
        id=asset_id,
        world=active_world,
    )
    asset_id_for_message = asset.id
    source_job_id = asset.job_id
    stored_file = asset.file
    stored_file_name = stored_file.name if stored_file else ""

    asset.delete()

    if stored_file_name:
        stored_file.delete(save=False)

    message = f"Deleted generated media asset #{asset_id_for_message}."
    if source_job_id:
        message += f" Source job #{source_job_id} was left unchanged."
    messages.success(request, message)
    return redirect("wanda_media_jobs")


def scene_page(request):
    worlds = World.objects.all().order_by("name")
    active_world = World.objects.filter(is_active=True).first()

    if not active_world and worlds.exists():
        active_world = worlds.first()
        active_world.is_active = True
        active_world.save()

    if not active_world:
        return render(request, "story/scene_page.html", {
            "worlds": [],
            "active_world": None,
            "proposal": None,
            "committed_scenes": [],
            "scene_state": None,
            "narrative_memories": [],
            "active_story_arcs": [],
            "inactive_story_arcs": [],
            "story_arc_form": None,
            "error": "No worlds exist yet. Create one from the New World page.",
        })
    scene_state, _ = SceneState.objects.get_or_create(
        world=active_world,
        defaults={
            "location": "opening scene",
            "cast_json": {},
            "pending_intents_json": {},
            "alias_cache_json": {},
            "topology_json": {},
        }
    )

    latest_proposal = (
        active_world.proposals
        .order_by("-created_at")
        .first()
    )
    latest_proposal_committed_scene = None
    if latest_proposal and latest_proposal.is_approved:
        latest_proposal_committed_scene = (
            CommittedScene.objects
            .filter(
                world=active_world,
                user_text=latest_proposal.user_input,
                cassandra_text=latest_proposal.draft,
            )
            .order_by("-turn_number")
            .first()
        )
    committed_scenes = list(
        active_world.committed_scenes.order_by("-created_at")[:10]
    )
    committed_scene_rows = _committed_scene_rows_with_media_jobs(
        committed_scenes
    )
    media_portrait_character_options = _media_portrait_character_options(
        active_world
    )
    narrative_memories = active_narrative_memories_for_context(active_world)
    topology = scene_state.topology_json or {}
    if not isinstance(topology, dict):
        topology = {}
    current_narrative_frame = normalize_narrative_frame(
        topology.get("narrative_frame", {}),
        spaces=topology.get("spaces", {}),
    )
    active_story_arcs = active_story_arc_records(active_world)
    active_arc_ooc_tags = active_story_arc_ooc_tags(active_world)
    active_arc_ooc_tag_block = "\n\n".join(active_arc_ooc_tags)
    active_arc_ooc_tag_prefill = (
        f"\n\n{active_arc_ooc_tag_block}"
        if active_arc_ooc_tag_block
        else ""
    )
    inactive_story_arcs = (
        StoryArc.objects
        .filter(world=active_world)
        .exclude(status=StoryArc.STATUS_ACTIVE)
        .order_by("status", "-updated_at", "title")[:12]
    )
    edit_story_arc = None
    edit_story_arc_form = None
    edit_story_arc_id = request.GET.get("edit_arc")
    if str(edit_story_arc_id or "").isdigit():
        edit_story_arc = (
            StoryArc.objects
            .filter(world=active_world, id=int(edit_story_arc_id))
            .first()
        )
        if edit_story_arc:
            edit_story_arc_form = StoryArcForm(instance=edit_story_arc)

    latest_committed_scene = (
        CommittedScene.objects
        .filter(world=active_world)
        .order_by("-turn_number")
        .first()
    )
    _auto_skip_stale_story_arc_update_proposals(
        active_world,
        latest_committed_scene,
    )

    pending_story_arc_update_proposals = []
    if (
        latest_proposal
        and not latest_proposal.is_approved
        and latest_committed_scene
    ):
        pending_story_arc_update_proposals = list(
            StoryArcUpdateProposal.objects
            .filter(
                world=active_world,
                status=StoryArcUpdateProposal.STATUS_PENDING,
                source_scene=latest_committed_scene,
            )
            .select_related("story_arc", "source_scene")
            .order_by("-created_at")[:12]
        )
    character_scenes = (
        CharacterScene.objects
        .filter(world=active_world)
        .select_related("character", "source_scene")
        .order_by("-turn_number", "character__name")[:30]
    )

    relationship_snapshots = (
        CharacterPerception.objects
        .filter(world=active_world)
        .select_related("observer", "target")
        .order_by("observer__name", "target__name")
    )

    relationship_rows = []

    for perception in relationship_snapshots:
        latest_change = None

        if latest_committed_scene:
            latest_change = (
                CharacterPerceptionChange.objects
                .filter(
                    world=active_world,
                    observer=perception.observer,
                    target=perception.target,
                    source_scene=latest_committed_scene,
                    is_context_active=True,
                )
                .order_by("-created_at")
                .first()
            )

        relationship_archives = list(
            CharacterPerceptionChange.objects
            .filter(
                world=active_world,
                observer=perception.observer,
                target=perception.target,
                is_context_active=True,
                change_layer__in=[
                    CharacterPerceptionChange.CHANGE_LAYER_HISTORY,
                    CharacterPerceptionChange.CHANGE_LAYER_PAST,
                ],
            )
            .order_by("change_layer", "-created_at")[:6]
        )

        active_belief_candidates = list(
            CharacterBelief.objects
            .filter(
                world=active_world,
                character=perception.observer,
            )
            .exclude(belief_status=CharacterBelief.BELIEF_STATUS_DISCARDED)
            .exclude(source=PENDING_BELIEF_REDUCTION_SOURCE)
            .order_by("-updated_at")[:25]
        )
        related_beliefs = [
            belief for belief in active_belief_candidates
            if belief_involves_slug(belief, perception.target.slug)
        ][:5]

        relationship_rows.append({
            "observer": perception.observer,
            "target": perception.target,
            "summary": perception.summary,
            "knowledge_basis": perception.knowledge_basis,
            "open_questions": perception.open_questions_json or [],
            "last_change_summary": perception.last_change_summary,
            "latest_change": latest_change,
            "relationship_archives": relationship_archives,
            "beliefs": related_beliefs,
            "has_recent_change": latest_change is not None,
            "scores": [
                {
                    "label": "Trust",
                    "value": perception.trust,
                    "delta": latest_change.trust_delta if latest_change else 0,
                },
                {
                    "label": "Attraction",
                    "value": perception.attraction,
                    "delta": latest_change.attraction_delta if latest_change else 0,
                },
                {
                    "label": "Fear",
                    "value": perception.fear,
                    "delta": latest_change.fear_delta if latest_change else 0,
                },
                {
                    "label": "Resentment",
                    "value": perception.resentment,
                    "delta": latest_change.resentment_delta if latest_change else 0,
                },
            ],
        })

    return render(request, "story/scene_page.html", {
        "worlds": worlds,
        "active_world": active_world,
        "proposal": latest_proposal,
        "latest_proposal_committed_scene": latest_proposal_committed_scene,
        "committed_scenes": committed_scenes,
        "committed_scene_rows": committed_scene_rows,
        "scene_state": scene_state,
        "current_narrative_frame": current_narrative_frame,
        "narrative_memories": narrative_memories,
        "active_story_arcs": active_story_arcs,
        "active_arc_ooc_tags": active_arc_ooc_tags,
        "active_arc_ooc_tag_prefill": active_arc_ooc_tag_prefill,
        "inactive_story_arcs": inactive_story_arcs,
        "pending_story_arc_update_proposals": (
            pending_story_arc_update_proposals
        ),
        "story_arc_form": StoryArcForm(),
        "edit_story_arc": edit_story_arc,
        "edit_story_arc_form": edit_story_arc_form,
        "relationship_rows": relationship_rows,
        "character_scenes": character_scenes,
        "media_portrait_character_options": media_portrait_character_options,
    })


def switch_world(request):
    if request.method == "POST":
        world_id = request.POST.get("world_id")
        selected = get_object_or_404(World, id=world_id)

        World.objects.update(is_active=False)
        selected.is_active = True
        selected.save()

    return redirect("scene_page")


def create_world(request):
    worlds, active_world = _active_world_or_first()

    if request.method == "POST":
        form = WorldForm(request.POST)

        if form.is_valid():
            make_active = form.cleaned_data.get("make_active")

            with transaction.atomic():
                world = form.save(commit=False)

                if make_active or not World.objects.filter(is_active=True).exists():
                    World.objects.update(is_active=False)
                    world.is_active = True

                world.save()

            if world.is_active:
                messages.success(
                    request,
                    f"Created {world.name} and made it the active world.",
                )
            else:
                messages.success(request, f"Created {world.name}.")

            return redirect("scene_page")
    else:
        form = WorldForm()

    return render(request, "story/create_world.html", {
        "worlds": worlds,
        "active_world": active_world,
        "form": form,
    })


def create_story_arc(request):
    if request.method != "POST":
        return redirect("scene_page")

    active_world = World.objects.filter(is_active=True).first()
    if not active_world:
        messages.error(request, "No active world is available for a story arc.")
        return redirect("scene_page")

    form = StoryArcForm(request.POST)

    if not form.is_valid():
        messages.error(
            request,
            "Story arc was not created. Check that character lenses are valid JSON.",
        )
        return redirect("scene_page")

    candidate_slug = _story_arc_candidate_slug(form)

    if _story_arc_slug_conflicts(
        active_world,
        candidate_slug,
    ):
        messages.error(
            request,
            f"A story arc with slug '{candidate_slug}' already exists.",
        )
        return redirect("scene_page")

    story_arc = form.save(commit=False)
    story_arc.world = active_world
    story_arc.status = StoryArc.STATUS_ACTIVE
    story_arc.save()

    messages.success(request, f"Story arc '{story_arc.title}' is now active.")
    return redirect("scene_page")


def _story_arc_candidate_slug(form):
    return (
        form.cleaned_data.get("slug")
        or slugify(form.cleaned_data.get("title") or "")
    )


def _story_arc_slug_conflicts(world, candidate_slug, exclude_arc=None):
    if not world or not candidate_slug:
        return False

    queryset = StoryArc.objects.filter(
        world=world,
        slug=candidate_slug,
    )
    if exclude_arc and exclude_arc.pk:
        queryset = queryset.exclude(id=exclude_arc.id)
    return queryset.exists()


def edit_story_arc(request, arc_id):
    if request.method != "POST":
        return redirect("scene_page")

    active_world = World.objects.filter(is_active=True).first()
    if not active_world:
        messages.error(request, "No active world is available for a story arc.")
        return redirect("scene_page")

    story_arc = get_object_or_404(
        StoryArc,
        id=arc_id,
        world=active_world,
    )
    previous_status = story_arc.status
    form = StoryArcForm(request.POST, instance=story_arc)

    if not form.is_valid():
        messages.error(
            request,
            "Story arc was not updated. Check that character lenses are valid JSON.",
        )
        return redirect(f"{reverse('scene_page')}?edit_arc={story_arc.id}")

    candidate_slug = _story_arc_candidate_slug(form)
    if _story_arc_slug_conflicts(
        active_world,
        candidate_slug,
        exclude_arc=story_arc,
    ):
        messages.error(
            request,
            f"A story arc with slug '{candidate_slug}' already exists.",
        )
        return redirect(f"{reverse('scene_page')}?edit_arc={story_arc.id}")

    story_arc = form.save(commit=False)
    story_arc.world = active_world
    story_arc.status = previous_status
    story_arc.save()

    messages.success(request, f"Updated story arc '{story_arc.title}'.")
    return redirect("scene_page")


def update_story_arc_status(request, arc_id):
    if request.method != "POST":
        return redirect("scene_page")

    active_world = World.objects.filter(is_active=True).first()
    if not active_world:
        messages.error(request, "No active world is available for a story arc.")
        return redirect("scene_page")

    story_arc = get_object_or_404(
        StoryArc,
        id=arc_id,
        world=active_world,
    )

    status = request.POST.get("status")
    valid_statuses = {
        choice[0]
        for choice in StoryArc.STATUS_CHOICES
    }

    if status not in valid_statuses:
        messages.error(request, "That story arc status is not valid.")
        return redirect("scene_page")

    story_arc.status = status
    story_arc.save()

    messages.success(
        request,
        f"Story arc '{story_arc.title}' is now {story_arc.get_status_display().lower()}.",
    )
    return redirect("scene_page")


def _clean_arc_update_text(value, limit=None):
    text = str(value or "").strip()
    if limit and len(text) > limit:
        return text[:limit].strip()
    return text


def _persist_story_arc_update_proposals(world, source_scene, arc_updates):
    if not world or not source_scene or not isinstance(arc_updates, list):
        return 0

    valid_statuses = {
        choice[0]
        for choice in StoryArc.STATUS_CHOICES
    }
    created_count = 0

    for raw_update in arc_updates:
        if not isinstance(raw_update, dict):
            continue

        arc_slug = _clean_slug(raw_update.get("arc_slug"))
        if not arc_slug:
            continue

        story_arc = StoryArc.objects.filter(
            world=world,
            slug=arc_slug,
        ).first()
        if not story_arc:
            continue

        proposed_status = (
            _clean_arc_update_text(raw_update.get("proposed_status"))
            or story_arc.status
        )
        if proposed_status not in valid_statuses:
            continue

        if StoryArcUpdateProposal.objects.filter(
            story_arc=story_arc,
            source_scene=source_scene,
        ).exists():
            continue

        StoryArcUpdateProposal.objects.create(
            world=world,
            story_arc=story_arc,
            source_scene=source_scene,
            horizon_reached=bool(raw_update.get("horizon_reached")),
            evidence_summary=_clean_arc_update_text(
                raw_update.get("evidence_summary")
            ),
            rationale=_clean_arc_update_text(raw_update.get("rationale")),
            current_status=story_arc.status,
            current_phase=story_arc.current_phase,
            current_horizon=story_arc.horizon,
            current_summary=story_arc.summary,
            current_narrator_guidance=story_arc.narrator_guidance,
            current_constraints=story_arc.constraints,
            proposed_status=proposed_status,
            proposed_phase=_clean_arc_update_text(
                raw_update.get("proposed_current_phase")
            ),
            proposed_horizon=_clean_arc_update_text(
                raw_update.get("proposed_horizon")
            ),
            proposed_summary=_clean_arc_update_text(
                raw_update.get("proposed_summary")
            ),
            proposed_narrator_guidance=_clean_arc_update_text(
                raw_update.get("proposed_narrator_guidance")
            ),
            proposed_constraints=_clean_arc_update_text(
                raw_update.get("proposed_constraints")
            ),
            raw_payload_json=raw_update,
        )
        created_count += 1

    return created_count


def decide_story_arc_update_proposal(request, proposal_id):
    if request.method != "POST":
        return redirect("scene_page")

    active_world = World.objects.filter(is_active=True).first()
    if not active_world:
        messages.error(request, "No active world is available for a story arc.")
        return redirect("scene_page")

    proposal = get_object_or_404(
        StoryArcUpdateProposal.objects.select_related("story_arc", "source_scene"),
        id=proposal_id,
        world=active_world,
    )

    if proposal.status != StoryArcUpdateProposal.STATUS_PENDING:
        messages.info(request, "That story arc proposal has already been decided.")
        return redirect("scene_page")

    decision = request.POST.get("decision")
    if decision == "apply":
        title = proposal.story_arc.title
        proposal.apply()
        messages.success(request, f"Applied arc update for '{title}'.")
    elif decision == "skip":
        title = proposal.story_arc.title
        proposal.skip()
        messages.success(request, f"Skipped arc update for '{title}'.")
    else:
        messages.error(request, "That story arc proposal decision is not valid.")

    return redirect("scene_page")


def generate_draft(request):
    if request.method != "POST":
        return redirect("scene_page")

    active_world = World.objects.filter(is_active=True).first()
    if not active_world:
        return redirect("scene_page")

    scene_state, _ = SceneState.objects.get_or_create(
        world=active_world,
        defaults={
            "location": "opening scene",
            "cast_json": {},
            "pending_intents_json": {},
            "alias_cache_json": {},
            "topology_json": {},
        }
    )

    user_input = _draft_user_input_from_post(request.POST, world=active_world)
    if not user_input:
        return redirect("scene_page")

    _log_story_heading("GENERATE DRAFT")
    print(
        "[story] world=",
        active_world.name,
        "scene_state_id=",
        scene_state.id,
        flush=True,
    )
    _log_story_text("user_input", user_input)

    player_character = Character.objects.filter(
        world=active_world,
        is_player=True,
        is_active=True,
    ).first()

    pending_cassandra_aftermath_scene = (
        CommittedScene.objects
        .filter(
            world=active_world,
            cassandra_aftermath_processed=False,
        )
        .order_by("turn_number")
        .first()
    )

    try:
        participant_result = infer_scene_participants_and_positions(
            world=active_world,
            scene_state=scene_state,
            scene_text=f"[User]\n{user_input}",
            pov_slug=player_character.slug if player_character else None,
        )

        participant_update = participant_result.get("scene_state_update", {})
        alias_update = participant_result.get("alias_cache_update", {})
        _log_story_json(
            "participant_update_summary",
            _scene_state_log_summary(participant_update),
        )
        _log_story_json(
            "alias_update_summary",
            {
                "alias_count": (
                    len(alias_update)
                    if isinstance(alias_update, dict)
                    else 0
                ),
                "aliases": (
                    list(alias_update.keys())
                    if isinstance(alias_update, dict)
                    else []
                ),
            },
        )

        valid_slugs = _valid_character_slugs(build_character_registry(active_world))
        merged_alias_cache = _merge_alias_cache(
            existing=scene_state.alias_cache_json,
            update=alias_update,
            valid_slugs=valid_slugs,
        )

        # Build a temporary scene state for Cassandra using participant inference first.
        pre_draft_scene_state = resolve_proposed_scene_state(
            current_state=serialize_scene_state(scene_state),
            scene_state_update=participant_update,
            pending_intents=scene_state.pending_intents_json,
        )

        temp_scene_state = TempSceneState(
            location=pre_draft_scene_state.get("location", ""),
            cast_json=pre_draft_scene_state.get("cast", {}),
            pending_intents_json=pre_draft_scene_state.get("pending_intents", {}),
            alias_cache_json=merged_alias_cache,
            topology_json={
                "narrative_frame": pre_draft_scene_state.get("narrative_frame", {}),
                "spaces": pre_draft_scene_state.get("spaces", {}),
            },
        )

        character_contributions, character_agent_debug = collect_character_contributions(
            world=active_world,
            scene_state=temp_scene_state,
            user_input=user_input,
            include_debug=True,
        )
        _log_story_json(
            "character_contributions",
            _character_contribution_log_rows(character_contributions),
        )

        character_authored_intents = (
            collect_character_authored_intents_from_contributions(
                character_contributions
            )
        )
        pending_cassandra_aftermath_scene = (
            CommittedScene.objects
            .filter(
                world=active_world,
                cassandra_aftermath_processed=False,
            )
            .order_by("turn_number")
            .first()
        )

        context = build_turn_context(
            active_world,
            temp_scene_state,
            user_input,
            character_authored_intents=character_authored_intents,
            character_contributions=character_contributions,
            pending_previous_cassandra_aftermath=pending_cassandra_aftermath_scene,
        )

        result = call_cassandra(context)
        scene_events = result.get("scene_events") or []
        _log_story_text("cassandra_draft", result.get("draft", ""))
        _log_story_json("scene_events", scene_events)
        _log_story_json(
            "resolved_pending_intents",
            result.get("resolved_pending_intents", {}),
        )
        cassandra_scene_state_update = _normalize_cassandra_scene_state_update(
            result.get("post_draft_scene_state_update") or {},
            active_slugs=valid_slugs,
            scene_events=scene_events,
        )
        _log_story_json(
            "cassandra_scene_state_update_summary",
            _scene_state_log_summary(cassandra_scene_state_update),
        )
        final_proposed_scene_state = _merge_cassandra_scene_state_consequences(
            pre_draft_scene_state=pre_draft_scene_state,
            cassandra_scene_state_update=cassandra_scene_state_update,
            pending_intents=scene_state.pending_intents_json,
            alias_cache=merged_alias_cache,
        )
        apply_narrative_continuity_maintenance(
            active_world,
            context.get("continuity_maintenance_tasks") or {},
            result.get("continuity_maintenance") or {},
        )

        previous_aftermath = result.get("previous_scene_aftermath") or {}
        source_scene_id = previous_aftermath.get("source_scene_id")

        if source_scene_id:
            pending_scene = CommittedScene.objects.filter(
                id=source_scene_id,
                world=active_world,
                cassandra_aftermath_processed=False,
            ).first()

            if pending_scene:
                created_narrative_memory_count = 0
                for memory_text in previous_aftermath.get("narrative_memories", []):
                    if memory_text:
                        NarrativeMemory.objects.create(
                            world=active_world,
                            content=memory_text,
                            source_scene=pending_scene,
                            memory_layer=NarrativeMemory.MEMORY_LAYER_RAW,
                            is_context_active=True,
                        )
                        created_narrative_memory_count += 1

                created_arc_update_count = _persist_story_arc_update_proposals(
                    active_world,
                    pending_scene,
                    previous_aftermath.get("story_arc_updates") or [],
                )

                pending_scene.cassandra_aftermath_processed = True
                pending_scene.save()
                _log_story_json(
                    "persisted_cassandra_previous_scene_aftermath",
                    {
                        "source_scene_id": pending_scene.id,
                        "turn_number": pending_scene.turn_number,
                        "narrative_memory_count": len(
                            previous_aftermath.get("narrative_memories", [])
                        ),
                        "story_arc_update_proposal_count": (
                            created_arc_update_count
                        ),
                    },
                )

    except Exception as e:
        print("DRAFT GENERATION ERROR:", type(e).__name__, e)
        return render(request, "story/scene_page.html", {
            "worlds": World.objects.all().order_by("name"),
            "active_world": active_world,
            "proposal": (
                active_world.proposals
                .filter(is_approved=False)
                .order_by("-created_at")
                .first()
            ),
            "committed_scenes": active_world.committed_scenes.order_by("-created_at")[:20],
            "scene_state": scene_state,
            "error": f"Draft generation error: {type(e).__name__}: {e}",
        })

    proposal = Proposal.objects.create(
        world=active_world,
        user_input=user_input,
        draft=result["draft"],
        scene_events_json=scene_events,
        character_authored_intents_json=character_authored_intents,
        resolved_pending_intents_json=normalize_intents(
            result.get("resolved_pending_intents", [])
        ),
        is_approved=False,
        revision_change_summary=result.get("change_summary", ""),
        revision_intent_summary=result.get("inferred_editorial_intent", ""),
        editors_craft_memory_json=result.get("editors_craft_memory", []),
        character_contributions_json=character_contributions,
        proposed_scene_state_json=final_proposed_scene_state,
        scene_state_update_json=participant_update,
        cassandra_scene_state_update_json=cassandra_scene_state_update,
        alias_cache_update_json=alias_update,
        character_agent_debug_json=character_agent_debug,
    )
    print(
        "[story] proposal_created id=",
        proposal.id,
        "draft_chars=",
        len(proposal.draft or ""),
        flush=True,
    )

    return redirect("scene_page")


def approve_draft(request, proposal_id):
    if request.method != "POST":
        return redirect("scene_page")

    proposal = get_object_or_404(Proposal, id=proposal_id)
    world = proposal.world

    _log_story_heading("APPROVE DRAFT")
    print(
        "[story] proposal_id=",
        proposal.id,
        "world=",
        world.name,
        flush=True,
    )

    if proposal.is_approved:
        print("[story] approval_skipped already_approved=True", flush=True)
        return redirect("scene_page")

    scene_state, _ = SceneState.objects.get_or_create(
        world=world,
        defaults={
            "location": "opening scene",
            "cast_json": {},
            "pending_intents_json": {},
            "alias_cache_json": {},
            "topology_json": {},
        }
    )

    proposal.is_approved = True
    proposal.save()

    last_scene = (
        CommittedScene.objects
        .filter(world=world)
        .order_by("-turn_number")
        .first()
    )

    next_turn_number = (last_scene.turn_number + 1) if last_scene else 1

    committed_scene = CommittedScene.objects.create(
        world=world,
        turn_number=next_turn_number,
        user_text=proposal.user_input,
        cassandra_text=proposal.draft,
        scene_events_json=proposal.scene_events_json or [],
        cassandra_aftermath_processed=False,
    )
    print(
        "[story] committed_scene_created id=",
        committed_scene.id,
        "turn=",
        committed_scene.turn_number,
        flush=True,
    )
    _log_story_text("approved_user_input", proposal.user_input)
    _log_story_text("approved_cassandra_text", proposal.draft)
    _log_story_json("approved_scene_events", committed_scene.scene_events_json or [])

    # Use the draft-time scene-state inference.
    # Do NOT call MissPots again here.

    first_pass_state = {
        **(proposal.proposed_scene_state_json or {}),
        "pending_intents": scene_state.pending_intents_json or {},
    }
    resolved_pending_intents = proposal.resolved_pending_intents_json or {}

    final_resolved_state = {
        **first_pass_state,
        "pending_intents": resolved_pending_intents,
    }
    _log_story_json(
        "final_resolved_state_summary",
        _scene_state_log_summary(final_resolved_state),
    )

    active_characters = Character.objects.filter(
        world=world,
        is_active=True,
    )

    cast = final_resolved_state.get("cast", {}) if isinstance(final_resolved_state, dict) else {}

    for character in active_characters:
        cast_entry = cast.get(character.slug, {}) if isinstance(cast, dict) else {}

        event_record_text = build_character_event_record_text(
            character_slug=character.slug,
            resolved_scene_state=final_resolved_state,
            scene_events=committed_scene.scene_events_json or [],
            character_contributions=proposal.character_contributions_json or [],
        )

        perceived_events = [
            event
            for event in (committed_scene.scene_events_json or [])
            if isinstance(event, dict)
            and (
                character.slug in (event.get("perceived_by") or [])
                or event.get("actor_slug") == character.slug
                or character.slug in (event.get("target_slugs") or [])
            )
        ]

        event_record_json = {
            "cast_entry": cast_entry,
            "scene_events_perceived_by_character": perceived_events,
        }

        character_scene, _ = CharacterScene.objects.update_or_create(
            character=character,
            source_scene=committed_scene,
            defaults={
                "world": world,
                "turn_number": committed_scene.turn_number,

                "event_record_text": event_record_text,
                "event_record_json": event_record_json,

                "subjective_scene_text": "",
                "participation": cast_entry.get("presence", ""),
                "aftermath_processed": False,

                "local_scene_state_json": cast_entry,
                "acting_character_snapshot_json": {
                    "slug": character.slug,
                    "name": character.name,
                    "is_player": character.is_player,
                    "description": character.description or "",
                },
                "state_before_json": character_state_snapshot(character),
                "state_after_json": {},
            },
        )

        contribution = find_character_contribution(
            proposal.character_contributions_json or [],
            character.slug,
        )

        apply_character_contribution_to_scene(character_scene, contribution)
        character_scene.save()
        print(
            "[story] character_scene_prepared id=",
            character_scene.id,
            "turn=",
            character_scene.turn_number,
            "character=",
            character.slug,
            "participation=",
            character_scene.participation,
            "perceived_events=",
            len(perceived_events),
            "has_contribution=",
            bool(contribution),
            flush=True,
        )

    scene_state.location = final_resolved_state.get(
        "location",
        scene_state.location,
    )

    scene_state.cast_json = final_resolved_state.get(
        "cast",
        scene_state.cast_json,
    )

    scene_state.topology_json = {
        "narrative_frame": final_resolved_state.get("narrative_frame", {}),
        "spaces": final_resolved_state.get("spaces", {}),
    }

    scene_state.pending_intents_json = final_resolved_state.get(
        "pending_intents",
        scene_state.pending_intents_json,
    )

    scene_state.alias_cache_json = final_resolved_state.get(
        "alias_cache",
        scene_state.alias_cache_json,
    )

    scene_state.save()
    skipped_arc_update_count = _auto_skip_stale_story_arc_update_proposals(
        world,
        committed_scene,
    )
    print(
        "[story] scene_state_updated id=",
        scene_state.id,
        "location=",
        scene_state.location,
        "cast_count=",
        len(scene_state.cast_json or {}),
        "pending_intents_count=",
        len(scene_state.pending_intents_json or {}),
        "stale_arc_updates_skipped=",
        skipped_arc_update_count,
        flush=True,
    )

    return redirect("scene_page")


def delete_committed_scene(request, scene_id):
    if request.method != "POST":
        return redirect("scene_page")

    target_scene = get_object_or_404(CommittedScene, id=scene_id)
    world = target_scene.world

    with transaction.atomic():
        scenes_to_delete = list(
            CommittedScene.objects
            .filter(
                world=world,
                turn_number__gte=target_scene.turn_number,
            )
            .order_by("turn_number")
        )
        scene_ids = [scene.id for scene in scenes_to_delete]
        deleted_turns = [scene.turn_number for scene in scenes_to_delete]

        character_scenes = list(
            CharacterScene.objects
            .filter(world=world, source_scene_id__in=scene_ids)
            .select_related("character")
        )

        affected_character_ids = set(
            CharacterStateChange.objects
            .filter(world=world, source_scene_id__in=scene_ids)
            .values_list("character_id", flat=True)
        )
        affected_character_ids.update(
            character_scene.character_id
            for character_scene in character_scenes
        )

        affected_perception_pairs = set(
            CharacterPerceptionChange.objects
            .filter(world=world, source_scene_id__in=scene_ids)
            .values_list("observer_id", "target_id")
        )
        affected_subjective_edge_pairs = set(
            SubjectiveRelationshipEdgeChange.objects
            .filter(world=world, source_scene_id__in=scene_ids)
            .values_list("observer_id", "subject_a_id", "subject_b_id")
        )

        _delete_legacy_null_source_beliefs(character_scenes)

        for scene in scenes_to_delete:
            Proposal.objects.filter(
                world=world,
                is_approved=True,
                user_input=scene.user_text,
                draft=scene.cassandra_text,
            ).delete()

        CommittedScene.objects.filter(id__in=scene_ids).delete()

        _rebuild_character_state_for_rewind(world, affected_character_ids)
        _rebuild_perceptions_for_rewind(world, affected_perception_pairs)
        _rebuild_subjective_relationship_edges_for_rewind(
            world,
            affected_subjective_edge_pairs,
        )
        _rewind_scene_state(world)

    _log_story_heading("DELETE COMMITTED SCENE")
    print(
        "[story] deleted_scene_ids=",
        scene_ids,
        "deleted_turns=",
        deleted_turns,
        "world=",
        world.name,
        flush=True,
    )

    return redirect("scene_page")


def cast_page(request):
    active_world = World.objects.filter(is_active=True).first()
    if not active_world:
        return render(request, "story/cast_page.html", {
            "active_world": None,
            "characters": [],
            "error": "No active world. Please create a world from the New World page.",
        })
    characters = active_world.characters.order_by("name")
    return render(request, "story/cast_page.html", {
        "active_world": active_world,
        "characters": characters,
    })

def _profile_notes(payload):
    if not isinstance(payload, dict):
        return ""

    return payload.get("notes", "")


def _save_character_profile_from_form(character, form):
    profile, _ = CharacterProfile.objects.get_or_create(
        character=character,
    )

    profile.summary = form.cleaned_data.get("profile_summary", "")
    profile.archetype = form.cleaned_data.get("archetype", "")
    profile.gender = form.cleaned_data.get("gender", "")

    profile.pronouns_json = {
        "subject": form.cleaned_data.get("pronoun_subject", ""),
        "object": form.cleaned_data.get("pronoun_object", ""),
    }

    profile.personality_json = {
        "notes": form.cleaned_data.get("personality", "")
    }

    profile.permabeliefs_json = {
        "notes": form.cleaned_data.get("permabeliefs", "")
    }

    profile.diction_json = {
        "notes": form.cleaned_data.get("diction", "")
    }

    profile.craft_notes_json = {
        "notes": form.cleaned_data.get("craft_notes", "")
    }

    profile.background_json = {
        "notes": form.cleaned_data.get("background", "")
    }

    profile.save()
    return profile


def _character_profile_initial(profile):
    pronouns = profile.pronouns_json or {}

    return {
        "profile_summary": profile.summary,
        "archetype": profile.archetype,
        "gender": profile.gender,
        "pronoun_subject": pronouns.get("subject", "they"),
        "pronoun_object": pronouns.get("object", "them"),
        "personality": _profile_notes(profile.personality_json),
        "permabeliefs": _profile_notes(profile.permabeliefs_json),
        "diction": _profile_notes(profile.diction_json),
        "craft_notes": _profile_notes(profile.craft_notes_json),
        "background": _profile_notes(profile.background_json),
    }

def character_creation_form(request):
    if request.method == "POST":
        form = CharacterForm(request.POST)

        if form.is_valid():
            character = form.save()
            _save_character_profile_from_form(character, form)

            return redirect("cast_page")

    else:
        form = CharacterForm()

    return render(request, "story/create_character.html", {
        "form": form,
        "is_editing": False,
    })

def character_edit_form(request, character_id):
    character = get_object_or_404(Character, id=character_id)
    profile, _ = CharacterProfile.objects.get_or_create(
        character=character,
    )

    if request.method == "POST":
        form = CharacterForm(request.POST, instance=character)

        if form.is_valid():
            character = form.save()
            _save_character_profile_from_form(character, form)

            return redirect("cast_page")

    else:
        form = CharacterForm(
            instance=character,
            initial=_character_profile_initial(profile),
        )

    return render(request, "story/create_character.html", {
        "form": form,
        "character": character,
        "is_editing": True,
    })


def revise_draft(request, proposal_id):
    if request.method != "POST":
        return redirect("scene_page")

    proposal = get_object_or_404(Proposal, id=proposal_id)
    if proposal.is_approved:
        return redirect("scene_page")

    active_world = proposal.world
    scene_state, _ = SceneState.objects.get_or_create(
        world=active_world,
        defaults={
            "location": "opening scene",
            "cast_json": {},
            "pending_intents_json": {},
            "alias_cache_json": {},
            "topology_json": {},
        }
    )
    original_draft = proposal.draft or ""
    edited_draft = request.POST.get("edited_draft", "").strip()
    revision_feedback = request.POST.get("revision_feedback", "").strip()
    rewrite_from_scratch = request.POST.get("rewrite_from_scratch") == "true"

    if not edited_draft and not rewrite_from_scratch:
        return render(request, "story/scene_page.html", {
            "worlds": World.objects.all().order_by("name"),
            "active_world": active_world,
            "proposal": proposal,
            "committed_scenes": active_world.committed_scenes.order_by("-created_at")[:20],
            "scene_state": scene_state,
            "error": "Revision error: edited draft cannot be empty.",
        })

    text_changed = materially_changed(original_draft, edited_draft)

    if not text_changed and not revision_feedback and not rewrite_from_scratch:
        return redirect("scene_page")

    revision_mode = choose_revision_mode(
        original_draft=original_draft,
        revised_draft=edited_draft,
        revision_feedback=revision_feedback,
        rewrite_from_scratch=rewrite_from_scratch,
    )

    effective_revised_draft = edited_draft if edited_draft else original_draft

    try:
        context = build_revision_context(
            world=active_world,
            scene_state=scene_state,
            user_input=proposal.user_input,
            original_draft=original_draft,
            revised_draft=effective_revised_draft,
            revision_feedback=revision_feedback,
            revision_mode=revision_mode,
            character_authored_intents=proposal.character_authored_intents_json or {},
            character_contributions=proposal.character_contributions_json or [],
        )

        if revision_mode in {"rewrite_from_scratch", "rewrite_based_on_feedback"}:
            try:
                player_character = Character.objects.filter(
                    world=active_world,
                    is_player=True,
                    is_active=True,
                ).first()

                participant_result = infer_scene_participants_and_positions(
                    world=active_world,
                    scene_state=scene_state,
                    scene_text=f"[User]\n{proposal.user_input}",
                    pov_slug=player_character.slug if player_character else None,
                )

                participant_update = participant_result.get("scene_state_update", {})
                alias_update = participant_result.get("alias_cache_update", {})

                valid_slugs = _valid_character_slugs(
                    build_character_registry(active_world)
                )

                merged_alias_cache = _merge_alias_cache(
                    existing=scene_state.alias_cache_json,
                    update=alias_update,
                    valid_slugs=valid_slugs,
                )

                pre_draft_scene_state = resolve_proposed_scene_state(
                    current_state=serialize_scene_state(scene_state),
                    scene_state_update=participant_update,
                    pending_intents=scene_state.pending_intents_json,
                )

                temp_scene_state = TempSceneState(
                    location=pre_draft_scene_state.get("location", ""),
                    cast_json=pre_draft_scene_state.get("cast", {}),
                    pending_intents_json=pre_draft_scene_state.get("pending_intents", {}),
                    alias_cache_json=merged_alias_cache,
                    topology_json={
                        "narrative_frame": pre_draft_scene_state.get("narrative_frame", {}),
                        "spaces": pre_draft_scene_state.get("spaces", {}),
                    },
                )

                character_contributions, character_agent_debug = collect_character_contributions(
                    world=active_world,
                    scene_state=temp_scene_state,
                    user_input=proposal.user_input,
                    revision_feedback=revision_feedback if revision_mode == "rewrite_based_on_feedback" else "",
                    revision_mode=revision_mode,
                    include_debug=True,
                )

                character_authored_intents = (
                    collect_character_authored_intents_from_contributions(
                        character_contributions
                    )
                )

                context = build_turn_context(
                    active_world,
                    temp_scene_state,
                    proposal.user_input,
                    character_authored_intents=character_authored_intents,
                    character_contributions=character_contributions,
                )

                if revision_mode == "rewrite_based_on_feedback":
                    context["revision_context"] = {
                        "mode": "rewrite_based_on_feedback",
                        "feedback": revision_feedback,
                    }

                result = call_cassandra(context)
                scene_events = result.get("scene_events") or []
                cassandra_scene_state_update = _normalize_cassandra_scene_state_update(
                    result.get("post_draft_scene_state_update") or {},
                    active_slugs=valid_slugs,
                    scene_events=scene_events,
                )
                final_proposed_scene_state = _merge_cassandra_scene_state_consequences(
                    pre_draft_scene_state=pre_draft_scene_state,
                    cassandra_scene_state_update=cassandra_scene_state_update,
                    pending_intents=scene_state.pending_intents_json,
                    alias_cache=merged_alias_cache,
                )

                proposal.draft = result["draft"]
                proposal.scene_events_json = scene_events
                proposal.character_authored_intents_json = character_authored_intents
                proposal.resolved_pending_intents_json = normalize_intents(
                    result.get("resolved_pending_intents", [])
                )

                proposal.character_contributions_json = character_contributions
                proposal.character_agent_debug_json = character_agent_debug
                proposal.proposed_scene_state_json = final_proposed_scene_state
                proposal.scene_state_update_json = participant_update
                proposal.cassandra_scene_state_update_json = (
                    cassandra_scene_state_update
                )
                proposal.alias_cache_update_json = alias_update

                proposal.revision_change_summary = (
                    "Regenerated from scratch with fresh character-agent contributions."
                )
                proposal.revision_intent_summary = (
                    revision_feedback
                    or "Full regeneration requested; character agents were re-run."
                )
                proposal.editors_craft_memory_json = []

                proposal.save()
                return redirect("scene_page")

            except Exception as e:
                worlds = World.objects.all().order_by("name")
                latest_proposal = (
                    active_world.proposals
                    .filter(is_approved=False)
                    .order_by("-created_at")
                    .first()
                )
                committed_scenes = active_world.committed_scenes.order_by("-created_at")[:20]

                return render(request, "story/scene_page.html", {
                    "worlds": worlds,
                    "active_world": active_world,
                    "proposal": latest_proposal,
                    "committed_scenes": committed_scenes,
                    "scene_state": scene_state,
                    "error": f"Rewrite from scratch error: {type(e).__name__}: {e}",
                })

        result = call_cassandra_revision(context)

        if revision_mode == "interpret_user_edit":
            # User-authored edit is authoritative.
            proposal.draft = edited_draft
        else:
            proposal.draft = result.get("draft") or proposal.draft

        proposal.scene_events_json = result.get(
            "scene_events",
            proposal.scene_events_json or [],
        )

        valid_slugs = _valid_character_slugs(build_character_registry(active_world))
        pre_draft_scene_state = resolve_proposed_scene_state(
            current_state=serialize_scene_state(scene_state),
            scene_state_update=proposal.scene_state_update_json or {},
            pending_intents=scene_state.pending_intents_json,
        )
        existing_proposed_scene_state = proposal.proposed_scene_state_json or {}
        proposal_alias_cache = (
            existing_proposed_scene_state.get("alias_cache")
            if isinstance(existing_proposed_scene_state, dict)
            else None
        )
        cassandra_scene_state_update = _normalize_cassandra_scene_state_update(
            result.get("post_draft_scene_state_update") or {},
            active_slugs=valid_slugs,
            scene_events=proposal.scene_events_json or [],
        )
        proposal.cassandra_scene_state_update_json = cassandra_scene_state_update
        proposal.proposed_scene_state_json = _merge_cassandra_scene_state_consequences(
            pre_draft_scene_state=pre_draft_scene_state,
            cassandra_scene_state_update=cassandra_scene_state_update,
            pending_intents=scene_state.pending_intents_json,
            alias_cache=proposal_alias_cache or scene_state.alias_cache_json,
        )

        proposal.resolved_pending_intents_json = normalize_intents(
            result.get(
                "resolved_pending_intents",
                proposal.resolved_pending_intents_json or {},
            )
        )

        proposal.editors_craft_memory_json = result.get("editors_craft_memory", [])
        proposal.revision_change_summary = result.get("change_summary", "")
        proposal.revision_intent_summary = result.get("inferred_editorial_intent", "")

        proposal.save()
        proposal.refresh_from_db()

    except Exception as e:
        worlds = World.objects.all().order_by("name")
        latest_proposal = (
            active_world.proposals
            .filter(is_approved=False)
            .order_by("-created_at")
            .first()
        )
        committed_scenes = active_world.committed_scenes.order_by("-created_at")[:20]

        return render(request, "story/scene_page.html", {
            "worlds": worlds,
            "active_world": active_world,
            "proposal": latest_proposal,
            "committed_scenes": committed_scenes,
            "scene_state": scene_state,
            "error": f"Revision error: {type(e).__name__}: {e}",
        })

    return redirect("scene_page")
