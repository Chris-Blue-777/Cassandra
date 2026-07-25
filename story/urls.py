from django.urls import path
from . import views

urlpatterns = [
    path("", views.scene_page, name="scene_page"),
    path("worlds/create/", views.create_world, name="create_world"),
    path("switch-world/", views.switch_world, name="switch_world"),
    path("story-arcs/create/", views.create_story_arc, name="create_story_arc"),
    path(
        "story-arcs/<int:arc_id>/status/",
        views.update_story_arc_status,
        name="update_story_arc_status",
    ),
    path(
        "story-arcs/<int:arc_id>/edit/",
        views.edit_story_arc,
        name="edit_story_arc",
    ),
    path(
        "story-arc-proposals/<int:proposal_id>/decision/",
        views.decide_story_arc_update_proposal,
        name="decide_story_arc_update_proposal",
    ),
    path("generate-draft/", views.generate_draft, name="generate_draft"),
    path("approve-draft/<int:proposal_id>/", views.approve_draft, name="approve_draft"),
    path(
        "delete-scene/<int:scene_id>/",
        views.delete_committed_scene,
        name="delete_committed_scene",
    ),
    path("relationship-map/", views.relationship_map_page, name="relationship_map"),
    path(
        "wanda/visual-identities/",
        views.wanda_visual_identity_page,
        name="wanda_visual_identities",
    ),
    path(
        "wanda/media-jobs/",
        views.wanda_media_jobs_page,
        name="wanda_media_jobs",
    ),
    path(
        "wanda/media-jobs/review-portrait/",
        views.review_portrait_media_job,
        name="review_portrait_media_job",
    ),
    path(
        "wanda/media-jobs/review-general-image/",
        views.review_general_image_media_job,
        name="review_general_image_media_job",
    ),
    path(
        "wanda/media-jobs/<int:job_id>/edit-general-image/",
        views.review_general_image_media_job,
        name="edit_general_image_media_job",
    ),
    path(
        "wanda/media-jobs/review-scene-image/",
        views.review_scene_image_media_job,
        name="review_scene_image_media_job",
    ),
    path(
        "wanda/media-jobs/<int:job_id>/edit-scene-image/",
        views.review_scene_image_media_job,
        name="edit_scene_image_media_job",
    ),
    path(
        "wanda/media-jobs/review-video/",
        views.review_video_media_job,
        name="review_video_media_job",
    ),
    path(
        "wanda/media-assets/<int:asset_id>/review-video/",
        views.review_asset_video_media_job,
        name="review_asset_video_media_job",
    ),
    path(
        "wanda/media-jobs/quick-create-portrait/",
        views.quick_create_portrait_media_job,
        name="quick_create_portrait_media_job",
    ),
    path(
        "wanda/media-jobs/<int:job_id>/upload-asset/",
        views.upload_generated_media_asset,
        name="upload_generated_media_asset",
    ),
    path(
        "wanda/media-assets/<int:asset_id>/save-as-reference/",
        views.save_generated_media_asset_as_reference,
        name="save_generated_media_asset_as_reference",
    ),
    path(
        "wanda/media-assets/<int:asset_id>/delete/",
        views.delete_generated_media_asset,
        name="delete_generated_media_asset",
    ),
    path(
        "wanda/media-jobs/<int:job_id>/generate/",
        views.confirm_media_provider_generation,
        name="confirm_media_provider_generation",
    ),
    path(
        "wanda/media-jobs/<int:job_id>/check-provider/",
        views.check_media_provider_generation_status,
        name="check_media_provider_generation_status",
    ),
    path(
        "wanda/media-jobs/<int:job_id>/copy/",
        views.copy_media_job,
        name="copy_media_job",
    ),
    path(
        "wanda/media-jobs/<int:job_id>/delete/",
        views.delete_media_job,
        name="delete_media_job",
    ),
    path("cast/", views.cast_page, name="cast_page"),
    path("create-character/", views.character_creation_form, name="create_character"),
    path("revise-draft/<int:proposal_id>/", views.revise_draft, name="revise_draft"),
    path(
    "characters/<int:character_id>/edit/",
    views.character_edit_form,
    name="character_edit_form",
),
]
