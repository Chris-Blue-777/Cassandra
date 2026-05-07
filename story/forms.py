from django import forms
from .models import Character


class CharacterForm(forms.ModelForm):
    profile_summary = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Profile Summary",
    )
    archetype = forms.CharField(required=False)

    gender = forms.CharField(required=False)

    pronoun_subject = forms.CharField(required=False, initial="they")
    pronoun_object = forms.CharField(required=False, initial="them")
    pronoun_possessive = forms.CharField(required=False, initial="their")
    pronoun_possessive_pronoun = forms.CharField(required=False, initial="theirs")
    pronoun_reflexive = forms.CharField(required=False, initial="themself")

    class Meta:
        model = Character
        fields = [
            "world",
            "name",
            "slug",
            "description",
            "is_player",
            "agent_provider",

            "profile_summary",
            "archetype",
            "gender",
            "pronoun_subject",
            "pronoun_object",
            "pronoun_possessive",
            "pronoun_possessive_pronoun",
            "pronoun_reflexive",
        ]
