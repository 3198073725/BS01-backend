from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase


@override_settings(
    MEDIA_ROOT=str(Path(settings.BASE_DIR) / 'test_media_upload_limits'),
    VIDEO_MAX_SIZE_BYTES=10,
    CHUNK_SIZE_BYTES=4,
)
class VideoUploadLimitTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='upload-limits', password='p@ssw0rd')
        self.client.force_authenticate(self.user)

    def test_upload_init_rejects_file_larger_than_video_max(self):
        resp = self.client.post('/api/videos/upload/init/', {
            'filename': 'demo.mp4',
            'filesize': 11,
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('detail', resp.data)

    def test_upload_chunk_rejects_index_out_of_range(self):
        init_resp = self.client.post('/api/videos/upload/init/', {
            'filename': 'demo.mp4',
            'filesize': 8,
        }, format='json')
        self.assertEqual(init_resp.status_code, status.HTTP_200_OK)

        upload_id = init_resp.data['upload_id']
        chunk = SimpleUploadedFile('chunk.bin', b'abcd', content_type='application/octet-stream')
        resp = self.client.post('/api/videos/upload/chunk/', {
            'upload_id': upload_id,
            'index': 2,
            'chunk': chunk,
        })

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('errors', resp.data)
        self.assertIn('index', resp.data['errors'])
