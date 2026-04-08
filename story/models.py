from django.db import models
from django.utils.text import slugify

# Create your models here.
class World(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=False)

    def __str__(self):
        return self.name

class SceneState(models.Model):
    world = models.OneToOneField(World, on_delete=models.CASCADE, related_name="scene_state")
    location = models.CharField(max_length=255, blank=True, default="")
    cast_json = models.JSONField(default=dict)
    pending_intents_json = models.JSONField(default=dict)

    def __str__(self):
        return f"SceneState for {self.world.name}"

class Proposal(models.Model):
    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="proposals")
    user_input = models.TextField()
    draft = models.TextField()
    editors_craft_memory_json = models.JSONField(default=list, blank=True)
    revision_change_summary = models.TextField(blank=True, default="")
    revision_intent_summary = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    is_approved = models.BooleanField(default=False)
    character_authored_intents_json = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"Proposal {self.id} for {self.world.name}"

class CommittedScene(models.Model):
    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="committed_scenes")
    user_text = models.TextField(blank=True, default="")
    cassandra_text = models.TextField(blank=True, default="")
    combined_text = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"CommittedScene {self.id} for {self.world.name}"

class Character(models.Model):
    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="characters")
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, default="")
    profile_json = models.JSONField(default=dict, blank=True)
    status_json = models.JSONField(default=dict, blank=True)
    diction_json = models.JSONField(default=dict, blank=True)
    is_player = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["world", "slug"], name="uniq_character_slug_per_world"),
        ]
        indexes = [
            models.Index(fields=["world", "slug"]),
            models.Index(fields=["world", "is_active"]),
        ]
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} in {self.world.name}"

class CharacterProfile(models.Model):
    character = models.OneToOneField(Character, on_delete=models.CASCADE, related_name="profile")

    summary = models.TextField(blank=True, default="")
    archetype = models.CharField(max_length=100, blank=True, default="")
    personality_json = models.JSONField(default=dict, blank=True)
    diction_json = models.JSONField(default=dict, blank=True)
    craft_notes_json = models.JSONField(default=dict, blank=True)
    background_json = models.JSONField(default=dict, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

class NarrativeMemory(models.Model):
    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="narrative_memories")
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"NarrativeMemory {self.id} for {self.world.name}"


class CharacterState(models.Model):
    character = models.OneToOneField(Character, on_delete=models.CASCADE, related_name="state")

    emotional_state_json = models.JSONField(default=dict, blank=True)
    goals_json = models.JSONField(default=dict, blank=True)
    internal_conflicts_json = models.JSONField(default=dict, blank=True)
    status_json = models.JSONField(default=dict, blank=True)
    active_intents_json = models.JSONField(default=dict, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

class CharacterPerception(models.Model):
    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="character_perceptions")
    observer = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="outgoing_perceptions")
    target = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="incoming_perceptions")

    summary = models.TextField(blank=True, default="")
    impression_json = models.JSONField(default=dict, blank=True)
    relationship_json = models.JSONField(default=dict, blank=True)
    belief_json = models.JSONField(default=dict, blank=True)
    arc_json = models.JSONField(default=dict, blank=True)
    trust = models.FloatField(default=0.0)
    attraction = models.FloatField(default=0.0)
    fear = models.FloatField(default=0.0)
    resentment = models.FloatField(default=0.0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["world", "observer", "target"],
                name="uniq_character_perception"
            )
        ]

class CharacterMemory(models.Model):
    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="character_memories")
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="memories")

    content = models.TextField()
    memory_type = models.CharField(max_length=50, blank=True, default="")
    related_character = models.ForeignKey(
        Character,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memories_about_me"
    )
    source_scene = models.ForeignKey(
        CommittedScene,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="character_memories"
    )

    created_at = models.DateTimeField(auto_now_add=True)

class CharacterBelief(models.Model):
    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="character_beliefs")
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="beliefs")

    subject_type = models.CharField(max_length=50, blank=True, default="")  # character, world, event, relationship
    subject_slug = models.CharField(max_length=100, blank=True, default="")
    belief = models.TextField()
    confidence = models.FloatField(default=0.5)
    is_true = models.BooleanField(null=True, blank=True)  # optional internal debugging field
    source = models.CharField(max_length=100, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class TempSceneState:
    def __init__(self, location, cast_json, pending_intents_json):
        self.location = location
        self.cast_json = cast_json
        self.pending_intents_json = pending_intents_json
