from pathlib import Path
from django.test import APITestCase, override_settings
from django.conf import settings
from django.contrib.auth import get_user_model
from apps.videos.models import Video
from unittest.mock import patch


@override_settings(SITE_URL='', MEDIA_URL='/media/')
class MediaUrlConstructionTests(APITestCase):
    def setUp(self):
        # isolate media root
        self.media_root = Path(settings.BASE_DIR) / 'test_media_urls'
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: __import__('shutil').rmtree(self.media_root, ignore_errors=True))

    @override_settings(MEDIA_ROOT=str(Path(settings.BASE_DIR) / 'test_media_urls'))
    def test_video_list_urls_with_files_present(self):
        User = get_user_model()
        u = User.objects.create_user(username='alice', password='x')
        vid_key = 'abc123'
        video_rel = f'videos/{vid_key}.mp4'
        (self.media_root / video_rel).parent.mkdir(parents=True, exist_ok=True)
        (self.media_root / video_rel).write_bytes(b'00')
        vtt_rel = f'videos/thumbs/{vid_key}.vtt'
        (self.media_root / vtt_rel).parent.mkdir(parents=True, exist_ok=True)
        (self.media_root / vtt_rel).write_text('WEBVTT\n', encoding='utf-8')
        hls_rel = f'videos/hls/{vid_key}/master.m3u8'
        (self.media_root / hls_rel).parent.mkdir(parents=True, exist_ok=True)
        (self.media_root / hls_rel).write_text('#EXTM3U\n', encoding='utf-8')

        Video.objects.create(
            title='t', user=u,
            video_file=video_rel, video_file_f=video_rel,
            status='published', visibility='public'
        )

        resp = self.client.get('/api/videos/list/')
        self.assertEqual(resp.status_code, 200)
        item = (resp.json().get('results') or [])[0]
        # default_storage.url(FileSystem) returns MEDIA_URL + rel; with SITE_URL empty, view falls back to request host http://testserver
        self.assertEqual(item['video_url'], f'http://testserver/media/{video_rel}')
        self.assertEqual(item['thumbnail_vtt_url'], f'http://testserver/media/{vtt_rel}')
        self.assertEqual(item['hls_master_url'], f'http://testserver/media/{hls_rel}')

    @override_settings(MEDIA_ROOT=str(Path(settings.BASE_DIR) / 'test_media_urls'))
    def test_video_list_urls_absent_files_are_none(self):
        User = get_user_model()
        u = User.objects.create_user(username='bob', password='x')
        vid_key = 'nope'
        video_rel = f'videos/{vid_key}.mp4'
        # do not create any files
        Video.objects.create(
            title='t', user=u,
            video_file=video_rel, video_file_f=video_rel,
            status='published', visibility='public'
        )
        resp = self.client.get('/api/videos/list/')
        self.assertEqual(resp.status_code, 200)
        item = (resp.json().get('results') or [])[0]
        self.assertIsNone(item['thumbnail_vtt_url'])
        self.assertIsNone(item['hls_master_url'])

    @override_settings(MEDIA_ROOT=str(Path(settings.BASE_DIR) / 'test_media_urls'))
    def test_video_list_urls_with_object_storage(self):
        User = get_user_model()
        u = User.objects.create_user(username='carol', password='x')
        vid_key = 'oss1'
        video_rel = f'videos/{vid_key}.mp4'
        Video.objects.create(
            title='t', user=u,
            video_file=video_rel, video_file_f=video_rel,
            status='published', visibility='public'
        )
        cdn = 'https://cdn.example.com'
        def fake_url(rel):
            return f"{cdn}/{rel}"
        with patch('django.core.files.storage.default_storage.exists', return_value=True), \
             patch('django.core.files.storage.default_storage.url', side_effect=fake_url):
            resp = self.client.get('/api/videos/list/')
            self.assertEqual(resp.status_code, 200)
            item = (resp.json().get('results') or [])[0]
            self.assertTrue(item['video_url'].startswith(cdn))
            self.assertTrue(item['thumbnail_vtt_url'].startswith(cdn) or item['thumbnail_vtt_url'] is None)
            self.assertTrue(item['hls_master_url'].startswith(cdn) or item['hls_master_url'] is None)
