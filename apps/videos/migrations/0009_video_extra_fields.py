from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('videos', '0008_enable_pg_trgm'),
    ]

    operations = [
        migrations.AddField(
            model_name='video',
            name='allow_comments',
            field=models.BooleanField(default=True, verbose_name='允许评论'),
        ),
        migrations.AddField(
            model_name='video',
            name='allow_download',
            field=models.BooleanField(default=False, verbose_name='允许下载'),
        ),
        migrations.AddField(
            model_name='video',
            name='visibility',
            field=models.CharField(default='public', max_length=20, verbose_name='可见性'),
        ),
        migrations.AddConstraint(
            model_name='video',
            constraint=models.CheckConstraint(
                condition=models.Q(visibility__in=['public', 'unlisted', 'private']),
                name='chk_video_visibility',
            ),
        ),
    ]
