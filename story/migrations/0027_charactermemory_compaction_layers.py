from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("story", "0026_subjective_relationship_dossiers"),
    ]

    operations = [
        migrations.AddField(
            model_name="charactermemory",
            name="compacted_into",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="compacted_source_memories",
                to="story.charactermemory",
            ),
        ),
        migrations.AddField(
            model_name="charactermemory",
            name="is_context_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="charactermemory",
            name="memory_layer",
            field=models.CharField(
                choices=[
                    ("raw", "Raw"),
                    ("past", "Past"),
                    ("history", "History"),
                ],
                default="raw",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="charactermemory",
            name="source_memory_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="charactermemory",
            name="source_memory_ids_json",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddIndex(
            model_name="charactermemory",
            index=models.Index(
                fields=["world", "character", "memory_layer", "is_context_active"],
                name="story_chara_world_i_871de6_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="charactermemory",
            index=models.Index(
                fields=["character", "created_at"],
                name="story_chara_charact_8c5ad9_idx",
            ),
        ),
    ]
