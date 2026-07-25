from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("story", "0042_alter_storyarc_current_phase_alter_storyarc_horizon_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="proposal",
            name="cassandra_scene_state_update_json",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
