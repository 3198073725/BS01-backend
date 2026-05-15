from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.interactions.models import Comment
from apps.videos.models import Video


class SeventhRoundLogicFixTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username='round7-owner',
            email='round7-owner@example.com',
            password='Passw0rd!',
        )
        self.viewer = User.objects.create_user(
            username='round7-viewer',
            email='round7-viewer@example.com',
            password='Passw0rd!',
        )

    def auth(self, user):
        client = APIClient()
        res = client.post('/api/token/', {'username': user.username, 'password': 'Passw0rd!'}, format='json')
        self.assertEqual(res.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        return client

    def _video(self, title='round7-video'):
        return Video.objects.create(
            title=title,
            description='',
            video_file=f'videos/{title}.mp4',
            thumbnail=f'videos/thumbs/{title}.jpg',
            user=self.owner,
            status='published',
            visibility='public',
            published_at=timezone.now(),
        )

    def _comment(self, video, content='hello'):
        return Comment.objects.create(content=content, user=self.owner, video=video)

    def test_follow_rejects_invalid_user_uuid(self):
        client = self.auth(self.viewer)

        resp = client.post('/api/interactions/follow/', {'user_id': 'not-a-uuid'}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('user_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_unfollow_rejects_invalid_user_uuid(self):
        client = self.auth(self.viewer)

        resp = client.post('/api/interactions/unfollow/', {'user_id': 'not-a-uuid'}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('user_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_comments_list_rejects_invalid_video_uuid(self):
        resp = self.client.get('/api/interactions/comments/', {'video_id': 'not-a-uuid'})

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('video_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_comment_create_rejects_invalid_parent_uuid(self):
        client = self.auth(self.viewer)
        video = self._video()

        resp = client.post('/api/interactions/comments/', {
            'video_id': str(video.id),
            'content': 'reply',
            'parent_id': 'not-a-uuid',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('parent_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_comment_replies_rejects_invalid_parent_uuid(self):
        resp = self.client.get('/api/interactions/comments/replies/', {'parent_id': 'not-a-uuid'})

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('parent_id', str(resp.data))
        self.assertIn('格式不正确', str(resp.data))

    def test_comment_delete_invalid_comment_uuid_is_rejected_by_router(self):
        client = self.auth(self.owner)

        resp = client.delete('/api/interactions/comments/not-a-uuid/')

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_comment_like_toggle_invalid_comment_uuid_is_rejected_by_router(self):
        client = self.auth(self.viewer)

        resp = client.post('/api/interactions/comments/not-a-uuid/like/', {}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
