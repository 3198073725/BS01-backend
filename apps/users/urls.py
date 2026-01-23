"""Users 应用路由模块。

用于组织用户相关 API 路由，例如资料、登录、关注等。
后续可在此导入 views 并定义 path 列表。
"""

from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    # 注册（公开）
    path('register/', views.RegisterView.as_view(), name='register'),
    # 当前登录用户资料（需鉴权）
    path('me/', views.MeView.as_view(), name='me'),
    # 修改密码（需鉴权）
    path('change-password/', views.PasswordChangeView.as_view(), name='change-password'),
    # 公开用户信息（按主键）
    path('<uuid:pk>/', views.UserDetailView.as_view(), name='detail'),
    # 公开用户信息（按用户名）
    path('by-username/<str:username>/', views.UserByUsernameView.as_view(), name='by-username'),
    # 用户搜索（分页）
    path('search/', views.UserSearchView.as_view(), name='search'),
    # 占用校验
    path('check-username/', views.UsernameAvailableView.as_view(), name='check-username'),
    path('check-email/', views.EmailAvailableView.as_view(), name='check-email'),
    # 邮箱验证
    path('verify-email/request/', views.EmailVerificationRequestView.as_view(), name='verify-email-request'),
    path('verify-email/confirm/', views.EmailVerificationConfirmView.as_view(), name='verify-email-confirm'),
    # 重置密码
    path('password-reset/request/', views.PasswordResetRequestView.as_view(), name='password-reset-request'),
    path('password-reset/confirm/', views.PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    # 头像上传
    path('avatar/upload/', views.AvatarUploadView.as_view(), name='avatar-upload'),
    # 用户名改名（需鉴权）
    path('username/change/', views.UsernameChangeView.as_view(), name='username-change'),
    # 邮箱改绑（两步）
    path('email/change/request/', views.EmailChangeRequestView.as_view(), name='email-change-request'),
    path('email/change/confirm/', views.EmailChangeConfirmView.as_view(), name='email-change-confirm'),
    # 头像悬停弹窗数据
    path('popup/stats/', views.UserPopupStatsView.as_view(), name='popup-stats'),
    # 诊断探活
    path('ping/', views.PingView.as_view(), name='ping'),
    # 登录：邮箱验证码
    path('login/send-code/', views.LoginSendCodeView.as_view(), name='login-send-code'),
    path('login/with-code/', views.LoginWithCodeView.as_view(), name='login-with-code'),
    # 登录：扫码
    path('login/qr/create/', views.QrLoginCreateView.as_view(), name='login-qr-create'),
    path('login/qr/status/', views.QrLoginStatusView.as_view(), name='login-qr-status'),
    path('login/qr/confirm/', views.QrLoginConfirmView.as_view(), name='login-qr-confirm'),
    # 联系我们：表单提交到管理员邮箱
    path('contact/submit/', views.ContactSubmitView.as_view(), name='contact-submit'),
]
