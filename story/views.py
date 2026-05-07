#views.py#
from django.shortcuts import render, redirect, get_object_or_404
from .models import (
    World,
    SceneState,
    Proposal,
    CommittedScene,
    Character,
    CharacterProfile,
    NarrativeMemory,
    CharacterPerception,
    CharacterPerceptionChange,
    TempSceneState,
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
    persist_character_experience_updates
)
from .forms import CharacterForm

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
        }
    )

    latest_proposal = (
        active_world.proposals
        .filter(is_approved=False)
        .order_by("-created_at")
        .first()
    )
    committed_scenes = active_world.committed_scenes.order_by("-created_at")[:10]
    narrative_memories = active_world.narrative_memories.order_by("-created_at")[:2]

    relationship_snapshots = (
        CharacterPerception.objects
        .filter(world=active_world)
        .select_related("observer", "target")
        .order_by("observer__name", "target__name")
    )

    relationship_rows = []

    for perception in relationship_snapshots:
        latest_change = (
            CharacterPerceptionChange.objects
            .filter(
                world=active_world,
                observer=perception.observer,
                target=perception.target,
            )
            .order_by("-created_at")
            .first()
        )

        relationship_rows.append({
            "observer": perception.observer,
            "target": perception.target,
            "summary": perception.summary,
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
        }
    )

    user_input = request.POST.get("user_input", "").strip()
    if not user_input:
        return redirect("scene_page")

    player_character = Character.objects.filter(
        world=active_world,
        is_player=True,
        is_active=True,
    ).first()

    try:
        participant_result = infer_scene_participants_and_positions(
            world=active_world,
            scene_state=scene_state,
            scene_text=f"[User]\n{user_input}",
            pov_slug=player_character.slug if player_character else None,
        )

        participant_update = participant_result.get("scene_state_update", {})
        alias_update = participant_result.get("alias_cache_update", {})

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
        )

        character_contributions = collect_character_contributions(
            world=active_world,
            scene_state=temp_scene_state,
            user_input=user_input,
        )

        character_authored_intents = (
            collect_character_authored_intents_from_contributions(
                character_contributions
            )
        )

        context = build_turn_context(
            active_world,
            temp_scene_state,
            user_input,
            character_authored_intents=character_authored_intents,
            character_contributions=character_contributions,
        )

        result = call_cassandra(context)

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

    Proposal.objects.create(
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
    )

    return redirect("scene_page")


def approve_draft(request, proposal_id):
    if request.method != "POST":
        return redirect("scene_page")

    proposal = get_object_or_404(Proposal, id=proposal_id)
    world = proposal.world

    if proposal.is_approved:
        return redirect("scene_page")

    scene_state, _ = SceneState.objects.get_or_create(
        world=world,
        defaults={
            "location": "opening scene",
            "cast_json": {},
            "pending_intents_json": {},
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
    )

    scene_text = (
        f"[User]\n{proposal.user_input or ''}\n\n"
        f"[Cassandra]\n{proposal.draft or ''}"
    )

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

    aftermath = extract_scene_aftermath(
        world=world,
        user_input=proposal.user_input,
        final_draft=committed_scene.cassandra_text,
        resolved_scene_state=final_resolved_state,
        scene_events=proposal.scene_events_json or [],
        character_contributions=proposal.character_contributions_json or [],
        character_registry=build_character_registry(world),
    )

    print(
        "AFTERMATH COUNTS:",
        "narrative_memories=", len(aftermath.get("narrative_memories", [])),
        "character_experience_updates=", len(aftermath.get("character_experience_updates", [])),
    )

    for memory_text in aftermath.get("narrative_memories", []):
        if memory_text:
            NarrativeMemory.objects.create(
                world=world,
                content=memory_text,
                source_scene=committed_scene,
            )

    aftermath_scene_state_update = aftermath.get("scene_state_update") or {}
    aftermath_location = aftermath_scene_state_update.get("location")

    if aftermath_location:
        final_resolved_state["location"] = aftermath_location

    persist_character_experience_updates(
        world=world,
        resolved_scene_state=final_resolved_state,
        experience_updates=aftermath.get("character_experience_updates", []),
        source_scene=committed_scene,
    )

    scene_state.alias_cache_json = final_resolved_state.get(
        "alias_cache",
        scene_state.alias_cache_json,
    )
    scene_state.location = final_resolved_state.get(
        "location",
        scene_state.location,
    )
    scene_state.cast_json = final_resolved_state.get(
        "cast",
        scene_state.cast_json,
    )
    scene_state.pending_intents_json = resolved_pending_intents
    scene_state.save()

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


def character_creation_form(request):
    if request.method == "POST":
        form = CharacterForm(request.POST)
        if form.is_valid():
            character = form.save()
            profile, _ = CharacterProfile.objects.get_or_create(
                character=character,
            )

            profile.summary = form.cleaned_data.get("profile_summary", "")
            profile.archetype = form.cleaned_data.get("archetype", "")
            profile.gender = form.cleaned_data.get("gender", "")
            profile.pronouns_json = {
                "subject": form.cleaned_data.get("pronoun_subject", ""),
                "object": form.cleaned_data.get("pronoun_object", ""),
                "possessive": form.cleaned_data.get("pronoun_possessive", ""),
                "possessive_pronoun": form.cleaned_data.get("pronoun_possessive_pronoun", ""),
                "reflexive": form.cleaned_data.get("pronoun_reflexive", ""),
            }
            profile.save()

            return redirect("cast_page")

    else:
        form = CharacterForm()

    return render(request, "story/create_character.html", {
        "form": form,
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
        }
    )
    original_draft = proposal.draft
    edited_draft = request.POST.get("edited_draft", "").strip()
    revision_feedback = request.POST.get("revision_feedback", "").strip()
    text_changed = materially_changed(original_draft, edited_draft)
    rewrite_from_scratch = request.POST.get("rewrite_from_scratch") == "true"

    if not text_changed and not revision_feedback and not rewrite_from_scratch:
        return redirect("scene_page")

    revision_mode = choose_revision_mode(
        original_draft=original_draft,
        revised_draft=edited_draft,
        revision_feedback=revision_feedback,
        rewrite_from_scratch=rewrite_from_scratch,
    )

    effective_revised_draft = edited_draft or original_draft

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
        )

        result = call_cassandra_revision(context)

        # 🔹 Draft handling
        if revision_mode == "interpret_user_edit":
            proposal.draft = edited_draft
        else:
            proposal.draft = result.get("draft", proposal.draft)

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
