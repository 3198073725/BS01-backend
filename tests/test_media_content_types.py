import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, override_settings

from backend.urls import media_serve_with_range


@override_settings(MEDIA_ROOT=str(Path(settings.BASE_DIR) / 'test_media_content_types'))
class MediaContentTypeTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.media_root = Path(settings.MEDIA_ROOT)

    def test_hls_playlist_uses_apple_mpegurl_content_type(self):
        rel = f'videos/hls/{uuid.uuid4().hex}/480p/index.m3u8'
        path = self.media_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#EXTM3U\n#EXTINF:6.0,\n480p_000.ts\n', encoding='utf-8')

        request = self.factory.get(f'/media/{rel}')
        request.user = AnonymousUser()
        response = media_serve_with_range(request, rel, document_root=settings.MEDIA_ROOT)

        body = b''.join(response.streaming_content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/vnd.apple.mpegurl')
        self.assertEqual(int(response['Content-Length']), len(body))

    def test_hls_segment_uses_video_mp2t_content_type_for_range_response(self):
        rel = f'videos/hls/{uuid.uuid4().hex}/480p/480p_000.ts'
        path = self.media_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'abcdef')

        request = self.factory.get(f'/media/{rel}', HTTP_RANGE='bytes=0-0')
        request.user = AnonymousUser()
        response = media_serve_with_range(request, rel, document_root=settings.MEDIA_ROOT)

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response['Content-Type'], 'video/mp2t')
        self.assertEqual(response['Content-Range'], 'bytes 0-0/6')
        self.assertEqual(response.content, b'a')
