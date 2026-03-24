from urllib import request

from django.shortcuts import render, redirect, get_object_or_404
from .models import World, SceneState, Proposal, CommittedScene, Character
from .cassandra import call_cassandra, call_cassandra_revision

def fake_cassandra(world, scene_state, user_input):

    draft = (
        f"[World: {World.name}]\n\n"
        f"You:\n{user_input}\n\n"
        f"Cassandra draft:\n"
        f"the scene continues from {scene_state.location or 'an unspecified location'}, "
        f"With the current cast reaction to the new input."
    )

    scene_state_update = {
        "location": scene_state.location or "unspecified location",
        "cast": scene_state.cast_json or {},
    }

    pending_intents = scene_state.pending_intents_json or {}

    return {
        "draft": draft,
        "scene_state_update": scene_state_update,
        "pending_intents": pending_intents,
    }

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
            "error": "No worlds exist yet. Create one in Django admin.",
        })
    scene_state, _ = SceneState.objects.get_or_create(
        world=active_world,
        defaults={
            "location": "opening scene",
            "cast_json": {},
            "pending_intents_json":{},
        }
    )

    latest_proposal = active_world.proposals.order_by("-created_at").first()
    committed_scenes = active_world.committed_scenes.order_by("-created_at")[:10]

    return render(request, "story/scene_page.html", {
        "worlds": worlds,
        "active_world": active_world,
        "proposal": latest_proposal,
        "committed_scenes": committed_scenes,
        "scene_state": scene_state,
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
        result = call_cassandra(active_world, scene_state, user_input)
    except Exception as e:
        return render(request, "story/scene_page.html", {
            "worlds": World.objects.all().order_by("name"),
            "active_world": active_world,
            "proposal": active_world.proposals.order_by("-created_at").first(),
            "committed_scenes": active_world.committed_scenes.order_by("-created_at")[:10],
            "scene_state": scene_state,
            "error": f"Cassandra error: {e}",
    })

    Proposal.objects.create(
        world=active_world,
        user_input=user_input,
        draft=result["draft"],
        scene_state_update_json=result["scene_state_update"],
        pending_intents_json=result["pending_intents"],
        is_approved=False,
    )

    return redirect("scene_page")

def approve_draft(reuest, proposal_id):
    if reuest.method != "POST":
        return redirect("scene_page")

    proposal = get_object_or_404(Proposal, id=proposal_id)
    world = proposal.world

    proposal.is_approved = True
    proposal.save()

    CommittedScene.objects.create(
        world=world,
        text=proposal.draft,
    )

    scene_state, _ = SceneState.objects.get_or_create(world=world)
    scene_state.location = proposal.scene_state_update_json.get("location", scene_state.location)
    scene_state.cast_json = proposal.scene_state_update_json.get("cast", scene_state.cast_json)
    scene_state.pending_intents_json = proposal.pending_intents_json
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
    active_world = proposal.world

    revision_feedback = request.POST.get("revision_feedback", "").strip()

    if not revision_feedback:
        return redirect("scene_page")

    scene_state, _ = SceneState.objects.get_or_create(
        world=active_world,
        defaults={
            "location": "opening scene",
            "cast_json": [],
            "pending_intents_json": [],
        }
        )
    try:
        result = call_cassandra_revision(
            world=active_world,
            scene_state=scene_state,
            current_draft=proposal.draft,
            revision_feedback=revision_feedback,
            )

    except Exception as e:
            worlds = World.objects.all().order_by("name")
            latest_proposal = active_world.proposals.order_by("-created_at").first()
            committed_scenes = active_world.committed_scenes.order_by("-created_at")[:10]

            return render(request, "story/scene_page.html", {
                "worlds": worlds,
                "active_world": active_world,
                "proposal": latest_proposal,
                "committed_scenes": committed_scenes,
                "scene_state": scene_state,
                "error": f"Cassandra error during revision: {type(e).__name__}: {e}",
            })

    proposal.draft = result["draft"]
    proposal.scene_state_update_json = result["scene_state_update"]
    proposal.pending_intents_json = result["pending_intents"]
    proposal.save()

    return redirect("scene_page")





# Create your views here.
