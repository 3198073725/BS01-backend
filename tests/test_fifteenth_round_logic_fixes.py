from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch

from apps.interactions.models import Comment
from apps.videos.models import Video


class FifteenthRoundLogicFixTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.super_admin = User.objects.create_user(
            username='round15-super',
            email='round15-super@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='super_admin',
        )
        self.reviewer = User.objects.create_user(
            username='round15-reviewer',
            email='round15-reviewer@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='reviewer',
        )
        self.moderator = User.objects.create_user(
            username='round15-moderator',
            email='round15-moderator@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='moderator',
        )
        self.target_admin = User.objects.create_user(
            username='round15-target',
            email='round15-target@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='admin',
        )
        self.admin = User.objects.create_user(
            username='round15-admin',
            email='round15-admin@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='admin',
        )
        self.user_to_manage = User.objects.create_user(
            username='round15-managed',
            email='round15-managed@example.com',
            password='Passw0rd!',
        )
        self.owner = User.objects.create_user(
            username='round15-owner',
            email='round15-owner@example.com',
            password='Passw0rd!',
        )

    def auth(self, user):
        client = APIClient()
        res = client.post('/api/token/', {'username': user.username, 'password': 'Passw0rd!'}, format='json')
        self.assertEqual(res.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        return client

    def _video(self, title='round15-video', owner=None, video_file='videos/round15-video.mp4'):
        return Video.objects.create(
            title=title,
            description='',
            video_file=video_file,
            user=owner or self.owner,
            status='processing',
            visibility='public',
        )

    def test_switch_user_requires_authenticated_super_admin(self):
        resp = self.client.post('/api/admin/switch-user/', {
            'target_username': self.target_admin.username,
            'target_password': 'Passw0rd!',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

        reviewer_client = self.auth(self.reviewer)
        resp = reviewer_client.post('/api/admin/switch-user/', {
            'target_username': self.target_admin.username,
            'target_password': 'Passw0rd!',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_switch_user_returns_target_admin_tokens_for_super_admin(self):
        super_client = self.auth(self.super_admin)

        resp = super_client.post('/api/admin/switch-user/', {
            'target_username': self.target_admin.username,
            'target_password': 'Passw0rd!',
        }, format='json')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['user']['username'], self.target_admin.username)
        self.assertEqual(resp.data['user']['admin_role'], 'admin')
        self.assertTrue(resp.data['access'])
        self.assertTrue(resp.data['refresh'])
        self.assertTrue(resp.data['switched'])

    def test_only_super_admin_role_can_change_admin_role_flags(self):
        reviewer_client = self.auth(self.reviewer)

        resp = reviewer_client.patch(f'/api/admin/users/{self.user_to_manage.id}/', {
            'is_staff': True,
            'admin_role': 'reviewer',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        super_client = self.auth(self.super_admin)
        resp = super_client.patch(f'/api/admin/users/{self.user_to_manage.id}/', {
            'is_staff': True,
            'admin_role': 'reviewer',
        }, format='json')

        self.assertEqual(resp.status_code, 200)
        self.user_to_manage.refresh_from_db()
        self.assertTrue(self.user_to_manage.is_staff)
        self.assertEqual(self.user_to_manage.admin_role, 'reviewer')

    def test_system_config_requires_admin_role_or_higher(self):
        reviewer_client = self.auth(self.reviewer)
        resp = reviewer_client.get('/api/configs/admin/list/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        admin_client = self.auth(self.admin)
        resp = admin_client.get('/api/configs/admin/list/')
        self.assertEqual(resp.status_code, 200)

        resp = admin_client.post('/api/configs/admin/update/', {
            'featured_limit': 6,
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'ok')

    def test_reviewer_cannot_patch_users_but_admin_can(self):
        reviewer_client = self.auth(self.reviewer)
        resp = reviewer_client.patch(f'/api/admin/users/{self.user_to_manage.id}/', {
            'is_active': False,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        admin_client = self.auth(self.admin)
        resp = admin_client.patch(f'/api/admin/users/{self.user_to_manage.id}/', {
            'is_active': False,
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.user_to_manage.refresh_from_db()
        self.assertFalse(self.user_to_manage.is_active)

    def test_category_tag_and_announcement_writes_require_admin(self):
        reviewer_client = self.auth(self.reviewer)
        admin_client = self.auth(self.admin)

        resp = reviewer_client.post('/api/admin/categories/', {'name': 'round15-cat'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = admin_client.post('/api/admin/categories/', {'name': 'round15-cat'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        category_id = resp.data['id']

        resp = reviewer_client.post('/api/admin/tags/', {'name': 'round15-tag'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = admin_client.post('/api/admin/tags/', {'name': 'round15-tag'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        tag_id = resp.data['id']

        resp = reviewer_client.post('/api/admin/announcements/', {'title': 'round15-ann'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = admin_client.post('/api/admin/announcements/', {'title': 'round15-ann'}, format='json')
        self.assertEqual(resp.status_code, 200)

        resp = reviewer_client.patch(f'/api/admin/categories/{category_id}/', {'name': 'round15-cat-2'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = reviewer_client.patch(f'/api/admin/tags/{tag_id}/', {'name': 'round15-tag-2'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_comment_delete_requires_moderator(self):
        video = self._video()
        comment = Comment.objects.create(user=self.owner, video=video, content='round15-comment')

        reviewer_client = self.auth(self.reviewer)
        resp = reviewer_client.delete(f'/api/admin/comments/{comment.id}/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        moderator_client = self.auth(self.moderator)
        resp = moderator_client.delete(f'/api/admin/comments/{comment.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Comment.objects.filter(id=comment.id).exists())

    @patch('apps.tasks.tasks.transcode_video_to_hls.delay')
    @patch('apps.tasks.tasks.generate_vtt_and_thumbnail.delay')
    def test_video_delete_and_retry_require_moderator(self, mock_thumb_delay, mock_hls_delay):
        mock_thumb_delay.return_value.id = 'thumb-task'
        mock_hls_delay.return_value.id = 'hls-task'
        video = self._video(title='round15-video-ops', video_file='videos/round15-video-ops.mp4')

        reviewer_client = self.auth(self.reviewer)
        resp = reviewer_client.post(f'/api/admin/videos/{video.id}/retry-transcode/', format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = reviewer_client.delete(f'/api/admin/videos/{video.id}/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = reviewer_client.post('/api/admin/videos/bulk-delete/', {'video_ids': [str(video.id)]}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        moderator_client = self.auth(self.moderator)
        resp = moderator_client.post(f'/api/admin/videos/{video.id}/retry-transcode/', format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'processing')

        resp = moderator_client.post('/api/admin/videos/bulk-delete/', {'video_ids': [str(video.id)]}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['removed'], 1)
        self.assertFalse(Video.objects.filter(id=video.id).exists())

    def test_reviewer_can_change_video_review_status_but_not_operational_fields(self):
        self.owner.is_verified = True
        self.owner.save(update_fields=['is_verified'])
        video = self._video(title='round15-review-status', video_file='videos/round15-review-status.mp4')

        reviewer_client = self.auth(self.reviewer)
        resp = reviewer_client.patch(f'/api/admin/videos/{video.id}/', {
            'status': 'published',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        video.refresh_from_db()
        self.assertEqual(video.status, 'published')

        resp = reviewer_client.patch(f'/api/admin/videos/{video.id}/', {
            'visibility': 'private',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_video_operational_bulk_update_requires_admin(self):
        video = self._video(title='round15-bulk-ops', video_file='videos/round15-bulk-ops.mp4')
        reviewer_client = self.auth(self.reviewer)
        resp = reviewer_client.post('/api/admin/videos/bulk-update/', {
            'video_ids': [str(video.id)],
            'allow_comments': False,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        resp = reviewer_client.post('/api/admin/videos/bulk-update/', {
            'video_ids': [str(video.id)],
            'status': 'banned',
        }, format='json')
        self.assertEqual(resp.status_code, 200)

        admin_client = self.auth(self.admin)
        resp = admin_client.post('/api/admin/videos/bulk-update/', {
            'video_ids': [str(video.id)],
            'allow_comments': False,
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        video.refresh_from_db()
        self.assertFalse(video.allow_comments)
