from django.db import models

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
    scene_state_update_json = models.JSONField(default=dict)
    pending_intents_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    is_approved = models.BooleanField(default=False)

    def __str__(self):
        return f"Proposal {self.id} for {self.world.name}"
class CommittedScene(models.Model):
    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="committed_scenes")
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"CommittedScene {self.id} for {self.world.name}"

class Character(models.Model):
    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="characters")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.name} in {self.world.name}"
