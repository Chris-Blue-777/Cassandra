from django.contrib import admin
from .models import World, SceneState, Proposal, CommittedScene, Character, NarrativeMemory

admin.site.register(World)
admin.site.register(SceneState)
admin.site.register(Proposal)
admin.site.register(CommittedScene)
admin.site.register(Character)
admin.site.register(NarrativeMemory)
# Register your models here.
