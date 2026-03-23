from django.urls import path
from . import views

urlpatterns = [
    path("", views.scene_page, name="scene_page"),
    path("switch-world/", views.switch_world, name="switch_world"),
    path("generate-draft/", views.generate_draft, name="generate_draft"),
    path("approve-draft/<int:proposal_id>/", views.approve_draft, name="approve_draft"),
]
