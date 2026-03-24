from django.urls import path
from . import views

urlpatterns = [
    path("", views.scene_page, name="scene_page"),
    path("switch-world/", views.switch_world, name="switch_world"),
    path("generate-draft/", views.generate_draft, name="generate_draft"),
    path("approve-draft/<int:proposal_id>/", views.approve_draft, name="approve_draft"),
    path("cast/", views.cast_page, name="cast_page"),
    path("create-character/", views.character_creation_form, name="create_character"),
    path("create-character/submit/", views.create_character, name="create_character_submit"),
    path("revise-draft/<int:proposal_id>/", views.revise_draft, name="revise_draft"),
]
