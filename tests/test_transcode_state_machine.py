import os
import uuid
from pathlib import Path
from unittest.mock import patch
from django.test import APITestCase, override_settings
from django.utils import timezone
from django.conf import settings
from django.contrib.auth import get_user_model
from apps.videos.models import Video
from apps.tasks.tasks import transcode_video_to_hls


class TranscodeStateMachineTests(APITestCase):
    @override_settings(MEDIA_ROOT=str(Path(settings.BASE_DIR) / 'test_media'))
    def test_transcode_marks_published_on_success(self):
        media_root = Path(settings.MEDIA_ROOT)
        (media_root / 'videos' / 'hls').mkdir(parents=True, exist_ok=True)
        (media_root / 'videos').mkdir(parents=True, exist_ok=True)

        # Prepare a fake video file
        vid_key = uuid.uuid4().hex
        video_rel = f"videos/{vid_key}.mp4"
        (media_root / video_rel).parent.mkdir(parents=True, exist_ok=True)
        (media_root / video_rel).write_bytes(b'00')

        # Create a user & video in processing state (width/height present to skip probe)
        User = get_user_model()
        user = User.objects.create_user(username='u1', password='p@ssw0rd')
        v = Video.objects.create(
            title='t', description='', user=user,
            video_file=video_rel, video_file_f=video_rel,
            status='processing', visibility='public',
            width=1280, height=720
        )

        # Pre-create variant playlists so the task collects them without running ffmpeg
        out_dir = media_root / 'videos' / 'hls' / vid_key
        (out_dir / '720p').mkdir(parents=True, exist_ok=True)
        (out_dir / '480p').mkdir(parents=True, exist_ok=True)
        (out_dir / '720p' / 'index.m3u8').write_text('#EXTM3U\n', encoding='utf-8')
        (out_dir / '480p' / 'index.m3u8').write_text('#EXTM3U\n', encoding='utf-8')

        with patch('apps.tasks.tasks.subprocess.run') as mrun:
            mrun.return_value.returncode = 0
            res = transcode_video_to_hls(str(v.id))
        self.assertTrue(res.get('ok'))

        v.refresh_from_db()
        self.assertEqual(v.status, 'published')
        self.assertIsNotNone(v.published_at)
