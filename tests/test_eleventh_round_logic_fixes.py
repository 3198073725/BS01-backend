from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.videos.models import Video


class EleventhRoundLogicFixTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username='round11-owner',
            email='round11-owner@example.com',
            password='Passw0rd!',
        )

    def _video(self, **kwargs):
        payload = {
            'title': 'round11-video',
            'description': '',
            'video_file': 'videos/round11.mp4',
            'user': self.owner,
        }
        payload.update(kwargs)
        return Video.objects.create(**payload)

    def test_video_counter_constraint_rejects_negative_like_count(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._video(like_count=-1)

    def test_video_counter_constraint_rejects_negative_comment_count(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._video(comment_count=-1)

    def test_video_counter_constraint_rejects_negative_view_count(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._video(view_count=-1)
