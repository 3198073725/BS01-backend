from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient


class EighthRoundLogicFixTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='round8-user',
            email='round8-user@example.com',
            password='Passw0rd!',
        )

    def auth(self):
        client = APIClient()
        res = client.post('/api/token/', {'username': self.user.username, 'password': 'Passw0rd!'}, format='json')
        self.assertEqual(res.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        return client

    def test_followers_reject_invalid_user_uuid(self):
        resp = self.client.get('/api/interactions/followers/', {'user_id': 'not-a-uuid'})

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('user_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_following_reject_invalid_user_uuid(self):
        resp = self.client.get('/api/interactions/following/', {'user_id': 'not-a-uuid'})

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('user_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_likes_bulk_unlike_rejects_invalid_video_uuid(self):
        client = self.auth()

        resp = client.post('/api/interactions/likes/bulk-unlike/', {'video_ids': ['not-a-uuid']}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('video_ids', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_favorites_bulk_remove_rejects_invalid_video_uuid(self):
        client = self.auth()

        resp = client.post('/api/interactions/favorites/bulk-remove/', {'video_ids': ['not-a-uuid']}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('video_ids', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_watch_later_bulk_remove_rejects_invalid_video_uuid(self):
        client = self.auth()

        resp = client.post('/api/interactions/watch-later/bulk-remove/', {'video_ids': ['not-a-uuid']}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('video_ids', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_history_bulk_remove_rejects_invalid_video_uuid(self):
        client = self.auth()

        resp = client.post('/api/interactions/history/bulk-remove/', {'video_ids': ['not-a-uuid']}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('video_ids', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))
