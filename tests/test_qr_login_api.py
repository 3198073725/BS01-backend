import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from django.core.cache import cache
from apps.users.models import User

@pytest.fixture
def api_client():
    return APIClient()

@pytest.fixture
def user(db):
    return User.objects.create_user(username='testuser', email='test@example.com', password='password123')

@pytest.mark.django_db
def test_qr_login_flow(api_client, user):
    # 1. Web端创建QR会话
    create_url = reverse('popup-stats').replace('popup/stats/', 'login/qr/create/') # 手动拼接确认路由名
    # 实际路由在 urls.py 中是 login-qr-create
    create_url = reverse('login-qr-create')
    resp = api_client.post(create_url)
    assert resp.status_code == 200
    session = resp.data['session']
    
    # 2. Web端轮询状态 (pending)
    status_url = reverse('login-qr-status')
    resp = api_client.get(f"{status_url}?session={session}")
    assert resp.status_code == 200
    assert resp.data['status'] == 'pending'
    
    # 3. 移动端确认登录
    api_client.force_authenticate(user=user)
    confirm_url = reverse('login-qr-confirm')
    resp = api_client.post(confirm_url, {'session': session})
    assert resp.status_code == 204
    
    # 4. Web端再次轮询状态 (confirmed)
    api_client.force_authenticate(user=None) # 模拟Web端匿名轮询
    resp = api_client.get(f"{status_url}?session={session}")
    assert resp.status_code == 200
    assert resp.data['status'] == 'confirmed'
    assert 'access' in resp.data
    assert 'refresh' in resp.data
    assert resp.data['user']['username'] == 'testuser'
