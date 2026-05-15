from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('videos', '0011_video_is_featured'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='video',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(comment_count__gte=0)
                    & models.Q(like_count__gte=0)
                    & models.Q(view_count__gte=0)
                ),
                name='chk_video_counters_nonneg',
            ),
        ),
    ]
