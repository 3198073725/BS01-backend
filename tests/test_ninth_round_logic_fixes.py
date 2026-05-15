from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.interactions.models import History, Like
from apps.videos.models import Video, WatchLater


class NinthRoundLogicFixTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username='round9-owner',
            email='round9-owner@example.com',
            password='Passw0rd!',
        )
        self.viewer = User.objects.create_user(
            username='round9-viewer',
            email='round9-viewer@example.com',
            password='Passw0rd!',
        )

    def auth(self, user):
        client = APIClient()
        res = client.post('/api/token/', {'username': user.username, 'password': 'Passw0rd!'}, format='json')
        self.assertEqual(res.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        return client

    def _video(self, title, visibility='public'):
        return Video.objects.create(
            title=title,
            description='',
            video_file=f'videos/{title}.mp4',
            user=self.owner,
            status='published',
            visibility=visibility,
            published_at=timezone.now(),
        )

    def test_relationship_rejects_invalid_user_uuid(self):
        resp = self.client.get('/api/interactions/relationship/', {'user_id': 'not-a-uuid'})

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('user_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_likes_list_rejects_invalid_user_uuid(self):
        resp = self.client.get('/api/interactions/likes/', {'user_id': 'not-a-uuid'})

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('user_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_watch_later_list_rejects_invalid_user_uuid(self):
        resp = self.client.get('/api/interactions/watch-later/', {'user_id': 'not-a-uuid'})

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('user_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_history_list_rejects_invalid_user_uuid(self):
        resp = self.client.get('/api/interactions/history/', {'user_id': 'not-a-uuid'})

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('user_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_public_likes_pagination_total_matches_visible_items(self):
        viewer_client = self.auth(self.viewer)
        public_video = self._video('like-public')
        private_video = self._video('like-private', visibility='private')

        Like.objects.create(user=self.owner, video=public_video)
        Like.objects.create(user=self.owner, video=private_video)

        resp = viewer_client.get('/api/interactions/likes/', {'user_id': str(self.owner.id)})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['total'], 1)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['id'], str(public_video.id))

    def test_public_watch_later_pagination_total_matches_visible_items(self):
        viewer_client = self.auth(self.viewer)
        public_video = self._video('watchlater-public')
        private_video = self._video('watchlater-private', visibility='private')

        WatchLater.objects.create(user=self.owner, video=public_video)
        WatchLater.objects.create(user=self.owner, video=private_video)

        resp = viewer_client.get('/api/interactions/watch-later/', {'user_id': str(self.owner.id)})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['total'], 1)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['id'], str(public_video.id))

    def test_public_history_pagination_total_matches_visible_items(self):
        viewer_client = self.auth(self.viewer)
        public_video = self._video('history-public')
        private_video = self._video('history-private', visibility='private')

        History.objects.create(user=self.owner, video=public_video, progress=0.5, watch_duration=10)
        History.objects.create(user=self.owner, video=private_video, progress=0.6, watch_duration=12)

        resp = viewer_client.get('/api/interactions/history/', {'user_id': str(self.owner.id)})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['total'], 1)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['id'], str(public_video.id))
