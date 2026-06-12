from django.contrib import admin
from .models import (
    CharacterProfile,
    World,
    SceneState,
    Proposal,
    CommittedScene,
    Character,
    NarrativeMemory,
    CharacterState,
    CharacterPerception,
    CharacterBelief,
    CharacterMemory,
    CharacterScene)

admin.site.register(World)
admin.site.register(SceneState)
admin.site.register(Proposal)
admin.site.register(CommittedScene)
admin.site.register(Character)
admin.site.register(NarrativeMemory)
admin.site.register(CharacterProfile)
admin.site.register(CharacterState)
admin.site.register(CharacterPerception)
admin.site.register(CharacterBelief)
admin.site.register(CharacterMemory)
admin.site.register(CharacterScene)
# Register your models here.
