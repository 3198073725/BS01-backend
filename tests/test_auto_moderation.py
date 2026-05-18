from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.configs.utils import get_system_setting, set_config
from apps.content.models import AuditLog
from apps.interactions.models import Comment
from apps.videos.models import Video
from apps.tasks.tasks import moderate_video_content


class AutoModerationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.user = User.objects.create_user(
            username='automod-user',
            email='automod-user@example.com',
            password='Passw0rd!',
        )
        self.reviewer = User.objects.create_user(
            username='reviewer-user',
            email='reviewer-user@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='reviewer',
        )
        self.admin = User.objects.create_user(
            username='admin-user',
            email='admin-user@example.com',
            password='Passw0rd!',
            is_staff=True,
            admin_role='admin',
        )
        self.client.force_authenticate(self.user)
        self.video = Video.objects.create(
            title='clean video',
            description='',
            video_file='videos/clean.mp4',
            video_file_f='videos/clean.mp4',
            status='published',
            visibility='public',
            user=self.user,
        )
        set_config('system', 'AUTO_MODERATION_ENABLED', True, value_type='bool')
        set_config('system', 'COMMENT_AUTOMOD_ENABLED', True, value_type='bool')
        set_config('system', 'VIDEO_AUTOMOD_ENABLED', True, value_type='bool')
        set_config('system', 'COMMENT_BLOCKED_KEYWORDS', 'spamword', value_type='string')
        set_config('system', 'VIDEO_BLOCKED_KEYWORDS', 'forbidden-title', value_type='string')

    def test_comment_is_rejected_when_keyword_matches(self):
        resp = self.client.post('/api/interactions/comments/', {
            'video_id': str(self.video.id),
            'content': 'this contains spamword inside',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Comment.objects.exists())
        self.assertTrue(
            AuditLog.objects.filter(verb='content.automod.blocked', meta__scenario='comment.create').exists()
        )

    def test_comment_uses_builtin_profanity_fallback_when_keywords_empty(self):
        set_config('system', 'COMMENT_BLOCKED_KEYWORDS', '', value_type='string')

        resp = self.client.post('/api/interactions/comments/', {
            'video_id': str(self.video.id),
            'content': '测试质控，操你妈',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Comment.objects.exists())
        self.assertTrue(
            AuditLog.objects.filter(
                verb='content.automod.blocked',
                meta__scenario='comment.create',
                meta__source='local-keywords',
            ).exists()
        )

    def test_comment_blocks_homophone_variant_when_keywords_empty(self):
        set_config('system', 'COMMENT_BLOCKED_KEYWORDS', '', value_type='string')

        resp = self.client.post('/api/interactions/comments/', {
            'video_id': str(self.video.id),
            'content': '测试质控，草泥马',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Comment.objects.exists())

    def test_comment_blocks_obfuscated_variant_when_keywords_empty(self):
        set_config('system', 'COMMENT_BLOCKED_KEYWORDS', '', value_type='string')

        resp = self.client.post('/api/interactions/comments/', {
            'video_id': str(self.video.id),
            'content': '测试质控，c n m',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Comment.objects.exists())

    def test_comment_blocks_leetspeak_variant_when_keywords_empty(self):
        set_config('system', 'COMMENT_BLOCKED_KEYWORDS', '', value_type='string')

        resp = self.client.post('/api/interactions/comments/', {
            'video_id': str(self.video.id),
            'content': '测试质控，c4o n1 m4',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Comment.objects.exists())

    def test_comment_blocks_sb_abbreviation_when_keywords_empty(self):
        set_config('system', 'COMMENT_BLOCKED_KEYWORDS', '', value_type='string')

        resp = self.client.post('/api/interactions/comments/', {
            'video_id': str(self.video.id),
            'content': '你个 s.b',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Comment.objects.exists())

    def test_comment_blocks_with_custom_canonical_rule(self):
        set_config('system', 'COMMENT_BLOCKED_KEYWORDS', '', value_type='string')
        set_config('system', 'COMMENT_CANONICAL_RULES', '草拟吗=操你妈', value_type='string')

        resp = self.client.post('/api/interactions/comments/', {
            'video_id': str(self.video.id),
            'content': '测试质控，草拟吗',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Comment.objects.exists())

    def test_comment_blocks_with_custom_pattern_rule(self):
        set_config('system', 'COMMENT_BLOCKED_KEYWORDS', '', value_type='string')
        set_config('system', 'COMMENT_PATTERN_RULES', r'n[\s._-]*m[\s._-]*d=nm-pattern', value_type='string')

        resp = self.client.post('/api/interactions/comments/', {
            'video_id': str(self.video.id),
            'content': '测试质控，n.m.d',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Comment.objects.exists())

    def test_comment_audit_log_contains_matched_details(self):
        set_config('system', 'COMMENT_BLOCKED_KEYWORDS', '', value_type='string')

        resp = self.client.post('/api/interactions/comments/', {
            'video_id': str(self.video.id),
            'content': '测试质控，草泥马',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        audit = AuditLog.objects.filter(
            verb='content.automod.blocked',
            meta__scenario='comment.create',
        ).latest('created_at')
        self.assertTrue(audit.meta.get('matched_details'))
        self.assertEqual(audit.meta['matched_details'][0]['type'], 'canonical')
        self.assertEqual(audit.meta['matched_details'][0]['matched_text'], '草泥马')

    def test_admin_automod_summary_aggregates_rule_hits(self):
        set_config('system', 'COMMENT_BLOCKED_KEYWORDS', '', value_type='string')
        set_config('system', 'COMMENT_PATTERN_RULES', r'n[\s._-]*m[\s._-]*d=nmd', value_type='string')

        self.client.post('/api/interactions/comments/', {
            'video_id': str(self.video.id),
            'content': '测试质控，草泥马',
        }, format='json')
        self.client.post('/api/interactions/comments/', {
            'video_id': str(self.video.id),
            'content': '测试质控，n.m.d',
        }, format='json')

        self.client.force_authenticate(self.reviewer)
        resp = self.client.get('/api/admin/audit-logs/automod-summary/', {
            'scenario': 'comment.create',
            'source': 'local-keywords',
            'days': 7,
        })

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data['total_hits'], 2)
        labels = {(row['type'], row['label']) for row in resp.data['results']}
        self.assertIn(('canonical', '操你妈'), labels)
        self.assertIn(('pattern', 'nmd'), labels)

    def test_admin_can_append_automod_rule(self):
        self.client.force_authenticate(self.admin)

        resp = self.client.post('/api/admin/audit-logs/automod-rules/apply/', {
            'rule_type': 'canonical',
            'label': '操你妈',
            'matched_text': '草拟吗',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['added'])
        self.assertIn('草拟吗=操你妈', str(get_system_setting('COMMENT_CANONICAL_RULES', '') or ''))

        resp2 = self.client.post('/api/admin/audit-logs/automod-rules/apply/', {
            'rule_type': 'canonical',
            'label': '操你妈',
            'matched_text': '草拟吗',
        }, format='json')
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertFalse(resp2.data['added'])

    def test_admin_can_append_pattern_rule(self):
        self.client.force_authenticate(self.admin)

        resp = self.client.post('/api/admin/audit-logs/automod-rules/apply/', {
            'rule_type': 'pattern',
            'label': 'nmd',
            'matched_text': r'n[\s._-]*m[\s._-]*d',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['added'])
        self.assertIn(r'n[\s._-]*m[\s._-]*d=nmd', str(get_system_setting('COMMENT_PATTERN_RULES', '') or ''))

    def test_admin_can_append_comment_keyword_rule(self):
        self.client.force_authenticate(self.admin)

        resp = self.client.post('/api/admin/audit-logs/automod-rules/apply/', {
            'content_type': 'comment',
            'rule_type': 'keyword',
            'label': '新敏感词',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['added'])
        self.assertIn('新敏感词', str(get_system_setting('COMMENT_BLOCKED_KEYWORDS', '') or ''))

    def test_admin_can_append_video_keyword_rule(self):
        self.client.force_authenticate(self.admin)

        resp = self.client.post('/api/admin/audit-logs/automod-rules/apply/', {
            'content_type': 'video',
            'rule_type': 'keyword',
            'label': '违规片名',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['added'])
        self.assertIn('违规片名', str(get_system_setting('VIDEO_BLOCKED_KEYWORDS', '') or ''))

    def test_admin_can_remove_comment_keyword_rule(self):
        set_config('system', 'COMMENT_BLOCKED_KEYWORDS', '旧词\n待删词', value_type='string')
        self.client.force_authenticate(self.admin)

        resp = self.client.delete('/api/admin/audit-logs/automod-rules/apply/', {
            'content_type': 'comment',
            'rule_type': 'keyword',
            'label': '待删词',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['removed'])
        current = str(get_system_setting('COMMENT_BLOCKED_KEYWORDS', '') or '')
        self.assertIn('旧词', current)
        self.assertNotIn('待删词', current)

    def test_admin_can_remove_canonical_rule(self):
        set_config('system', 'COMMENT_CANONICAL_RULES', '草拟吗=操你妈\n艹拟吗=操你妈', value_type='string')
        self.client.force_authenticate(self.admin)

        resp = self.client.delete('/api/admin/audit-logs/automod-rules/apply/', {
            'content_type': 'comment',
            'rule_type': 'canonical',
            'label': '操你妈',
            'matched_text': '草拟吗',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['removed'])
        current = str(get_system_setting('COMMENT_CANONICAL_RULES', '') or '')
        self.assertNotIn('草拟吗=操你妈', current)
        self.assertIn('艹拟吗=操你妈', current)

    def test_admin_cannot_duplicate_builtin_comment_keyword_rule(self):
        self.client.force_authenticate(self.admin)

        resp = self.client.post('/api/admin/audit-logs/automod-rules/apply/', {
            'content_type': 'comment',
            'rule_type': 'keyword',
            'label': '操你妈',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['added'])
        self.assertEqual(resp.data['source'], 'default')
        current = str(get_system_setting('COMMENT_BLOCKED_KEYWORDS', '') or '')
        self.assertNotIn('操你妈', current)

    def test_admin_cannot_remove_builtin_canonical_rule(self):
        self.client.force_authenticate(self.admin)

        resp = self.client.delete('/api/admin/audit-logs/automod-rules/apply/', {
            'content_type': 'comment',
            'rule_type': 'canonical',
            'label': '操你妈',
            'matched_text': '草泥马',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('内置规则不能删除', str(resp.data))

    def test_video_patch_forces_draft_when_keyword_matches(self):
        video = Video.objects.create(
            title='published title',
            description='clean',
            video_file='videos/pub.mp4',
            video_file_f='videos/pub.mp4',
            status='published',
            visibility='public',
            user=self.user,
        )

        resp = self.client.patch(f'/api/videos/{video.id}/', {
            'title': 'forbidden-title appears here',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        video.refresh_from_db()
        self.assertEqual(video.status, 'draft')
        self.assertIsNone(video.published_at)
        self.assertTrue(
            AuditLog.objects.filter(target_type='video', target_id=video.id, meta__scenario='video.update').exists()
        )

    @patch('apps.content.moderation._post_zhipu_moderation')
    def test_comment_is_rejected_by_zhipu_moderation_result(self, post_zhipu_mock):
        set_config('system', 'COMMENT_BLOCKED_KEYWORDS', '', value_type='string')
        set_config('system', 'ZHIPU_API_KEY', 'test-key', value_type='string')
        post_zhipu_mock.return_value = {
            'result_list': [{
                'content_type': 'text',
                'risk_level': 'REJECT',
                'risk_type': ['abuse'],
            }]
        }

        resp = self.client.post('/api/interactions/comments/', {
            'video_id': str(self.video.id),
            'content': 'hello world',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Comment.objects.exists())
        self.assertTrue(
            AuditLog.objects.filter(
                verb='content.automod.blocked',
                meta__scenario='comment.create',
                meta__source='zhipu-moderation',
            ).exists()
        )


@override_settings(MEDIA_ROOT=str(Path(settings.BASE_DIR) / 'test_media_automod'))
class AutoModerationUploadTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.user = User.objects.create_user(username='automod-upload', password='Passw0rd!')
        self.client.force_authenticate(self.user)
        set_config('system', 'AUTO_MODERATION_ENABLED', True, value_type='bool')
        set_config('system', 'VIDEO_AUTOMOD_ENABLED', True, value_type='bool')
        set_config('system', 'VIDEO_BLOCKED_KEYWORDS', 'forbidden-title', value_type='string')

    @patch('apps.videos.views.generate_vtt_and_thumbnail.delay', return_value=SimpleNamespace(id='thumb-task'))
    @patch('apps.videos.views.transcode_video_to_hls.delay', return_value=SimpleNamespace(id='hls-task'))
    @patch('apps.videos.views.moderate_video_content.delay', return_value=SimpleNamespace(id='moderation-task'))
    @patch('apps.videos.views._make_thumbnail', return_value=False)
    @patch('apps.videos.views._probe_video', return_value=(1920, 1080, 12))
    @patch('apps.videos.views._assert_video_file', return_value=None)
    def test_video_upload_returns_moderation_block_payload(
        self,
        _assert_mock,
        _probe_mock,
        _thumb_mock,
        _moderation_mock,
        _hls_mock,
        _vtt_mock,
    ):
        file = SimpleUploadedFile('clean.mp4', b'fake-video-data', content_type='video/mp4')

        resp = self.client.post('/api/videos/upload/', {
            'title': 'forbidden-title demo',
            'description': 'clean desc',
            'file': file,
        })

        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(resp.data['moderation']['blocked'])
        self.assertTrue(
            AuditLog.objects.filter(verb='content.automod.blocked', target_type='video', meta__scenario='video.upload').exists()
        )

    @patch('apps.content.moderation._post_zhipu_moderation')
    def test_video_media_task_drafts_video_when_zhipu_flags_thumbnail(self, post_zhipu_mock):
        set_config('system', 'VIDEO_BLOCKED_KEYWORDS', '', value_type='string')
        set_config('system', 'ZHIPU_API_KEY', 'test-key', value_type='string')
        set_config('system', 'MODERATION_MEDIA_PUBLIC_BASE_URL', 'https://media.example.com', value_type='string')
        post_zhipu_mock.return_value = {
            'result_list': [{
                'content_type': 'image',
                'risk_level': 'REJECT',
                'risk_type': ['violence'],
            }]
        }

        videos_dir = Path(settings.MEDIA_ROOT) / 'videos'
        thumbs_dir = videos_dir / 'thumbs'
        thumbs_dir.mkdir(parents=True, exist_ok=True)
        thumb_rel = 'videos/thumbs/flagged.jpg'
        (Path(settings.MEDIA_ROOT) / thumb_rel).write_bytes(b'jpeg-data')

        video = Video.objects.create(
            title='media-review',
            description='',
            video_file='videos/media-review.mp4',
            video_file_f='videos/media-review.mp4',
            thumbnail=thumb_rel,
            thumbnail_f=thumb_rel,
            status='published',
            visibility='public',
            user=self.user,
        )

        result = moderate_video_content(str(video.id))

        self.assertTrue(result['ok'])
        self.assertFalse(result['allowed'])
        video.refresh_from_db()
        self.assertEqual(video.status, 'draft')
        self.assertTrue(
            AuditLog.objects.filter(
                verb='content.automod.blocked',
                target_type='video',
                target_id=video.id,
                meta__scenario='video.media',
                meta__source='zhipu-moderation',
            ).exists()
        )
