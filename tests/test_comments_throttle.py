from django.test import override_settings
from rest_framework.test import APITestCase, APIClient
from django.contrib.auth import get_user_model
from apps.videos.models import Video


@override_settings(REST_FRAMEWORK={
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.ScopedRateThrottle',
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'comments': '2/min',
        'anon': '100/hour',
        'user': '1000/hour',
    },
})
class CommentThrottleTests(APITestCase):
    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(username='u1', password='p@ssw0rd')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.video = Video.objects.create(
            title='t', description='',
            video_file='videos/x.mp4', video_file_f='videos/x.mp4',
            status='published', visibility='public', user=self.user
        )

    def test_comments_post_throttled(self):
        url = '/api/interactions/comments/'
        payload = {'video_id': str(self.video.id), 'content': 'hi'}
        r1 = self.client.post(url, payload, format='json')
        r2 = self.client.post(url, payload, format='json')
        r3 = self.client.post(url, payload, format='json')
        # 第三次应触发节流 429
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r3.status_code, 429)
