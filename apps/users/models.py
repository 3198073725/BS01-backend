import uuid
import secrets
import string
from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from django.core.validators import MinLengthValidator, RegexValidator
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin


class UserManager(BaseUserManager):
    def create_user(self, username, email=None, password=None, **extra_fields):
        if not username:
            raise ValueError("用户名必须提供")
        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            # 避免依赖已废弃的 make_random_password，生成强随机密码
            alphabet = string.ascii_letters + string.digits
            rand_pwd = ''.join(secrets.choice(alphabet) for _ in range(24))
            user.set_password(rand_pwd)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('超级用户必须是 is_staff=True')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('超级用户必须是 is_superuser=True')
        return self.create_user(username, email=email, password=password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """用户模型 - 对应 users_user 表"""
    
    GENDER_CHOICES = [
        ('male', '男'),
        ('female', '女'),
        ('other', '其他'),
        ('private', '保密')
    ]
    
    PRIVACY_CHOICES = [
        ('public', '公开'),
        ('private', '私密'),
        ('friends_only', '仅好友可见')
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(
        max_length=150, 
        unique=True,
        validators=[
            MinLengthValidator(3),
            RegexValidator(
                regex='^[a-zA-Z0-9_.]+$',
                message='用户名只能包含字母、数字、下划线和点号'
            )
        ],
        verbose_name="用户名"
    )
    email = models.EmailField(max_length=254, unique=True, verbose_name="邮箱")
    
    profile_picture = models.CharField(max_length=100, null=True, blank=True, verbose_name="头像")
    profile_picture_f = models.ImageField(upload_to='avatars/', null=True, blank=True, max_length=200, verbose_name="头像(ImageField)")
    bio = models.TextField(null=True, blank=True, verbose_name="个人简介")
    is_active = models.BooleanField(default=True, verbose_name="是否激活")
    is_staff = models.BooleanField(default=False, verbose_name="是否员工")
    date_joined = models.DateTimeField(auto_now_add=True, verbose_name="注册时间")
    
    # 新增字段（基于设计SQL的建议）
    nickname = models.CharField(max_length=64, null=True, blank=True, verbose_name="昵称")
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, default='private', verbose_name="性别")
    birth_date = models.DateField(null=True, blank=True, verbose_name="出生日期")
    location = models.CharField(max_length=100, null=True, blank=True, verbose_name="所在地")
    website = models.URLField(max_length=200, null=True, blank=True, verbose_name="个人网站")
    phone_number = models.CharField(max_length=20, null=True, blank=True, unique=True, verbose_name="手机号")
    is_verified = models.BooleanField(default=False, verbose_name="是否认证用户")
    is_creator = models.BooleanField(default=False, verbose_name="是否创作者")
    privacy_mode = models.CharField(max_length=20, choices=PRIVACY_CHOICES, default='public', verbose_name="隐私模式")
    
    # 统计字段（缓存）
    followers_count = models.PositiveIntegerField(default=0, verbose_name="粉丝数")
    following_count = models.PositiveIntegerField(default=0, verbose_name="关注数")
    video_count = models.PositiveIntegerField(default=0, verbose_name="视频数")
    total_likes_received = models.PositiveIntegerField(default=0, verbose_name="获赞总数")
    total_views_received = models.PositiveBigIntegerField(default=0, verbose_name="总播放量")
    
    # 时间戳
    last_active = models.DateTimeField(null=True, blank=True, verbose_name="最后活跃时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    objects = UserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email']
    
    class Meta:
        db_table = 'users_user'
        verbose_name = "用户"
        verbose_name_plural = "用户"
        indexes = [
            models.Index(fields=['username'], name='idx_users_username'),
            models.Index(fields=['email'], name='idx_users_email'),
            models.Index(fields=['date_joined'], name='idx_users_joined'),
            models.Index(fields=['followers_count'], name='idx_users_followers_cnt'),
        ]
        ordering = ['-date_joined']

    def __str__(self):
        return f"{self.username} ({self.nickname or '无昵称'})"
    
    def set_password(self, raw_password):
        """设置密码（哈希存储）"""
        self.password = make_password(raw_password)
    
    def check_password(self, raw_password):
        """验证密码"""
        return check_password(raw_password, self.password)
    
    @property
    def display_name(self):
        """显示名称（优先使用昵称）"""
        return self.nickname or self.username


## 统一使用 apps.interactions.Follow 管理 interactions_follow 表，避免重复模型


class UserStatistic(models.Model):
    """用户统计数据 - 可选表，用于详细统计"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="统计ID")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='statistics', verbose_name="用户")
    date = models.DateField(verbose_name="统计日期")
    
    # 互动数据
    new_followers = models.PositiveIntegerField(default=0, verbose_name="新增粉丝")
    new_following = models.PositiveIntegerField(default=0, verbose_name="新增关注")
    likes_received = models.PositiveIntegerField(default=0, verbose_name="收到点赞")
    comments_received = models.PositiveIntegerField(default=0, verbose_name="收到评论")
    shares_received = models.PositiveIntegerField(default=0, verbose_name="收到分享")
    
    # 内容数据
    videos_uploaded = models.PositiveIntegerField(default=0, verbose_name="上传视频")
    total_views = models.PositiveBigIntegerField(default=0, verbose_name="总播放量")
    watch_time = models.PositiveBigIntegerField(default=0, verbose_name="总观看时长(秒)")
    
    # 活跃度数据
    login_count = models.PositiveIntegerField(default=0, verbose_name="登录次数")
    active_days = models.PositiveIntegerField(default=0, verbose_name="活跃天数")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    class Meta:
        db_table = 'users_user_statistic'
        verbose_name = "用户统计数据"
        verbose_name_plural = "用户统计数据"
        constraints = [
            models.UniqueConstraint(fields=['user', 'date'], name='unique_user_date_stat')
        ]
        indexes = [
            models.Index(fields=['user', 'date']),
        ]