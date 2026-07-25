import story.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("story", "0040_remove_visual_identity_recognition_aliases"),
    ]

    operations = [
        migrations.AlterField(
            model_name="generatedmediajob",
            name="source",
            field=models.CharField(
                choices=[
                    ("wanda_identity", "Wanda identity"),
                    ("approved_scene", "Approved scene"),
                    ("general", "General"),
                ],
                default="wanda_identity",
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="generatedmediajob",
            name="generation_mode",
            field=models.CharField(
                choices=[
                    ("portrait", "Portrait"),
                    ("scene_image", "Scene image"),
                    ("general_image", "General image"),
                    ("video_image", "Image to video"),
                    ("video_text", "Text to video"),
                ],
                default="portrait",
                max_length=40,
            ),
        ),
        migrations.CreateModel(
            name="GeneratedMediaJobReference",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "file",
                    models.FileField(
                        upload_to=story.models.generated_media_job_reference_upload_path
                    ),
                ),
                ("caption", models.TextField(blank=True, default="")),
                (
                    "provider",
                    models.CharField(blank=True, default="", max_length=80),
                ),
                ("metadata_json", models.JSONField(blank=True, default=dict)),
                ("ordering", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reference_uploads",
                        to="story.generatedmediajob",
                    ),
                ),
                (
                    "world",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="generated_media_job_references",
                        to="story.world",
                    ),
                ),
            ],
            options={
                "ordering": ["ordering", "id"],
                "indexes": [
                    models.Index(
                        fields=["world", "created_at"],
                        name="story_gener_world_i_7a5b98_idx",
                    ),
                    models.Index(
                        fields=["job", "ordering"],
                        name="story_gener_job_id_967808_idx",
                    ),
                ],
            },
        ),
    ]
