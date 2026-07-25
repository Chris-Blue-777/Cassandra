from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("story", "0043_proposal_cassandra_scene_state_update_json"),
    ]

    operations = [
        migrations.AddField(
            model_name="storyarc",
            name="ooc_tag",
            field=models.TextField(blank=True, default=""),
        ),
    ]
