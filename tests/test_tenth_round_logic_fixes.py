from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.interactions.models import Follow, Notification


class TenthRoundLogicFixTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username='round10-owner',
            email='round10-owner@example.com',
            password='Passw0rd!',
        )
        self.viewer = User.objects.create_user(
            username='round10-viewer',
            email='round10-viewer@example.com',
            password='Passw0rd!',
        )

    def auth(self, user):
        client = APIClient()
        res = client.post('/api/token/', {'username': user.username, 'password': 'Passw0rd!'}, format='json')
        self.assertEqual(res.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        return client

    def test_notifications_mark_read_rejects_invalid_ids_uuid(self):
        client = self.auth(self.viewer)

        resp = client.post('/api/interactions/notifications/mark-read/', {'ids': ['not-a-uuid']}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('ids', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    @patch('apps.interactions.signals.Notification.objects.create', side_effect=RuntimeError('db insert failed'))
    def test_follow_notification_failure_is_logged_and_follow_still_persists(self, _mock_create):
        with self.assertLogs('apps.interactions.signals', level='ERROR') as logs:
            follow = Follow.objects.create(follower=self.viewer, followed=self.owner)

        self.assertTrue(Follow.objects.filter(id=follow.id).exists())
        self.assertFalse(Notification.objects.filter(user=self.owner, actor=self.viewer, verb='follow').exists())
        joined = '\n'.join(logs.output)
        self.assertIn('interaction_notification_create_failed', joined)
        self.assertIn('RuntimeError: db insert failed', joined)
