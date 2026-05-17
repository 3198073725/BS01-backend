from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
import uuid
from rest_framework import status
from rest_framework.test import APIClient

from apps.content.models import ModerationAction, Report
from apps.interactions.models import Comment, Notification
from apps.videos.models import Video


class FourthRoundLogicFixTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.owner = User.objects.create_user(username='round4-owner', email='round4-owner@example.com', password='Passw0rd!')
        self.reporter = User.objects.create_user(username='round4-reporter', email='round4-reporter@example.com', password='Passw0rd!')
        self.second_reporter = User.objects.create_user(username='round4-reporter-2', email='round4-reporter-2@example.com', password='Passw0rd!')
        self.reviewer = User.objects.create_user(
            username='round4-reviewer',
            email='round4-reviewer@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='reviewer',
        )
        self.admin = User.objects.create_user(
            username='round4-admin',
            email='round4-admin@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='admin',
        )
        self.super_admin = User.objects.create_user(
            username='round4-super-admin',
            email='round4-super-admin@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='super_admin',
        )
        self.moderator = User.objects.create_user(
            username='round4-moderator',
            email='round4-moderator@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='moderator',
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

    def test_report_handle_rejects_non_pending_report(self):
        admin_client = self.auth(self.admin)
        video = self._video('already-handled')
        report = Report.objects.create(
            reporter=self.reporter,
            target_type='video',
            target_id=video.id,
            reason_code='spam',
            status='resolved',
        )

        resp = admin_client.post(f'/api/admin/reports/{report.id}/handle/', {
            'action': 'dismiss',
            'notes': 'duplicate attempt',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        report.refresh_from_db()
        self.assertEqual(report.status, 'resolved')
        self.assertEqual(ModerationAction.objects.filter(report=report).count(), 0)

    def test_report_handle_keeps_pending_when_delete_target_missing(self):
        admin_client = self.auth(self.admin)
        report = Report.objects.create(
            reporter=self.reporter,
            target_type='video',
            target_id=uuid.uuid4(),
            reason_code='spam',
            status='pending',
        )

        resp = admin_client.post(f'/api/admin/reports/{report.id}/handle/', {
            'action': 'delete_content',
            'notes': 'remove missing object',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        report.refresh_from_db()
        self.assertEqual(report.status, 'pending')
        self.assertIsNone(report.handled_by)
        self.assertEqual(ModerationAction.objects.filter(report=report).count(), 0)

    def test_delete_content_report_resolves_other_pending_reports_for_same_target(self):
        admin_client = self.auth(self.admin)
        video = self._video('shared-target')
        primary = Report.objects.create(
            reporter=self.reporter,
            target_type='video',
            target_id=video.id,
            reason_code='spam',
            status='pending',
        )
        sibling = Report.objects.create(
            reporter=self.second_reporter,
            target_type='video',
            target_id=video.id,
            reason_code='abuse',
            status='pending',
        )

        resp = admin_client.post(f'/api/admin/reports/{primary.id}/handle/', {
            'action': 'delete_content',
            'notes': 'removed after review',
        }, format='json')

        self.assertEqual(resp.status_code, 200)
        primary.refresh_from_db()
        sibling.refresh_from_db()
        self.assertEqual(primary.status, 'resolved')
        self.assertEqual(sibling.status, 'resolved')
        self.assertEqual(str(sibling.handled_by_id), str(self.admin.id))
        self.assertIsNotNone(sibling.handled_at)

    def test_report_detail_hides_user_email_from_reviewer(self):
        reviewer_client = self.auth(self.reviewer)
        admin_client = self.auth(self.admin)
        report = Report.objects.create(
            reporter=self.reporter,
            target_type='user',
            target_id=self.owner.id,
            reason_code='spam',
            status='pending',
        )

        reviewer_resp = reviewer_client.get(f'/api/admin/reports/{report.id}/')
        admin_resp = admin_client.get(f'/api/admin/reports/{report.id}/')

        self.assertEqual(reviewer_resp.status_code, 200)
        self.assertEqual(admin_resp.status_code, 200)
        self.assertNotIn('email', reviewer_resp.data['target_detail'])
        self.assertEqual(admin_resp.data['target_detail']['email'], self.owner.email)

    def test_escalated_video_report_can_be_finished_by_moderator(self):
        reviewer_client = self.auth(self.reviewer)
        moderator_client = self.auth(self.moderator)
        video = self._video('escalated-video')
        report = Report.objects.create(
            reporter=self.reporter,
            target_type='video',
            target_id=video.id,
            reason_code='spam',
            status='pending',
        )

        escalate_resp = reviewer_client.post(f'/api/admin/reports/{report.id}/handle/', {
            'action': 'escalate',
            'notes': 'need moderator decision',
        }, format='json')
        self.assertEqual(escalate_resp.status_code, 200)

        report.refresh_from_db()
        self.assertEqual(report.status, 'escalated')

        finish_resp = moderator_client.post(f'/api/admin/reports/{report.id}/handle/', {
            'action': 'delete_content',
            'notes': 'confirmed violation',
        }, format='json')
        self.assertEqual(finish_resp.status_code, 200)

        report.refresh_from_db()
        self.assertEqual(report.status, 'resolved')
        self.assertFalse(Video.objects.filter(id=video.id).exists())
        self.assertEqual(ModerationAction.objects.filter(report=report).count(), 2)

    def test_escalate_requires_handoff_notes(self):
        reviewer_client = self.auth(self.reviewer)
        video = self._video('escalate-needs-notes')
        report = Report.objects.create(
            reporter=self.reporter,
            target_type='video',
            target_id=video.id,
            reason_code='spam',
            status='pending',
        )

        resp = reviewer_client.post(f'/api/admin/reports/{report.id}/handle/', {
            'action': 'escalate',
            'notes': '   ',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        report.refresh_from_db()
        self.assertEqual(report.status, 'pending')
        self.assertEqual(ModerationAction.objects.filter(report=report).count(), 0)

    def test_super_admin_cannot_escalate_report(self):
        super_admin_client = self.auth(self.super_admin)
        video = self._video('super-admin-should-not-escalate')
        report = Report.objects.create(
            reporter=self.reporter,
            target_type='video',
            target_id=video.id,
            reason_code='spam',
            status='pending',
        )

        resp = super_admin_client.post(f'/api/admin/reports/{report.id}/handle/', {
            'action': 'escalate',
            'notes': 'should handle directly',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        report.refresh_from_db()
        self.assertEqual(report.status, 'pending')
        self.assertEqual(ModerationAction.objects.filter(report=report).count(), 0)

    def test_reviewer_cannot_finalize_escalated_video_report(self):
        reviewer_client = self.auth(self.reviewer)
        video = self._video('reviewer-escalation-limit')
        report = Report.objects.create(
            reporter=self.reporter,
            target_type='video',
            target_id=video.id,
            reason_code='spam',
            status='pending',
        )

        escalate_resp = reviewer_client.post(f'/api/admin/reports/{report.id}/handle/', {
            'action': 'escalate',
            'notes': 'need higher role',
        }, format='json')
        self.assertEqual(escalate_resp.status_code, 200)

        finish_resp = reviewer_client.post(f'/api/admin/reports/{report.id}/handle/', {
            'action': 'dismiss',
            'notes': 'trying to self-finish',
        }, format='json')
        self.assertEqual(finish_resp.status_code, status.HTTP_403_FORBIDDEN)

        report.refresh_from_db()
        self.assertEqual(report.status, 'escalated')
        self.assertEqual(ModerationAction.objects.filter(report=report).count(), 1)

    def test_report_list_exposes_latest_escalation_handoff(self):
        reviewer_client = self.auth(self.reviewer)
        video = self._video('escalation-list-handoff')
        report = Report.objects.create(
            reporter=self.reporter,
            target_type='video',
            target_id=video.id,
            reason_code='spam',
            status='pending',
        )

        escalate_resp = reviewer_client.post(f'/api/admin/reports/{report.id}/handle/', {
            'action': 'escalate',
            'notes': 'needs moderator review for removal',
        }, format='json')
        self.assertEqual(escalate_resp.status_code, 200)

        list_resp = reviewer_client.get('/api/admin/reports/')
        self.assertEqual(list_resp.status_code, 200)
        item = next(x for x in list_resp.data['results'] if x['id'] == str(report.id))
        self.assertEqual(item['status'], 'escalated')
        self.assertEqual(item['latest_action']['action'], 'escalate')
        self.assertEqual(item['latest_action']['reason'], 'needs moderator review for removal')
        self.assertEqual(item['latest_action']['moderator']['username'], self.reviewer.username)

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
