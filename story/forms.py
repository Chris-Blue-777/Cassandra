from django import forms
from .models import Character


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
