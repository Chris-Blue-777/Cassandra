from django.contrib import admin
from .models import World, SceneState, Proposal, CommittedScene

admin.site.register(World)
admin.site.register(SceneState)
admin.site.register(Proposal)
admin.site.register(CommittedScene)
# Register your models here.
