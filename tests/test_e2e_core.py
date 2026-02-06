from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.users.models import User
from apps.videos.models import Video


class E2ECoreTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username='owner', email='o@example.com', password='Passw0rd!')
        self.other = User.objects.create_user(username='other', email='x@example.com', password='Passw0rd!')

    def auth(self, user):
        c = APIClient()
        res = c.post('/api/token/', {'username': user.username, 'password': 'Passw0rd!'}, format='json')
        self.assertEqual(res.status_code, 200)
        access = res.data.get('access')
        self.assertTrue(access)
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        return c

    def test_private_video_invisible_and_interactions_forbidden(self):
        v = Video.objects.create(
            title='pvt', description='', video_file='videos/a.mp4', user=self.owner,
            status='published', visibility='private', published_at=timezone.now()
        )
        c_owner = self.auth(self.owner)
        c_other = self.auth(self.other)
        r = c_other.get(f'/api/videos/{v.id}/')
        self.assertEqual(r.status_code, 404)
        r = c_owner.get(f'/api/videos/{v.id}/')
        self.assertEqual(r.status_code, 200)
        r = c_other.post('/api/interactions/like/toggle/', {'video_id': str(v.id)}, format='json')
        self.assertEqual(r.status_code, 404)
        r = c_other.get('/api/interactions/comments/', {'video_id': str(v.id)})
        self.assertEqual(r.status_code, 404)
        r = c_other.post('/api/interactions/comments/', {'video_id': str(v.id), 'content': 'hi'}, format='json')
        self.assertEqual(r.status_code, 404)
        r = c_owner.post('/api/interactions/comments/', {'video_id': str(v.id), 'content': 'ok'}, format='json')
        self.assertEqual(r.status_code, 201)
        Video.objects.filter(id=v.id).update(allow_comments=False)
        r = c_owner.post('/api/interactions/comments/', {'video_id': str(v.id), 'content': 'again'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_unpublished_video_interactions_forbidden(self):
        v = Video.objects.create(
            title='proc', description='', video_file='videos/b.mp4', user=self.owner,
            status='processing', visibility='public', published_at=None
        )
        c_other = self.auth(self.other)
        r = c_other.post('/api/interactions/like/toggle/', {'video_id': str(v.id)}, format='json')
        self.assertEqual(r.status_code, 404)
        r = c_other.post('/api/interactions/history/record/', {'video_id': str(v.id), 'current': 1, 'duration': 10}, format='json')
        self.assertEqual(r.status_code, 404)
        r = self.client.get('/api/interactions/comments/', {'video_id': str(v.id)})
        self.assertEqual(r.status_code, 404)
        r = c_other.post('/api/interactions/comments/', {'video_id': str(v.id), 'content': 'x'}, format='json')
        self.assertEqual(r.status_code, 404)

    def test_avatar_url_absolute_in_videos_list_and_published_filter(self):
        u = self.owner
        u.profile_picture = 'avatars/test.jpg'
        u.save(update_fields=['profile_picture'])
        v_pub = Video.objects.create(title='pub', description='', video_file='videos/p.mp4', user=u, status='published', visibility='public', published_at=timezone.now())
        v_proc = Video.objects.create(title='proc', description='', video_file='videos/q.mp4', user=u, status='processing', visibility='public', published_at=None)
        r = self.client.get('/api/videos/list/')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        items = data.get('results') or []
        ids = {it.get('id') for it in items}
        self.assertIn(str(v_pub.id), ids)
        self.assertNotIn(str(v_proc.id), ids)
        it = next((x for x in items if x.get('id') == str(v_pub.id)), None)
        self.assertIsNotNone(it)
        author = (it or {}).get('author') or {}
        avatar = author.get('avatar_url')
        self.assertIsInstance(avatar, str)
        self.assertTrue(avatar.startswith('http://') or avatar.startswith('https://'))
