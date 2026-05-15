from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient


class SixthRoundLogicFixTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='round6-user',
            email='round6-user@example.com',
            password='Passw0rd!',
        )

    def auth(self):
        client = APIClient()
        res = client.post('/api/token/', {'username': self.user.username, 'password': 'Passw0rd!'}, format='json')
        self.assertEqual(res.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        return client

    def test_favorite_toggle_rejects_invalid_video_uuid(self):
        client = self.auth()

        resp = client.post('/api/interactions/favorite/toggle/', {'video_id': 'not-a-uuid'}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('video_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_history_record_rejects_invalid_video_uuid(self):
        client = self.auth()

        resp = client.post('/api/interactions/history/record/', {'video_id': 'not-a-uuid', 'current': 1, 'duration': 10}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('video_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_report_create_rejects_invalid_target_uuid(self):
        client = self.auth()

        resp = client.post('/api/interactions/reports/', {
            'target_type': 'video',
            'target_id': 'not-a-uuid',
            'reason_code': 'spam',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('target_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))
