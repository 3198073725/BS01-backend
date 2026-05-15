from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.content.models import Report
from apps.interactions.models import Comment, Notification
from apps.videos.models import Video


class FourthRoundLogicFixTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.owner = User.objects.create_user(username='round4-owner', email='round4-owner@example.com', password='Passw0rd!')
        self.reporter = User.objects.create_user(username='round4-reporter', email='round4-reporter@example.com', password='Passw0rd!')
        self.admin = User.objects.create_user(
            username='round4-admin',
            email='round4-admin@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='admin',
        )
        self.other_user = User.objects.create_user(username='round4-other', email='round4-other@example.com', password='Passw0rd!')

    def auth(self, user):
        client = APIClient()
        res = client.post('/api/token/', {'username': user.username, 'password': 'Passw0rd!'}, format='json')
        self.assertEqual(res.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        return client

    def _video(self, title, owner=None):
        return Video.objects.create(
            title=title,
            description='',
            video_file=f'videos/{title}.mp4',
            user=owner or self.owner,
            status='published',
            visibility='public',
        )

    def test_report_handle_rejects_action_mismatched_with_target_type(self):
        admin_client = self.auth(self.admin)
        video = self._video('report-video')
        report = Report.objects.create(
            reporter=self.reporter,
            target_type='video',
            target_id=video.id,
            reason_code='spam',
            status='pending',
        )

        resp = admin_client.post(f'/api/admin/reports/{report.id}/handle/', {
            'action': 'ban_user',
            'notes': 'invalid',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('errors', resp.data)
        self.assertIn('action', resp.data['errors'])

        report.refresh_from_db()
        self.assertEqual(report.status, 'pending')

    def test_delete_content_report_still_notifies_original_owner(self):
        admin_client = self.auth(self.admin)
        video = self._video('delete-me')
        report = Report.objects.create(
            reporter=self.reporter,
            target_type='video',
            target_id=video.id,
            reason_code='spam',
            status='pending',
        )

        resp = admin_client.post(f'/api/admin/reports/{report.id}/handle/', {
            'action': 'delete_content',
            'notes': 'removed by moderation',
        }, format='json')

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Video.objects.filter(id=video.id).exists())
        notice = Notification.objects.filter(user_id=self.owner.id, verb='content_removed').first()
        self.assertIsNotNone(notice)
        self.assertEqual(str(notice.actor_id), str(self.admin.id))

    def test_qr_login_confirm_cannot_override_confirmed_session(self):
        create_url = reverse('users:login-qr-create')
        status_url = reverse('users:login-qr-status')
        confirm_url = reverse('users:login-qr-confirm')

        create_resp = self.client.post(create_url)
        self.assertEqual(create_resp.status_code, 200)
        session = create_resp.data['session']

        owner_client = self.auth(self.owner)
        first_confirm = owner_client.post(confirm_url, {'session': session}, format='json')
        self.assertEqual(first_confirm.status_code, 204)

        other_client = self.auth(self.other_user)
        second_confirm = other_client.post(confirm_url, {'session': session}, format='json')
        self.assertEqual(second_confirm.status_code, status.HTTP_400_BAD_REQUEST)

        status_resp = self.client.get(f'{status_url}?session={session}')
        self.assertEqual(status_resp.status_code, 200)
        self.assertEqual(status_resp.data['status'], 'confirmed')
        self.assertEqual(status_resp.data['user']['username'], self.owner.username)
