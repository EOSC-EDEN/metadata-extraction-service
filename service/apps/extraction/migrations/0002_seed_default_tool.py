from django.db import migrations


def seed_default_tool(apps, schema_editor):
    ExtractionTool = apps.get_model("extraction", "ExtractionTool")
    ExtractionTool.objects.get_or_create(
        name="exiftool",
        defaults={
            "display_name": "ExifTool",
            "adapter_kind": "subprocess",
            "invocation_command": "exiftool",
            "timeout_seconds": 30,
            "is_enabled": True,
            "is_default": True,
            "is_mandatory": False,
        },
    )


def remove_default_tool(apps, schema_editor):
    ExtractionTool = apps.get_model("extraction", "ExtractionTool")
    ExtractionTool.objects.filter(name="exiftool").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("extraction", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_default_tool, remove_default_tool),
    ]
