from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('videos', '0010_video_low_mp4_video_transcode_error'),
    ]

    operations = [
        migrations.AddField(
            model_name='video',
            name='is_featured',
            field=models.BooleanField(default=False, verbose_name='精选'),
        ),
    ]
