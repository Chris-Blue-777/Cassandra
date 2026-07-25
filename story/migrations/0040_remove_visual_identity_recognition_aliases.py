from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("story", "0039_alter_generatedmediajob_generation_mode_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="charactervisualidentity",
            name="recognition_aliases_json",
        ),
        migrations.RemoveField(
            model_name="charactervisualidentityversion",
            name="recognition_aliases_json",
        ),
    ]
