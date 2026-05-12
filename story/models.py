# models.py#

from dataclasses import dataclass, field
from django.db import models
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

    def __str__(self):
        return f"SceneState for {self.world.name}"


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
    impression_json = models.JSONField(default=dict, blank=True)
    relationship_json = models.JSONField(default=dict, blank=True)
    belief_json = models.JSONField(default=dict, blank=True)
    arc_json = models.JSONField(default=dict, blank=True)
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
    created_at = models.DateTimeField(auto_now_add=True)

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
        on_delete=models.CASCADE,
        related_name="character_memories"
    )
    created_at = models.DateTimeField(auto_now_add=True)

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
    belief = models.TextField()
    confidence = models.FloatField(default=0.5)
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
    impression_json = models.JSONField(default=dict, blank=True)
    relationship_json = models.JSONField(default=dict, blank=True)
    belief_json = models.JSONField(default=dict, blank=True)
    arc_json = models.JSONField(default=dict, blank=True)
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
        ]

    def __str__(self):
        return (
            f"PerceptionChange {self.id}: "
            f"{self.observer.name} -> {self.target.name} in {self.world.name}"
        )


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
    alias_cache_update_json = models.JSONField(default=dict, blank=True)
    resolved_pending_intents_json = models.JSONField(default=dict, blank=True)
    resolved_character_experience_updates_json = models.JSONField(default=list, blank=True)

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
