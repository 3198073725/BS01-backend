from django.db import migrations
from django.contrib.postgres.indexes import GinIndex


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0005_user_profile_picture_f'),
    ]

    operations = [
        migrations.RunSQL(sql="CREATE EXTENSION IF NOT EXISTS pg_trgm;", reverse_sql=migrations.RunSQL.noop),
        migrations.AddIndex(
            model_name='user',
            index=GinIndex(fields=['username'], name='idx_users_username_trgm', opclasses=['gin_trgm_ops']),
        ),
        migrations.AddIndex(
            model_name='user',
            index=GinIndex(fields=['nickname'], name='idx_users_nickname_trgm', opclasses=['gin_trgm_ops']),
        ),
    ]
