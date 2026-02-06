"""Users 视图模块。

用于实现用户相关的 API 视图，例如资料、登录/注册、关注关系等。
可结合 DRF 的 APIView/ViewSet 来定义接口，并在 urls 中进行路由绑定。
"""

from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.contrib.postgres.search import TrigramSimilarity
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions, status, generics
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import ValidationError
from rest_framework.exceptions import AuthenticationFailed
from django.conf import settings
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.template.exceptions import TemplateDoesNotExist
from django.http import HttpResponse
from django.core.cache import cache
from django.core.validators import validate_email as dj_validate_email
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from django.db import IntegrityError, DatabaseError
import os
import mimetypes
from PIL import Image, UnidentifiedImageError
import re
import logging
import secrets
import string
import base64
import time
from io import BytesIO
from datetime import timedelta
from urllib.parse import quote_plus

from .models import User
from apps.interactions.models import Like, Favorite, History
from apps.videos.models import WatchLater, Video
from .serializers import (
    UserPublicSerializer,
    UserMeSerializer,
    RegisterSerializer,
    PasswordChangeSerializer,
)
from .tokens import email_verification_token

logger = logging.getLogger(__name__)
# 在此编写视图，例如：
# from rest_framework.views import APIView
# class ProfileView(APIView):
#     ...


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'register'

    def post(self, request):
        serializer = RegisterSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        data = UserPublicSerializer(user).data
        return Response(data, status=status.HTTP_201_CREATED)


class MeView(APIView):
    def get(self, request):
        data = UserMeSerializer(request.user).data
        return Response(data)

    def patch(self, request):
        serializer = UserMeSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def put(self, request):
        serializer = UserMeSerializer(request.user, data=request.data, partial=False)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class PasswordChangeView(APIView):
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ContactSubmitView(APIView):
    """接收前端联系表单并转发到管理员邮箱。

    - 请求体：{ type, name, email, subject, message }
    - 收件人：settings.CONTACT_EMAIL_TO（若未配置则使用 mediacms@126.com）
    - 频率限制：contact_submit
    - 鉴权：允许匿名提交
    """
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'contact_submit'

    def post(self, request):
        data = request.data or {}
        ctype = str(data.get('type') or '').strip().lower()
        name = str(data.get('name') or '').strip()
        email = str(data.get('email') or '').strip()
        subject = str(data.get('subject') or '').strip()
        message = str(data.get('message') or '').strip()

        # 基本校验
        allowed_types = {'feedback', 'business', 'infringement', 'privacy', 'other'}
        if ctype not in allowed_types:
            ctype = 'other'
        if not subject:
            # 主题可缺省：用正文前 30 字作为主题，或使用固定占位
            subject = (message[:30] + '...') if len(message) > 30 else (message or '联系表单')
        if not message:
            raise ValidationError({'message': '内容不能为空'})
        if len(name) > 60:
            raise ValidationError({'name': '姓名过长'})
        if len(subject) > 120:
            raise ValidationError({'subject': '主题过长'})
        if len(message) > 4000:
            raise ValidationError({'message': '内容过长'})
        try:
            dj_validate_email(email)
        except Exception:
            raise ValidationError({'email': '邮箱格式不正确'})

        # 组织邮件
        site = getattr(settings, 'SITE_URL', '')
        prefix = getattr(settings, 'EMAIL_SUBJECT_PREFIX', '').strip()
        tag = f"[Contact]" if not ctype else f"[Contact][{ctype}]"
        final_subject = f"{prefix}{tag} {subject}".strip()
        # 获取客户端 IP（尽力而为）
        try:
            ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR') or 'unknown').lower()
        except Exception:
            ip = 'unknown'
        user = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
        uid = str(getattr(user, 'id', 'anonymous'))
        now = timezone.now().strftime('%Y-%m-%d %H:%M:%S %Z')
        text_lines = [
            f"提交时间: {now}",
            f"提交类型: {ctype}",
            f"姓名: {name or '(未留)'}",
            f"邮箱: {email}",
            f"用户: {uid}",
            f"IP: {ip}",
            f"来源: {site}",
            "",
            message,
        ]
        text_body = "\n".join(text_lines)

        to_addr = (getattr(settings, 'CONTACT_EMAIL_TO', '') or '').strip()
        if not to_addr:
            try:
                logger.warning("contact_submit_missing_recipient type=%s from=%s ip=%s", ctype, email, ip)
            except Exception:
                pass
            raise ValidationError({'detail': '服务暂不可用：未配置收件邮箱，请稍后再试或直接使用邮件客户端'})
        bcc_list = getattr(settings, 'ADMIN_EMAIL_LIST', []) or []
        try:
            msg = EmailMultiAlternatives(final_subject, text_body, settings.DEFAULT_FROM_EMAIL, [to_addr], bcc=bcc_list, reply_to=[email])
            msg.send(fail_silently=False)
        except Exception:
            logger.exception("contact_submit_send_failed type=%s from=%s ip=%s", ctype, email, ip)
            # 不暴露服务端错误细节
            raise ValidationError({'detail': '发送失败，请稍后再试或改用邮箱联系'})

        return Response(status=status.HTTP_204_NO_CONTENT)


class UserDetailView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        data = UserPublicSerializer(user).data
        return Response(data)


class UserByUsernameView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, username):
        user = get_object_or_404(User, username__iexact=username)
        data = UserPublicSerializer(user).data
        return Response(data)


class UserSearchView(generics.ListAPIView):
    """用户搜索/列表（分页）

    支持查询参数：
    - q: 关键词（匹配 username/nickname）
    - verified: 仅返回认证用户（1/true/yes）
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = UserPublicSerializer

    def get_queryset(self):
        qs = User.objects.all()
        q = (self.request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(nickname__icontains=q))
        verified = (self.request.query_params.get('verified') or '').lower()
        if verified in ('1', 'true', 'yes'):
            qs = qs.filter(is_verified=True)
        order = (self.request.query_params.get('order') or '').lower()
        if order == 'relevance' and q:
            qs = qs.annotate(sim=TrigramSimilarity('username', q) + 0.5 * TrigramSimilarity('nickname', q)).order_by('-sim', '-followers_count', '-date_joined')
        else:
            qs = qs.order_by('-date_joined')
        return qs


class UsernameAvailableView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        username = request.query_params.get('username', '').strip()
        if not username:
            raise ValidationError({'username': '缺少参数 username'})
        if not re.fullmatch(r'^[a-zA-Z0-9_.]+$', username):
            return Response({'available': False, 'reason': '格式不合法'}, status=status.HTTP_200_OK)
        exists = User.objects.filter(username=username).exists()
        return Response({'available': not exists})


class EmailAvailableView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        email = request.query_params.get('email', '').strip()
        if not email:
            raise ValidationError({'email': '缺少参数 email'})
        # 基础邮箱格式校验交给前端或 Django EmailValidator；此处仅检查占用
        exists = User.objects.filter(email=email).exists()
        return Response({'available': not exists})


class EmailVerificationRequestView(APIView):
    """当前用户请求发送邮箱验证邮件"""
    throttle_scope = 'verify_email'

    def post(self, request):
        user: User = request.user
        if user.is_verified:
            return Response({'detail': '已认证，无需重复验证'}, status=status.HTTP_200_OK)
        token = email_verification_token.make_token(user)
        link = f"{settings.SITE_URL}/api/users/verify-email/confirm/?uid={user.id}&token={token}"
        # 选择语言（简单按 Accept-Language 判断），默认中文
        lang = 'en' if str(request.headers.get('Accept-Language', '')).lower().startswith('en') else 'zh'
        subject = 'Verify your email' if lang == 'en' else '邮箱验证'
        # 渲染 HTML 模板（APP_DIRS=True 将在 apps 下查找）
        template = f"users/email_verify_{lang}.html"
        try:
            html = render_to_string(template, {'user': user, 'link': link})
        except TemplateDoesNotExist:
            html = None
        # 纯文本降级
        text = f"{subject}:\n{link}"
        try:
            send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False, html_message=html)
        except Exception as e:
            logger.exception("Send verify email failed for user=%s email=%s", user.id, user.email)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmailVerificationConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        uid = request.query_params.get('uid')
        token = request.query_params.get('token')
        if not uid or not token:
            raise ValidationError({'detail': '缺少参数'})
        user = get_object_or_404(User, pk=uid)
        if email_verification_token.check_token(user, token):
            if not user.is_verified:
                user.is_verified = True
                user.save(update_fields=['is_verified', 'updated_at'])
            return Response({'detail': '邮箱验证成功'})
        raise ValidationError({'detail': '链接无效或已过期'})


class PasswordResetRequestView(APIView):
    """请求重置密码邮件（总是返回 204，避免账户枚举）"""
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'password_reset'

    def post(self, request):
        email = request.data.get('email', '').strip()
        if email:
            # 避免大小写差异导致查不到用户（邮箱匹配不区分大小写）
            user = User.objects.filter(email__iexact=email).first()
            logger.info("password_reset_request: email=%s exists=%s", email, bool(user))
            if user:
                from django.contrib.auth.tokens import default_token_generator
                token = default_token_generator.make_token(user)
                # 优先跳转前端页面；未配置则回退到后端确认页
                if getattr(settings, 'FRONTEND_URL', ''):
                    path = getattr(settings, 'PASSWORD_RESET_FRONTEND_PATH', '/#/reset-password')
                    base = settings.FRONTEND_URL.rstrip('/')
                    link = f"{base}{path}?uid={user.id}&token={token}"
                else:
                    link = f"{settings.SITE_URL}/api/users/password-reset/confirm/?uid={user.id}&token={token}"
                # 选择语言
                lang = 'en' if str(request.headers.get('Accept-Language', '')).lower().startswith('en') else 'zh'
                subject = 'Reset your password' if lang == 'en' else '重置密码'
                template = f"users/password_reset_{lang}.html"
                try:
                    html = render_to_string(template, {'user': user, 'link': link})
                except TemplateDoesNotExist:
                    html = None
                text = f"{subject}:\n{link}"
                try:
                    # 若设置了管理员邮箱列表，则抄送一份，便于验证投递与诊断
                    admins = getattr(settings, 'ADMIN_EMAIL_LIST', []) or []
                    if admins:
                        msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [user.email], bcc=admins)
                        if html:
                            msg.attach_alternative(html, "text/html")
                        msg.send(fail_silently=False)
                    else:
                        send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False, html_message=html)
                    logger.info("password_reset_sent: to=%s uid=%s", user.email, user.id)
                except Exception:
                    logger.exception("Send password reset email failed for email=%s user=%s", user.email, user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        uid = request.data.get('uid')
        token = request.data.get('token')
        new_password = request.data.get('new_password')
        if not uid or not token or not new_password:
            raise ValidationError({'detail': '缺少参数'})
        user = get_object_or_404(User, pk=uid)
        from django.contrib.auth.tokens import default_token_generator
        if not default_token_generator.check_token(user, token):
            raise ValidationError({'detail': '链接无效或已过期'})
        from django.contrib.auth import password_validation
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            password_validation.validate_password(new_password, user=user)
        except DjangoValidationError as e:
            # 将 Django 的校验错误转换为 DRF 格式，交由全局异常处理器统一输出
            raise ValidationError({'new_password': e.messages})
        user.set_password(new_password)
        user.save(update_fields=['password', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get(self, request):
        """提供一个极简 HTML 界面，便于直接在浏览器完成密码重置。

        注意：该页面仅用于辅助测试与临时使用，生产可跳转到前端页面完成重置。
        """
        uid = request.query_params.get('uid')
        token = request.query_params.get('token')
        if not uid or not token:
            return Response({'detail': '缺少参数'}, status=status.HTTP_400_BAD_REQUEST)
        html = f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>重置密码</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,\"PingFang SC\",\"Microsoft YaHei\",sans-serif; background:#f5f7fb; margin:0; padding:24px; }}
    .card {{ max-width: 520px; margin: 0 auto; background:#fff; border-radius:12px; padding:24px; box-shadow:0 1px 3px rgba(0,0,0,.08) }}
    .row {{ display:flex; gap:8px; align-items:center; margin:8px 0; }}
    input {{ flex:1; padding:10px 12px; border:1px solid #d1d5db; border-radius:8px; }}
    button {{ padding:10px 16px; background:#2563eb; color:#fff; border:none; border-radius:8px; cursor:pointer; }}
    .msg {{ margin-top:12px; font-size:14px; }}
    .ok {{ color:#065f46 }} .err {{ color:#991b1b }}
  </style>
  <script>
    async function submitReset() {{
      const pwd = document.getElementById('pwd').value;
      const pwd2 = document.getElementById('pwd2').value;
      const msg = document.getElementById('msg');
      msg.className = 'msg';
      msg.textContent = '';
      if (!pwd || !pwd2) {{ msg.className += ' err'; msg.textContent = '请输入两次新密码'; return; }}
      if (pwd !== pwd2) {{ msg.className += ' err'; msg.textContent = '两次输入不一致'; return; }}
      try {{
        const res = await fetch('/api/users/password-reset/confirm/', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json', 'Accept': 'application/json' }},
          body: JSON.stringify({{ uid: '{uid}', token: '{token}', new_password: pwd }})
        }});
        const isJson = (res.headers.get('Content-Type')||'').includes('application/json');
        const data = isJson ? await res.json() : await res.text();
        if (!res.ok) {{
          msg.className += ' err';
          msg.textContent = (data && data.detail) ? ('失败：' + data.detail) : '请求失败';
        }} else {{
          msg.className += ' ok';
          msg.textContent = '密码已重置成功，您可以关闭本页并返回应用登录。';
        }}
      }} catch (e) {{
        msg.className += ' err';
        msg.textContent = '网络异常：' + e;
      }}
    }}
  </script>
  </head>
<body>
  <div class="card">
    <h2>重置密码</h2>
    <div class="row"><input id="pwd" type="password" placeholder="新密码" /></div>
    <div class="row"><input id="pwd2" type="password" placeholder="确认新密码" /></div>
    <div class="row"><button onclick="submitReset()">提交</button></div>
    <div id="msg" class="msg"></div>
  </div>
</body>
</html>
"""
        return HttpResponse(html, content_type='text/html')


class UsernameChangeView(APIView):
    def post(self, request):
        user: User = request.user
        new_username = str(request.data.get('username') or '').strip()
        if not new_username:
            raise ValidationError({'username': '缺少参数 username'})
        if not re.fullmatch(r'^[a-zA-Z0-9_.]+$', new_username):
            raise ValidationError({'username': '用户名只能包含字母、数字、下划线和点号'})
        if new_username.lower() in {u.lower() for u in (settings.RESERVED_USERNAMES or [])}:
            raise ValidationError({'username': '该用户名为保留词，无法使用'})
        if new_username.lower() == user.username.lower():
            return Response({'username': user.username})
        try:
            cooldown_days = int(os.getenv('USERNAME_CHANGE_COOLDOWN_DAYS', '30'))
        except Exception:
            cooldown_days = 30
        cooldown_sec = max(0, cooldown_days * 24 * 3600)
        key = f"username_change:last:{user.id}"
        last_ts = cache.get(key)
        now_ts = int(time.time())
        if isinstance(last_ts, (int, float)) and last_ts is not None:
            elapsed = max(0, now_ts - int(last_ts))
            if elapsed < cooldown_sec:
                left = max(1, cooldown_sec - elapsed)
                return Response({'success': False, 'code': 'cooling_down', 'detail': '改名过于频繁，请稍后再试', 'errors': None, 'cool_down_seconds': left}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        if User.objects.filter(username__iexact=new_username).exclude(pk=user.pk).exists():
            raise ValidationError({'username': '该用户名已被占用'})
        old = user.username
        user.username = new_username
        user.save(update_fields=['username', 'updated_at'])
        if cooldown_sec > 0:
            cache.set(key, now_ts, timeout=cooldown_sec)
        try:
            from apps.content.models import AuditLog
            AuditLog.objects.create(actor=user, verb='username_change', target_type='user', target_id=user.id, meta={'old': old, 'new': new_username})
        except Exception:
            pass
        return Response({'username': user.username})


class EmailChangeRequestView(APIView):
    def post(self, request):
        user: User = request.user
        new_email = str(request.data.get('new_email') or '').strip()
        if not new_email:
            raise ValidationError({'new_email': '缺少参数 new_email'})
        try:
            dj_validate_email(new_email)
        except Exception:
            raise ValidationError({'new_email': '邮箱格式不正确'})
        if user.email and user.email.lower() == new_email.lower():
            return Response(status=status.HTTP_204_NO_CONTENT)
        if '@' in new_email:
            domain = new_email.rsplit('@', 1)[-1].lower()
            deny = {d.lower() for d in (settings.EMAIL_DOMAIN_BLACKLIST or [])} | {d.lower() for d in (settings.DISPOSABLE_DOMAINS or [])}
            if domain in deny:
                raise ValidationError({'new_email': '该邮箱域名不被允许'})
            if settings.EMAIL_CHECK_MX:
                try:
                    import dns.resolver  # type: ignore
                    try:
                        _answers = dns.resolver.resolve(domain, 'MX')  # noqa: F841
                    except Exception:
                        raise ValidationError({'new_email': '该邮箱域名无有效邮件服务器(MX)'})
                except Exception:
                    pass
        if User.objects.filter(email__iexact=new_email).exists():
            raise ValidationError({'new_email': '该邮箱已被占用'})
        token = secrets.token_urlsafe(24)
        try:
            max_age = int(os.getenv('EMAIL_CHANGE_TOKEN_MAX_AGE', '86400'))
        except Exception:
            max_age = 86400
        cache.set(f"email_change:{token}", {'uid': str(user.id), 'email': new_email.lower()}, timeout=max_age)
        if getattr(settings, 'FRONTEND_URL', ''):
            base = settings.FRONTEND_URL.rstrip('/')
            link = f"{base}/#/email-change-confirm?token={token}"
        else:
            link = f"{settings.SITE_URL}/api/users/email/change/confirm/?token={token}"
        subject = '[BS01] 邮箱改绑确认'
        text = f"您正在将账户邮箱更改为：{new_email}。点击链接确认：{link}（24小时内有效）。如非本人操作请忽略。"
        try:
            send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [new_email], fail_silently=False)
        except Exception:
            logger.exception("send_email_change_confirm_failed user=%s new=%s", user.id, new_email)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmailChangeConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request):
        token = request.data.get('token') or request.query_params.get('token')
        if not token:
            raise ValidationError({'detail': '缺少参数 token'})
        data = cache.get(f"email_change:{token}")
        if not data or 'uid' not in data or 'email' not in data:
            raise ValidationError({'detail': '链接无效或已过期'})
        user = get_object_or_404(User, pk=data['uid'])
        new_email = data['email'].strip().lower()
        if User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
            raise ValidationError({'detail': '该邮箱已被占用'})
        user.email = new_email
        try:
            user.is_verified = False
            user.save(update_fields=['email', 'is_verified', 'updated_at'])
        except Exception:
            user.save(update_fields=['email', 'updated_at'])
        cache.delete(f"email_change:{token}")
        return Response({'detail': '邮箱已更新'})


class UserPopupStatsView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = []
    throttle_scope = 'popup_stats'
    """头像悬停弹窗所需的汇总数据。

    返回字段：
    - display_name, nickname, username, profile_picture
    - followers_count, following_count
    - likes_count, favorites_count, watch_later_count, my_works_count

    支持 query: force=1 强制刷新，默认使用短缓存。
    缓存时长由环境变量 POPUP_STATS_CACHE_SECONDS 控制，默认 120 秒。
    """
    def get(self, request):
        try:
            logger.info("popup_stats_enter")
        except Exception:
            pass
        # Diagnostic: allow short-circuit to verify routing/rendering
        try:
            d = str(request.query_params.get('diag', '')).lower()
            if d in ('1','true','yes'):
                return Response({'diag': True})
        except Exception:
            pass
        user: User = request.user
        if not getattr(user, 'id', None):
            data = {
                'id': '',
                'username': None,
                'nickname': None,
                'display_name': None,
                'profile_picture': None,
                'followers_count': 0,
                'following_count': 0,
                'likes_count': 0,
                'favorites_count': 0,
                'watch_later_count': 0,
                'my_works_count': 0,
            }
            return Response(data)
        try:
            ttl = int(os.getenv('POPUP_STATS_CACHE_SECONDS', '120'))
        except Exception:
            ttl = 120
        force = str(request.query_params.get('force', '')).lower() in ('1', 'true', 'yes')
        key = f"user_popup:summary:{user.id}"
        def default_data():
            return {
                'id': str(user.id),
                'username': user.username,
                'nickname': getattr(user, 'nickname', None),
                'display_name': getattr(user, 'display_name', None),
                'profile_picture': getattr(user, 'profile_picture', None),
                'followers_count': getattr(user, 'followers_count', 0),
                'following_count': getattr(user, 'following_count', 0),
                'likes_count': 0,
                'favorites_count': 0,
                'watch_later_count': 0,
                'my_works_count': 0,
            }
        try:
            if not force:
                cached = cache.get(key)
                if cached:
                    resp = Response(cached)
                    resp['Cache-Control'] = f"private, max-age={ttl}"
                    return resp
            # 统计
            def safe_count(qs):
                try:
                    return qs.count()
                except (DatabaseError, Exception):  # 容错：库未迁移或临时异常时不阻断
                    return 0
            likes_count = safe_count(Like.objects.filter(user=user))
            favorites_count = safe_count(Favorite.objects.filter(user=user))
            watch_later_count = safe_count(WatchLater.objects.filter(user=user))
            my_works_count = safe_count(Video.objects.filter(user=user))
            data = default_data()
            data.update({
                'likes_count': likes_count,
                'favorites_count': favorites_count,
                'watch_later_count': watch_later_count,
                'my_works_count': my_works_count,
            })
            cache.set(key, data, timeout=ttl)
            resp = Response(data)
            resp['Cache-Control'] = f"private, max-age={ttl}"
            return resp
        except Exception:
            logger.exception("user_popup_stats_failed user=%s", getattr(user, 'id', None))
            data = default_data()
            # 异常情况下也返回默认数据，避免前端因 500 中断
            try:
                cache.set(key, data, timeout=ttl)
            except Exception:
                pass
            resp = Response(data)
            resp['Cache-Control'] = f"private, max-age={ttl}"
            return resp


class PingView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request):
        return Response({'ok': True})

class AvatarUploadView(APIView):
    """上传头像，保存到 MEDIA_ROOT/avatars/<uuid>.<ext> 并更新 profile_picture（相对路径）"""
    parser_classes = [MultiPartParser, FormParser]
    # 权限默认 IsAuthenticated
    throttle_scope = 'avatar_upload'

    def post(self, request):
        file = request.FILES.get('file') or request.FILES.get('avatar')
        if not file:
            raise ValidationError({'file': '未收到文件'})
        if file.size > settings.AVATAR_MAX_SIZE_BYTES:
            raise ValidationError({'file': '文件过大'})
        content_type = file.content_type or mimetypes.guess_type(file.name)[0] or ''
        allowed_types = {'image/jpeg', 'image/jpg', 'image/pjpeg', 'image/png'}
        if content_type not in allowed_types:
            raise ValidationError({'file': '仅支持 JPEG/PNG'})
        # 读取图像
        try:
            img = Image.open(file)
        except UnidentifiedImageError:
            raise ValidationError({'file': '非法图片文件'})
        mw, mh = img.size
        # 像素安全上限（防止恶意超大图片导致内存占用）
        max_pixels = int(getattr(settings, 'AVATAR_MAX_PIXELS', 25000000))  # 25MP 默认
        if mw * mh > max_pixels:
            raise ValidationError({'file': '图片像素过大'})
        # 对非常规模式进行标准化（保留 alpha 的 PNG 在保存 PNG 时不受影响）
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGB')
        # 解析可选裁剪参数（x,y,w,h）；缺省则中心裁剪为正方形
        try:
            x = int(request.data.get('x')) if 'x' in request.data else None
            y = int(request.data.get('y')) if 'y' in request.data else None
            w = int(request.data.get('w')) if 'w' in request.data else None
            h = int(request.data.get('h')) if 'h' in request.data else None
        except (TypeError, ValueError):
            x = y = w = h = None
        if None in (x, y, w, h):
            # 中心裁剪为最短边的正方形
            side = min(mw, mh)
            x = (mw - side) // 2
            y = (mh - side) // 2
            w = h = side
        # 规范化裁剪框，确保落在图片内部
        left = max(0, x)
        top = max(0, y)
        right = min(mw, x + w)
        bottom = min(mh, y + h)
        if right <= left or bottom <= top:
            raise ValidationError({'box': '裁剪区域无效'})
        box = (left, top, right, bottom)
        img_cropped = img.crop(box)
        # 生成标准头像（例如 512x512）与缩略图（256x256）
        avatar_size = 512
        thumb_size = 256
        avatar_img = img_cropped.resize((avatar_size, avatar_size), Image.LANCZOS)
        thumb_img = img_cropped.resize((thumb_size, thumb_size), Image.LANCZOS)
        # 输出路径
        avatars_dir = os.path.join(settings.MEDIA_ROOT, 'avatars')
        os.makedirs(avatars_dir, exist_ok=True)
        # 统一 JPEG 扩展名映射
        jpeg_types = {'image/jpeg', 'image/jpg', 'image/pjpeg'}
        ext = '.jpg' if content_type in jpeg_types else '.png'
        base = f"{request.user.id}"
        avatar_filename = f"{base}{ext}"
        thumb_filename = f"{base}_thumb{ext}"
        avatar_path = os.path.join(avatars_dir, avatar_filename)
        thumb_path = os.path.join(avatars_dir, thumb_filename)
        # 保存文件
        save_kwargs = {'quality': 90} if ext == '.jpg' else {}
        # JPEG 不支持 alpha，确保转换为 RGB
        if ext == '.jpg':
            if avatar_img.mode != 'RGB':
                avatar_img = avatar_img.convert('RGB')
            if thumb_img.mode != 'RGB':
                thumb_img = thumb_img.convert('RGB')
        avatar_img.save(avatar_path, format='JPEG' if ext == '.jpg' else 'PNG', **save_kwargs)
        thumb_img.save(thumb_path, format='JPEG' if ext == '.jpg' else 'PNG', **save_kwargs)
        # 更新资料并返回两个路径（相对路径）
        rel_avatar = f"avatars/{avatar_filename}"
        rel_thumb = f"avatars/{thumb_filename}"
        request.user.profile_picture = rel_avatar[:100]
        try:
            request.user.profile_picture_f = rel_avatar[:200]
            request.user.save(update_fields=['profile_picture', 'profile_picture_f', 'updated_at'])
        except Exception:
            request.user.save(update_fields=['profile_picture', 'updated_at'])
        return Response({'profile_picture': rel_avatar, 'profile_picture_thumb': rel_thumb})


class LoginSendCodeView(APIView):
    """发送邮箱验证码（用于登录/注册）。

    - 请求体：{ email }
    - 总是返回 204（避免暴露邮箱是否存在）
    """
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'login_code'

    def post(self, request):
        email = str(request.data.get('email', '')).strip()
        if not email:
            raise ValidationError({'email': '缺少邮箱'})
        # 基础格式校验
        try:
            dj_validate_email(email)
        except Exception:
            raise ValidationError({'email': '邮箱格式不正确'})
        # 域名黑名单与一次性邮箱检查
        if '@' in email:
            domain = email.rsplit('@', 1)[-1].lower()
            deny = {d.lower() for d in (settings.EMAIL_DOMAIN_BLACKLIST or [])} | {d.lower() for d in (settings.DISPOSABLE_DOMAINS or [])}
            if domain in deny:
                raise ValidationError({'email': '该邮箱域名不被允许'})
            # 可选 MX 检查
            if settings.EMAIL_CHECK_MX:
                try:
                    import dns.resolver  # type: ignore
                    try:
                        _answers = dns.resolver.resolve(domain, 'MX')  # noqa: F841
                    except Exception:
                        raise ValidationError({'email': '该邮箱域名无有效邮件服务器(MX)'})
                except Exception:
                    # 未安装依赖或网络解析失败时不阻断
                    pass
        # 频率限制：同邮箱/同 IP 冷却与日上限
        try:
            ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR') or 'unknown').lower()
        except Exception:
            ip = 'unknown'
        now = timezone.now()
        today = now.strftime('%Y%m%d')
        min_interval = int(getattr(settings, 'LOGIN_CODE_MIN_INTERVAL_SECONDS', 60))
        max_email_daily = int(getattr(settings, 'LOGIN_CODE_DAILY_LIMIT_EMAIL', 20))
        max_ip_daily = int(getattr(settings, 'LOGIN_CODE_DAILY_LIMIT_IP', 200))
        # 上次发送时间（按邮箱）
        last_key = f"login_code:last_email:{email.lower()}"
        last_ts = cache.get(last_key)
        if last_ts is not None:
            try:
                elapsed = int(time.time()) - int(last_ts)
            except Exception:
                elapsed = min_interval
            if elapsed < min_interval:
                cool = max(1, min_interval - elapsed)
                return Response({'success': False, 'code': 'cooling_down', 'detail': '发送过于频繁，请稍后再试', 'errors': None, 'cool_down_seconds': cool}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        # 当日计数（邮箱与 IP）
        # 计算剩余到当天结束的秒数
        try:
            end_of_day = (now + timezone.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        except Exception:
            from datetime import datetime, timedelta
            end_of_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        ttl_day = max(60, int((end_of_day - now).total_seconds()))
        email_cnt_key = f"login_code:cnt_email:{email.lower()}:{today}"
        ip_cnt_key = f"login_code:cnt_ip:{ip}:{today}"
        email_cnt = int(cache.get(email_cnt_key) or 0)
        ip_cnt = int(cache.get(ip_cnt_key) or 0)
        if email_cnt >= max_email_daily or ip_cnt >= max_ip_daily:
            return Response({'success': False, 'code': 'daily_limit_reached', 'detail': '今日发送次数已达上限', 'errors': None, 'cool_down_seconds': ttl_day}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        # 生成 6 位数字验证码，有效期 5 分钟
        code = ''.join(secrets.choice('0123456789') for _ in range(6))
        cache_key = f"login_code:{email.lower()}"
        cache.set(cache_key, code, timeout=300)
        saved_check = cache.get(cache_key)
        if saved_check != code:
            cache.set(cache_key, code, timeout=300)
            saved_check = cache.get(cache_key)
            if saved_check != code:
                logger.error("login_code_cache_set_failed email=%s", email)
        # 发送邮件：优先模板（HTML + 文本），无模板时回退纯文本
        subject = '登录验证码'
        text = f"您的登录验证码是：{code}（5分钟内有效）。如非本人操作请忽略。"
        try:
            context = {'code': code, 'minutes': 5, 'site_url': settings.SITE_URL, 'email': email}
            try:
                html_body = render_to_string('emails/login_code.html', context)
            except TemplateDoesNotExist:
                html_body = None
            try:
                text_body = render_to_string('emails/login_code.txt', context)
            except TemplateDoesNotExist:
                text_body = text
            if html_body:
                msg = EmailMultiAlternatives(subject, text_body or text, settings.DEFAULT_FROM_EMAIL, [email])
                msg.attach_alternative(html_body, 'text/html')
                msg.send()
            else:
                send_mail(subject, text_body or text, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
        except Exception:
            logger.exception("send_login_code_failed email=%s", email)
        # 写入冷却与计数
        cache.set(last_key, int(time.time()), timeout=min_interval)
        cache.set(email_cnt_key, email_cnt + 1, timeout=ttl_day)
        cache.set(ip_cnt_key, ip_cnt + 1, timeout=ttl_day)
        resp = Response(status=status.HTTP_204_NO_CONTENT)
        try:
            debug_flag = (getattr(settings, 'DEBUG', False) is True) and (str(os.getenv('ECHO_LOGIN_CODE', 'false')).lower() in ('true','1','yes'))
        except Exception:
            debug_flag = False
        if debug_flag:
            # 仅用于开发调试，生产请关闭
            resp['X-Debug-Login-Code'] = code
        return resp


class LoginWithCodeView(APIView):
    """使用邮箱验证码登录/注册。

    - 请求体：{ email, code }
    - 返回：{ access, refresh }
    """
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'login_code'

    def post(self, request):
        email = str(request.data.get('email', '')).strip()
        code = str(request.data.get('code', '')).strip()
        if not email or not code:
            raise ValidationError({'code': 'missing_params', 'detail': '缺少参数'})

        # 失败冷却与锁定（按邮箱/IP）
        try:
            ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR') or 'unknown').lower()
        except Exception:
            ip = 'unknown'
        email_l = email.lower()
        window_sec = int(getattr(settings, 'LOGIN_CODE_LOGIN_FAIL_WINDOW_SECONDS', 600))
        max_email = int(getattr(settings, 'LOGIN_CODE_LOGIN_FAIL_MAX_TRIES_EMAIL', 5))
        max_ip = int(getattr(settings, 'LOGIN_CODE_LOGIN_FAIL_MAX_TRIES_IP', 50))
        cooldown = int(getattr(settings, 'LOGIN_CODE_LOGIN_FAIL_COOLDOWN_SECONDS', 300))

        lock_e_key = f"code_login:lock_email:{email_l}"
        lock_ip_key = f"code_login:lock_ip:{ip}"
        now_ts = int(time.time())
        for lk in (lock_e_key, lock_ip_key):
            lock_val = cache.get(lk)
            if lock_val:
                try:
                    seconds_left = max(1, int(lock_val) - now_ts)
                except Exception:
                    seconds_left = cooldown
                return Response({'success': False, 'code': 'cooling_down', 'detail': '尝试过于频繁，请稍后再试', 'errors': None, 'cool_down_seconds': seconds_left}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        cache_key = f"login_code:{email_l}"
        saved = cache.get(cache_key)
        if not saved:
            # 记录失败并触发可能的锁定
            e_cnt_key = f"code_login:fail_email:{email_l}"
            ip_cnt_key = f"code_login:fail_ip:{ip}"
            e_cnt = int(cache.get(e_cnt_key) or 0) + 1
            ip_cnt = int(cache.get(ip_cnt_key) or 0) + 1
            cache.set(e_cnt_key, e_cnt, timeout=window_sec)
            cache.set(ip_cnt_key, ip_cnt, timeout=window_sec)
            if e_cnt >= max_email:
                cache.set(lock_e_key, now_ts + cooldown, timeout=cooldown)
            if ip_cnt >= max_ip:
                cache.set(lock_ip_key, now_ts + cooldown, timeout=cooldown)
            return Response({'success': False, 'code': 'code_expired', 'detail': '验证码已过期', 'errors': None}, status=status.HTTP_400_BAD_REQUEST)
        if saved != code:
            # 记录失败并触发可能的锁定
            e_cnt_key = f"code_login:fail_email:{email_l}"
            ip_cnt_key = f"code_login:fail_ip:{ip}"
            e_cnt = int(cache.get(e_cnt_key) or 0) + 1
            ip_cnt = int(cache.get(ip_cnt_key) or 0) + 1
            cache.set(e_cnt_key, e_cnt, timeout=window_sec)
            cache.set(ip_cnt_key, ip_cnt, timeout=window_sec)
            if e_cnt >= max_email:
                cache.set(lock_e_key, now_ts + cooldown, timeout=cooldown)
            if ip_cnt >= max_ip:
                cache.set(lock_ip_key, now_ts + cooldown, timeout=cooldown)
            return Response({'success': False, 'code': 'invalid_code', 'detail': '验证码错误', 'errors': None}, status=status.HTTP_400_BAD_REQUEST)
        # 一次性使用
        cache.delete(cache_key)
        # 找到或创建用户
        user = User.objects.filter(email__iexact=email_l).first()
        if not user:
            # 生成唯一用户名：基于邮箱前缀清洗 + 随机后缀
            base = email_l.split('@', 1)[0]
            base = re.sub(r'[^a-zA-Z0-9_.]', '.', base)[:16] or 'user'
            for _ in range(20):
                suffix = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
                uname = f"{base}{suffix}"
                try:
                    user = User.objects.create_user(username=uname, email=email_l)
                    break
                except IntegrityError:
                    # 可能是并发导致 email 已被其他请求创建；重查一次
                    existing = User.objects.filter(email__iexact=email_l).first()
                    if existing:
                        user = existing
                        break
                    user = None
                    continue
            if not user:
                for _ in range(100):
                    try:
                        uname = f"user{secrets.randbelow(1_000_000)}"
                        user = User.objects.create_user(username=uname, email=email_l)
                        break
                    except IntegrityError:
                        existing = User.objects.filter(email__iexact=email_l).first()
                        if existing:
                            user = existing
                            break
                        user = None
                        continue
                if not user:
                    return Response({'success': False, 'code': 'conflict', 'detail': '生成用户失败，请重试'}, status=status.HTTP_409_CONFLICT)
        # 成功：清空失败计数并发放 JWT
        cache.delete(f"code_login:fail_email:{email_l}")
        cache.delete(f"code_login:fail_ip:{ip}")
        refresh = RefreshToken.for_user(user)
        return Response({'access': str(refresh.access_token), 'refresh': str(refresh)})


class QrLoginCreateView(APIView):
    """创建扫码登录会话并返回二维码地址。

    - 返回：{ session, qr_url }
    """
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'qr_login_create'

    def post(self, request):
        session = secrets.token_urlsafe(16)
        try:
            ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR') or 'unknown').lower()
        except Exception:
            ip = 'unknown'
        ua = (request.META.get('HTTP_USER_AGENT') or '')[:200]
        cache.set(f"qr_login:{session}", {'status': 'pending', 'ip': ip, 'ua': ua}, timeout=300)
        # 二维码内容为确认链接（移动端打开后请求确认接口）
        confirm_url = f"{settings.SITE_URL}/api/users/login/qr/confirm/?session={session}"
        # 优先在后端生成二维码图片（base64 data URI）；若缺少依赖则回退到占位服务
        try:
            import qrcode  # 可选依赖
            img = qrcode.make(confirm_url)
            buf = BytesIO()
            img.save(buf, format='PNG')
            b64 = base64.b64encode(buf.getvalue()).decode('ascii')
            qr_image = f"data:image/png;base64,{b64}"
            return Response({'session': session, 'qr_image': qr_image})
        except Exception:
            logger.exception("qrcode_generate_failed, fallback to external url")
            encoded = quote_plus(confirm_url)
            qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=248x248&data={encoded}"
            return Response({'session': session, 'qr_url': qr_url})


class QrLoginStatusView(APIView):
    """查询扫码登录会话状态。

    - 查询参数：session
    - 返回：{ status: 'pending'|'confirmed', access?, refresh? }
    """
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'qr_login_status'

    def get(self, request):
        session = request.query_params.get('session', '')
        if not session:
            raise ValidationError({'detail': '缺少参数'})
        key = f"qr_login:{session}"
        data = cache.get(key)
        if not data:
            return Response({'status': 'pending'})
        # 同源校验：若创建时绑定了 ip/ua，仅允许相同来源获取已确认的令牌
        try:
            ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR') or 'unknown').lower()
        except Exception:
            ip = 'unknown'
        ua = (request.META.get('HTTP_USER_AGENT') or '')[:200]
        b_ip = data.get('ip')
        b_ua = data.get('ua')
        if b_ip and b_ua and (ip != b_ip or ua != b_ua):
            return Response({'status': 'pending'})
        if data.get('status') == 'confirmed':
            # 一次性消费，避免重复获取令牌
            cache.delete(key)
            return Response({'status': 'confirmed', 'access': data.get('access'), 'refresh': data.get('refresh')})
        return Response({'status': 'pending'})


class QrLoginConfirmView(APIView):
    """移动端确认扫码登录。

    需要用户已登录（携带 JWT）。确认后在会话中写入 JWT，供 Web 端轮询获取。
    - 请求：POST/GET 均可，参数 session
    - 返回：204
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'qr_login_confirm'

    def post(self, request):
        session = request.data.get('session') or request.query_params.get('session')
        if not session:
            raise ValidationError({'detail': '缺少参数 session'})
        data = cache.get(f"qr_login:{session}")
        if not data:
            raise ValidationError({'detail': '会话不存在或已过期'})
        user: User = request.user
        refresh = RefreshToken.for_user(user)
        cache.set(f"qr_login:{session}", {'status': 'confirmed', 'access': str(refresh.access_token), 'refresh': str(refresh)}, timeout=60)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get(self, request):
        # 允许 GET 方式（便于简单扫码确认），逻辑同 POST
        return self.post(request)


class TokenObtainPairViewWithCooldown(TokenObtainPairView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'login_password'

    def post(self, request, *args, **kwargs):
        uname = str(request.data.get('username', '') or '').strip()
        try:
            ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR') or 'unknown').lower()
        except Exception:
            ip = 'unknown'

        window_sec = int(getattr(settings, 'LOGIN_PASSWORD_FAIL_WINDOW_SECONDS', 600))
        max_u = int(getattr(settings, 'LOGIN_PASSWORD_FAIL_MAX_TRIES_USERNAME', 5))
        max_ip = int(getattr(settings, 'LOGIN_PASSWORD_FAIL_MAX_TRIES_IP', 50))
        cooldown = int(getattr(settings, 'LOGIN_PASSWORD_FAIL_COOLDOWN_SECONDS', 300))

        u_lock_key = f"login_pwd:lock_u:{uname.lower()}"
        ip_lock_key = f"login_pwd:lock_ip:{ip}"
        u_lock_val = cache.get(u_lock_key)
        ip_lock_val = cache.get(ip_lock_key)
        now_ts = int(time.time())
        if u_lock_val:
            try:
                seconds_left = max(1, int(u_lock_val) - now_ts)
            except Exception:
                seconds_left = cooldown
            return Response({'success': False, 'code': 'cooling_down', 'detail': '尝试过于频繁，请稍后再试', 'errors': None, 'cool_down_seconds': seconds_left}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        if ip_lock_val:
            try:
                seconds_left = max(1, int(ip_lock_val) - now_ts)
            except Exception:
                seconds_left = cooldown
            return Response({'success': False, 'code': 'cooling_down', 'detail': '尝试过于频繁，请稍后再试', 'errors': None, 'cool_down_seconds': seconds_left}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            self._record_failure(uname, ip, window_sec, max_u, max_ip, cooldown)
            raise InvalidToken(e.args[0])
        except AuthenticationFailed as e:
            self._record_failure(uname, ip, window_sec, max_u, max_ip, cooldown)
            raise e
        except ValidationError as e:
            self._record_failure(uname, ip, window_sec, max_u, max_ip, cooldown)
            raise e

        data = serializer.validated_data
        self._clear_failure(uname, ip)
        return Response(data, status=status.HTTP_200_OK)

    def _record_failure(self, uname, ip, window_sec, max_u, max_ip, cooldown):
        u_fail_key = f"login_pwd:fail_u:{uname.lower()}"
        ip_fail_key = f"login_pwd:fail_ip:{ip}"
        u_cnt = int(cache.get(u_fail_key) or 0) + 1
        ip_cnt = int(cache.get(ip_fail_key) or 0) + 1
        cache.set(u_fail_key, u_cnt, timeout=window_sec)
        cache.set(ip_fail_key, ip_cnt, timeout=window_sec)
        if u_cnt >= max_u:
            cache.set(f"login_pwd:lock_u:{uname.lower()}", int(time.time()) + cooldown, timeout=cooldown)
        if ip_cnt >= max_ip:
            cache.set(f"login_pwd:lock_ip:{ip}", int(time.time()) + cooldown, timeout=cooldown)

    def _clear_failure(self, uname, ip):
        cache.delete(f"login_pwd:fail_u:{uname.lower()}")
        cache.delete(f"login_pwd:fail_ip:{ip}")


class TokenRefreshViewWithRevoke(TokenRefreshView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        # Deny refresh if the refresh token was issued before force-logout cutoff
        try:
            raw = request.data.get('refresh') if hasattr(request, 'data') else None
        except Exception:
            raw = None
        if raw:
            try:
                r = RefreshToken(raw)
                uid = str(r.payload.get('user_id') or r.payload.get('user') or '')
                iat = int(r.payload.get('iat') or 0)
                if uid:
                    key = f"logout_after:{uid}"
                    val = cache.get(key)
                    if val:
                        try:
                            cutoff = int(val)
                        except Exception:
                            cutoff = 0
                        if iat and iat < cutoff:
                            raise AuthenticationFailed('凭证已失效，请重新登录')
            except Exception:
                # Let parent serializer handle invalid token/format
                pass
        return super().post(request, *args, **kwargs)
