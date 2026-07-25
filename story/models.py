# models.py#

from dataclasses import dataclass, field
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify


# =========================================================
# SETUP / IDENTITY
# =========================================================

class World(models.Model):  # pylint: disable=too-few-public-methods
    """
    Top-level container for an entire narrative world / story instance.

    A world owns:
    - characters
    - scene state
    - committed scenes
    - memories
    - beliefs
    - perception/state snapshots and deltas

    In practice, this is the main scoping boundary for all canonical data.
    """
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class Character(models.Model):
    AGENT_PROVIDER_OPENAI = "openai"
    AGENT_PROVIDER_GROK = "grok"

    AGENT_PROVIDER_CHOICES = [
        (AGENT_PROVIDER_OPENAI, "OpenAI"),
        (AGENT_PROVIDER_GROK, "Grok"),
    ]
    """
    Canonical identity record for a character in a world.

    This should stay lean:
    - identity
    - description
    - activation / player status

    Detailed persona/configuration belongs in CharacterProfile.
    """
    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="characters")
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, blank=True, null=True)
    agent_provider = models.CharField(
        max_length=32,
        choices=AGENT_PROVIDER_CHOICES,
        default=AGENT_PROVIDER_OPENAI,
    )
    description = models.TextField(blank=True, default="")
    is_player = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:  # pylint: disable=missing-class-docstring
        # pylint: disable=too-few-public-methods
        constraints = [
            models.UniqueConstraint(
                fields=["world", "slug"],
                name="uniq_character_slug_per_world"),
        ]
        indexes = [
            models.Index(fields=["world", "slug"]),
            models.Index(fields=["world", "is_active"]),
        ]

    def save(self, *args, **kwargs):
        # pylint: disable=missing-function-docstring
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} in {self.world.name}"


class CharacterProfile(models.Model):  # pylint: disable=too-few-public-methods
    """
    Structured setup/profile data for a character.

    This is the right place for authored persona, voice,
    and background material.
    It replaces the need for a generic Character.profile_json field.

    Fields:
    - summary: compact overview of the character
    - archetype: optional narrative shorthand label
    - personality_json: temperament, traits, drives, etc.
    - diction_json: speaking style / verbal habits
    - craft_notes_json: authorial guidance not meant as in-world truth
    - background_json: history, biography, formative events, etc.
    """
    character = models.OneToOneField(
        Character,
        on_delete=models.CASCADE,
        related_name="profile")
    summary = models.TextField(blank=True, default="")
    archetype = models.CharField(max_length=100, blank=True, default="")
    gender = models.CharField(max_length=100, blank=True, default="")
    pronouns_json = models.JSONField(default=dict, blank=True)
    personality_json = models.JSONField(default=dict, blank=True)
    diction_json = models.JSONField(default=dict, blank=True)
    craft_notes_json = models.JSONField(default=dict, blank=True)
    background_json = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    permabeliefs_json = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"Profile for {self.character.name}"


def character_visual_reference_upload_path(instance, filename):
    safe_world = slugify(getattr(instance.world, "name", "") or "world")
    safe_character = slugify(getattr(instance.character, "slug", "") or "character")
    safe_filename = str(filename or "reference").split("/")[-1]
    return f"visual-identities/{safe_world}/{safe_character}/{safe_filename}"


def generated_media_asset_upload_path(instance, filename):
    safe_world = slugify(getattr(instance.world, "name", "") or "world")
    safe_character = slugify(
        getattr(instance.target_character, "slug", "") or "character"
    )
    scene_label = "unscened"
    if getattr(instance, "source_scene_id", None) and instance.source_scene:
        scene_label = f"scene-{instance.source_scene.turn_number}"
    safe_filename = str(filename or "generated-media").split("/")[-1]
    return (
        f"generated-media/{safe_world}/{scene_label}/"
        f"{safe_character}/{safe_filename}"
    )


def generated_media_job_reference_upload_path(instance, filename):
    safe_world = slugify(getattr(instance.world, "name", "") or "world")
    job_label = (
        f"job-{instance.job_id}"
        if getattr(instance, "job_id", None)
        else "unjobbed"
    )
    safe_filename = str(filename or "job-reference").split("/")[-1]
    return f"generated-media-job-references/{safe_world}/{job_label}/{safe_filename}"


class CharacterVisualIdentity(models.Model):  # pylint: disable=too-few-public-methods
    """
    Canonical objective physical identity for future image/video generation.

    This is authorial media scaffolding, not character knowledge and not a
    normal Cassandra/character-agent text-generation context source.
    """
    STATUS_DRAFT = "draft"
    STATUS_ACTIVE = "active"
    STATUS_RETIRED = "retired"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_RETIRED, "Retired"),
    ]

    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="character_visual_identities",
    )
    character = models.OneToOneField(
        Character,
        on_delete=models.CASCADE,
        related_name="visual_identity",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
    )
    is_locked = models.BooleanField(default=False)
    appearance_summary = models.TextField(blank=True, default="")
    canonical_identity_prompt = models.TextField(blank=True, default="")
    negative_identity_prompt = models.TextField(blank=True, default="")
    traits_json = models.JSONField(default=dict, blank=True)
    allowed_variations_json = models.JSONField(default=dict, blank=True)
    provider_notes_json = models.JSONField(default=dict, blank=True)
    current_version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["world", "status"]),
            models.Index(fields=["world", "character"]),
        ]

    def __str__(self):
        return f"Visual identity for {self.character.name}"


class CharacterVisualIdentityVersion(models.Model):
    """
    Versioned working copy of a CharacterVisualIdentity revision.
    """
    SOURCE_MANUAL = "manual"
    SOURCE_IMPORTED = "imported"
    SOURCE_GENERATED = "generated"
    SOURCE_REVISED = "revised"

    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_IMPORTED, "Imported"),
        (SOURCE_GENERATED, "Generated"),
        (SOURCE_REVISED, "Revised"),
    ]

    visual_identity = models.ForeignKey(
        CharacterVisualIdentity,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="character_visual_identity_versions",
    )
    character = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="visual_identity_versions",
    )
    version_number = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=CharacterVisualIdentity.STATUS_CHOICES,
        default=CharacterVisualIdentity.STATUS_DRAFT,
    )
    is_locked = models.BooleanField(default=False)
    appearance_summary = models.TextField(blank=True, default="")
    canonical_identity_prompt = models.TextField(blank=True, default="")
    negative_identity_prompt = models.TextField(blank=True, default="")
    traits_json = models.JSONField(default=dict, blank=True)
    allowed_variations_json = models.JSONField(default=dict, blank=True)
    provider_notes_json = models.JSONField(default=dict, blank=True)
    change_reason = models.TextField(blank=True, default="")
    source = models.CharField(
        max_length=40,
        choices=SOURCE_CHOICES,
        default=SOURCE_MANUAL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["visual_identity", "version_number"],
                name="uniq_visual_identity_version_number",
            ),
            models.UniqueConstraint(
                fields=["visual_identity"],
                condition=Q(status=CharacterVisualIdentity.STATUS_ACTIVE),
                name="uniq_active_visual_identity_version",
            ),
        ]
        indexes = [
            models.Index(fields=["world", "character", "version_number"]),
        ]
        ordering = ["-version_number"]

    def __str__(self):
        return (
            f"{self.character.name} visual identity "
            f"v{self.version_number}"
        )


class CharacterVisualReference(models.Model):
    """
    Reference asset attached to a canonical character visual identity.
    """
    KIND_FACE = "face"
    KIND_FULL_BODY = "full_body"
    KIND_EXPRESSION_SHEET = "expression_sheet"
    KIND_OUTFIT = "outfit"
    KIND_MOOD_REFERENCE = "mood_reference"
    KIND_GENERATED_REFERENCE = "generated_reference"
    KIND_OTHER = "other"

    KIND_CHOICES = [
        (KIND_FACE, "Face"),
        (KIND_FULL_BODY, "Full body"),
        (KIND_EXPRESSION_SHEET, "Expression sheet"),
        (KIND_OUTFIT, "Outfit"),
        (KIND_MOOD_REFERENCE, "Mood reference"),
        (KIND_GENERATED_REFERENCE, "Generated reference"),
        (KIND_OTHER, "Other"),
    ]

    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="character_visual_references",
    )
    character = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="visual_references",
    )
    visual_identity = models.ForeignKey(
        CharacterVisualIdentity,
        on_delete=models.CASCADE,
        related_name="references",
    )
    identity_version = models.ForeignKey(
        CharacterVisualIdentityVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="references",
    )
    file = models.FileField(upload_to=character_visual_reference_upload_path)
    kind = models.CharField(
        max_length=40,
        choices=KIND_CHOICES,
        default=KIND_FACE,
    )
    is_primary = models.BooleanField(default=False)
    caption = models.TextField(blank=True, default="")
    generation_prompt = models.TextField(blank=True, default="")
    provider = models.CharField(max_length=80, blank=True, default="")
    provider_asset_id = models.CharField(max_length=200, blank=True, default="")
    metadata_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["character", "identity_version"],
                condition=Q(is_primary=True),
                name="uniq_primary_visual_reference_per_character_version",
            ),
        ]
        indexes = [
            models.Index(fields=["world", "character", "kind"]),
            models.Index(fields=["visual_identity", "is_primary"]),
        ]
        ordering = ["-is_primary", "-created_at"]

    def __str__(self):
        return f"{self.character.name} visual reference {self.id}"


class GeneratedMediaJob(models.Model):
    """
    Persistent Wanda media-generation prompt/job record.

    Jobs preserve the exact prompt packet, provider status, and generated
    asset provenance for Wanda's photo/video workbench.
    """
    SOURCE_WANDA_IDENTITY = "wanda_identity"
    SOURCE_APPROVED_SCENE = "approved_scene"
    SOURCE_GENERAL = "general"

    SOURCE_CHOICES = [
        (SOURCE_WANDA_IDENTITY, "Wanda identity"),
        (SOURCE_APPROVED_SCENE, "Approved scene"),
        (SOURCE_GENERAL, "General"),
    ]

    MEDIA_TYPE_PHOTO = "photo"
    MEDIA_TYPE_VIDEO = "video"

    MEDIA_TYPE_CHOICES = [
        (MEDIA_TYPE_PHOTO, "Photo"),
        (MEDIA_TYPE_VIDEO, "Video"),
    ]

    MODE_PORTRAIT = "portrait"
    MODE_SCENE_IMAGE = "scene_image"
    MODE_GENERAL_IMAGE = "general_image"
    MODE_VIDEO_IMAGE = "video_image"
    MODE_VIDEO_TEXT = "video_text"

    GENERATION_MODE_CHOICES = [
        (MODE_PORTRAIT, "Portrait"),
        (MODE_SCENE_IMAGE, "Scene image"),
        (MODE_GENERAL_IMAGE, "General image"),
        (MODE_VIDEO_IMAGE, "Image to video"),
        (MODE_VIDEO_TEXT, "Text to video"),
    ]

    STATUS_DRAFT = "draft"
    STATUS_QUEUED = "queued"
    STATUS_READY_FOR_PROVIDER = "ready_for_provider"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELED = "canceled"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_QUEUED, "Queued"),
        (STATUS_READY_FOR_PROVIDER, "Ready for provider"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELED, "Canceled"),
    ]

    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="generated_media_jobs",
    )
    source = models.CharField(
        max_length=40,
        choices=SOURCE_CHOICES,
        default=SOURCE_WANDA_IDENTITY,
    )
    media_type = models.CharField(
        max_length=20,
        choices=MEDIA_TYPE_CHOICES,
        default=MEDIA_TYPE_PHOTO,
    )
    generation_mode = models.CharField(
        max_length=40,
        choices=GENERATION_MODE_CHOICES,
        default=MODE_PORTRAIT,
    )
    status = models.CharField(
        max_length=40,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
    )
    title = models.CharField(max_length=220, blank=True, default="")
    source_scene = models.ForeignKey(
        "CommittedScene",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="generated_media_jobs",
    )
    target_character = models.ForeignKey(
        Character,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_media_jobs",
    )
    visual_identity = models.ForeignKey(
        CharacterVisualIdentity,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_media_jobs",
    )
    visual_identity_version = models.ForeignKey(
        CharacterVisualIdentityVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_media_jobs",
    )
    provider = models.CharField(max_length=80, blank=True, default="")
    prompt = models.TextField(blank=True, default="")
    negative_prompt = models.TextField(blank=True, default="")
    user_prompt_override = models.TextField(blank=True, default="")
    prompt_packet_json = models.JSONField(default=dict, blank=True)
    provider_request_json = models.JSONField(default=dict, blank=True)
    provider_response_json = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["world", "status", "media_type"]),
            models.Index(fields=["world", "source", "created_at"]),
            models.Index(fields=["source_scene", "created_at"]),
            models.Index(fields=["target_character", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        target_name = (
            self.target_character.name
            if self.target_character_id and self.target_character
            else "untargeted"
        )
        return f"MediaJob {self.id}: {target_name} {self.generation_mode}"


class GeneratedMediaJobSubject(models.Model):
    """
    A character visually participating in a generated media job.

    This lets a single Wanda job bind multiple story slugs to their own
    canonical visual identity packets: Mallory as Mallory, Tom as Tom, etc.
    """
    job = models.ForeignKey(
        GeneratedMediaJob,
        on_delete=models.CASCADE,
        related_name="subjects",
    )
    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="generated_media_job_subjects",
    )
    character = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="generated_media_job_subjects",
    )
    visual_identity_version = models.ForeignKey(
        CharacterVisualIdentityVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_media_job_subjects",
    )
    role_label = models.CharField(max_length=120, blank=True, default="")
    scene_role_summary = models.TextField(blank=True, default="")
    selected_reference_ids_json = models.JSONField(default=list, blank=True)
    primary_reference_id = models.PositiveIntegerField(null=True, blank=True)
    identity_packet_snapshot_json = models.JSONField(default=dict, blank=True)
    ordering = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job", "character"],
                name="uniq_media_job_subject_character",
            ),
        ]
        indexes = [
            models.Index(fields=["world", "character"]),
            models.Index(fields=["job", "ordering"]),
        ]
        ordering = ["ordering", "id"]

    def __str__(self):
        return f"MediaJob {self.job_id}: {self.character.name}"


class GeneratedMediaJobReference(models.Model):
    """
    Freeform reference image uploaded directly for one Wanda media job.

    Unlike CharacterVisualReference, these are not canonical character identity
    assets. They are provider inputs scoped to a specific general/media job.
    """
    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="generated_media_job_references",
    )
    job = models.ForeignKey(
        GeneratedMediaJob,
        on_delete=models.CASCADE,
        related_name="reference_uploads",
    )
    file = models.FileField(upload_to=generated_media_job_reference_upload_path)
    caption = models.TextField(blank=True, default="")
    provider = models.CharField(max_length=80, blank=True, default="")
    metadata_json = models.JSONField(default=dict, blank=True)
    ordering = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["world", "created_at"]),
            models.Index(fields=["job", "ordering"]),
        ]
        ordering = ["ordering", "id"]

    def __str__(self):
        return f"MediaJobReference {self.id}: job {self.job_id}"


class GeneratedMediaAsset(models.Model):
    """
    Future/manual media result attached primarily to a scene and media job.

    Character and visual identity version are retained as provenance for
    consistency, not as ownership.
    """
    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="generated_media_assets",
    )
    source_scene = models.ForeignKey(
        "CommittedScene",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="generated_media_assets",
    )
    job = models.ForeignKey(
        GeneratedMediaJob,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assets",
    )
    target_character = models.ForeignKey(
        Character,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_media_assets",
    )
    visual_identity_version = models.ForeignKey(
        CharacterVisualIdentityVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_media_assets",
    )
    media_type = models.CharField(
        max_length=20,
        choices=GeneratedMediaJob.MEDIA_TYPE_CHOICES,
        default=GeneratedMediaJob.MEDIA_TYPE_PHOTO,
    )
    file = models.FileField(upload_to=generated_media_asset_upload_path)
    caption = models.TextField(blank=True, default="")
    provider = models.CharField(max_length=80, blank=True, default="")
    provider_asset_id = models.CharField(max_length=200, blank=True, default="")
    metadata_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["world", "created_at"]),
            models.Index(fields=["source_scene", "created_at"]),
            models.Index(fields=["job", "created_at"]),
            models.Index(fields=["target_character", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        target_name = (
            self.target_character.name
            if self.target_character_id and self.target_character
            else "untargeted"
        )
        return f"MediaAsset {self.id}: {target_name} {self.media_type}"


# =========================================================
# CANONICAL LIVE STATE
# =========================================================

class SceneState(models.Model):  # pylint: disable=too-few-public-methods
    """
    Canonical current scene-level state for a world.

    This is not a history table.
    It represents the latest resolved scene context used to continue the story.

    Fields:
    - location: current active scene location
    - cast_json: canonical current scene cast entries,
      including presence/position and participation eligibility flags
    - pending_intents_json: unresolved carry-forward intent pressure relevant
      to the next turn / next approved scene
    - alias_cache_json: scene-local reference map from observed
      aliases/titles/descriptors to canonical slugs or temp slugs
    """
    world = models.OneToOneField(World, on_delete=models.CASCADE, related_name="scene_state")
    location = models.CharField(max_length=255, blank=True, default="")
    cast_json = models.JSONField(default=dict)
    pending_intents_json = models.JSONField(default=dict)
    alias_cache_json = models.JSONField(default=dict, blank=True)
    topology_json = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"SceneState for {self.world.name}"


class StoryArc(models.Model):  # pylint: disable=too-few-public-methods
    """
    Authorial directional pressure that can persist across multiple turns.

    A StoryArc is not an objective world fact and not a character belief.
    It is a prose-forward steering layer:
    - Cassandra receives narrator_guidance as durable story direction.
    - Perspective rewriting receives character-specific lenses that shape how
      a scene beat is subjectively presented to a character.

    The fields are intentionally compact and loose. The program only needs to
    know which arcs are active, how important they are, who they involve, and
    what prose guidance should be applied.
    """
    STATUS_ACTIVE = "active"
    STATUS_DORMANT = "dormant"
    STATUS_RESOLVED = "resolved"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_DORMANT, "Dormant"),
        (STATUS_RESOLVED, "Resolved"),
    ]

    SCOPE_WORLD = "world"
    SCOPE_CHARACTER = "character"
    SCOPE_RELATIONSHIP = "relationship"
    SCOPE_GROUP = "group"

    SCOPE_CHOICES = [
        (SCOPE_WORLD, "World"),
        (SCOPE_CHARACTER, "Character"),
        (SCOPE_RELATIONSHIP, "Relationship"),
        (SCOPE_GROUP, "Group"),
    ]

    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="story_arcs",
    )
    slug = models.SlugField(max_length=120, blank=True)
    title = models.CharField(max_length=200)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )
    scope = models.CharField(
        max_length=40,
        choices=SCOPE_CHOICES,
        default=SCOPE_WORLD,
    )
    subject_slugs_json = models.JSONField(default=list, blank=True)
    summary = models.TextField(blank=True, default="")
    narrator_guidance = models.TextField(blank=True, default="")
    ooc_tag = models.TextField(blank=True, default="")
    character_lenses_json = models.JSONField(default=dict, blank=True)
    priority = models.FloatField(default=0.5)
    current_phase = models.TextField(blank=True, default="")
    horizon = models.TextField(blank=True, default="")
    constraints = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:  # pylint: disable=missing-class-docstring
        constraints = [
            models.UniqueConstraint(
                fields=["world", "slug"],
                name="uniq_story_arc_slug_per_world",
            ),
        ]
        indexes = [
            models.Index(fields=["world", "status", "priority"]),
            models.Index(fields=["world", "scope"]),
        ]
        ordering = ["-priority", "-updated_at", "title"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} ({self.world.name})"


class StoryArcUpdateProposal(models.Model):  # pylint: disable=too-few-public-methods
    """
    Cassandra-suggested arc advancement after an approved scene.

    These proposals are intentionally approval-gated. Cassandra may notice that
    a horizon has landed or that a new phase would be healthier, but the app
    does not silently mutate the arc until the user applies the proposal.
    """
    STATUS_PENDING = "pending"
    STATUS_APPLIED = "applied"
    STATUS_SKIPPED = "skipped"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_SKIPPED, "Skipped"),
    ]

    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="story_arc_update_proposals",
    )
    story_arc = models.ForeignKey(
        StoryArc,
        on_delete=models.CASCADE,
        related_name="update_proposals",
    )
    source_scene = models.ForeignKey(
        "CommittedScene",
        on_delete=models.CASCADE,
        related_name="story_arc_update_proposals",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    horizon_reached = models.BooleanField(default=False)
    evidence_summary = models.TextField(blank=True, default="")
    rationale = models.TextField(blank=True, default="")
    current_status = models.CharField(max_length=20, blank=True, default="")
    current_phase = models.TextField(blank=True, default="")
    current_horizon = models.TextField(blank=True, default="")
    current_summary = models.TextField(blank=True, default="")
    current_narrator_guidance = models.TextField(blank=True, default="")
    current_constraints = models.TextField(blank=True, default="")
    proposed_status = models.CharField(
        max_length=20,
        choices=StoryArc.STATUS_CHOICES,
        default=StoryArc.STATUS_ACTIVE,
    )
    proposed_phase = models.TextField(blank=True, default="")
    proposed_horizon = models.TextField(blank=True, default="")
    proposed_summary = models.TextField(blank=True, default="")
    proposed_narrator_guidance = models.TextField(blank=True, default="")
    proposed_constraints = models.TextField(blank=True, default="")
    raw_payload_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["story_arc", "source_scene"],
                name="uniq_story_arc_update_proposal_per_scene",
            ),
        ]
        indexes = [
            models.Index(fields=["world", "status", "created_at"]),
            models.Index(fields=["story_arc", "status", "created_at"]),
            models.Index(fields=["source_scene", "status"]),
        ]
        ordering = ["-created_at"]

    def apply(self):
        self.story_arc.status = self.proposed_status
        self.story_arc.current_phase = self.proposed_phase
        self.story_arc.horizon = self.proposed_horizon
        self.story_arc.summary = self.proposed_summary
        self.story_arc.narrator_guidance = self.proposed_narrator_guidance
        self.story_arc.constraints = self.proposed_constraints
        self.story_arc.save(
            update_fields=[
                "status",
                "current_phase",
                "horizon",
                "summary",
                "narrator_guidance",
                "constraints",
                "updated_at",
            ]
        )
        self.status = self.STATUS_APPLIED
        self.decided_at = timezone.now()
        self.save(update_fields=["status", "decided_at"])

    def skip(self):
        self.status = self.STATUS_SKIPPED
        self.decided_at = timezone.now()
        self.save(update_fields=["status", "decided_at"])

    def __str__(self):
        return (
            f"Arc proposal for {self.story_arc.title} "
            f"after turn {self.source_scene.turn_number}"
        )


class CharacterState(models.Model):  # pylint: disable=too-few-public-methods
    """
    Canonical current internal state snapshot for a character.

    This is the current aggregate view, not a history log.
    Scene-derived shifts over time should be recorded in CharacterStateChange.

    Note:
    motivational_state_json is the broader character-level motivational state.
    This is distinct from SceneState.pending_intents_json, which is the
    scene-level carry-forward pressure for the next turn.
    """
    character = models.OneToOneField(
        Character,
        on_delete=models.CASCADE,
        related_name="state")
    emotional_state_json = models.JSONField(default=dict, blank=True)
    goals_json = models.JSONField(default=dict, blank=True)
    internal_conflicts_json = models.JSONField(default=dict, blank=True)
    motivational_state_json = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"State for {self.character.name}"


class CharacterPerception(models.Model):
    # pylint: disable=too-few-public-methods
    """
    Canonical current relational/perceptual snapshot from observer -> target.

    This is the aggregate current state of how one character sees another.

    Important distinction:
    - This is a rolled-up snapshot.
    - CharacterPerceptionChange stores historical deltas.
    - CharacterBelief can store individual atomic beliefs if you want them
      separately queryable.

    If CharacterBelief is retained, belief_json here should be treated as
    a summary/aggregate layer rather than a second source of truth.
    """
    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="character_perceptions")
    observer = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="outgoing_perceptions")
    target = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="incoming_perceptions")
    summary = models.TextField(blank=True, default="")
    knowledge_basis = models.TextField(blank=True, default="")
    open_questions_json = models.JSONField(default=list, blank=True)
    last_change_summary = models.TextField(blank=True, default="")
    impression_json = models.JSONField(default=dict, blank=True)
    relationship_json = models.JSONField(default=dict, blank=True)
    belief_json = models.JSONField(default=dict, blank=True)
    trust = models.FloatField(default=0.0)
    attraction = models.FloatField(default=0.0)
    fear = models.FloatField(default=0.0)
    resentment = models.FloatField(default=0.0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:  # pylint: disable=missing-class-docstring
        # pylint: disable=too-few-public-methods
        constraints = [
            models.UniqueConstraint(
                fields=["world", "observer", "target"],
                name="uniq_character_perception"
            )
        ]

    def __str__(self):
        return f"{self.observer.name} -> {self.target.name} perception"


class SubjectiveRelationshipEdge(models.Model):
    # pylint: disable=too-few-public-methods
    """
    Canonical current social-graph edge as understood by one observer.

    This is observer-scoped and subjective:
    - Mallory's Donnie/Byrne edge may differ from Byrne's Donnie/Mallory edge.
    - subject_a/subject_b are stored as a canonical undirected pair.
    - directional_notes_json carries asymmetric meaning when needed.
    """
    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="subjective_relationship_edges",
    )
    observer = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="subjective_relationship_edges_observed",
    )
    subject_a = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="subjective_relationship_edges_as_a",
    )
    subject_b = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="subjective_relationship_edges_as_b",
    )
    relationship_label = models.CharField(max_length=160, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    knowledge_basis = models.TextField(blank=True, default="")
    confidence = models.FloatField(default=0.5)
    open_questions_json = models.JSONField(default=list, blank=True)
    directional_notes_json = models.JSONField(default=dict, blank=True)
    last_change_summary = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:  # pylint: disable=missing-class-docstring
        constraints = [
            models.UniqueConstraint(
                fields=["world", "observer", "subject_a", "subject_b"],
                name="uniq_subjective_edge",
            ),
        ]
        indexes = [
            models.Index(
                fields=["world", "observer"],
                name="story_edge_observer_idx",
            ),
            models.Index(
                fields=["world", "subject_a", "subject_b"],
                name="story_edge_pair_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.observer.name}'s {self.subject_a.name} ↔ "
            f"{self.subject_b.name} edge"
        )


# =========================================================
# HISTORICAL RECORDS
# =========================================================

class CommittedScene(models.Model):  # pylint: disable=too-few-public-methods
    """
    Canonical history of approved turns.

    Each row is one committed turn in sequence.
    This is the narrative transcript and primary provenance anchor for
    downstream memories, beliefs, and state/perception deltas.
    """
    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="committed_scenes")
    turn_number = models.PositiveIntegerField()
    user_text = models.TextField(blank=True, default="")
    cassandra_text = models.TextField(blank=True, default="")
    scene_events_json = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    cassandra_aftermath_processed = models.BooleanField(default=False)

    class Meta:  # pylint: disable=missing-class-docstring
        # pylint: disable=too-few-public-methods
        constraints = [
            models.UniqueConstraint(
                fields=["world", "turn_number"],
                name="uniq_committed_scene_turn_number_per_world",
            ),
        ]
        indexes = [
            models.Index(fields=["world", "turn_number"]),
            models.Index(fields=["world", "created_at"]),
        ]

    def __str__(self):
        return f"CommittedScene turn {self.turn_number} for {self.world.name}"


class NarrativeMemory(models.Model):  # pylint: disable=too-few-public-methods
    """
    World-level continuity memory.

    These are not owned by a single character.
    They capture important story facts, pressures, and continuity anchors that
    should influence future scene generation.
    """
    MEMORY_LAYER_RAW = "raw"
    MEMORY_LAYER_PAST = "past"
    MEMORY_LAYER_HISTORY = "history"

    MEMORY_LAYER_CHOICES = [
        (MEMORY_LAYER_RAW, "Raw"),
        (MEMORY_LAYER_PAST, "Past"),
        (MEMORY_LAYER_HISTORY, "History"),
    ]

    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="narrative_memories")
    source_scene = models.ForeignKey(
        CommittedScene,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="narrative_memories",
    )
    content = models.TextField()
    memory_layer = models.CharField(
        max_length=20,
        choices=MEMORY_LAYER_CHOICES,
        default=MEMORY_LAYER_RAW,
    )
    is_context_active = models.BooleanField(default=True)
    compacted_into = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="compacted_source_memories",
    )
    source_memory_ids_json = models.JSONField(default=list, blank=True)
    source_memory_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["world", "memory_layer", "is_context_active"],
                name="story_narra_world_i_7aa78e_idx",
            ),
            models.Index(
                fields=["world", "created_at"],
                name="story_narra_world_i_91b1f2_idx",
            ),
        ]

    def __str__(self):
        if self.source_scene_id:
            return (
                f"NarrativeMemory {self.id} from turn "
                f"{self.source_scene.turn_number} for {self.world.name}"
            )
        return f"NarrativeMemory {self.id} for {self.world.name}"


class CharacterMemory(models.Model):  # pylint: disable=too-few-public-methods
    """
    Character-owned remembered event, impression, or fact.

    Unlike NarrativeMemory, this is subjective and scoped to one character.
    It may optionally point at:
    - a related character
    - the source committed scene
    """
    MEMORY_LAYER_RAW = "raw"
    MEMORY_LAYER_PAST = "past"
    MEMORY_LAYER_HISTORY = "history"

    MEMORY_LAYER_CHOICES = [
        (MEMORY_LAYER_RAW, "Raw"),
        (MEMORY_LAYER_PAST, "Past"),
        (MEMORY_LAYER_HISTORY, "History"),
    ]

    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="character_memories")
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="memories")
    content = models.TextField()
    memory_type = models.CharField(max_length=50, blank=True, default="")
    memory_layer = models.CharField(
        max_length=20,
        choices=MEMORY_LAYER_CHOICES,
        default=MEMORY_LAYER_RAW,
    )
    is_context_active = models.BooleanField(default=True)
    compacted_into = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="compacted_source_memories",
    )
    source_memory_ids_json = models.JSONField(default=list, blank=True)
    source_memory_count = models.PositiveIntegerField(default=0)
    related_character_slugs_json = models.JSONField(default=list, blank=True)
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
        on_delete=models.CASCADE,
        related_name="character_memories"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["world", "character", "memory_layer", "is_context_active"],
                name="story_chara_world_i_871de6_idx",
            ),
            models.Index(
                fields=["character", "created_at"],
                name="story_chara_charact_8c5ad9_idx",
            ),
        ]

    def __str__(self):
        return f"Memory {self.id} for {self.character.name}"


class CharacterBelief(models.Model):  # pylint: disable=too-few-public-methods
    """
    Atomic belief held by a character about some subject.

    This is useful when you want beliefs to be:
    - individually queryable
    - confidence-scored
    - true/false/unknown
    - historically attributable to scenes

    This overlaps somewhat with CharacterPerception.belief_json.
    Best practice is to treat this table as the atomic layer and the
    perception belief_json as an aggregate summary if both are kept.
    """
    BELIEF_STATUS_TRANSIENT = "transient"
    BELIEF_STATUS_REINFORCED = "reinforced"
    BELIEF_STATUS_PROMOTED = "promoted"
    BELIEF_STATUS_DISCARDED = "discarded"

    BELIEF_STATUS_CHOICES = [
        (BELIEF_STATUS_TRANSIENT, "Transient"),
        (BELIEF_STATUS_REINFORCED, "Reinforced"),
        (BELIEF_STATUS_PROMOTED, "Promoted"),
        (BELIEF_STATUS_DISCARDED, "Discarded"),
    ]

    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="character_beliefs")
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="beliefs")
    subject_type = models.CharField(max_length=50, blank=True, default="")
    subject_slug = models.CharField(max_length=100, blank=True, default="")
    related_subject_slugs_json = models.JSONField(default=list, blank=True)
    belief = models.TextField()
    confidence = models.FloatField(default=0.5)
    basis = models.TextField(blank=True, default="")
    is_true = models.BooleanField(null=True, blank=True)
    source = models.CharField(max_length=100, blank=True, default="")
    source_scene = models.ForeignKey(
        CommittedScene,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="character_beliefs",
    )
    belief_status = models.CharField(
        max_length=50,
        choices=BELIEF_STATUS_CHOICES,
        default=BELIEF_STATUS_TRANSIENT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Belief {self.id} for {self.character.name}"


class CharacterPerceptionChange(models.Model):
    # pylint: disable=too-few-public-methods
    """
    Historical delta record for observer -> target perception changes.

    This captures what changed, when it changed, and why.
    It is the provenance/history layer for CharacterPerception.
    """
    CHANGE_LAYER_RAW = "raw"
    CHANGE_LAYER_PAST = "past"
    CHANGE_LAYER_HISTORY = "history"

    CHANGE_LAYER_CHOICES = [
        (CHANGE_LAYER_RAW, "Raw"),
        (CHANGE_LAYER_PAST, "Past"),
        (CHANGE_LAYER_HISTORY, "History"),
    ]

    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="character_perception_changes",
    )
    observer = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="perception_changes_made",
    )
    target = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="perception_changes_received",
    )
    source_scene = models.ForeignKey(
        CommittedScene,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="character_perception_changes",
    )
    change_source = models.CharField(max_length=50, blank=True, default="scene_delta")
    summary = models.TextField(blank=True, default="")
    revised_summary = models.TextField(blank=True, default="")
    knowledge_basis = models.TextField(blank=True, default="")
    open_questions_json = models.JSONField(default=list, blank=True)
    access_gate_json = models.JSONField(default=dict, blank=True)
    change_layer = models.CharField(
        max_length=20,
        choices=CHANGE_LAYER_CHOICES,
        default=CHANGE_LAYER_RAW,
    )
    is_context_active = models.BooleanField(default=True)
    compacted_into = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="compacted_source_changes",
    )
    source_change_ids_json = models.JSONField(default=list, blank=True)
    source_change_count = models.PositiveIntegerField(default=0)
    impression_json = models.JSONField(default=dict, blank=True)
    relationship_json = models.JSONField(default=dict, blank=True)
    belief_json = models.JSONField(default=dict, blank=True)
    trust_delta = models.FloatField(default=0.0)
    attraction_delta = models.FloatField(default=0.0)
    fear_delta = models.FloatField(default=0.0)
    resentment_delta = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta: # pylint: disable=missing-class-docstring
        indexes = [
            models.Index(fields=["world", "observer", "target"]),
            models.Index(fields=["world", "source_scene"]),
            models.Index(fields=["observer", "target", "created_at"]),
            models.Index(
                fields=["world", "observer", "target", "change_layer", "is_context_active"],
                name="story_chara_world_i_8af31d_idx",
            ),
        ]

    def __str__(self):
        return (
            f"PerceptionChange {self.id}: "
            f"{self.observer.name} -> {self.target.name} in {self.world.name}"
        )


class SubjectiveRelationshipEdgeChange(models.Model):
    # pylint: disable=too-few-public-methods
    """
    Historical delta record for one observer's understanding of a social edge.

    This is the provenance/history layer for SubjectiveRelationshipEdge.
    """
    CHANGE_LAYER_RAW = "raw"
    CHANGE_LAYER_PAST = "past"
    CHANGE_LAYER_HISTORY = "history"

    CHANGE_LAYER_CHOICES = [
        (CHANGE_LAYER_RAW, "Raw"),
        (CHANGE_LAYER_PAST, "Past"),
        (CHANGE_LAYER_HISTORY, "History"),
    ]

    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="subjective_relationship_edge_changes",
    )
    observer = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="subjective_relationship_edge_changes_observed",
    )
    subject_a = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="subjective_relationship_edge_changes_as_a",
    )
    subject_b = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="subjective_relationship_edge_changes_as_b",
    )
    current_edge = models.ForeignKey(
        SubjectiveRelationshipEdge,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="changes",
    )
    source_scene = models.ForeignKey(
        CommittedScene,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="subjective_relationship_edge_changes",
    )
    change_source = models.CharField(max_length=50, blank=True, default="scene_delta")
    relationship_label = models.CharField(max_length=160, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    revised_summary = models.TextField(blank=True, default="")
    knowledge_basis = models.TextField(blank=True, default="")
    confidence = models.FloatField(default=0.5)
    open_questions_json = models.JSONField(default=list, blank=True)
    directional_notes_json = models.JSONField(default=dict, blank=True)
    access_gate_json = models.JSONField(default=dict, blank=True)
    change_layer = models.CharField(
        max_length=20,
        choices=CHANGE_LAYER_CHOICES,
        default=CHANGE_LAYER_RAW,
    )
    is_context_active = models.BooleanField(default=True)
    compacted_into = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="compacted_source_edge_changes",
    )
    source_change_ids_json = models.JSONField(default=list, blank=True)
    source_change_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:  # pylint: disable=missing-class-docstring
        indexes = [
            models.Index(
                fields=["world", "observer", "subject_a", "subject_b"],
                name="story_edge_ch_pair_idx",
            ),
            models.Index(
                fields=["world", "source_scene"],
                name="story_edge_ch_scene_idx",
            ),
            models.Index(
                fields=[
                    "world",
                    "observer",
                    "subject_a",
                    "subject_b",
                    "change_layer",
                    "is_context_active",
                ],
                name="story_edge_ch_ctx_idx",
            ),
        ]

    def __str__(self):
        return (
            f"EdgeChange {self.id}: {self.observer.name}'s "
            f"{self.subject_a.name} ↔ {self.subject_b.name}"
        )


class CharacterScene(models.Model):
    world = models.ForeignKey("World", on_delete=models.CASCADE)
    character = models.ForeignKey("Character", on_delete=models.CASCADE)
    source_scene = models.ForeignKey(
        "CommittedScene",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="character_scenes",
    )

    turn_number = models.IntegerField(default=0)
    participation = models.CharField(max_length=64, blank=True, default="")

    # What the character-agent knew/was at draft time.
    local_scene_state_json = models.JSONField(default=dict, blank=True)
    acting_character_snapshot_json = models.JSONField(default=dict, blank=True)

    # What the character attempted during this scene.
    scene_contribution_json = models.JSONField(default=dict, blank=True)
    authored_intent_json = models.JSONField(default=dict, blank=True)
    current_turn_reflection_json = models.JSONField(default=dict, blank=True)

    # Convenience fields broken out from scene_contribution/current_turn_reflection.
    attempted_action = models.TextField(blank=True, default="")
    attempted_dialogue = models.TextField(blank=True, default="")
    internal_intent = models.TextField(blank=True, default="")
    emotional_posture = models.TextField(blank=True, default="")
    active_pressure = models.TextField(blank=True, default="")
    anticipated_consequence = models.TextField(blank=True, default="")

    observed_focus_json = models.JSONField(default=list, blank=True)
    beliefs_in_play_json = models.JSONField(default=list, blank=True)
    memory_pressures_json = models.JSONField(default=list, blank=True)
    proposed_effects_json = models.JSONField(default=list, blank=True)

    target_slugs_json = models.JSONField(default=list, blank=True)
    required_visibility = models.CharField(max_length=32, blank=True, default="")
    required_audibility = models.CharField(max_length=32, blank=True, default="")
    interrupt_priority = models.CharField(max_length=32, blank=True, default="")
    body_motion = models.TextField(blank=True, default="")

    # What Cassandra/scene-events determined this character experienced.
    event_record_json = models.JSONField(default=dict, blank=True)
    event_record_text = models.TextField(blank=True, default="")

    # The character-agent's later subjective aftermath of this approved scene.
    subjective_scene_text = models.TextField(blank=True, default="")
    previous_scene_aftermath_json = models.JSONField(default=dict, blank=True)

    memories_created_json = models.JSONField(default=list, blank=True)
    state_update_json = models.JSONField(default=dict, blank=True)
    perception_updates_json = models.JSONField(default=list, blank=True)
    beliefs_created_json = models.JSONField(default=list, blank=True)

    # Optional snapshots for historical inspection.
    state_before_json = models.JSONField(default=dict, blank=True)
    state_after_json = models.JSONField(default=dict, blank=True)

    aftermath_processed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["turn_number", "character__slug"]
        indexes = [
            models.Index(fields=["world", "character", "turn_number"]),
            models.Index(fields=["source_scene", "character"]),
            models.Index(fields=["aftermath_processed"]),
        ]


# pylint: disable=too-few-public-methods
class CharacterStateChange(models.Model):
    """
    Historical delta record for internal character-state changes.

    This is the provenance/history layer for CharacterState.
    It stores scene-derived or manually-authored shifts rather than the
    current total state.

    These records should be append-only in normal scene processing.
    CharacterState stores the current aggregate snapshot.
    """
    world = models.ForeignKey(
        World,
        on_delete=models.CASCADE,
        related_name="character_state_changes",
    )
    character = models.ForeignKey(
        Character,
        on_delete=models.CASCADE,
        related_name="state_changes",
    )
    source_scene = models.ForeignKey(
        CommittedScene,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="character_state_changes",
    )
    change_source = models.CharField(max_length=50, blank=True, default="scene_delta")
    summary = models.TextField(blank=True, default="")
    emotional_state_json = models.JSONField(default=dict, blank=True)
    goals_json = models.JSONField(default=dict, blank=True)
    internal_conflicts_json = models.JSONField(default=dict, blank=True)
    motivational_state_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:  # pylint: disable=missing-class-docstring
        indexes = [
            models.Index(fields=["world", "character"]),
            models.Index(fields=["world", "source_scene"]),
            models.Index(fields=["character", "created_at"]),
        ]

    def __str__(self):
        return f"StateChange {self.id} for {self.character.name} in {self.world.name}"


# =========================================================
# WORKFLOW / TRANSIENT HELPERS
# =========================================================

class Proposal(models.Model):  # pylint: disable=too-few-public-methods
    """
    A draft candidate for a user turn before approval/commit.

    This is the temporary working object used during generation and revision.

    Fields:
    - user_input: the user's story contribution for the turn
    - draft: the generated candidate response
    - editors_craft_memory_json: transient editorial guidance used during revision
    - revision_change_summary: plain-language summary of what changed during revision
    - revision_intent_summary: plain-language summary of narrative / intent direction
    - character_authored_intents_json: authored motivations supplied into the turn
    - character_contributions_json: structured character-agent proposals supplied into Cassandra
    - is_approved: whether this proposal became canon
    """
    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="proposals")
    user_input = models.TextField()
    draft = models.TextField()
    editors_craft_memory_json = models.JSONField(default=list, blank=True)
    revision_change_summary = models.TextField(blank=True, default="")
    revision_intent_summary = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    is_approved = models.BooleanField(default=False)
    character_authored_intents_json = models.JSONField(default=dict, blank=True)
    character_contributions_json = models.JSONField(default=list, blank=True)
    scene_events_json = models.JSONField(default=list, blank=True)
    proposed_scene_state_json = models.JSONField(default=dict, blank=True)
    scene_state_update_json = models.JSONField(default=dict, blank=True)
    cassandra_scene_state_update_json = models.JSONField(default=dict, blank=True)
    alias_cache_update_json = models.JSONField(default=dict, blank=True)
    resolved_pending_intents_json = models.JSONField(default=dict, blank=True)
    resolved_character_experience_updates_json = models.JSONField(default=list, blank=True)
    character_agent_debug_json = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"Proposal {self.id} for {self.world.name}"


@dataclass
class TempSceneState:
    """
    Transient scene-state container used during draft generation.

    Provides Cassandra with a fully merged, pre-approval view of the scene,
    including alias_cache, without mutating the canonical SceneState.
    """
    location: str
    cast_json: dict
    pending_intents_json: dict
    alias_cache_json: dict = field(default_factory=dict)
    topology_json: dict = field(default_factory=dict)

@dataclass
class SceneEvent:
    """
    A resolved causal event from an approved or proposed scene.

    This records what actually happened after Cassandra adjudicates
    character attempts, interruptions, conflicts, perception limits,
    and narrative consequences.

    SceneEvents are not raw character intentions.
    They are Cassandra's resolved account of meaningful causal beats.
    """

    event_id: str = ""
    event_type: str = ""
    actor_slug: str | None = None
    target_slugs: list[str] = field(default_factory=list)

    summary: str = ""
    content: str = ""

    outcome: str = ""
    space_id: str = ""
    reader_visibility: str = ""
    cue_summary: str = ""
    visibility: str = ""
    audibility: str = ""

    perceived_by: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    caused_by: list[str] = field(default_factory=list)

    confidence: float = 1.0

VALID_SCENE_EVENT_TYPES = {
    "action",
    "dialogue",
    "interruption",
    "observation",
    "revelation",
    "emotional_shift",
    "relationship_shift",
    "state_change",
}

VALID_SCENE_EVENT_OUTCOMES = {
    "succeeds",
    "fails",
    "partial",
    "blocked",
    "delayed",
    "unnoticed",
}


VALID_SCENE_EVENT_VISIBILITY = {
    "public",
    "limited",
    "private",
    "hidden",
}


VALID_SCENE_EVENT_AUDIBILITY = {
    "audible",
    "partially_audible",
    "inaudible",
    "text_only",
    "none",
}
