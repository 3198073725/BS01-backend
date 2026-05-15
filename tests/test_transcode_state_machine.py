import uuid
from pathlib import Path
from unittest.mock import patch
from django.test import override_settings
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase
from apps.videos.models import Video
from apps.tasks.tasks import transcode_video_to_hls


class TranscodeStateMachineTests(APITestCase):
    @override_settings(MEDIA_ROOT=str(Path(settings.BASE_DIR) / 'test_media'))
    def test_transcode_marks_published_on_success(self):
        media_root = Path(settings.MEDIA_ROOT)
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

        def fake_run(cmd, **kwargs):
            out_path = Path(cmd[-1])
            if str(out_path).endswith('.m3u8'):
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text('#EXTM3U\n', encoding='utf-8')
            elif str(out_path).endswith('.mp4'):
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(b'00')
            return type('Result', (), {'returncode': 0})()

        with patch('apps.tasks.tasks.subprocess.run', side_effect=fake_run):
            res = transcode_video_to_hls(str(v.id))
        self.assertTrue(res.get('ok'))

        v.refresh_from_db()
        self.assertEqual(v.status, 'draft')
        self.assertIsNone(v.published_at)
        self.assertTrue((media_root / res['master_rel']).exists())

    @override_settings(MEDIA_ROOT=str(Path(settings.BASE_DIR) / 'test_media'))
    def test_transcode_failure_moves_processing_video_back_to_draft_and_saves_error(self):
        media_root = Path(settings.MEDIA_ROOT)
        (media_root / 'videos').mkdir(parents=True, exist_ok=True)

        vid_key = uuid.uuid4().hex
        video_rel = f"videos/{vid_key}.mp4"
        (media_root / video_rel).parent.mkdir(parents=True, exist_ok=True)
        (media_root / video_rel).write_bytes(b'00')

        User = get_user_model()
        user = User.objects.create_user(username='u2', password='p@ssw0rd')
        v = Video.objects.create(
            title='t2', description='', user=user,
            video_file=video_rel, video_file_f=video_rel,
            status='processing', visibility='public',
            width=1280, height=720
        )

        def fake_run(cmd, **kwargs):
            return type('Result', (), {'returncode': 0})()

        with patch('apps.tasks.tasks.subprocess.run', side_effect=fake_run):
            res = transcode_video_to_hls(str(v.id))

        self.assertFalse(res.get('ok'))
        self.assertEqual(res.get('error'), 'no_variants')
        v.refresh_from_db()
        self.assertEqual(v.status, 'draft')
        self.assertEqual(v.transcode_error, 'no_variants')

    def test_retry_transcode_rejects_published_video(self):
        User = get_user_model()
        user = User.objects.create_user(username='u3', password='p@ssw0rd')
        self.client.force_authenticate(user)
        v = Video.objects.create(
            title='published', description='', user=user,
            video_file='videos/pub.mp4', video_file_f='videos/pub.mp4',
            status='published', visibility='public'
        )

        resp = self.client.post(f'/api/videos/{v.id}/retry-transcode/', {}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('detail', resp.data)
