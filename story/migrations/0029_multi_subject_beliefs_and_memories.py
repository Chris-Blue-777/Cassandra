from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("story", "0028_narrative_and_relationship_compaction_layers"),
    ]

    operations = [
        migrations.AddField(
            model_name="characterbelief",
            name="related_subject_slugs_json",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="charactermemory",
            name="related_character_slugs_json",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
