from django.contrib.auth import password_validation
from rest_framework import serializers
import re
from django.utils import timezone

from .models import User
from django.conf import settings
from django.db import IntegrityError


class UserPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            'id', 'username', 'nickname', 'display_name', 'profile_picture', 'bio',
            'is_verified', 'is_creator', 'privacy_mode',
            'followers_count', 'following_count', 'video_count',
            'total_likes_received', 'total_views_received',
        )
        read_only_fields = fields


class UserFollowListSerializer(UserPublicSerializer):
    is_following = serializers.SerializerMethodField()
    is_mutual = serializers.SerializerMethodField()

    class Meta(UserPublicSerializer.Meta):
        fields = UserPublicSerializer.Meta.fields + (
            'is_following', 'is_mutual',
        )

    def get_is_following(self, obj: User) -> bool:
        s = self.context.get('following_id_set') or set()
        try:
            return str(obj.id) in s or obj.id in s
        except Exception:
            return False

    def get_is_mutual(self, obj: User) -> bool:
        my_following = self.context.get('following_id_set') or set()
        fans_of_me = self.context.get('followers_of_me_id_set') or set()
        try:
            sid = str(obj.id)
            in_my_following = sid in my_following or obj.id in my_following
            in_fans = sid in fans_of_me or obj.id in fans_of_me
            return bool(in_my_following and in_fans)
        except Exception:
            return False

class UserMeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        # 仅允许用户更新公开资料相关字段
        fields = (
            'id', 'username', 'email', 'nickname', 'profile_picture', 'bio',
            'gender', 'birth_date', 'location', 'website', 'phone_number',
            'privacy_mode',
            'is_verified', 'is_creator',
            'followers_count', 'following_count', 'video_count',
            'total_likes_received', 'total_views_received',
            'date_joined', 'last_active', 'updated_at',
        )
        read_only_fields = (
            'id', 'username', 'email', 'is_verified', 'is_creator',
            'followers_count', 'following_count', 'video_count',
            'total_likes_received', 'total_views_received',
            'date_joined', 'last_active', 'updated_at',
        )

    def to_internal_value(self, data):
        # 容错：将空字符串或占位符转为空值，避免 URL/日期等字段在校验阶段报错
        try:
            d = dict(data)
        except Exception:
            # 某些情况下 data 可能是 QueryDict，直接读取 get
            d = {k: data.get(k) for k in data.keys()} if hasattr(data, 'keys') else {}

        try:
            ws = d.get('website', None)
            if isinstance(ws, str):
                w = ws.strip()
                if not w or w in ('http://', 'https://'):
                    d['website'] = None
        except Exception:
            pass

        try:
            bd = d.get('birth_date', None)
            if bd in ('', None):
                d['birth_date'] = None
        except Exception:
            pass

        try:
            ph = d.get('phone_number', None)
            if isinstance(ph, str) and not ph.strip():
                d['phone_number'] = None
        except Exception:
            pass

        return super().to_internal_value(d)

    def validate_nickname(self, v):
        v = (v or '').strip()
        if len(v) > 64:
            raise serializers.ValidationError('昵称过长')
        return v

    def validate_birth_date(self, v):
        if v is None:
            return v
        today = timezone.now().date()
        if v > today:
            raise serializers.ValidationError('出生日期不能晚于今天')
        min_age = 13
        age = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if age < min_age:
            raise serializers.ValidationError('未满足最小年龄要求')
        return v

    def validate_phone_number(self, v):
        v = (v or '').strip()
        if not v:
            return v
        if not re.fullmatch(r'^\+?\d{6,20}$', v):
            raise serializers.ValidationError('手机号格式不正确')
        qs = User.objects.filter(phone_number=v)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('该手机号已被占用')
        return v

    def validate_location(self, v):
        v = (v or '').strip()
        if len(v) > 100:
            raise serializers.ValidationError('所在地过长')
        return v


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    captcha = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'password', 'captcha')
        read_only_fields = ('id',)

    def validate_password(self, value):
        password_validation.validate_password(password=value)
        return value

    def validate_username(self, value: str):
        v = value.strip()
        if v.lower() in {u.lower() for u in settings.RESERVED_USERNAMES}:
            raise serializers.ValidationError('该用户名为保留词，无法注册')
        if User.objects.filter(username__iexact=v).exists():
            raise serializers.ValidationError('该用户名已被占用')
        return v

    def validate_email(self, value: str):
        v = value.strip()
        if '@' in v:
            domain = v.rsplit('@', 1)[-1].lower()
            # 黑名单与一次性域名检查
            deny = {d.lower() for d in (settings.EMAIL_DOMAIN_BLACKLIST or [])} | {d.lower() for d in (settings.DISPOSABLE_DOMAINS or [])}
            if domain in deny:
                raise serializers.ValidationError('该邮箱域名不被允许注册')
            # 可选：检查 MX 记录（需要 dnspython）
            if settings.EMAIL_CHECK_MX:
                try:
                    import dns.resolver  # type: ignore
                    try:
                        # 查询 MX 记录，不抛异常表示存在
                        answers = dns.resolver.resolve(domain, 'MX')  # noqa: F841
                    except Exception:
                        raise serializers.ValidationError('该邮箱域名无有效邮件服务器(MX)')
                except Exception:
                    # 未安装依赖或解析失败时不阻断注册，仅作弱校验
                    pass
        if User.objects.filter(email__iexact=v).exists():
            raise serializers.ValidationError('该邮箱已被占用')
        return v

    def validate(self, attrs):
        if settings.REGISTRATION_REQUIRE_CAPTCHA:
            captcha = self.initial_data.get('captcha', '')
            if not captcha:
                raise serializers.ValidationError({'captcha': '缺少验证码'})
        return attrs

    def create(self, validated_data):
        validated_data.pop('captcha', None)
        password = validated_data.pop('password')
        email = (validated_data.get('email') or '').strip().lower()
        validated_data['email'] = email
        try:
            user = User.objects.create_user(**validated_data, password=password)
        except IntegrityError:
            raise serializers.ValidationError({'detail': '用户名或邮箱已被占用'})
        return user


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        user = self.context['request'].user
        if not user.check_password(attrs['old_password']):
            raise serializers.ValidationError({'old_password': '原密码不正确'})
        password_validation.validate_password(password=attrs['new_password'], user=user)
        return attrs

    def save(self, **kwargs):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save(update_fields=['password', 'updated_at'])
        return user
