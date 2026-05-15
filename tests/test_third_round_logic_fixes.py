import os
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.interactions.models import Notification
from apps.videos.models import Video


class ThirdRoundLogicFixTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.owner = User.objects.create_user(username='round3-owner', email='round3-owner@example.com', password='Passw0rd!')
        self.viewer = User.objects.create_user(username='round3-viewer', email='round3-viewer@example.com', password='Passw0rd!')
        self.admin = User.objects.create_user(
            username='round3-admin',
            email='round3-admin@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='reviewer',
        )

    def auth(self, user):
        client = APIClient()
        res = client.post('/api/token/', {'username': user.username, 'password': 'Passw0rd!'}, format='json')
        self.assertEqual(res.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        return client

    def _video(self, title, owner, status_value='draft', published_at=None):
        return Video.objects.create(
            title=title,
            description='',
            video_file=f'videos/{title}.mp4',
            thumbnail=f'videos/thumbs/{title}.jpg',
            user=owner,
            status=status_value,
            visibility='public',
            published_at=published_at,
        )

    def test_admin_batch_approve_rejects_unverified_owner(self):
        admin_client = self.auth(self.admin)
        video = self._video('approve-unverified', self.owner, status_value='draft')

        resp = admin_client.post('/api/admin/videos/batch-approve/', {
            'video_ids': [str(video.id)],
            'action': 'approve',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('errors', resp.data)
        self.assertIn('action', resp.data['errors'])

        video.refresh_from_db()
        self.assertEqual(video.status, 'draft')

    def test_admin_bulk_publish_preserves_existing_published_at(self):
        admin_client = self.auth(self.admin)
        self.owner.is_verified = True
        self.owner.save(update_fields=['is_verified', 'updated_at'])
        original_published_at = timezone.now() - timezone.timedelta(days=3)
        video = self._video('already-published', self.owner, status_value='published', published_at=original_published_at)

        resp = admin_client.post('/api/admin/videos/bulk-update/', {
            'video_ids': [str(video.id)],
            'status': 'published',
        }, format='json')

        self.assertEqual(resp.status_code, 200)
        video.refresh_from_db()
        self.assertEqual(video.status, 'published')
        self.assertEqual(video.published_at, original_published_at)

    def test_login_send_code_returns_error_and_no_cooldown_on_send_failure(self):
        cache.clear()
        with patch('apps.users.views.send_mail', side_effect=RuntimeError('smtp down')):
            resp = self.client.post('/api/users/login/send-code/', {'email': 'codefail@example.com'}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('detail', resp.data)
        self.assertIsNone(cache.get('login_code:last_email:codefail@example.com'))

    def test_notifications_list_builds_media_urls(self):
        video = self._video('notif-video', self.owner, status_value='published', published_at=timezone.now())
        self.owner.profile_picture = 'avatars/owner.jpg'
        self.owner.save(update_fields=['profile_picture'])
        Notification.objects.create(
            user=self.viewer,
            actor=self.owner,
            verb='like_video',
            video=video,
        )

        viewer_client = self.auth(self.viewer)
        resp = viewer_client.get('/api/interactions/notifications/')

        self.assertEqual(resp.status_code, 200)
        item = resp.data['results'][0]
        self.assertTrue(item['actor']['avatar_url'].startswith('http://') or item['actor']['avatar_url'].startswith('https://'))
        self.assertTrue(item['video']['thumbnail_url'].startswith('http://') or item['video']['thumbnail_url'].startswith('https://'))

    @patch('apps.interactions.views._media_url', side_effect=RuntimeError('url build failed'))
    def test_notifications_list_keeps_row_when_media_url_build_fails(self, _mock_media_url):
        video = self._video('notif-safe', self.owner, status_value='published', published_at=timezone.now())
        self.owner.profile_picture = 'avatars/owner-safe.jpg'
        self.owner.save(update_fields=['profile_picture'])
        Notification.objects.create(
            user=self.viewer,
            actor=self.owner,
            verb='like_video',
            video=video,
        )

        viewer_client = self.auth(self.viewer)
        resp = viewer_client.get('/api/interactions/notifications/')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['total'], 1)
        self.assertEqual(len(resp.data['results']), 1)
        item = resp.data['results'][0]
        self.assertEqual(item['actor']['avatar_url'], '')
        self.assertEqual(item['video']['thumbnail_url'], '')

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='bs01-bulk-delete-'))
    def test_video_bulk_delete_removes_media_files(self):
        owner_client = self.auth(self.owner)
        video = Video.objects.create(
            title='bulk-delete-target',
            description='',
            video_file='videos/bulk-delete-target.mp4',
            thumbnail='videos/thumbs/bulk-delete-target.jpg',
            video_file_f='videos/bulk-delete-target.mp4',
            thumbnail_f='videos/thumbs/bulk-delete-target.jpg',
            low_mp4='videos/bulk-delete-target-low.mp4',
            user=self.owner,
            status='published',
            visibility='public',
            published_at=timezone.now(),
        )

        media_root = settings.MEDIA_ROOT
        paths = [
            os.path.join(media_root, 'videos', 'bulk-delete-target.mp4'),
            os.path.join(media_root, 'videos', 'thumbs', 'bulk-delete-target.jpg'),
            os.path.join(media_root, 'videos', 'bulk-delete-target-low.mp4'),
            os.path.join(media_root, 'videos', 'thumbs', 'bulk-delete-target.vtt'),
            os.path.join(media_root, 'videos', 'hls', 'bulk-delete-target', 'master.m3u8'),
        ]
        for path in paths:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as fh:
                fh.write(b'x')

        resp = owner_client.post('/api/videos/bulk-delete/', {'ids': [str(video.id)]}, format='json')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['removed'], 1)
        self.assertFalse(Video.objects.filter(id=video.id).exists())
        for path in paths[:-1]:
            self.assertFalse(os.path.exists(path), path)
        self.assertFalse(os.path.exists(os.path.join(media_root, 'videos', 'hls', 'bulk-delete-target')))
