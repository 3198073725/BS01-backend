from django.urls import reverse
from rest_framework.test import APITestCase
from apps.users.models import User


class QrLoginApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='password123',
        )

    def test_qr_login_flow(self):
        create_url = reverse('users:login-qr-create')
        resp = self.client.post(create_url)
        self.assertEqual(resp.status_code, 200)
        session = resp.data['session']

        status_url = reverse('users:login-qr-status')
        resp = self.client.get(f'{status_url}?session={session}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'pending')

        self.client.force_authenticate(user=self.user)
        confirm_url = reverse('users:login-qr-confirm')
        resp = self.client.post(confirm_url, {'session': session})
        self.assertEqual(resp.status_code, 204)

        self.client.force_authenticate(user=None)
        resp = self.client.get(f'{status_url}?session={session}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'confirmed')
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)
        self.assertEqual(resp.data['user']['username'], 'testuser')
