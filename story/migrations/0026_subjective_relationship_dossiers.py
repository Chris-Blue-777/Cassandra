from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("story", "0025_alter_characterscene_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="characterbelief",
            name="basis",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="characterperception",
            name="knowledge_basis",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="characterperception",
            name="last_change_summary",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="characterperception",
            name="open_questions_json",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="characterperceptionchange",
            name="access_gate_json",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="characterperceptionchange",
            name="knowledge_basis",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="characterperceptionchange",
            name="open_questions_json",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="characterperceptionchange",
            name="revised_summary",
            field=models.TextField(blank=True, default=""),
        ),
    ]
