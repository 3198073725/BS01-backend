import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.interactions.models import Comment
from apps.videos.models import Video


class TwelfthRoundLogicFixTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username='round12-owner',
            email='round12-owner@example.com',
            password='Passw0rd!',
        )
        self.video = Video.objects.create(
            title='round12-video',
            description='',
            video_file='videos/round12.mp4',
            user=self.owner,
            status='published',
            visibility='public',
            published_at=timezone.now(),
        )

    def test_comment_constraint_rejects_self_parent_reference(self):
        comment_id = uuid.uuid4()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Comment.objects.create(
                    id=comment_id,
                    content='loop',
                    user=self.owner,
                    video=self.video,
                    parent_id=comment_id,
                )
