#views.py#
import json

from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from .models import (
    World,
    SceneState,
    Proposal,
    CommittedScene,
    Character,
    CharacterProfile,
    NarrativeMemory,
    CharacterBelief,
    CharacterPerception,
    CharacterPerceptionChange,
    CharacterState,
    CharacterStateChange,
    TempSceneState,
    CharacterScene,
)
from .Cassandra import (
    call_cassandra,
    call_cassandra_revision,
    extract_scene_aftermath,
    materially_changed,
    choose_revision_mode,
)
from .Wanda import (
    build_turn_context,
    build_revision_context,
    resolve_proposed_scene_state,
    serialize_scene_state,
)
from .MissPots.cast_tracker import (
    infer_scene_participants_and_positions,
    _merge_alias_cache,
    _valid_character_slugs
)
from .MissPots.characters import (
    build_character_registry,
    collect_character_contributions,
    collect_character_authored_intents_from_contributions,
    build_character_event_record_text,
    apply_character_contribution_to_scene,
    character_state_snapshot,
)
from .forms import CharacterForm

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


def _character_contribution_log_rows(character_contributions):
    rows = []

    for contribution in character_contributions or []:
        if not isinstance(contribution, dict):
            continue

        rows.append({
            "slug": contribution.get("slug", ""),
            "attempted_action": contribution.get("attempted_action", ""),
            "attempted_dialogue": contribution.get("attempted_dialogue", ""),
            "internal_intent": contribution.get("internal_intent", ""),
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
    arc = {}
    trust = 0.0
    attraction = 0.0
    fear = 0.0
    resentment = 0.0

    for change in changes:
        summary = change.summary or summary
        impression = change.impression_json or impression
        relationship = change.relationship_json or relationship
        belief = change.belief_json or belief
        arc = change.arc_json or arc
        trust += change.trust_delta or 0.0
        attraction += change.attraction_delta or 0.0
        fear += change.fear_delta or 0.0
        resentment += change.resentment_delta or 0.0

    perception.summary = summary
    perception.impression_json = impression
    perception.relationship_json = relationship
    perception.belief_json = belief
    perception.arc_json = arc
    perception.trust = trust
    perception.attraction = attraction
    perception.fear = fear
    perception.resentment = resentment
    perception.save()


def _rebuild_perceptions_for_rewind(world, perception_pairs):
    for observer_id, target_id in perception_pairs:
        _rebuild_perception_for_pair(world, observer_id, target_id)


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
            "error": "No worlds exist yet. Create one in Django admin.",
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
    committed_scenes = active_world.committed_scenes.order_by("-created_at")[:10]
    narrative_memories = active_world.narrative_memories.order_by("-created_at")[:2]
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

    latest_committed_scene = (
        CommittedScene.objects
        .filter(world=active_world)
        .order_by("-turn_number")
        .first()
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
                )
                .order_by("-created_at")
                .first()
            )

        relationship_rows.append({
            "observer": perception.observer,
            "target": perception.target,
            "summary": perception.summary,
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
        "committed_scenes": committed_scenes,
        "scene_state": scene_state,
        "narrative_memories": narrative_memories,
        "relationship_rows": relationship_rows,
        "character_scenes": character_scenes,
    })


def switch_world(request):
    if request.method == "POST":
        world_id = request.POST.get("world_id")
        selected = get_object_or_404(World, id=world_id)

        World.objects.update(is_active=False)
        selected.is_active = True
        selected.save()

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

    user_input = request.POST.get("user_input", "").strip()
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
        _log_story_text("cassandra_draft", result.get("draft", ""))
        _log_story_json("scene_events", result.get("scene_events", []))
        _log_story_json(
            "resolved_pending_intents",
            result.get("resolved_pending_intents", {}),
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
                for memory_text in previous_aftermath.get("narrative_memories", []):
                    if memory_text:
                        NarrativeMemory.objects.create(
                            world=active_world,
                            content=memory_text,
                            source_scene=pending_scene,
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
        scene_events_json=result.get("scene_events", []),
        character_authored_intents_json=character_authored_intents,
        resolved_pending_intents_json=normalize_intents(
            result.get("resolved_pending_intents", [])
        ),
        is_approved=False,
        revision_change_summary=result.get("change_summary", ""),
        revision_intent_summary=result.get("inferred_editorial_intent", ""),
        editors_craft_memory_json=result.get("editors_craft_memory", []),
        character_contributions_json=character_contributions,
        proposed_scene_state_json={
            **pre_draft_scene_state,
            "alias_cache": merged_alias_cache,
        },
        scene_state_update_json=participant_update,
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

    # aftermath = extract_scene_aftermath(
    #     world=world,
    #     user_input=proposal.user_input,
    #     final_draft=committed_scene.cassandra_text,
    #     resolved_scene_state=final_resolved_state,
    #     scene_events=proposal.scene_events_json or [],
    #     character_contributions=proposal.character_contributions_json or [],
    #     character_registry=build_character_registry(world),
    # )

    # print(
    #     "AFTERMATH COUNTS:",
    #     "narrative_memories=", len(aftermath.get("narrative_memories", [])),
    # )

    # for memory_text in aftermath.get("narrative_memories", []):
    #     if memory_text:
    #         NarrativeMemory.objects.create(
    #             world=world,
    #             content=memory_text,
    #             source_scene=committed_scene,
    #         )

    # aftermath_scene_state_update = aftermath.get("scene_state_update") or {}
    # aftermath_location = aftermath_scene_state_update.get("location")

    # if aftermath_location:
    #     final_resolved_state["location"] = aftermath_location

    # character_experience_updates = collect_single_character_experience_updates(
    #     world=world,
    #     resolved_scene_state=final_resolved_state,
    #     final_draft=committed_scene.cassandra_text,
    #     scene_events=committed_scene.scene_events_json or [],
    #     character_contributions=proposal.character_contributions_json or [],
    # )

    # print(
    #     "PER-CHARACTER EXPERIENCE COUNTS:",
    #     "updates=",
    #     len(character_experience_updates),
    # )

    # persist_character_experience_updates(
    #     world=world,
    #     resolved_scene_state=final_resolved_state,
    #     experience_updates=character_experience_updates,
    #     source_scene=committed_scene,
    # )

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
    print(
        "[story] scene_state_updated id=",
        scene_state.id,
        "location=",
        scene_state.location,
        "cast_count=",
        len(scene_state.cast_json or {}),
        "pending_intents_count=",
        len(scene_state.pending_intents_json or {}),
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
            "characters": [],
            "error": "No active world. Please create and activate a world in Django admin.",
        })
    characters = active_world.characters.order_by("name")
    return render(request, "story/cast_page.html", {
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

                proposal.draft = result["draft"]
                proposal.scene_events_json = result.get("scene_events", [])
                proposal.character_authored_intents_json = character_authored_intents
                proposal.resolved_pending_intents_json = normalize_intents(
                    result.get("resolved_pending_intents", [])
                )

                proposal.character_contributions_json = character_contributions
                proposal.character_agent_debug_json = character_agent_debug
                proposal.proposed_scene_state_json = {
                    **pre_draft_scene_state,
                    "alias_cache": merged_alias_cache,
                }
                proposal.scene_state_update_json = participant_update
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
