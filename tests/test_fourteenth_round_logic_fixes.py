import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.interactions.models import Comment
from apps.videos.models import Video


class FourteenthRoundLogicFixTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username='round14-owner',
            email='round14-owner@example.com',
            password='Passw0rd!',
        )
        self.video = Video.objects.create(
            title='round14-video',
            description='',
            video_file='videos/round14.mp4',
            user=self.owner,
            status='published',
            visibility='public',
            published_at=timezone.now(),
        )

    def test_repair_comment_integrity_dry_run_does_not_modify_rows(self):
        root = Comment.objects.create(content='root', user=self.owner, video=self.video)
        child = Comment.objects.create(content='child', user=self.owner, video=self.video, parent=root)
        grandchild = Comment.objects.create(content='grandchild', user=self.owner, video=self.video, parent=child)

        out = StringIO()
        call_command('repair_comment_integrity', '--format', 'json', stdout=out)

        payload = json.loads(out.getvalue())
        grandchild.refresh_from_db()
        self.assertEqual(payload['mode'], 'dry-run')
        self.assertEqual(payload['repairs'][0]['count'], 1)
        self.assertEqual(payload['repairs'][0]['samples'][0]['id'], str(grandchild.id))
        self.assertEqual(grandchild.parent_id, child.id)

    def test_repair_comment_integrity_apply_reparents_to_top_level(self):
        root = Comment.objects.create(content='root', user=self.owner, video=self.video)
        child = Comment.objects.create(content='child', user=self.owner, video=self.video, parent=root)
        grandchild = Comment.objects.create(content='grandchild', user=self.owner, video=self.video, parent=child)

        out = StringIO()
        call_command('repair_comment_integrity', '--apply', '--format', 'json', stdout=out)

        payload = json.loads(out.getvalue())
        grandchild.refresh_from_db()
        self.assertEqual(payload['mode'], 'apply')
        self.assertEqual(payload['applied']['comment_reply_to_second_level'], 1)
        self.assertEqual(grandchild.parent_id, root.id)
