from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("story", "0027_charactermemory_compaction_layers"),
    ]

    operations = [
        migrations.AddField(
            model_name="narrativememory",
            name="compacted_into",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="compacted_source_memories",
                to="story.narrativememory",
            ),
        ),
        migrations.AddField(
            model_name="narrativememory",
            name="is_context_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="narrativememory",
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
            model_name="narrativememory",
            name="source_memory_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="narrativememory",
            name="source_memory_ids_json",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="characterperceptionchange",
            name="change_layer",
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
            model_name="characterperceptionchange",
            name="compacted_into",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="compacted_source_changes",
                to="story.characterperceptionchange",
            ),
        ),
        migrations.AddField(
            model_name="characterperceptionchange",
            name="is_context_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="characterperceptionchange",
            name="source_change_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="characterperceptionchange",
            name="source_change_ids_json",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddIndex(
            model_name="narrativememory",
            index=models.Index(
                fields=["world", "memory_layer", "is_context_active"],
                name="story_narra_world_i_7aa78e_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="narrativememory",
            index=models.Index(
                fields=["world", "created_at"],
                name="story_narra_world_i_91b1f2_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="characterperceptionchange",
            index=models.Index(
                fields=["world", "observer", "target", "change_layer", "is_context_active"],
                name="story_chara_world_i_8af31d_idx",
            ),
        ),
    ]
