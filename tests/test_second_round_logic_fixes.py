from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.interactions.models import Comment, Favorite
from apps.videos.models import Video


class SecondRoundLogicFixTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.owner = User.objects.create_user(username='round2-owner', email='round2-owner@example.com', password='Passw0rd!')
        self.other = User.objects.create_user(username='round2-other', email='round2-other@example.com', password='Passw0rd!')

    def auth(self, user):
        client = APIClient()
        res = client.post('/api/token/', {'username': user.username, 'password': 'Passw0rd!'}, format='json')
        self.assertEqual(res.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        return client

    def _video(self, title, owner=None, status_value='published', visibility='public'):
        return Video.objects.create(
            title=title,
            description='',
            video_file=f'videos/{title}.mp4',
            user=owner or self.owner,
            status=status_value,
            visibility=visibility,
            published_at=(timezone.now() if status_value == 'published' else None),
        )

    def test_comments_reject_reply_to_second_level_comment(self):
        client = self.auth(self.owner)
        video = self._video('comment-tree')
        root = Comment.objects.create(content='root', user=self.owner, video=video)
        child = Comment.objects.create(content='child', user=self.owner, video=video, parent=root)

        resp = client.post('/api/interactions/comments/', {
            'video_id': str(video.id),
            'parent_id': str(child.id),
            'content': 'third level',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('errors', resp.data)
        self.assertIn('parent_id', resp.data['errors'])

    def test_history_list_moves_rewatched_video_to_front(self):
        client = self.auth(self.owner)
        first = self._video('history-a')
        second = self._video('history-b')

        r1 = client.post('/api/interactions/history/record/', {'video_id': str(first.id), 'current': 10, 'duration': 100}, format='json')
        self.assertEqual(r1.status_code, 200)
        r2 = client.post('/api/interactions/history/record/', {'video_id': str(second.id), 'current': 20, 'duration': 100}, format='json')
        self.assertEqual(r2.status_code, 200)
        r3 = client.post('/api/interactions/history/record/', {'video_id': str(first.id), 'current': 30, 'duration': 100}, format='json')
        self.assertEqual(r3.status_code, 200)

        listing = client.get('/api/interactions/history/')
        self.assertEqual(listing.status_code, 200)
        ids = [item['id'] for item in listing.data['results']]
        self.assertEqual(ids[0], str(first.id))
        self.assertEqual(ids[1], str(second.id))

    def test_public_favorites_pagination_total_matches_visible_items(self):
        owner_client = self.auth(self.owner)
        viewer_client = self.auth(self.other)
        public_video = self._video('fav-public')
        private_video = self._video('fav-private', visibility='private')

        Favorite.objects.create(user=self.owner, video=public_video)
        Favorite.objects.create(user=self.owner, video=private_video)

        resp = viewer_client.get('/api/interactions/favorites/', {'user_id': str(self.owner.id)})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['total'], 1)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['id'], str(public_video.id))

    def test_verify_email_request_returns_error_when_send_fails(self):
        client = self.auth(self.owner)

        with patch('apps.users.views.send_mail', side_effect=RuntimeError('smtp down')):
            resp = client.post('/api/users/verify-email/request/', {}, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('detail', resp.data)
