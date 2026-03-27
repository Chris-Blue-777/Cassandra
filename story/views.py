from django.shortcuts import render, redirect, get_object_or_404
from .models import World, SceneState, Proposal, CommittedScene, Character, NarrativeMemory, TempSceneState
from .Cassandra import call_cassandra, call_cassandra_revision, extract_memory_from_scene, call_cassandra_cast_resolution
from .Wanda import build_turn_context, build_revision_context, resolve_proposed_scene_state, serialize_scene_state, diff_scene_states, build_cast_resolution_context


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

    state_changes = []
    if latest_proposal and not latest_proposal.is_approved:
        state_changes = diff_scene_states(
            latest_proposal.prior_scene_state_json,
            latest_proposal.proposed_scene_state_json,
        )

    return render(request, "story/scene_page.html", {
        "worlds": worlds,
        "active_world": active_world,
        "proposal": latest_proposal,
        "committed_scenes": committed_scenes,
        "scene_state": scene_state,
        "narrative_memories": narrative_memories,
        "state_changes": state_changes,
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

    try:
        cast_resolution_context = build_cast_resolution_context(active_world, scene_state, user_input)
        cast_resolve_result = call_cassandra_cast_resolution(cast_resolution_context)
        resolved_scene_state = resolve_proposed_scene_state(
            current_state=serialize_scene_state(scene_state),
            scene_state_update=cast_resolve_result.get("scene_state_update", {}),
            pending_intents=cast_resolve_result.get("pending_intents", scene_state.pending_intents_json),
        )
        temp_scene_state = TempSceneState(
            location=resolved_scene_state.get("location", ""),
            cast_json=resolved_scene_state.get("cast", {}),
            pending_intents_json=resolved_scene_state.get("pending_intents", {}),
        )
        context = build_turn_context(active_world, temp_scene_state, user_input)
        result = call_cassandra(context)
    except Exception as e:
        return render(request, "story/scene_page.html", {
            "worlds": World.objects.all().order_by("name"),
            "active_world": active_world,
            "proposal": active_world.proposals.order_by("-created_at").first(),
            "committed_scenes": active_world.committed_scenes.order_by("-created_at")[:20],
            "scene_state": scene_state,
            "state_changes": [],
            "error": f"Cassandra error: {e}",
        })

    prior_scene_state = serialize_scene_state(scene_state)
    proposed_scene_state = resolve_proposed_scene_state(
        current_state=prior_scene_state,
        scene_state_update=result["scene_state_update"],
        pending_intents=result["pending_intents"],
    )

    Proposal.objects.create(
        world=active_world,
        user_input=user_input,
        draft=result["draft"],
        scene_state_update_json=result["scene_state_update"],
        prior_scene_state_json=prior_scene_state,
        proposed_scene_state_json=proposed_scene_state,
        pending_intents_json=result["pending_intents"],
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
    narrative_memory = extract_memory_from_scene(
        user_input=proposal.user_input,
        draft=committed_scene.cassandra_text,
        )
    if narrative_memory:
        NarrativeMemory.objects.create(
            world=world,
            content=narrative_memory
        )

    scene_state, _ = SceneState.objects.get_or_create(
        world=world,
        defaults={
            "location": "opening scene",
            "cast_json": {},
            "pending_intents_json": {},
        }
    )
    approved_state = proposal.proposed_scene_state_json or {}
    scene_state.location = approved_state.get("location", scene_state.location)
    scene_state.cast_json = approved_state.get("cast", scene_state.cast_json)
    scene_state.pending_intents_json = approved_state.get(
        "pending_intents",
        scene_state.pending_intents_json,
    )
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
    # ^^^ redirect to home page if URL typed in manually ^^^ #

    proposal = get_object_or_404(Proposal, id=proposal_id)
    if proposal.is_approved:
        return redirect("scene_page")
    # ^^^ prevent revising already approved proposals ^^^ #

    active_world = proposal.world
    # ^^^ get world from proposal to ensure correct context even if user
    # switched worlds since proposal creation ^^^ #
    original_draft = proposal.draft
    # ^^^ keep original draft in case user wants to revert their edits ^^^ #
    edited_draft = request.POST.get("edited_draft", "").strip()
    # ^^^ allow user to optionally edit the draft themselves before sending to
    # Cassandra for revision ^^^ #
    revision_feedback = request.POST.get("revision_feedback", "").strip()
    # ^^^ get user's revision feedback from form input ^^^ #
    text_changed = bool(edited_draft) and edited_draft != original_draft
    # ^^^ determine if user made their own edits to the draft ^^^ #

    if not text_changed and not revision_feedback:
        return redirect("scene_page")
    # ^^^ if no user edits or feedback provided, just redirect without calling

    scene_state, _ = SceneState.objects.get_or_create(
        world=active_world,
        defaults={
            "location": "opening scene",
            "cast_json": {},
            "pending_intents_json": {},
        }
    )
    # ^^^ ensure scene state exists for Cassandra context ^^^ #
    try:
        if text_changed:
            context = build_revision_context(
                world=active_world,
                scene_state=scene_state,
                original_draft=original_draft,
                revised_draft=edited_draft,
                revision_feedback=revision_feedback,
                revision_mode="interpret_user_edit",
            )
    # ^^^ User-edited prose is authoritative ^^^
        else:
            context = build_revision_context(
                world=active_world,
                scene_state=scene_state,
                original_draft=original_draft,
                revised_draft=original_draft,
                revision_feedback=revision_feedback,
                revision_mode="rewrite_based_on_feedback",
             )

        result = call_cassandra_revision(context)
    # ^^^ call Cassandra revision endpoint with context including user's
    # revision feedback ^^^ #
        print("\n--- RAW CASSANDRA REVISION RESULT ---")
        print(result)

        print("\n--- MEMORY FIELD FROM RESULT ---")
        print(result.get("editors_craft_memory", "MISSING"))

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
                "state_changes": [],
                "error": f"Cassandra error during revision: {type(e).__name__}: {e}",
            })
    # ^^^ handle Cassandra errors gracefully and show error message on page ^^^ #

    prior_scene_state = proposal.prior_scene_state_json or serialize_scene_state(scene_state)
    proposed_scene_state = resolve_proposed_scene_state(
        current_state=prior_scene_state,
        scene_state_update=result["scene_state_update"],
        pending_intents=result["pending_intents"],
    )
    # ^^^ resolve proposed scene state based on Cassandra's revision response ^^^ #

    if text_changed:
        proposal.draft = edited_draft
    # ^^^ User-edited prose remains the proposal draft ^^^ #
    else:
        proposal.draft = result.get("draft", proposal.draft)
    # ^^^ Cassandra rewrites prose from editorial notes ^^^ #

    proposal.scene_state_update_json = result.get("scene_state_update", {})
    proposal.pending_intents_json = result.get("pending_intents", {})
    proposal.proposed_scene_state_json = proposed_scene_state

    print("\n--- BEFORE SAVE ---")
    print("Narrative memory about to assign:")
    print(result.get("editors_craft_memory", []))

    proposal.editors_craft_memory_json = result.get("editors_craft_memory", [])
    proposal.revision_change_summary = result.get("change_summary", "")
    proposal.revision_intent_summary = result.get("inferred_editorial_intent", "")

    proposal.save()
    # ^^^ update proposal with revised draft and new proposed scene state ^^^ #

    proposal.refresh_from_db()

    print("\n--- AFTER SAVE ---")
    print(proposal.editors_craft_memory_json)
    proposal.refresh_from_db()

    print("\n--- AFTER SAVE FULL ---")
    print("proposal.id =", proposal.id)
    print("editors_craft_memory_json =", proposal.editors_craft_memory_json)
    print("revision_change_summary =", proposal.revision_change_summary)
    print("revision_intent_summary =", proposal.revision_intent_summary)

    print("\n--- DJANGO MODEL FIELDS ---")
    print([f.name for f in Proposal._meta.fields])

    return redirect("scene_page")
