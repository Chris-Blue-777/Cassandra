import json

from django import forms
from .models import (
    Character,
    CharacterVisualIdentity,
    CharacterVisualReference,
    GeneratedMediaAsset,
    GeneratedMediaJob,
    StoryArc,
    World,
)
from .arcs import normalize_story_arc_ooc_tag
from .Wanda import (
    MEDIA_STYLE_MATCH_REFERENCE,
    wanda_media_provider,
    wanda_media_provider_choices,
    wanda_media_style_choices,
)


class CharacterForm(forms.ModelForm):
    # CharacterProfile fields shown on the same creation screen.
    profile_summary = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Compact overview of who this character is.",
    )
    archetype = forms.CharField(
        required=False,
        help_text="Optional narrative shorthand, such as rival, mentor, wildcard, etc.",
    )

    gender = forms.CharField(
        required=False,
        help_text="Optional. Use only if narratively relevant.",
    )

    pronoun_subject = forms.CharField(required=False, initial="they")
    pronoun_object = forms.CharField(required=False, initial="them")
    personality = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Temperament, traits, drives, contradictions, fears, desires.",
    )
    permabeliefs = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Semi-permanent beliefs, assumptions, worldview, or self-concepts this character begins with.",
    )
    diction = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="How this character speaks: rhythm, vocabulary, bluntness, formality, slang, silence, etc.",
    )
    craft_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Author-facing guidance for how this character should be written.",
    )
    background = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Backstory, formative events, history, biography, or relevant context.",
    )

    class Meta:
        model = Character
        fields = [
            "world",
            "name",
            "slug",
            "description",
            "is_player",
            "agent_provider",
        ]


class WorldForm(forms.ModelForm):
    make_active = forms.BooleanField(
        required=False,
        initial=True,
        label="Make this the active world",
        help_text="Switch Cassandra to this world as soon as it is created.",
    )

    class Meta:
        model = World
        fields = ["name", "description"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "name": "The story-world name shown in Cassandra’s world selector.",
            "description": "Optional author-facing note about this world or continuity container.",
        }


class StoryArcForm(forms.ModelForm):
    subject_slugs = forms.CharField(
        required=False,
        help_text="Optional comma-separated character slugs this arc is mainly about.",
        widget=forms.TextInput(attrs={
            "placeholder": "mallory, donnie, byrne",
        }),
    )
    character_lenses = forms.CharField(
        required=False,
        help_text=(
            "Optional JSON object keyed by character slug. Values can be strings "
            "or objects with presentation_bias, emotional_pressure, perceptual_bias, and limits."
        ),
        widget=forms.Textarea(attrs={
            "rows": 4,
            "placeholder": (
                '{\n'
                '  "mallory": "Donnie’s attention lands as physically charged.",\n'
                '  "byrne": "Ambiguity around Mallory feeds insecurity rather than reassurance."\n'
                '}'
            ),
        }),
    )

    class Meta:
        model = StoryArc
        fields = [
            "title",
            "slug",
            "scope",
            "summary",
            "narrator_guidance",
            "ooc_tag",
            "current_phase",
            "horizon",
            "constraints",
            "priority",
        ]
        widgets = {
            "summary": forms.Textarea(attrs={"rows": 3}),
            "narrator_guidance": forms.Textarea(attrs={"rows": 4}),
            "ooc_tag": forms.Textarea(attrs={"rows": 2}),
            "current_phase": forms.Textarea(attrs={"rows": 3}),
            "horizon": forms.Textarea(attrs={"rows": 3}),
            "constraints": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "slug": "Optional. Leave blank to generate from the title.",
            "summary": "What this arc is about in plain language.",
            "narrator_guidance": (
                "What Cassandra should keep trending toward across turns. "
                "This is directional pressure, not an absolute command."
            ),
            "ooc_tag": (
                "Optional OOC instruction automatically attached to user submissions "
                "while this arc is active. Plain text will be wrapped as [OOC: ...]."
            ),
            "current_phase": "Optional label for the current stage of the arc.",
            "horizon": "Optional near-term destination or pressure point.",
            "constraints": "Optional limits Cassandra should respect while leaning into the arc.",
            "priority": "Higher arcs are presented first. 0.5 is a neutral default.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = getattr(self, "instance", None)
        if instance and instance.pk:
            self.fields["subject_slugs"].initial = ", ".join(
                instance.subject_slugs_json or []
            )
            self.fields["character_lenses"].initial = (
                json.dumps(
                    instance.character_lenses_json or {},
                    ensure_ascii=False,
                    indent=2,
                )
                if instance.character_lenses_json
                else ""
            )

    def clean_subject_slugs(self):
        value = self.cleaned_data.get("subject_slugs") or ""
        slugs = []

        for item in value.replace("\n", ",").split(","):
            slug = item.strip()
            if slug and slug not in slugs:
                slugs.append(slug)

        return slugs

    def clean_character_lenses(self):
        value = (self.cleaned_data.get("character_lenses") or "").strip()
        if not value:
            return {}

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(
                "Character lenses must be valid JSON."
            ) from exc

        if not isinstance(parsed, dict):
            raise forms.ValidationError(
                "Character lenses must be a JSON object keyed by character slug."
            )

        return parsed

    def clean_ooc_tag(self):
        return normalize_story_arc_ooc_tag(
            self.cleaned_data.get("ooc_tag") or ""
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.subject_slugs_json = self.cleaned_data.get("subject_slugs") or []
        instance.character_lenses_json = self.cleaned_data.get("character_lenses") or {}

        if commit:
            instance.save()

        return instance


class CharacterVisualIdentityForm(forms.ModelForm):
    traits = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text="JSON object of stable physical anchors: face, build, hair, eyes, marks, bearing.",
    )
    allowed_variations = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text="JSON object describing what may vary across scenes: clothing, pose, lighting, expression.",
    )
    provider_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Optional JSON object for provider-specific media-generation notes.",
    )
    change_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Optional note stored on the new identity version snapshot.",
    )

    class Meta:
        model = CharacterVisualIdentity
        fields = [
            "status",
            "appearance_summary",
            "canonical_identity_prompt",
            "negative_identity_prompt",
        ]
        widgets = {
            "appearance_summary": forms.Textarea(attrs={"rows": 4}),
            "canonical_identity_prompt": forms.Textarea(attrs={"rows": 6}),
            "negative_identity_prompt": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "canonical_identity_prompt": "Provider-neutral identity prompt for future image/video generation.",
            "negative_identity_prompt": "Identity drift to avoid: wrong age, face, build, hair, eyes, marks, etc.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance

        if instance and (
            instance.pk
            or instance.traits_json
            or instance.allowed_variations_json
            or instance.provider_notes_json
        ):
            self.fields["traits"].initial = json.dumps(
                instance.traits_json or {},
                indent=2,
                ensure_ascii=False,
            )
            self.fields["allowed_variations"].initial = json.dumps(
                instance.allowed_variations_json or {},
                indent=2,
                ensure_ascii=False,
            )
            self.fields["provider_notes"].initial = json.dumps(
                instance.provider_notes_json or {},
                indent=2,
                ensure_ascii=False,
            )

    def _clean_json_field(self, field_name, expected_type):
        value = (self.cleaned_data.get(field_name) or "").strip()
        if not value:
            return expected_type()

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(
                f"{field_name.replace('_', ' ').title()} must be valid JSON."
            ) from exc

        if not isinstance(parsed, expected_type):
            expected_label = "array" if expected_type is list else "object"
            raise forms.ValidationError(
                f"{field_name.replace('_', ' ').title()} must be a JSON {expected_label}."
            )

        return parsed

    def clean_traits(self):
        return self._clean_json_field("traits", dict)

    def clean_allowed_variations(self):
        return self._clean_json_field("allowed_variations", dict)

    def clean_provider_notes(self):
        return self._clean_json_field("provider_notes", dict)

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.traits_json = self.cleaned_data.get("traits") or {}
        instance.allowed_variations_json = (
            self.cleaned_data.get("allowed_variations") or {}
        )
        instance.provider_notes_json = (
            self.cleaned_data.get("provider_notes") or {}
        )

        if commit:
            instance.save()

        return instance


class CharacterVisualReferenceForm(forms.ModelForm):
    metadata = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Optional JSON object with provider or asset metadata.",
    )

    class Meta:
        model = CharacterVisualReference
        fields = [
            "file",
            "kind",
            "is_primary",
            "caption",
            "generation_prompt",
            "provider",
            "provider_asset_id",
        ]
        widgets = {
            "caption": forms.Textarea(attrs={"rows": 2}),
            "generation_prompt": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_metadata(self):
        value = (self.cleaned_data.get("metadata") or "").strip()
        if not value:
            return {}

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError("Metadata must be valid JSON.") from exc

        if not isinstance(parsed, dict):
            raise forms.ValidationError("Metadata must be a JSON object.")

        return parsed

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.metadata_json = self.cleaned_data.get("metadata") or {}

        if commit:
            instance.save()

        return instance


class GeneratedMediaJobReviewForm(forms.ModelForm):
    PROVIDER_NONE = ""
    PROVIDER_GOOGLE_NANO_BANANA_2 = "google_nano_banana_2"
    PROVIDER_GOOGLE_GEMINI_3_PRO_IMAGE = "google_gemini_3_pro_image"

    PROVIDER_CHOICES = [
        (PROVIDER_GOOGLE_NANO_BANANA_2, "Google Nano Banana 2"),
        (PROVIDER_GOOGLE_GEMINI_3_PRO_IMAGE, "Google Gemini 3 Pro Image"),
        (PROVIDER_NONE, "No live provider / packet only"),
    ]

    provider = forms.ChoiceField(
        required=False,
        choices=PROVIDER_CHOICES,
        help_text=(
            "Choose the provider this job is being prepared for. "
            "Nano Banana 2 can use up to 4 selected character references; "
            "Gemini 3 Pro Image can use up to 5."
        ),
    )
    style_mode = forms.ChoiceField(
        required=False,
        choices=wanda_media_style_choices(),
        initial=MEDIA_STYLE_MATCH_REFERENCE,
        help_text=(
            "Controls the visual medium/style Wanda asks the provider to use. "
            "The default treats selected references as both identity and style anchors."
        ),
    )
    custom_style_prompt = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=(
            "Optional. Add a provider-neutral style instruction, or use this "
            "as the whole style instruction when Style is Custom."
        ),
    )
    selected_reference_ids = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    primary_reference_id = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    original_prompt = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    original_negative_prompt = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    reference_asset_limit = forms.IntegerField(
        required=False,
        min_value=1,
        help_text=(
            "Optional. Use this when a future provider/agent can only accept "
            "a limited number of reference assets."
        ),
    )

    class Meta:
        model = GeneratedMediaJob
        fields = [
            "title",
            "provider",
            "style_mode",
            "custom_style_prompt",
            "prompt",
            "negative_prompt",
            "user_prompt_override",
        ]
        widgets = {
            "prompt": forms.Textarea(attrs={"rows": 12}),
            "negative_prompt": forms.Textarea(attrs={"rows": 5}),
            "user_prompt_override": forms.Textarea(attrs={"rows": 5}),
        }
        help_texts = {
            "prompt": "Editable assembled portrait prompt saved with the job.",
            "negative_prompt": "Identity drift, continuity errors, and visual changes to avoid.",
            "user_prompt_override": (
                "Optional human direction preserved alongside Wanda's assembled packet."
            ),
        }


class GeneratedVideoMediaJobReviewForm(forms.ModelForm):
    VIDEO_MODE_IMAGE = GeneratedMediaJob.MODE_VIDEO_IMAGE
    VIDEO_MODE_TEXT = GeneratedMediaJob.MODE_VIDEO_TEXT

    VIDEO_MODE_CHOICES = [
        (VIDEO_MODE_IMAGE, "Image to video"),
        (VIDEO_MODE_TEXT, "Text to video"),
    ]

    provider = forms.ChoiceField(
        required=True,
        choices=[],
        help_text=(
            "Choose the video provider/model this job will be sent to. "
            "Available choices come from Wanda's media provider registry."
        ),
    )
    video_mode = forms.ChoiceField(
        required=True,
        choices=VIDEO_MODE_CHOICES,
        help_text=(
            "Image-to-video uses selected frame reference(s), according to "
            "the selected provider's workflow. "
            "Character identity is still mandatory and is carried in Wanda's "
            "identity packet and prompt. Text-to-video sends that identity "
            "packet as prose only."
        ),
    )
    selected_reference_ids = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    primary_reference_id = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    original_prompt = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    original_negative_prompt = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, **kwargs):
        provider_choices = kwargs.pop("provider_choices", None)
        super().__init__(*args, **kwargs)
        self.fields["provider"].choices = (
            provider_choices
            if provider_choices is not None
            else wanda_media_provider_choices(
                media_type=GeneratedMediaJob.MEDIA_TYPE_VIDEO,
            )
        )

    def clean(self):
        cleaned_data = super().clean()
        provider_id = cleaned_data.get("provider")
        video_mode = cleaned_data.get("video_mode")
        provider = wanda_media_provider(provider_id)

        if not provider or provider.get("media_type") != GeneratedMediaJob.MEDIA_TYPE_VIDEO:
            raise forms.ValidationError("Choose a registered video provider.")

        modes = provider.get("modes") or []
        if modes and video_mode not in modes:
            raise forms.ValidationError(
                f"{provider['label']} does not support the selected video mode."
            )

        return cleaned_data

    class Meta:
        model = GeneratedMediaJob
        fields = [
            "title",
            "provider",
            "video_mode",
            "prompt",
            "negative_prompt",
            "user_prompt_override",
        ]
        widgets = {
            "prompt": forms.Textarea(attrs={"rows": 10}),
            "negative_prompt": forms.Textarea(attrs={"rows": 4}),
            "user_prompt_override": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "prompt": "Motion/video prompt sent to the selected provider.",
            "negative_prompt": "Motion, identity drift, or visual errors to avoid.",
            "user_prompt_override": (
                "Optional human direction preserved alongside Wanda's video packet."
            ),
        }


class GeneratedSceneImageMediaJobReviewForm(forms.ModelForm):
    PROVIDER_CHOICES = GeneratedMediaJobReviewForm.PROVIDER_CHOICES

    provider = forms.ChoiceField(
        required=False,
        choices=PROVIDER_CHOICES,
        help_text=(
            "Choose the image provider. Wanda will attach selected character "
            "identity references from the scene cast."
        ),
    )
    style_mode = forms.ChoiceField(
        required=False,
        choices=wanda_media_style_choices(),
        initial=MEDIA_STYLE_MATCH_REFERENCE,
        help_text=(
            "Controls the visual medium/style for the whole scene image."
        ),
    )
    custom_style_prompt = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional scene-wide style direction.",
    )
    scene_excerpt = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 7}),
        help_text=(
            "The piece of the approved scene Wanda should depict. Edit this "
            "down to the moment you want if the full scene is too broad."
        ),
    )
    subject_slugs = forms.CharField(
        required=False,
        help_text=(
            "Comma-separated character slugs to bind visually, e.g. mallory,tom."
        ),
    )
    reference_asset_limit = forms.IntegerField(
        required=False,
        min_value=1,
        help_text=(
            "Optional total reference cap for this provider, counted across "
            "all selected character references."
        ),
    )
    original_prompt = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    original_negative_prompt = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = GeneratedMediaJob
        fields = [
            "title",
            "provider",
            "style_mode",
            "custom_style_prompt",
            "scene_excerpt",
            "subject_slugs",
            "reference_asset_limit",
            "prompt",
            "negative_prompt",
            "user_prompt_override",
        ]
        widgets = {
            "prompt": forms.Textarea(attrs={"rows": 13}),
            "negative_prompt": forms.Textarea(attrs={"rows": 5}),
            "user_prompt_override": forms.Textarea(attrs={"rows": 5}),
        }
        help_texts = {
            "prompt": (
                "Editable base scene-image prompt. The scene excerpt, selected "
                "references, and override are saved separately and attached "
                "when Wanda sends the job to the provider."
            ),
            "negative_prompt": (
                "Identity swaps, character merging, continuity errors, and "
                "visual mistakes to avoid."
            ),
            "user_prompt_override": (
                "Optional human direction preserved alongside Wanda's scene packet."
            ),
        }


class GeneratedGeneralImageMediaJobReviewForm(forms.ModelForm):
    PROVIDER_CHOICES = GeneratedMediaJobReviewForm.PROVIDER_CHOICES

    provider = forms.ChoiceField(
        required=False,
        choices=PROVIDER_CHOICES,
        help_text=(
            "Choose the image provider. General image jobs are not tied to "
            "a scene or character identity."
        ),
    )
    style_mode = forms.ChoiceField(
        required=False,
        choices=wanda_media_style_choices(),
        initial=MEDIA_STYLE_MATCH_REFERENCE,
        help_text="Controls the visual medium/style for the general image.",
    )
    custom_style_prompt = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional style direction for this image.",
    )
    reference_asset_limit = forms.IntegerField(
        required=False,
        min_value=1,
        help_text=(
            "Optional provider/reference cap. Uploaded reference images count "
            "against the selected provider's limit."
        ),
    )
    original_prompt = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    original_negative_prompt = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = GeneratedMediaJob
        fields = [
            "title",
            "provider",
            "style_mode",
            "custom_style_prompt",
            "reference_asset_limit",
            "prompt",
            "negative_prompt",
            "user_prompt_override",
        ]
        widgets = {
            "prompt": forms.Textarea(attrs={"rows": 12}),
            "negative_prompt": forms.Textarea(attrs={"rows": 5}),
            "user_prompt_override": forms.Textarea(attrs={"rows": 5}),
        }
        help_texts = {
            "prompt": "Freeform image prompt to send to the selected provider.",
            "negative_prompt": "Visual mistakes, styles, or content to avoid.",
            "user_prompt_override": (
                "Optional extra direction preserved alongside the saved packet."
            ),
        }

    def clean_prompt(self):
        prompt = (self.cleaned_data.get("prompt") or "").strip()
        if not prompt:
            raise forms.ValidationError("Enter a freeform image prompt.")
        return prompt


class GeneratedMediaAssetForm(forms.ModelForm):
    metadata = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Optional JSON object with provider or asset metadata.",
    )

    class Meta:
        model = GeneratedMediaAsset
        fields = [
            "file",
            "caption",
            "provider",
            "provider_asset_id",
        ]
        widgets = {
            "caption": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_metadata(self):
        value = (self.cleaned_data.get("metadata") or "").strip()
        if not value:
            return {}

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError("Metadata must be valid JSON.") from exc

        if not isinstance(parsed, dict):
            raise forms.ValidationError("Metadata must be a JSON object.")

        return parsed

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.metadata_json = self.cleaned_data.get("metadata") or {}

        if commit:
            instance.save()

        return instance
