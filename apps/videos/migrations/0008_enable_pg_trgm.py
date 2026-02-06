from django.db import migrations

SQL = "CREATE EXTENSION IF NOT EXISTS pg_trgm;"


class Migration(migrations.Migration):
    dependencies = [
        ('videos', '0007_video_thumbnail_f_video_video_file_f'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
