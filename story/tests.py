import base64
import json
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Character,
    CharacterBelief,
    CharacterPerception,
    CharacterVisualIdentity,
    CharacterVisualIdentityVersion,
    CharacterVisualReference,
    CommittedScene,
    GeneratedMediaAsset,
    GeneratedMediaJob,
    GeneratedMediaJobSubject,
    Proposal,
    StoryArc,
    StoryArcUpdateProposal,
    SubjectiveRelationshipEdge,
    World,
)
from .MissPots.characters import PENDING_BELIEF_REDUCTION_SOURCE
from .forms import StoryArcForm
from .arcs import active_story_arc_ooc_tags, normalize_story_arc_ooc_tag
from .Wanda import (
    GOOGLE_GEMINI_3_PRO_IMAGE_PROVIDER_ID,
    GOOGLE_NANO_BANANA_2_PROVIDER_ID,
    MEDIA_STYLE_MATCH_REFERENCE,
    MEDIA_STYLE_REALISTIC_PHOTO,
    RUNWAY_GEN45_VIDEO_PROVIDER_ID,
    check_runway_media_job_status,
    copy_media_job_for_retry,
    create_asset_video_media_job,
    create_portrait_media_job,
    create_scene_image_media_job,
    create_video_media_job,
    enqueue_media_job_with_provider,
    generate_media_job_with_provider,
    media_job_can_restart_background_generation,
    run_media_job_background_worker,
)
from .views import (
    _draft_user_input_from_post,
    _merge_cassandra_scene_state_consequences,
    _normalize_cassandra_scene_state_update,
)


class _MockGoogleResponse:
    def __init__(
        self,
        payload,
        *,
        status_code=200,
        text="{}",
        content=b"",
        headers=None,
        raise_error=None,
    ):
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.content = content
        self.headers = headers or {}
        self.raise_error = raise_error

    def raise_for_status(self):
        if self.raise_error:
            raise self.raise_error
        return None

    def json(self):
        return self._payload


class WorldCreationPageTests(TestCase):
    def test_create_world_page_loads_without_existing_worlds(self):
        response = self.client.get(reverse("create_world"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create World")
        self.assertContains(response, "Make this the active world")

    def test_create_world_can_make_new_world_active(self):
        response = self.client.post(
            reverse("create_world"),
            {
                "name": "New Story World",
                "description": "A fresh continuity container.",
                "make_active": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)

        world = World.objects.get(name="New Story World")
        self.assertTrue(world.is_active)
        self.assertContains(
            response,
            "Created New Story World and made it the active world.",
        )

    def test_scene_topbar_links_to_create_world_not_media_jobs(self):
        World.objects.create(name="Existing World", is_active=True)

        response = self.client.get(reverse("scene_page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("create_world"))
        self.assertNotContains(response, ">Media Jobs</button>")


class RelationshipMapPageTests(TestCase):
    def setUp(self):
        self.world = World.objects.create(name="Map Test World", is_active=True)
        self.mallory = Character.objects.create(
            world=self.world,
            name="Mallory",
            slug="mallory",
        )
        self.byrne = Character.objects.create(
            world=self.world,
            name="Byrne",
            slug="byrne",
        )
        self.donnie = Character.objects.create(
            world=self.world,
            name="Donnie",
            slug="donnie",
        )

    def _map_edges(self):
        response = self.client.get(
            reverse("relationship_map"),
            {"observer": "mallory"},
        )
        self.assertEqual(response.status_code, 200)
        return response.context["map_data"]["graph_edges"], response

    @staticmethod
    def _edge_matches(edge, slug_a, slug_b):
        return {
            edge.get("from_slug"),
            edge.get("to_slug"),
        } == {slug_a, slug_b}

    def test_active_multi_subject_belief_draws_belief_edge(self):
        belief = CharacterBelief.objects.create(
            world=self.world,
            character=self.mallory,
            subject_type="character",
            subject_slug="byrne",
            related_subject_slugs_json=["donnie"],
            belief="Donnie is Byrne's brother, and that connection matters.",
            confidence=0.9,
        )

        edges, response = self._map_edges()
        target_anchor = f"relationship-belief-edge-{belief.id}-byrne-donnie"

        self.assertTrue(
            any(
                edge["kind"] == "belief"
                and self._edge_matches(edge, "byrne", "donnie")
                and edge["target_anchor"] == target_anchor
                and edge["summary"] == belief.belief
                for edge in edges
            )
        )
        self.assertContains(response, "relationship-graph-edge-belief")
        self.assertContains(response, f'href="#{target_anchor}"')
        self.assertContains(response, f'id="{target_anchor}"')
        self.assertContains(response, "relationship-belief-edge-card")
        self.assertContains(response, "relationship-graph-bubble")

    def test_pending_multi_subject_belief_draws_pending_edge(self):
        belief = CharacterBelief.objects.create(
            world=self.world,
            character=self.mallory,
            subject_type="character",
            subject_slug="byrne",
            related_subject_slugs_json=["donnie"],
            belief="Byrne and Donnie may be locked into an old rivalry.",
            confidence=0.65,
            source=PENDING_BELIEF_REDUCTION_SOURCE,
        )

        edges, response = self._map_edges()
        target_anchor = (
            f"relationship-pending-belief-edge-{belief.id}-byrne-donnie"
        )

        self.assertTrue(
            any(
                edge["kind"] == "pending-belief"
                and self._edge_matches(edge, "byrne", "donnie")
                and edge["target_anchor"] == target_anchor
                and edge["summary"] == belief.belief
                for edge in edges
            )
        )
        self.assertContains(response, "relationship-graph-edge-pending-belief")
        self.assertContains(response, f'href="#{target_anchor}"')
        self.assertContains(response, f'id="{target_anchor}"')
        self.assertContains(response, "pending belief candidate")

    def test_two_known_non_observer_nodes_form_triangle(self):
        CharacterBelief.objects.create(
            world=self.world,
            character=self.mallory,
            subject_type="character",
            subject_slug="byrne",
            related_subject_slugs_json=["donnie"],
            belief="Byrne and Donnie have a relationship Mallory is tracking.",
            confidence=0.7,
        )

        edges, response = self._map_edges()
        observer_node = next(
            node for node in response.context["map_data"]["graph_nodes"]
            if node["slug"] == "mallory"
        )
        pair_edge = next(
            edge for edge in edges
            if self._edge_matches(edge, "byrne", "donnie")
        )

        self.assertEqual(round(pair_edge["y1"], 1), round(pair_edge["y2"], 1))
        self.assertNotEqual(
            round(observer_node["y"], 1),
            round(pair_edge["y1"], 1),
        )

    def test_direct_edge_uses_relationship_summary_not_personal_synopsis(self):
        CharacterPerception.objects.create(
            world=self.world,
            observer=self.mallory,
            target=self.donnie,
            summary="Mallory thinks Donnie is charming but dangerous.",
            relationship_json={
                "summary": "Mallory and Donnie have a charged flirtation.",
                "relationship_label": "charged flirtation",
            },
            knowledge_basis="Mallory noticed Donnie's eye contact.",
        )

        edges, response = self._map_edges()
        direct_edge = next(
            edge for edge in edges
            if edge["kind"] == "direct"
            and self._edge_matches(edge, "mallory", "donnie")
        )
        node = next(
            node for node in response.context["map_data"]["graph_nodes"]
            if node["slug"] == "donnie"
        )

        self.assertEqual(direct_edge["label"], "charged flirtation")
        self.assertEqual(
            direct_edge["summary"],
            "Mallory and Donnie have a charged flirtation.",
        )
        self.assertEqual(
            direct_edge["target_anchor"],
            "relationship-direct-mallory-donnie",
        )
        self.assertEqual(
            node["summary"],
            "Mallory thinks Donnie is charming but dangerous.",
        )
        self.assertEqual(node["target_anchor"], "relationship-character-donnie")
        self.assertContains(response, 'href="#relationship-direct-mallory-donnie"')
        self.assertContains(response, 'id="relationship-character-donnie"')
        self.assertContains(response, "charged flirtation")
        self.assertContains(response, "relationship-direct-edge-card")

    def test_direct_edge_derives_label_from_old_summary_only_data(self):
        CharacterPerception.objects.create(
            world=self.world,
            observer=self.mallory,
            target=self.donnie,
            summary="Mallory thinks Donnie is charming but dangerous.",
            relationship_json={
                "summary": "Mallory and Donnie have a charged flirtation."
            },
            knowledge_basis="Mallory noticed Donnie's eye contact.",
        )

        edges, _response = self._map_edges()
        direct_edge = next(
            edge for edge in edges
            if edge["kind"] == "direct"
            and self._edge_matches(edge, "mallory", "donnie")
        )

        self.assertEqual(direct_edge["label"], "flirtation")

    def test_stored_subjective_edge_suppresses_belief_fallback_edge(self):
        CharacterBelief.objects.create(
            world=self.world,
            character=self.mallory,
            subject_type="character",
            subject_slug="byrne",
            related_subject_slugs_json=["donnie"],
            belief="Donnie is Byrne's brother, and that connection matters.",
            confidence=0.9,
        )
        stored_edge = SubjectiveRelationshipEdge.objects.create(
            world=self.world,
            observer=self.mallory,
            subject_a=self.byrne,
            subject_b=self.donnie,
            relationship_label="brothers",
            summary="Mallory understands Byrne and Donnie as brothers.",
            confidence=0.95,
        )

        edges, _response = self._map_edges()
        pair_edges = [
            edge for edge in edges
            if self._edge_matches(edge, "byrne", "donnie")
        ]

        self.assertTrue(any(edge["kind"] == "social" for edge in pair_edges))
        self.assertFalse(any(edge["kind"] == "belief" for edge in pair_edges))
        self.assertTrue(
            any(
                edge["kind"] == "social"
                and edge["target_anchor"] == f"relationship-edge-{stored_edge.id}"
                for edge in pair_edges
            )
        )


class GoogleNanoBananaProviderTests(TestCase):
    def setUp(self):
        self._media_root = tempfile.mkdtemp()
        self._settings = override_settings(
            MEDIA_ROOT=self._media_root,
            GEMINI_API_KEY="test-gemini-key",
            RUNWAYML_API_SECRET="test-runway-key",
        )
        self._settings.enable()

        self.world = World.objects.create(name="Provider Test World", is_active=True)
        self.character = Character.objects.create(
            world=self.world,
            name="Mallory",
            slug="mallory",
        )
        self.visual_identity = CharacterVisualIdentity.objects.create(
            world=self.world,
            character=self.character,
            status=CharacterVisualIdentity.STATUS_ACTIVE,
            is_locked=True,
            appearance_summary="Mallory has a consistent test appearance.",
            canonical_identity_prompt="Mallory's canonical face and body anchors.",
            current_version=1,
        )
        self.version = CharacterVisualIdentityVersion.objects.create(
            visual_identity=self.visual_identity,
            world=self.world,
            character=self.character,
            version_number=1,
            status=CharacterVisualIdentity.STATUS_ACTIVE,
            is_locked=True,
            appearance_summary=self.visual_identity.appearance_summary,
            canonical_identity_prompt=(
                self.visual_identity.canonical_identity_prompt
            ),
        )

    def tearDown(self):
        self._settings.disable()
        shutil.rmtree(self._media_root, ignore_errors=True)

    def _reference(self, index, *, primary=False):
        return CharacterVisualReference.objects.create(
            world=self.world,
            character=self.character,
            visual_identity=self.visual_identity,
            identity_version=self.version,
            kind=CharacterVisualReference.KIND_FACE,
            is_primary=primary,
            file=SimpleUploadedFile(
                f"mallory-ref-{index}.png",
                f"reference-{index}".encode("utf-8"),
                content_type="image/png",
            ),
        )

    def _identity_for(self, character, *, version_number=1):
        identity = CharacterVisualIdentity.objects.create(
            world=self.world,
            character=character,
            status=CharacterVisualIdentity.STATUS_ACTIVE,
            is_locked=True,
            appearance_summary=f"{character.name} has a consistent test appearance.",
            canonical_identity_prompt=f"{character.name}'s canonical anchors.",
            current_version=version_number,
        )
        version = CharacterVisualIdentityVersion.objects.create(
            visual_identity=identity,
            world=self.world,
            character=character,
            version_number=version_number,
            status=CharacterVisualIdentity.STATUS_ACTIVE,
            is_locked=True,
            appearance_summary=identity.appearance_summary,
            canonical_identity_prompt=identity.canonical_identity_prompt,
        )
        return identity, version

    def _reference_for(self, character, identity, version, index, *, primary=False):
        return CharacterVisualReference.objects.create(
            world=self.world,
            character=character,
            visual_identity=identity,
            identity_version=version,
            kind=CharacterVisualReference.KIND_FACE,
            is_primary=primary,
            file=SimpleUploadedFile(
                f"{character.slug}-ref-{index}.png",
                f"{character.slug}-reference-{index}".encode("utf-8"),
                content_type="image/png",
            ),
        )

    def _job(self, references, *, primary_reference=None):
        selected_ids = ",".join(str(reference.id) for reference in references)
        primary_reference = primary_reference or references[0]
        return create_portrait_media_job(
            self.world,
            self.character,
            visual_identity_version=self.version,
            selected_reference_ids=selected_ids,
            primary_reference_id=str(primary_reference.id),
            status=GeneratedMediaJob.STATUS_READY_FOR_PROVIDER,
        )

    def _video_job(
        self,
        references=None,
        *,
        video_mode=GeneratedMediaJob.MODE_VIDEO_IMAGE,
        primary_reference=None,
    ):
        references = references or []
        selected_ids = ",".join(str(reference.id) for reference in references)
        primary_reference = primary_reference or (references[0] if references else None)
        return create_video_media_job(
            self.world,
            self.character,
            visual_identity_version=self.version,
            video_mode=video_mode,
            selected_reference_ids=selected_ids,
            primary_reference_id=(
                str(primary_reference.id) if primary_reference else ""
            ),
            status=GeneratedMediaJob.STATUS_READY_FOR_PROVIDER,
        )

    def _success_payload(self):
        return {
            "interaction": {
                "id": "google-interaction-123",
                "model": "gemini-3.1-flash-image",
                "output_image": {
                    "data": base64.b64encode(b"fake-image-bytes").decode("ascii"),
                    "mime_type": "image/png",
                },
            }
        }

    def _step_success_payload(self):
        return {
            "id": "google-step-interaction-123",
            "model": "gemini-3.1-flash-image",
            "status": "completed",
            "steps": [
                {
                    "type": "user_input",
                    "input": [
                        {"type": "text", "text": "Create a portrait."},
                        {
                            "type": "image",
                            "data": base64.b64encode(
                                b"echoed-reference-image"
                            ).decode("ascii"),
                            "mime_type": "image/png",
                        },
                    ],
                },
                {
                    "type": "model_output",
                    "output": [
                        {
                            "type": "image",
                            "data": base64.b64encode(
                                b"fake-step-image-bytes"
                            ).decode("ascii"),
                            "mime_type": "image/png",
                        }
                    ],
                },
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
            },
        }

    @patch("story.Wanda.httpx.post")
    def test_success_creates_completed_asset_with_provenance(self, mock_post):
        references = [self._reference(1, primary=True), self._reference(2)]
        job = self._job(references, primary_reference=references[1])
        mock_post.return_value = _MockGoogleResponse(self._success_payload())

        result = generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )

        self.assertTrue(result["ok"])
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_COMPLETED)
        self.assertEqual(job.provider, GOOGLE_NANO_BANANA_2_PROVIDER_ID)

        asset = GeneratedMediaAsset.objects.get(job=job)
        self.assertEqual(asset.world, self.world)
        self.assertEqual(asset.target_character, self.character)
        self.assertEqual(asset.visual_identity_version, self.version)
        self.assertEqual(asset.provider, GOOGLE_NANO_BANANA_2_PROVIDER_ID)
        self.assertEqual(asset.provider_asset_id, "google-interaction-123")
        self.assertEqual(
            asset.metadata_json["selected_reference_ids"],
            [references[1].id, references[0].id],
        )
        self.assertEqual(
            asset.metadata_json["job_primary_reference_id"],
            references[1].id,
        )
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["input"][0]["type"], "text")
        self.assertIn("Mallory", payload["input"][0]["text"])
        self.assertEqual(payload["input"][1]["type"], "image")
        self.assertIn("data", payload["input"][1])
        self.assertIn("mime_type", payload["input"][1])
        self.assertNotIn("image", payload["input"][1])
        self.assertEqual(payload["response_format"]["type"], "image")

    def test_default_portrait_job_matches_reference_style_not_photo(self):
        reference = self._reference(1, primary=True)
        job = self._job([reference])

        self.assertEqual(
            job.prompt_packet_json["visual_style"]["mode"],
            MEDIA_STYLE_MATCH_REFERENCE,
        )
        self.assertIn("Create a portrait image of Mallory.", job.prompt)
        self.assertIn("Visual style mode: Match reference style.", job.prompt)
        self.assertIn("style anchors", job.prompt)
        self.assertNotIn("portrait-style photo", job.prompt)
        self.assertIn("different medium", job.negative_prompt)

    def test_realistic_photo_style_is_explicit_when_selected(self):
        reference = self._reference(1, primary=True)
        job = create_portrait_media_job(
            self.world,
            self.character,
            visual_identity_version=self.version,
            selected_reference_ids=str(reference.id),
            primary_reference_id=str(reference.id),
            style_mode=MEDIA_STYLE_REALISTIC_PHOTO,
            status=GeneratedMediaJob.STATUS_READY_FOR_PROVIDER,
        )

        self.assertEqual(
            job.prompt_packet_json["visual_style"]["mode"],
            MEDIA_STYLE_REALISTIC_PHOTO,
        )
        self.assertIn("Create a realistic portrait photo of Mallory.", job.prompt)
        self.assertIn("Visual style mode: Realistic photo.", job.prompt)
        self.assertIn("Avoid illustration", job.negative_prompt)

    @patch("story.Wanda._start_media_job_background_thread")
    def test_provider_post_queues_without_waiting_for_provider(self, mock_start):
        reference = self._reference(1, primary=True)
        job = self._job([reference])

        response = self.client.post(
            reverse("confirm_media_provider_generation", args=[job.id]),
            {"provider_id": GOOGLE_NANO_BANANA_2_PROVIDER_ID},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"?job={job.id}", response["Location"])
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_QUEUED)
        self.assertEqual(job.provider, GOOGLE_NANO_BANANA_2_PROVIDER_ID)
        self.assertEqual(
            job.provider_request_json["adapter_status"],
            "queued_for_local_worker",
        )
        self.assertEqual(
            job.provider_request_json["local_runner"]["phase"],
            "queued",
        )
        self.assertFalse(GeneratedMediaAsset.objects.filter(job=job).exists())
        mock_start.assert_not_called()

    @patch("story.Wanda.httpx.post")
    def test_enqueue_marks_job_queued_without_provider_call(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])

        result = enqueue_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
            start_worker=False,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "queued")
        mock_post.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_QUEUED)
        self.assertEqual(job.provider, GOOGLE_NANO_BANANA_2_PROVIDER_ID)
        self.assertIn("execution_id", job.provider_request_json["local_runner"])
        self.assertEqual(
            job.provider_request_json["local_runner"]["phase"],
            "queued",
        )
        self.assertFalse(GeneratedMediaAsset.objects.filter(job=job).exists())

    @patch("story.Wanda.httpx.post")
    def test_background_google_success_creates_asset(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])
        enqueue_result = enqueue_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
            start_worker=False,
        )
        mock_post.return_value = _MockGoogleResponse(self._success_payload())

        result = run_media_job_background_worker(
            job.id,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
            enqueue_result["execution_id"],
        )

        self.assertTrue(result["ok"])
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_COMPLETED)
        self.assertEqual(
            job.provider_request_json["local_runner"]["phase"],
            "completed",
        )
        self.assertTrue(job.provider_response_json["has_output_image"])
        self.assertEqual(GeneratedMediaAsset.objects.filter(job=job).count(), 1)

    @patch("story.Wanda.httpx.post")
    def test_background_google_failure_marks_job_failed(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])
        enqueue_result = enqueue_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
            start_worker=False,
        )
        mock_post.side_effect = RuntimeError("provider exploded")

        result = run_media_job_background_worker(
            job.id,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
            enqueue_result["execution_id"],
        )

        self.assertFalse(result["ok"])
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_FAILED)
        self.assertEqual(
            job.provider_request_json["local_runner"]["phase"],
            "failed",
        )
        self.assertIn("provider exploded", job.error_message)
        self.assertFalse(GeneratedMediaAsset.objects.filter(job=job).exists())

    @patch("story.Wanda.httpx.post")
    def test_duplicate_enqueue_does_not_start_second_provider_call(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])
        first_result = enqueue_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
            start_worker=False,
        )
        job.refresh_from_db()
        first_execution_id = job.provider_request_json["local_runner"]["execution_id"]

        second_result = enqueue_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
            start_worker=False,
        )

        self.assertTrue(first_result["ok"])
        self.assertFalse(second_result["ok"])
        mock_post.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(
            job.provider_request_json["local_runner"]["execution_id"],
            first_execution_id,
        )

    def test_stale_queued_job_can_be_restarted(self):
        reference = self._reference(1, primary=True)
        job = self._job([reference])
        first_result = enqueue_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
            start_worker=False,
        )
        job.refresh_from_db()
        request_json = job.provider_request_json
        request_json["local_runner"]["queued_at"] = (
            timezone.now() - timedelta(minutes=11)
        ).isoformat()
        job.provider_request_json = request_json
        job.save(update_fields=["provider_request_json", "updated_at"])

        self.assertTrue(media_job_can_restart_background_generation(job))
        restart_result = enqueue_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
            restart_stale=True,
            start_worker=False,
        )

        self.assertTrue(restart_result["ok"])
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_QUEUED)
        self.assertNotEqual(
            job.provider_request_json["local_runner"]["execution_id"],
            first_result["execution_id"],
        )
        self.assertEqual(job.provider_response_json, {})

    @patch("story.Wanda.httpx.post")
    def test_generated_asset_can_be_saved_as_character_reference(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])
        mock_post.return_value = _MockGoogleResponse(self._success_payload())
        generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )
        asset = GeneratedMediaAsset.objects.get(job=job)

        response = self.client.post(
            reverse("save_generated_media_asset_as_reference", args=[asset.id]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"?asset={asset.id}", response["Location"])
        visual_reference = CharacterVisualReference.objects.get(
            kind=CharacterVisualReference.KIND_GENERATED_REFERENCE,
            character=self.character,
        )
        self.assertEqual(visual_reference.world, self.world)
        self.assertEqual(visual_reference.visual_identity, self.visual_identity)
        self.assertEqual(visual_reference.identity_version, self.version)
        self.assertEqual(visual_reference.provider, GOOGLE_NANO_BANANA_2_PROVIDER_ID)
        self.assertEqual(
            visual_reference.metadata_json["source_generated_media_asset_id"],
            asset.id,
        )
        self.assertEqual(
            visual_reference.metadata_json["source_generated_media_job_id"],
            job.id,
        )
        with visual_reference.file.open("rb") as handle:
            self.assertEqual(handle.read(), b"fake-image-bytes")

    @patch("story.Wanda.httpx.post")
    def test_saving_generated_asset_as_reference_is_not_duplicated(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])
        mock_post.return_value = _MockGoogleResponse(self._success_payload())
        generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )
        asset = GeneratedMediaAsset.objects.get(job=job)

        self.client.post(
            reverse("save_generated_media_asset_as_reference", args=[asset.id]),
        )
        self.client.post(
            reverse("save_generated_media_asset_as_reference", args=[asset.id]),
        )

        self.assertEqual(
            CharacterVisualReference.objects.filter(
                kind=CharacterVisualReference.KIND_GENERATED_REFERENCE,
                character=self.character,
            ).count(),
            1,
        )

    def _arc(self):
        return StoryArc.objects.create(
            world=self.world,
            slug="mallory-temptation",
            title="Mallory's temptation",
            status=StoryArc.STATUS_ACTIVE,
            scope=StoryArc.SCOPE_CHARACTER,
            subject_slugs_json=["mallory"],
            summary="Mallory is drawn toward a dangerous choice.",
            narrator_guidance="Keep Mallory's temptation available.",
            current_phase="Testing the danger",
            horizon="Mallory privately admits the charge matters.",
            constraints="Do not force a confession.",
            priority=0.8,
        )

    def _committed_scene(self):
        return CommittedScene.objects.create(
            world=self.world,
            turn_number=1,
            user_text="Mallory hesitates.",
            cassandra_text="Mallory realizes the hesitation means something.",
        )

    def _pending_proposal(self):
        return Proposal.objects.create(
            world=self.world,
            user_input="Christopher waits.",
            draft="Mallory lets the moment answer him.",
            is_approved=False,
        )

    def test_applying_story_arc_update_proposal_advances_arc(self):
        arc = self._arc()
        scene = self._committed_scene()
        proposal = StoryArcUpdateProposal.objects.create(
            world=self.world,
            story_arc=arc,
            source_scene=scene,
            horizon_reached=True,
            evidence_summary="Mallory privately recognizes the charge.",
            rationale="The old horizon has landed; the arc needs a next pressure.",
            current_status=arc.status,
            current_phase=arc.current_phase,
            current_horizon=arc.horizon,
            current_summary=arc.summary,
            current_narrator_guidance=arc.narrator_guidance,
            current_constraints=arc.constraints,
            proposed_status=StoryArc.STATUS_ACTIVE,
            proposed_phase="Living with the knowledge",
            proposed_horizon="Mallory chooses whether to act on the charge.",
            proposed_summary="Mallory now knows the temptation is real.",
            proposed_narrator_guidance=(
                "Let the knowledge complicate her choices without forcing action."
            ),
            proposed_constraints="Do not make the decision for her.",
        )

        response = self.client.post(
            reverse("decide_story_arc_update_proposal", args=[proposal.id]),
            {"decision": "apply"},
        )

        self.assertEqual(response.status_code, 302)
        arc.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, StoryArcUpdateProposal.STATUS_APPLIED)
        self.assertEqual(arc.status, StoryArc.STATUS_ACTIVE)
        self.assertEqual(arc.current_phase, "Living with the knowledge")
        self.assertEqual(
            arc.horizon,
            "Mallory chooses whether to act on the charge.",
        )
        self.assertEqual(arc.summary, "Mallory now knows the temptation is real.")

    def test_skipping_story_arc_update_proposal_leaves_arc_unchanged(self):
        arc = self._arc()
        scene = self._committed_scene()
        proposal = StoryArcUpdateProposal.objects.create(
            world=self.world,
            story_arc=arc,
            source_scene=scene,
            proposed_status=StoryArc.STATUS_RESOLVED,
            proposed_phase="Done",
            proposed_horizon="",
            proposed_summary="Resolved.",
            proposed_narrator_guidance="",
            proposed_constraints="",
        )

        response = self.client.post(
            reverse("decide_story_arc_update_proposal", args=[proposal.id]),
            {"decision": "skip"},
        )

        self.assertEqual(response.status_code, 302)
        arc.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, StoryArcUpdateProposal.STATUS_SKIPPED)
        self.assertEqual(arc.status, StoryArc.STATUS_ACTIVE)
        self.assertEqual(arc.current_phase, "Testing the danger")
        self.assertEqual(
            arc.horizon,
            "Mallory privately admits the charge matters.",
        )

    def test_scene_page_renders_pending_story_arc_update_proposal(self):
        arc = self._arc()
        scene = self._committed_scene()
        self._pending_proposal()
        StoryArcUpdateProposal.objects.create(
            world=self.world,
            story_arc=arc,
            source_scene=scene,
            horizon_reached=True,
            evidence_summary="The horizon landed in the approved scene.",
            rationale="A new near-term direction is needed.",
            current_status=arc.status,
            current_phase=arc.current_phase,
            current_horizon=arc.horizon,
            current_summary=arc.summary,
            current_narrator_guidance=arc.narrator_guidance,
            current_constraints=arc.constraints,
            proposed_status=StoryArc.STATUS_ACTIVE,
            proposed_phase="After the admission",
            proposed_horizon="Mallory decides what to do next.",
            proposed_summary="Mallory has crossed into awareness.",
            proposed_narrator_guidance="Keep the consequence alive.",
            proposed_constraints="Do not force action.",
        )

        response = self.client.get(reverse("scene_page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cassandra suggested 1 arc update")
        self.assertContains(response, "The horizon landed in the approved scene.")
        self.assertContains(response, "Apply Update")
        self.assertNotContains(response, "<h4>Suggested arc updates</h4>")

    def test_scene_page_auto_skips_stale_story_arc_update_proposal(self):
        arc = self._arc()
        old_scene = self._committed_scene()
        CommittedScene.objects.create(
            world=self.world,
            turn_number=2,
            user_text="The scene moves on.",
            cassandra_text="The pressure changes.",
        )
        self._pending_proposal()
        proposal = StoryArcUpdateProposal.objects.create(
            world=self.world,
            story_arc=arc,
            source_scene=old_scene,
            evidence_summary="Old evidence.",
            proposed_status=StoryArc.STATUS_ACTIVE,
            proposed_phase="Old phase",
            proposed_horizon="Old horizon",
            proposed_summary="Old summary.",
            proposed_narrator_guidance="Old guidance.",
            proposed_constraints="Old constraints.",
        )

        response = self.client.get(reverse("scene_page"))

        self.assertEqual(response.status_code, 200)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, StoryArcUpdateProposal.STATUS_SKIPPED)
        self.assertIsNotNone(proposal.decided_at)
        self.assertNotContains(response, "Old evidence.")

    def test_approving_draft_skips_unapplied_arc_update_from_prior_turn(self):
        arc = self._arc()
        scene = self._committed_scene()
        draft = self._pending_proposal()
        arc_update = StoryArcUpdateProposal.objects.create(
            world=self.world,
            story_arc=arc,
            source_scene=scene,
            evidence_summary="Visible until the next approval.",
            proposed_status=StoryArc.STATUS_ACTIVE,
            proposed_phase="Timeout phase",
            proposed_horizon="Timeout horizon",
            proposed_summary="Timeout summary.",
            proposed_narrator_guidance="Timeout guidance.",
            proposed_constraints="Timeout constraints.",
        )

        response = self.client.post(reverse("approve_draft", args=[draft.id]))

        self.assertEqual(response.status_code, 302)
        arc_update.refresh_from_db()
        self.assertEqual(
            arc_update.status,
            StoryArcUpdateProposal.STATUS_SKIPPED,
        )
        self.assertIsNotNone(arc_update.decided_at)

    def test_story_arc_ooc_tag_normalization(self):
        self.assertEqual(
            normalize_story_arc_ooc_tag("Mallory is heavily attracted to Donnie"),
            "[OOC: Mallory is heavily attracted to Donnie.]",
        )
        self.assertEqual(
            normalize_story_arc_ooc_tag("[OOC: Mallory is heavily attracted to Donnie.]"),
            "[OOC: Mallory is heavily attracted to Donnie.]",
        )

    def test_story_arc_form_saves_normalized_ooc_tag(self):
        form = StoryArcForm({
            "title": "OOC tag arc",
            "slug": "ooc-tag-arc",
            "scope": StoryArc.SCOPE_WORLD,
            "subject_slugs": "",
            "summary": "An arc with a submission tag.",
            "narrator_guidance": "Keep the pressure active.",
            "ooc_tag": "Mallory is heavily attracted to Donnie",
            "current_phase": "",
            "horizon": "",
            "constraints": "",
            "priority": "0.5",
            "character_lenses": "",
        })

        self.assertTrue(form.is_valid(), form.errors)
        arc = form.save(commit=False)

        self.assertEqual(
            arc.ooc_tag,
            "[OOC: Mallory is heavily attracted to Donnie.]",
        )

    def test_active_story_arc_ooc_tags_order_by_priority(self):
        StoryArc.objects.create(
            world=self.world,
            slug="lower",
            title="Lower",
            status=StoryArc.STATUS_ACTIVE,
            ooc_tag="Lower pressure",
            priority=0.2,
        )
        StoryArc.objects.create(
            world=self.world,
            slug="higher",
            title="Higher",
            status=StoryArc.STATUS_ACTIVE,
            ooc_tag="[OOC: Higher pressure.]",
            priority=0.9,
        )
        StoryArc.objects.create(
            world=self.world,
            slug="dormant",
            title="Dormant",
            status=StoryArc.STATUS_DORMANT,
            ooc_tag="Dormant pressure",
            priority=1.0,
        )

        self.assertEqual(
            active_story_arc_ooc_tags(self.world),
            [
                "[OOC: Higher pressure.]",
                "[OOC: Lower pressure.]",
            ],
        )

    def test_draft_user_input_appends_active_arc_ooc_tags_once(self):
        StoryArc.objects.create(
            world=self.world,
            slug="attraction",
            title="Attraction",
            status=StoryArc.STATUS_ACTIVE,
            ooc_tag="[OOC: Mallory is heavily attracted to Donnie.]",
            priority=0.8,
        )

        user_input = _draft_user_input_from_post(
            {
                "user_input": (
                    "I clear my throat.\n\n"
                    "[OOC: Mallory is heavily attracted to Donnie.]"
                ),
            },
            world=self.world,
        )

        self.assertEqual(
            user_input.count("[OOC: Mallory is heavily attracted to Donnie.]"),
            1,
        )

    def test_surprise_me_includes_directive_and_active_arc_ooc_tags(self):
        StoryArc.objects.create(
            world=self.world,
            slug="attraction",
            title="Attraction",
            status=StoryArc.STATUS_ACTIVE,
            ooc_tag="Mallory is heavily attracted to Donnie",
            priority=0.8,
        )

        user_input = _draft_user_input_from_post(
            {
                "user_input": "I clear my throat.",
                "surprise_me": "true",
            },
            world=self.world,
        )

        self.assertEqual(
            user_input,
            (
                "I clear my throat.\n\n"
                "[OOC: surprise me]\n\n"
                "[OOC: Mallory is heavily attracted to Donnie.]"
            ),
        )

    def test_scene_page_prefills_active_arc_ooc_tags(self):
        StoryArc.objects.create(
            world=self.world,
            slug="attraction",
            title="Attraction",
            status=StoryArc.STATUS_ACTIVE,
            ooc_tag="Mallory is heavily attracted to Donnie",
            priority=0.8,
        )

        response = self.client.get(reverse("scene_page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "[OOC: Mallory is heavily attracted to Donnie.]",
        )
        self.assertContains(
            response,
            "Active story arc OOC tag will be included with your submission.",
        )

    def test_create_story_arc_accepts_long_phase_and_horizon(self):
        long_phase = ("Phase detail. " * 30).strip()
        long_horizon = ("Horizon detail. " * 30).strip()

        response = self.client.post(reverse("create_story_arc"), {
            "title": "Long arc",
            "slug": "long-arc",
            "scope": StoryArc.SCOPE_GROUP,
            "subject_slugs": "mallory, donnie",
            "summary": "A long-form arc.",
            "narrator_guidance": "Keep this pressure available.",
            "current_phase": long_phase,
            "horizon": long_horizon,
            "constraints": "Do not rush.",
            "priority": "0.7",
            "character_lenses": '{"mallory": "Notice the pressure."}',
        })

        self.assertEqual(response.status_code, 302)
        arc = StoryArc.objects.get(slug="long-arc")
        self.assertEqual(arc.current_phase, long_phase)
        self.assertEqual(arc.horizon, long_horizon)
        self.assertEqual(arc.subject_slugs_json, ["mallory", "donnie"])
        self.assertEqual(
            arc.character_lenses_json,
            {"mallory": "Notice the pressure."},
        )

    def test_edit_story_arc_updates_content_without_changing_status(self):
        arc = self._arc()
        arc.status = StoryArc.STATUS_DORMANT
        arc.save(update_fields=["status", "updated_at"])
        long_phase = ("Edited phase. " * 25).strip()
        long_horizon = ("Edited horizon. " * 25).strip()

        response = self.client.post(reverse("edit_story_arc", args=[arc.id]), {
            "title": "Edited temptation",
            "slug": "edited-temptation",
            "scope": StoryArc.SCOPE_GROUP,
            "subject_slugs": "mallory, donnie, christopher",
            "summary": "Edited summary.",
            "narrator_guidance": "Edited guidance.",
            "current_phase": long_phase,
            "horizon": long_horizon,
            "constraints": "Edited constraints.",
            "priority": "0.95",
            "character_lenses": '{"donnie": {"presentation_bias": "Charm first."}}',
        })

        self.assertEqual(response.status_code, 302)
        arc.refresh_from_db()
        self.assertEqual(arc.status, StoryArc.STATUS_DORMANT)
        self.assertEqual(arc.title, "Edited temptation")
        self.assertEqual(arc.slug, "edited-temptation")
        self.assertEqual(arc.scope, StoryArc.SCOPE_GROUP)
        self.assertEqual(
            arc.subject_slugs_json,
            ["mallory", "donnie", "christopher"],
        )
        self.assertEqual(arc.current_phase, long_phase)
        self.assertEqual(arc.horizon, long_horizon)
        self.assertEqual(
            arc.character_lenses_json,
            {"donnie": {"presentation_bias": "Charm first."}},
        )

    def test_edit_story_arc_rejects_duplicate_slug(self):
        arc = self._arc()
        StoryArc.objects.create(
            world=self.world,
            slug="existing-arc",
            title="Existing arc",
        )

        response = self.client.post(reverse("edit_story_arc", args=[arc.id]), {
            "title": arc.title,
            "slug": "existing-arc",
            "scope": arc.scope,
            "subject_slugs": "mallory",
            "summary": arc.summary,
            "narrator_guidance": arc.narrator_guidance,
            "current_phase": arc.current_phase,
            "horizon": arc.horizon,
            "constraints": arc.constraints,
            "priority": str(arc.priority),
            "character_lenses": "",
        })

        self.assertEqual(response.status_code, 302)
        arc.refresh_from_db()
        self.assertEqual(arc.slug, "mallory-temptation")

    def test_edit_story_arc_rejects_malformed_character_lenses(self):
        arc = self._arc()

        response = self.client.post(reverse("edit_story_arc", args=[arc.id]), {
            "title": arc.title,
            "slug": arc.slug,
            "scope": arc.scope,
            "subject_slugs": "mallory",
            "summary": arc.summary,
            "narrator_guidance": arc.narrator_guidance,
            "current_phase": arc.current_phase,
            "horizon": arc.horizon,
            "constraints": arc.constraints,
            "priority": str(arc.priority),
            "character_lenses": "{not-json",
        })

        self.assertEqual(response.status_code, 302)
        arc.refresh_from_db()
        self.assertEqual(arc.character_lenses_json, {})

    def test_story_arc_update_proposals_preserve_long_phase_and_horizon(self):
        from .views import _persist_story_arc_update_proposals

        arc = self._arc()
        scene = self._committed_scene()
        long_phase = ("Proposed phase. " * 25).strip()
        long_horizon = ("Proposed horizon. " * 25).strip()

        created_count = _persist_story_arc_update_proposals(
            self.world,
            scene,
            [{
                "arc_slug": arc.slug,
                "horizon_reached": True,
                "evidence_summary": "Evidence.",
                "rationale": "Rationale.",
                "proposed_status": StoryArc.STATUS_ACTIVE,
                "proposed_current_phase": long_phase,
                "proposed_horizon": long_horizon,
                "proposed_summary": "Updated summary.",
                "proposed_narrator_guidance": "Updated guidance.",
                "proposed_constraints": "Updated constraints.",
            }],
        )

        self.assertEqual(created_count, 1)
        proposal = StoryArcUpdateProposal.objects.get(
            story_arc=arc,
            source_scene=scene,
        )
        self.assertEqual(proposal.proposed_phase, long_phase)
        self.assertEqual(proposal.proposed_horizon, long_horizon)

    @patch("story.Wanda.httpx.post")
    def test_success_reads_image_from_interaction_steps(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])
        mock_post.return_value = _MockGoogleResponse(self._step_success_payload())

        result = generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )

        self.assertTrue(result["ok"])
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_COMPLETED)
        self.assertEqual(
            job.provider_response_json["id"],
            "google-step-interaction-123",
        )
        self.assertTrue(job.provider_response_json["has_output_image"])
        self.assertEqual(job.provider_response_json["step_count"], 2)
        self.assertEqual(
            job.provider_response_json["step_types"],
            ["user_input", "model_output"],
        )
        self.assertIn(
            "$.steps[1].output[0]",
            job.provider_response_json["output_source_path"],
        )

        asset = GeneratedMediaAsset.objects.get(job=job)
        with asset.file.open("rb") as handle:
            self.assertEqual(handle.read(), b"fake-step-image-bytes")

    @patch("story.Wanda.httpx.post")
    def test_provider_failure_marks_job_failed_without_asset(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])
        mock_post.side_effect = RuntimeError("provider exploded")

        result = generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )

        self.assertFalse(result["ok"])
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_FAILED)
        self.assertIn("provider exploded", job.error_message)
        self.assertFalse(GeneratedMediaAsset.objects.filter(job=job).exists())

    @patch("story.Wanda.httpx.post")
    def test_http_failure_prefers_provider_readable_error_message(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])
        provider_message = (
            "gemini-3.1-flash-image is currently experiencing high demand, "
            "spikes in demand are usually temporary. Please try again later."
        )
        mock_post.return_value = _MockGoogleResponse(
            {
                "error": {
                    "message": provider_message,
                    "code": "api_error",
                },
            },
            status_code=500,
            raise_error=RuntimeError("500 Internal Server Error"),
        )

        result = generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )

        self.assertFalse(result["ok"])
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_FAILED)
        self.assertEqual(job.error_message, provider_message)
        self.assertEqual(job.provider_response_json["error"]["code"], "api_error")
        self.assertFalse(GeneratedMediaAsset.objects.filter(job=job).exists())

    @patch("story.Wanda.httpx.post")
    def test_step_response_without_image_saves_safe_step_summary(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])
        mock_post.return_value = _MockGoogleResponse({
            "id": "google-step-no-image-123",
            "model": "gemini-3.1-flash-image",
            "status": "completed",
            "steps": [
                {
                    "type": "model_output",
                    "output": [
                        {
                            "type": "text",
                            "text": "No image was produced.",
                        }
                    ],
                },
            ],
        })

        result = generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )

        self.assertFalse(result["ok"])
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_FAILED)
        self.assertEqual(job.provider_response_json["step_count"], 1)
        self.assertEqual(job.provider_response_json["step_types"], ["model_output"])
        self.assertFalse(job.provider_response_json["has_output_image"])
        self.assertEqual(
            job.provider_response_json["text_snippets"],
            ["No image was produced."],
        )
        self.assertIn(
            "returned text instead of image data",
            job.error_message,
        )
        self.assertFalse(GeneratedMediaAsset.objects.filter(job=job).exists())

    @patch("story.Wanda.httpx.post")
    @override_settings(GEMINI_API_KEY="")
    def test_missing_api_key_blocks_without_changing_ready_status(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])

        result = generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        mock_post.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_READY_FOR_PROVIDER)

    @patch("story.Wanda.httpx.post")
    def test_more_than_four_selected_refs_blocks_google_submission(self, mock_post):
        references = [self._reference(index) for index in range(1, 6)]
        job = self._job(references)

        result = generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        mock_post.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_READY_FOR_PROVIDER)

    @patch("story.Wanda.httpx.post")
    def test_exactly_four_selected_refs_can_submit(self, mock_post):
        references = [self._reference(index) for index in range(1, 5)]
        job = self._job(references)
        mock_post.return_value = _MockGoogleResponse(self._success_payload())

        result = generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )

        self.assertTrue(result["ok"])
        mock_post.assert_called_once()

    @patch("story.Wanda.httpx.post")
    def test_gemini_3_pro_image_uses_pro_model_and_allows_five_refs(self, mock_post):
        references = [self._reference(index) for index in range(1, 6)]
        job = self._job(references)
        mock_post.return_value = _MockGoogleResponse({
            "interaction": {
                "id": "google-pro-interaction-123",
                "model": "gemini-3-pro-image",
                "output_image": {
                    "data": base64.b64encode(b"fake-pro-image").decode("ascii"),
                    "mime_type": "image/png",
                },
            }
        })

        result = generate_media_job_with_provider(
            job,
            GOOGLE_GEMINI_3_PRO_IMAGE_PROVIDER_ID,
        )

        self.assertTrue(result["ok"])
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "gemini-3-pro-image")
        self.assertEqual(len(payload["input"]) - 1, 5)
        job.refresh_from_db()
        self.assertEqual(job.provider, GOOGLE_GEMINI_3_PRO_IMAGE_PROVIDER_ID)
        self.assertEqual(job.provider_request_json["model"], "gemini-3-pro-image")

    @patch("story.Wanda.httpx.post")
    def test_raw_base64_is_not_persisted(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])
        mock_post.return_value = _MockGoogleResponse(self._success_payload())

        generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )

        job.refresh_from_db()
        persisted = json.dumps({
            "request": job.provider_request_json,
            "response": job.provider_response_json,
        })
        self.assertNotIn(base64.b64encode(b"reference-1").decode("ascii"), persisted)
        self.assertNotIn(
            base64.b64encode(b"fake-image-bytes").decode("ascii"),
            persisted,
        )

    @patch("story.Wanda.httpx.post")
    def test_google_text_only_response_saves_readable_failure(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._job([reference])
        mock_post.return_value = _MockGoogleResponse({
            "id": "google-text-only-123",
            "model": "gemini-3.1-flash-image",
            "status": "completed",
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "I cannot generate that image because the "
                                "request conflicts with safety policy."
                            ),
                        },
                    ],
                },
            ],
        })

        result = generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )

        self.assertFalse(result["ok"])
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_FAILED)
        self.assertIn(
            "returned text instead of image data",
            job.error_message,
        )
        self.assertIn(
            "safety policy",
            job.provider_response_json["text_snippets"][0],
        )
        self.assertFalse(GeneratedMediaAsset.objects.filter(job=job).exists())

    @patch("story.Wanda.httpx.post")
    def test_runway_image_to_video_submit_queues_task_without_asset(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._video_job([reference])
        mock_post.return_value = _MockGoogleResponse({
            "id": "runway-task-123",
            "status": "PENDING",
        })

        result = generate_media_job_with_provider(
            job,
            RUNWAY_GEN45_VIDEO_PROVIDER_ID,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "queued")
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "gen4.5")
        self.assertEqual(payload["duration"], 5)
        self.assertEqual(payload["ratio"], "1280:720")
        self.assertIn("promptImage", payload)
        self.assertTrue(payload["promptImage"].startswith("data:image/png;base64,"))

        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_QUEUED)
        self.assertEqual(job.provider, RUNWAY_GEN45_VIDEO_PROVIDER_ID)
        self.assertEqual(job.provider_request_json["task_id"], "runway-task-123")
        self.assertFalse(job.provider_request_json["raw_base64_persisted"])
        self.assertNotIn("promptImage", json.dumps(job.provider_request_json))
        self.assertFalse(GeneratedMediaAsset.objects.filter(job=job).exists())

    @patch("story.Wanda.httpx.post")
    def test_background_runway_submission_stores_task_id(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._video_job([reference])
        enqueue_result = enqueue_media_job_with_provider(
            job,
            RUNWAY_GEN45_VIDEO_PROVIDER_ID,
            start_worker=False,
        )
        mock_post.return_value = _MockGoogleResponse({
            "id": "runway-background-task-123",
            "status": "PENDING",
        })

        result = run_media_job_background_worker(
            job.id,
            RUNWAY_GEN45_VIDEO_PROVIDER_ID,
            enqueue_result["execution_id"],
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "queued")
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_QUEUED)
        self.assertEqual(
            job.provider_request_json["task_id"],
            "runway-background-task-123",
        )
        self.assertEqual(
            job.provider_request_json["local_runner"]["phase"],
            "submitted",
        )
        self.assertFalse(GeneratedMediaAsset.objects.filter(job=job).exists())

    @patch("story.Wanda.httpx.post")
    @override_settings(RUNWAYML_API_SECRET="")
    def test_runway_missing_api_key_blocks_without_changing_status(self, mock_post):
        reference = self._reference(1, primary=True)
        job = self._video_job([reference])

        result = generate_media_job_with_provider(
            job,
            RUNWAY_GEN45_VIDEO_PROVIDER_ID,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        mock_post.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_READY_FOR_PROVIDER)

    @patch("story.Wanda.httpx.post")
    def test_runway_image_to_video_requires_reference(self, mock_post):
        job = self._video_job([])

        result = generate_media_job_with_provider(
            job,
            RUNWAY_GEN45_VIDEO_PROVIDER_ID,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        mock_post.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_READY_FOR_PROVIDER)

    @patch("story.Wanda.httpx.post")
    def test_runway_text_to_video_omits_prompt_image(self, mock_post):
        job = self._video_job(
            [],
            video_mode=GeneratedMediaJob.MODE_VIDEO_TEXT,
        )
        mock_post.return_value = _MockGoogleResponse({
            "id": "runway-text-task-123",
            "status": "PENDING",
        })

        result = generate_media_job_with_provider(
            job,
            RUNWAY_GEN45_VIDEO_PROVIDER_ID,
        )

        self.assertTrue(result["ok"])
        payload = mock_post.call_args.kwargs["json"]
        self.assertNotIn("promptImage", payload)
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_QUEUED)

    def test_character_video_job_stores_visual_subject(self):
        reference = self._reference(1, primary=True)
        job = self._video_job([reference])

        self.assertEqual(job.subjects.count(), 1)
        subject = job.subjects.get()
        self.assertEqual(subject.character, self.character)
        self.assertEqual(subject.visual_identity_version, self.version)
        self.assertEqual(subject.selected_reference_ids_json, [reference.id])
        self.assertEqual(
            job.prompt_packet_json["visual_subjects"][0]["slug"],
            "mallory",
        )

    @patch("story.Wanda.httpx.post")
    def test_runway_two_selected_refs_submit_as_first_last_prompt_images(self, mock_post):
        references = [self._reference(1, primary=True), self._reference(2)]
        job = self._video_job(references)
        mock_post.return_value = _MockGoogleResponse({
            "id": "runway-two-frame-task-123",
            "status": "PENDING",
        })

        result = generate_media_job_with_provider(
            job,
            RUNWAY_GEN45_VIDEO_PROVIDER_ID,
        )

        self.assertTrue(result["ok"])
        payload = mock_post.call_args.kwargs["json"]
        self.assertIsInstance(payload["promptImage"], list)
        self.assertEqual(
            [item["position"] for item in payload["promptImage"]],
            ["first", "last"],
        )
        job.refresh_from_db()
        self.assertEqual(
            job.provider_request_json["prompt_image_reference_ids"],
            [references[0].id, references[1].id],
        )
        self.assertFalse(job.provider_request_json["raw_base64_persisted"])

    @patch("story.Wanda.httpx.post")
    def test_scene_image_job_binds_multiple_visual_subjects(self, mock_post):
        tom = Character.objects.create(
            world=self.world,
            name="Tom",
            slug="tom",
        )
        tom_identity, tom_version = self._identity_for(tom)
        mallory_ref = self._reference(1, primary=True)
        tom_ref = self._reference_for(
            tom,
            tom_identity,
            tom_version,
            1,
            primary=True,
        )
        scene = CommittedScene.objects.create(
            world=self.world,
            turn_number=1,
            user_text="Mallory follows Tom into the room.",
            cassandra_text="Tom turns back toward Mallory in the doorway.",
        )
        job = create_scene_image_media_job(
            self.world,
            scene,
            subject_slugs="mallory,tom",
            provider=GOOGLE_NANO_BANANA_2_PROVIDER_ID,
            reference_asset_limit=4,
        )

        self.assertEqual(job.generation_mode, GeneratedMediaJob.MODE_SCENE_IMAGE)
        self.assertEqual(job.source_scene, scene)
        self.assertEqual(job.subjects.count(), 2)
        self.assertEqual(
            set(job.subjects.values_list("character__slug", flat=True)),
            {"mallory", "tom"},
        )
        self.assertEqual(
            sorted([
                reference["id"]
                for reference in job.prompt_packet_json["selected_references"]
            ]),
            sorted([mallory_ref.id, tom_ref.id]),
        )
        self.assertIn("Mallory", job.prompt)
        self.assertIn("Tom", job.prompt)

        mock_post.return_value = _MockGoogleResponse(self._success_payload())
        result = generate_media_job_with_provider(
            job,
            GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )

        self.assertTrue(result["ok"])
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(len(payload["input"]) - 1, 2)
        asset = GeneratedMediaAsset.objects.get(job=job)
        self.assertEqual(asset.metadata_json["visual_subjects"][0]["slug"], "mallory")

    def test_copy_media_job_for_retry_preserves_refs_without_copying_asset(self):
        donnie = Character.objects.create(
            world=self.world,
            name="Donnie",
            slug="donnie",
        )
        donnie_identity, donnie_version = self._identity_for(donnie)
        mallory_ref = self._reference(1, primary=True)
        donnie_ref = self._reference_for(
            donnie,
            donnie_identity,
            donnie_version,
            1,
            primary=True,
        )
        scene = CommittedScene.objects.create(
            world=self.world,
            turn_number=3,
            user_text="Mallory and Donnie pose together.",
            cassandra_text="The moment settles into a shared frame.",
        )
        source_job = create_scene_image_media_job(
            self.world,
            scene,
            subject_slugs="mallory,donnie",
            provider=GOOGLE_NANO_BANANA_2_PROVIDER_ID,
            selected_reference_ids_by_slug={
                "mallory": [str(mallory_ref.id)],
                "donnie": [str(donnie_ref.id)],
            },
            primary_reference_ids_by_slug={
                "mallory": str(mallory_ref.id),
                "donnie": str(donnie_ref.id),
            },
        )
        source_job.status = GeneratedMediaJob.STATUS_COMPLETED
        source_job.save(update_fields=["status", "updated_at"])
        GeneratedMediaAsset.objects.create(
            world=self.world,
            source_scene=scene,
            job=source_job,
            media_type=GeneratedMediaJob.MEDIA_TYPE_PHOTO,
            file=SimpleUploadedFile(
                "source-result.png",
                b"fake-result",
                content_type="image/png",
            ),
        )

        copied_job = copy_media_job_for_retry(source_job)

        self.assertEqual(
            copied_job.status,
            GeneratedMediaJob.STATUS_READY_FOR_PROVIDER,
        )
        self.assertEqual(copied_job.assets.count(), 0)
        self.assertEqual(copied_job.source_scene, scene)
        self.assertEqual(copied_job.provider, source_job.provider)
        self.assertEqual(
            copied_job.prompt_packet_json["copied_from_job_id"],
            source_job.id,
        )
        self.assertEqual(
            [
                reference["id"]
                for reference in copied_job.prompt_packet_json["selected_references"]
            ],
            [mallory_ref.id, donnie_ref.id],
        )
        self.assertEqual(
            {
                subject.character.slug: subject.selected_reference_ids_json
                for subject in copied_job.subjects.select_related("character")
            },
            {
                "mallory": [mallory_ref.id],
                "donnie": [donnie_ref.id],
            },
        )

    @patch("story.Wanda.httpx.post")
    def test_asset_video_job_uses_photo_asset_as_runway_prompt_image(self, mock_post):
        scene = CommittedScene.objects.create(
            world=self.world,
            turn_number=2,
            user_text="Mallory pauses with Tom.",
            cassandra_text="The doorway holds them both.",
        )
        scene_job = create_scene_image_media_job(
            self.world,
            scene,
            subject_slugs="mallory",
            provider=GOOGLE_NANO_BANANA_2_PROVIDER_ID,
        )
        asset = GeneratedMediaAsset.objects.create(
            world=self.world,
            source_scene=scene,
            job=scene_job,
            media_type=GeneratedMediaJob.MEDIA_TYPE_PHOTO,
            caption="Generated scene still.",
            file=SimpleUploadedFile(
                "scene-still.png",
                b"fake-scene-still",
                content_type="image/png",
            ),
            metadata_json={
                "visual_subjects": scene_job.prompt_packet_json["visual_subjects"],
            },
        )
        video_job = create_asset_video_media_job(
            self.world,
            asset,
            provider=RUNWAY_GEN45_VIDEO_PROVIDER_ID,
        )
        mock_post.return_value = _MockGoogleResponse({
            "id": "runway-asset-task-123",
            "status": "PENDING",
        })

        result = generate_media_job_with_provider(
            video_job,
            RUNWAY_GEN45_VIDEO_PROVIDER_ID,
        )

        self.assertTrue(result["ok"])
        payload = mock_post.call_args.kwargs["json"]
        self.assertTrue(payload["promptImage"].startswith("data:image/png;base64,"))
        video_job.refresh_from_db()
        self.assertEqual(
            video_job.provider_request_json["prompt_image_media_asset_ids"],
            [asset.id],
        )
        self.assertNotIn(
            base64.b64encode(b"fake-scene-still").decode("ascii"),
            json.dumps(video_job.provider_request_json),
        )

    @patch("story.Wanda.httpx.get")
    def test_runway_poll_pending_keeps_job_queued(self, mock_get):
        job = self._video_job(
            [],
            video_mode=GeneratedMediaJob.MODE_VIDEO_TEXT,
        )
        job.status = GeneratedMediaJob.STATUS_QUEUED
        job.provider = RUNWAY_GEN45_VIDEO_PROVIDER_ID
        job.provider_request_json = {"task_id": "runway-task-123"}
        job.save()
        mock_get.return_value = _MockGoogleResponse({
            "id": "runway-task-123",
            "status": "RUNNING",
        })

        result = check_runway_media_job_status(job)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "queued")
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_QUEUED)
        self.assertEqual(job.provider_response_json["status"], "RUNNING")
        self.assertFalse(GeneratedMediaAsset.objects.filter(job=job).exists())

    @patch("story.Wanda.httpx.get")
    def test_runway_poll_succeeded_downloads_video_asset(self, mock_get):
        reference = self._reference(1, primary=True)
        job = self._video_job([reference])
        job.status = GeneratedMediaJob.STATUS_QUEUED
        job.provider = RUNWAY_GEN45_VIDEO_PROVIDER_ID
        job.provider_request_json = {
            "task_id": "runway-task-123",
            "selected_reference_ids": [reference.id],
            "prompt_image_reference_id": reference.id,
        }
        job.save()
        mock_get.side_effect = [
            _MockGoogleResponse({
                "id": "runway-task-123",
                "status": "SUCCEEDED",
                "output": ["https://example.test/runway-output.mp4"],
            }),
            _MockGoogleResponse(
                {},
                content=b"fake-video-bytes",
                headers={"content-type": "video/mp4"},
            ),
        ]

        result = check_runway_media_job_status(job)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_COMPLETED)

        asset = GeneratedMediaAsset.objects.get(job=job)
        self.assertEqual(asset.media_type, GeneratedMediaJob.MEDIA_TYPE_VIDEO)
        self.assertEqual(asset.provider, RUNWAY_GEN45_VIDEO_PROVIDER_ID)
        self.assertEqual(asset.provider_asset_id, "runway-task-123")
        self.assertEqual(asset.metadata_json["selected_reference_ids"], [reference.id])
        self.assertEqual(asset.metadata_json["prompt_image_reference_id"], reference.id)
        with asset.file.open("rb") as handle:
            self.assertEqual(handle.read(), b"fake-video-bytes")

    @patch("story.Wanda.httpx.get")
    def test_runway_poll_failed_marks_job_failed(self, mock_get):
        job = self._video_job(
            [],
            video_mode=GeneratedMediaJob.MODE_VIDEO_TEXT,
        )
        job.status = GeneratedMediaJob.STATUS_QUEUED
        job.provider = RUNWAY_GEN45_VIDEO_PROVIDER_ID
        job.provider_request_json = {"task_id": "runway-task-123"}
        job.save()
        mock_get.return_value = _MockGoogleResponse({
            "id": "runway-task-123",
            "status": "FAILED",
            "failure": {"message": "Runway could not generate the video."},
        })

        result = check_runway_media_job_status(job)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        job.refresh_from_db()
        self.assertEqual(job.status, GeneratedMediaJob.STATUS_FAILED)
        self.assertIn("Runway could not generate", job.error_message)
        self.assertFalse(GeneratedMediaAsset.objects.filter(job=job).exists())


class CassandraSceneStateConsequenceTests(TestCase):
    def test_normalizes_event_supported_cast_update(self):
        raw_update = {
            "location": None,
            "cast": [
                {
                    "slug": "donnie",
                    "presence": "present",
                    "space_id": "kitchen",
                    "local_space_label": "kitchen doorway",
                    "position": "in the kitchen threshold",
                    "spatial_relation": "inside_scene",
                    "sensory_access": "direct_full",
                    "perceives": [
                        {
                            "target_slug": "mallory",
                            "access": "direct_full",
                            "reason": "Donnie can now see her from the doorway.",
                        },
                        {
                            "target_slug": "stranger",
                            "access": "direct_full",
                            "reason": "Invalid target should be dropped.",
                        },
                    ],
                },
            ],
        }
        scene_events = [
            {
                "actor_slug": "donnie",
                "target_slugs": ["christopher"],
                "perceived_by": ["donnie", "mallory"],
            },
        ]

        normalized = _normalize_cassandra_scene_state_update(
            raw_update,
            active_slugs={"donnie", "mallory", "christopher"},
            scene_events=scene_events,
        )

        self.assertEqual(normalized["location"], None)
        self.assertEqual(normalized["cast"]["donnie"]["presence"], "present")
        self.assertEqual(
            normalized["cast"]["donnie"]["spatial_relation"],
            "inside_scene",
        )
        self.assertEqual(
            normalized["cast"]["donnie"]["sensory_access"],
            "direct_full",
        )
        self.assertEqual(normalized["cast"]["donnie"]["perception_scope"], "full")
        self.assertEqual(
            set(normalized["cast"]["donnie"]["perceives"].keys()),
            {"mallory"},
        )

    def test_drops_invalid_and_unrelated_cast_updates(self):
        raw_update = {
            "location": "kitchen",
            "cast": [
                {
                    "slug": "mallory",
                    "presence": "present",
                    "space_id": "kitchen",
                    "local_space_label": "kitchen",
                    "position": "near the counter",
                    "spatial_relation": "inside_scene",
                    "sensory_access": "direct_full",
                    "perceives": [],
                },
                {
                    "slug": "not-a-character",
                    "presence": "present",
                    "space_id": "kitchen",
                    "local_space_label": "kitchen",
                    "position": "near the counter",
                    "spatial_relation": "inside_scene",
                    "sensory_access": "direct_full",
                    "perceives": [],
                },
            ],
        }
        scene_events = [
            {
                "actor_slug": "donnie",
                "target_slugs": ["christopher"],
                "perceived_by": ["donnie"],
            },
        ]

        normalized = _normalize_cassandra_scene_state_update(
            raw_update,
            active_slugs={"donnie", "mallory", "christopher"},
            scene_events=scene_events,
        )

        self.assertEqual(normalized["location"], "kitchen")
        self.assertEqual(normalized["cast"], {})

    def test_merges_consequences_into_proposed_scene_state(self):
        pre_draft_state = {
            "location": "house",
            "cast": {
                "donnie": {
                    "presence": "nearby",
                    "spatial_relation": "adjacent",
                    "sensory_access": "direct_partial",
                    "position": "in the entryway",
                    "space_id": "entryway",
                    "local_space_label": "entryway",
                    "perceives": {},
                },
                "mallory": {
                    "presence": "present",
                    "spatial_relation": "inside_scene",
                    "sensory_access": "direct_full",
                    "position": "in the kitchen",
                    "space_id": "kitchen",
                    "local_space_label": "kitchen",
                    "perceives": {},
                },
            },
            "spaces": {},
            "narrative_frame": {},
        }
        cassandra_update = {
            "location": None,
            "cast": {
                "donnie": {
                    "presence": "present",
                    "spatial_relation": "inside_scene",
                    "sensory_access": "direct_full",
                    "position": "in the kitchen doorway",
                    "space_id": "kitchen",
                    "local_space_label": "kitchen doorway",
                    "perceives": {},
                    "perception_scope": "full",
                    "can_receive_memory": True,
                    "can_receive_state_change": True,
                    "can_receive_perception_change": True,
                },
            },
        }

        merged = _merge_cassandra_scene_state_consequences(
            pre_draft_scene_state=pre_draft_state,
            cassandra_scene_state_update=cassandra_update,
            pending_intents={"donnie": {"next": "press into the room"}},
            alias_cache={"your brother": "donnie"},
        )

        self.assertEqual(merged["location"], "house")
        self.assertEqual(merged["cast"]["donnie"]["presence"], "present")
        self.assertEqual(merged["cast"]["donnie"]["space_id"], "kitchen")
        self.assertEqual(merged["cast"]["mallory"]["space_id"], "kitchen")
        self.assertEqual(
            merged["pending_intents"],
            {"donnie": {"next": "press into the room"}},
        )
        self.assertEqual(merged["alias_cache"], {"your brother": "donnie"})
