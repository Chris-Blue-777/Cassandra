from django.utils.text import slugify
from story.models import Character

def build_character_registry(world):
    characters = Character.objects.filter(world=world, is_active=True)

    registry = []
    for c in characters:
        registry.append({
            "name": c.name,
            "slug": c.slug,
            "description": c.description or "",
            # optional but future-proof:
            "aliases": [],  # can fill later
        })

    return registry

def resolve_character_reference(world, raw_name):
    slug = slugify(raw_name or "")
    if not slug:
        return None

    return Character.objects.filter(world=world, slug=slug).first()
