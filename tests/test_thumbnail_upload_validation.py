from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.videos.models import Video


@override_settings(MEDIA_ROOT=str(Path(settings.BASE_DIR) / 'test_media_thumbnail_upload'))
class ThumbnailUploadValidationTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='thumb-user', password='p@ssw0rd')
        self.client.force_authenticate(self.user)
        self.video = Video.objects.create(
            title='video',
            description='',
            user=self.user,
            video_file='videos/source.mp4',
            video_file_f='videos/source.mp4',
            status='draft',
            visibility='public',
        )

    def test_thumbnail_upload_rejects_invalid_image_payload(self):
        resp = self.client.post(
            f'/api/videos/{self.video.id}/thumbnail/upload/',
            {'file': ('cover.jpg', b'not-an-image', 'image/jpeg')},
            format='multipart',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('errors', resp.data)
        self.assertIn('file', resp.data['errors'])

        self.video.refresh_from_db()
        self.assertFalse(bool(self.video.thumbnail))
