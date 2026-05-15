from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.content.models import Report
from apps.interactions.models import Comment, Like, Notification
from apps.videos.models import Video


class FifthRoundLogicFixTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.owner = User.objects.create_user(username='round5-owner', email='round5-owner@example.com', password='Passw0rd!')
        self.actor = User.objects.create_user(username='round5-actor', email='round5-actor@example.com', password='Passw0rd!')
        self.reporter = User.objects.create_user(username='round5-reporter', email='round5-reporter@example.com', password='Passw0rd!')
        self.viewer = User.objects.create_user(username='round5-viewer', email='round5-viewer@example.com', password='Passw0rd!')

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
            thumbnail=f'videos/thumbs/{title}.jpg',
            user=owner or self.owner,
            status='published',
            visibility='public',
            published_at=timezone.now(),
        )

    def test_pending_report_constraint_blocks_duplicate_rows(self):
        video = self._video('report-target')
        Report.objects.create(
            reporter=self.reporter,
            target_type='video',
            target_id=video.id,
            reason_code='spam',
            status='pending',
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Report.objects.create(
                    reporter=self.reporter,
                    target_type='video',
                    target_id=video.id,
                    reason_code='other',
                    status='pending',
                )

        Report.objects.create(
            reporter=self.reporter,
            target_type='video',
            target_id=video.id,
            reason_code='resolved-copy',
            status='resolved',
        )
        self.assertEqual(Report.objects.filter(reporter=self.reporter, target_type='video', target_id=video.id).count(), 2)

    def test_notification_survives_actor_deletion_and_list_handles_null_actor(self):
        video = self._video('notif-target', owner=self.owner)
        notification = Notification.objects.create(
            user=self.viewer,
            actor=self.actor,
            verb='like',
            video=video,
        )

        self.actor.delete()

        notification.refresh_from_db()
        self.assertIsNone(notification.actor_id)

        viewer_client = self.auth(self.viewer)
        resp = viewer_client.get('/api/interactions/notifications/')

        self.assertEqual(resp.status_code, 200)
        item = next(x for x in resp.data['results'] if x['id'] == str(notification.id))
        self.assertIsNone(item['actor'])
        self.assertEqual(item['video']['id'], str(video.id))

    def test_like_constraint_rejects_rows_without_target(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Like.objects.create(user=self.viewer, video=None, comment=None)

    def test_like_constraint_rejects_rows_with_both_targets(self):
        video = self._video('like-both-targets')
        comment = Comment.objects.create(content='hello', user=self.owner, video=video)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Like.objects.create(user=self.viewer, video=video, comment=comment)
