from django.utils.text import slugify
from story.models import Character

def build_character_registry(world):
    characters = Character.objects.filter(world=world, is_active=True)

    registry = []
    for c in characters:
        registry.append({
            "slug": c.slug,
            "name": c.name,
            "description": c.description or "",
            "is_player": c.is_player,
            "profile": c.profile_json or {},
            "status": c.status_json or {},
            "diction": c.diction_json or {},
        })

    return registry

def validate_resolved_slug(world, slug):
    if not slug:
        return None
    return Character.objects.filter(world=world, slug=slug, is_active=True).first()
