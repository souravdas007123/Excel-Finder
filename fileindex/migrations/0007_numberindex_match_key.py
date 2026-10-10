from django.db import migrations, models


def fill_match_key(apps, schema_editor):
    """Purani rows ke liye match_key bharo: 10 se lambe number ke aakhri 10 digits, baaki jaise ke taise.

    Ek hi SQL UPDATE hai, isliye 10 lakh+ rows par bhi kuch second me ho jata hai. Index baad me banta hai (tez).
    """
    qn = schema_editor.connection.ops.quote_name
    table = qn("fileindex_numberindex")
    number, key = qn("number"), qn("match_key")
    schema_editor.execute(
        f"UPDATE {table} SET {key} = CASE WHEN LENGTH({number}) > 10 "
        f"THEN SUBSTR({number}, LENGTH({number}) - 9) ELSE {number} END"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("fileindex", "0006_bulksearch"),
    ]

    operations = [
        # 1) column bina index ke, 2) data bharo, 3) tab index banao (bharte waqt index chalana dheema hota hai)
        migrations.AddField(
            model_name="numberindex",
            name="match_key",
            field=models.CharField(default="", max_length=32),
        ),
        migrations.RunPython(fill_match_key, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="numberindex",
            name="match_key",
            field=models.CharField(db_index=True, default="", max_length=32),
        ),
    ]
