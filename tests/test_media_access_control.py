import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings

from apps.users.models import User
from apps.videos.models import Video
from backend.urls import media_serve_with_range


@override_settings(MEDIA_ROOT=str(Path(settings.BASE_DIR) / 'test_media_access_control'))
class MediaAccessControlTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user(username='owner-media', email='owner-media@example.com', password='p@ssw0rd')
        self.other = User.objects.create_user(username='other-media', email='other-media@example.com', password='p@ssw0rd')
        self.media_root = Path(settings.MEDIA_ROOT)
        (self.media_root / 'videos').mkdir(parents=True, exist_ok=True)

    def _create_video(self, status='published', visibility='public'):
        key = uuid.uuid4().hex
        rel = f'videos/{key}.mp4'
        path = self.media_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'video-data')
        video = Video.objects.create(
            title=key,
            description='',
            user=self.owner,
            video_file=rel,
            video_file_f=rel,
            status=status,
            visibility=visibility,
        )
        return video, rel

    def test_private_media_hidden_from_other_users(self):
        _video, rel = self._create_video(status='published', visibility='private')
        request = self.factory.get(f'/media/{rel}')
        request.user = self.other

        with self.assertRaisesMessage(Http404, 'Not Found'):
            media_serve_with_range(request, rel, document_root=settings.MEDIA_ROOT)

    def test_processing_media_hidden_from_anonymous_users(self):
        _video, rel = self._create_video(status='processing', visibility='public')
        request = self.factory.get(f'/media/{rel}')
        request.user = AnonymousUser()

        with self.assertRaisesMessage(Http404, 'Not Found'):
            media_serve_with_range(request, rel, document_root=settings.MEDIA_ROOT)

    def test_owner_can_access_private_media(self):
        _video, rel = self._create_video(status='published', visibility='private')
        request = self.factory.get(f'/media/{rel}')
        request.user = self.owner

        resp = media_serve_with_range(request, rel, document_root=settings.MEDIA_ROOT)

        self.assertEqual(resp.status_code, 200)
