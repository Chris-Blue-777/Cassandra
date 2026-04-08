from django.shortcuts import render, redirect, get_object_or_404
from .models import (
    World,
    SceneState,
    Proposal,
    CommittedScene,
    Character,
    NarrativeMemory,
    TempSceneState,
)
from .Cassandra import (
    call_cassandra,
    call_cassandra_revision,
    extract_memory_from_scene,
    materially_changed,
    choose_revision_mode
)
from .Wanda import (
    build_turn_context,
    build_revision_context,
    resolve_proposed_scene_state,
    serialize_scene_state,
    collect_character_authored_intents,
    resolve_intents,
    resolve_approved_scene_state
)
from .MissPots.cast_tracker import (
    infer_scene_participants_and_positions,
)


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

    latest_proposal = active_world.proposals.order_by("-created_at").first()
    committed_scenes = active_world.committed_scenes.order_by("-created_at")[:10]
    narrative_memories = active_world.narrative_memories.order_by("-created_at")[:2]

    return render(request, "story/scene_page.html", {
        "worlds": worlds,
        "active_world": active_world,
        "proposal": latest_proposal,
        "committed_scenes": committed_scenes,
        "scene_state": scene_state,
        "narrative_memories": narrative_memories,
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

    character_authored_intents = collect_character_authored_intents(
        world=active_world,
        scene_state=scene_state,
        user_input=user_input,
    )

    try:
        participant_result = infer_scene_participants_and_positions(
            world=active_world,
            scene_state=scene_state,
            user_input=user_input,
            pov_slug=None,  # replace later when POV is wired in
        )

        participant_update = participant_result.get("scene_state_update", {})

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
        )

        context = build_turn_context(active_world, temp_scene_state, user_input, character_authored_intents=character_authored_intents)
        result = call_cassandra(context)

    except Exception as e:
        return render(request, "story/scene_page.html", {
            "worlds": World.objects.all().order_by("name"),
            "active_world": active_world,
            "proposal": active_world.proposals.order_by("-created_at").first(),
            "committed_scenes": active_world.committed_scenes.order_by("-created_at")[:20],
            "scene_state": scene_state,
            "error": f"Draft generation error: {type(e).__name__}: {e}",
        })

    Proposal.objects.create(
        world=active_world,
        user_input=user_input,
        draft=result["draft"],
        character_authored_intents_json=character_authored_intents,
        is_approved=False,
        revision_change_summary=result.get("change_summary", ""),
        revision_intent_summary=result.get("inferred_editorial_intent", ""),
        editors_craft_memory_json=result.get("editors_craft_memory", []),
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

    combined_scene_text = f"""[User]
    {proposal.user_input}

    [Cassandra]
    {proposal.draft}
    """

    committed_scene = CommittedScene.objects.create(
        world=world,
        user_text=proposal.user_input,
        cassandra_text=proposal.draft,
        combined_text=combined_scene_text,
    )

    resolved_pending_intents = resolve_intents(
        world=world,
        scene_state=scene_state,
        user_input=proposal.user_input,
        final_draft=proposal.draft,
        character_authored_intents=proposal.character_authored_intents_json or {},
    )

    resolved_state = resolve_approved_scene_state(
        world=world,
        scene_state=scene_state,
        user_input=proposal.user_input,
        final_draft=proposal.draft,
        pending_intents=resolved_pending_intents,
        pov_slug=None,
    )

    narrative_memory = extract_memory_from_scene(
        world=world,
        user_input=proposal.user_input,
        draft=committed_scene.cassandra_text,
    )
    if narrative_memory:
        NarrativeMemory.objects.create(
            world=world,
            content=narrative_memory
        )

    scene_state.location = resolved_state.get("location", scene_state.location)
    scene_state.cast_json = resolved_state.get("cast", scene_state.cast_json)
    scene_state.pending_intents_json = resolved_state.get("pending_intents", {})
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

    return render(request, "story/create_character.html")


def create_character(request):
    if request.method != "POST":
        return redirect("create_character")

    active_world = World.objects.filter(is_active=True).first()
    if not active_world:
        return redirect("create_character")

    name = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()

    if not name:
        return render(request, "story/create_character.html", {
            "error": "Character name cannot be empty.",
        })

    Character.objects.create(
        world=active_world,
        name=name,
        description=description,
    )

    return redirect("cast_page")


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
        latest_proposal = active_world.proposals.order_by("-created_at").first()
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
