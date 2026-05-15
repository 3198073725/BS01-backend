import json
from io import StringIO

from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.interactions.models import Comment
from apps.videos.models import Video


class ThirteenthRoundLogicFixTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username='round13-owner',
            email='round13-owner@example.com',
            password='Passw0rd!',
        )

    def _video(self, title):
        return Video.objects.create(
            title=title,
            description='',
            video_file=f'videos/{title}.mp4',
            user=self.owner,
            status='published',
            visibility='public',
            published_at=timezone.now(),
        )

    def test_audit_integrity_reports_zero_findings_on_clean_state(self):
        out = StringIO()

        call_command('audit_integrity', '--format', 'json', stdout=out)

        payload = json.loads(out.getvalue())
        self.assertEqual(payload['summary']['hard_violations'], 0)
        self.assertEqual(payload['summary']['soft_violations'], 0)

    def test_audit_integrity_detects_comment_tree_soft_violations(self):
        video_a = self._video('round13-a')
        video_b = self._video('round13-b')
        root = Comment.objects.create(content='root', user=self.owner, video=video_a)
        child = Comment.objects.create(content='child', user=self.owner, video=video_b, parent=root)
        Comment.objects.create(content='grandchild', user=self.owner, video=video_b, parent=child)

        out = StringIO()
        call_command('audit_integrity', '--format', 'json', '--limit', '5', stdout=out)

        payload = json.loads(out.getvalue())
        findings = {item['code']: item for item in payload['findings']}

        self.assertEqual(findings['comment_cross_video_parent']['count'], 1)
        self.assertEqual(findings['comment_reply_to_second_level']['count'], 1)
        self.assertEqual(findings['comment_cross_video_parent']['samples'][0]['id'], str(child.id))
