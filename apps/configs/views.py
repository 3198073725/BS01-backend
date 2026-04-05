from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from django.shortcuts import get_object_or_404
from .models import ConfigNamespace, ConfigKey, ConfigEntry
import time
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

# 加载 .env 文件（如果存在）
BASE_DIR = Path(__file__).resolve().parent.parent.parent
env_path = find_dotenv(BASE_DIR / '.env') or find_dotenv()
if env_path:
    load_dotenv(env_path, override=False)

def get_env_value(key, default=None):
    """从环境变量获取值，支持自动类型转换"""
    value = os.getenv(key)
    if value is None:
        return default
    
    # 布尔值转换
    if isinstance(default, bool):
        return value.lower() in ('true', '1', 'yes', 'y')
    
    # 整数转换
    if isinstance(default, int):
        try:
            return int(value)
        except ValueError:
            return default
    
    return value

# 定义所有可配置项及其元数据
SYSTEM_CONFIG_SCHEMA = {
    # 基础功能开关
    'features': {
        'label': '功能开关',
        'settings': {
            'show_api_base': {'type': 'bool', 'label': '显示API地址入口', 'default': True, 'help': '控制各端是否显示API基址切换按钮'},
            'allow_register': {'type': 'bool', 'label': '允许新用户注册', 'default': True, 'help': '关闭后禁止新用户注册'},
            'allow_anonymous_view': {'type': 'bool', 'label': '允许游客观看视频', 'default': True, 'help': '关闭后必须登录才能观看'},
            'maintenance_mode': {'type': 'bool', 'label': '全站维护模式', 'default': False, 'help': '开启后全站显示维护页面'},
            'allow_comments': {'type': 'bool', 'label': '启用全局评论', 'default': True, 'help': '关闭后禁用所有评论功能'},
            'allow_likes': {'type': 'bool', 'label': '启用全局点赞', 'default': True, 'help': '关闭后禁用所有点赞功能'},
            'video_auto_publish': {'type': 'bool', 'label': '视频转码后自动发布', 'default': True, 'help': '关闭后需手动审核发布'},
            'allow_download': {'type': 'bool', 'label': '允许用户下载视频', 'default': False, 'help': '开启后用户可下载视频'},
        }
    },
    # 基础站点设置
    'site': {
        'label': '站点设置',
        'settings': {
            'SITE_URL': {'type': 'string', 'label': '站点URL', 'default': 'http://localhost:8000', 'help': '后端API地址'},
            'FRONTEND_URL': {'type': 'string', 'label': '前端URL', 'default': '', 'help': '前端页面地址'},
            'DEBUG': {'type': 'bool', 'label': '调试模式', 'default': False, 'help': '开启后显示详细错误信息'},
            'LANGUAGE_CODE': {'type': 'select', 'label': '语言', 'default': 'zh-hans', 'options': ['zh-hans', 'en'], 'help': '站点默认语言'},
            'TIME_ZONE': {'type': 'string', 'label': '时区', 'default': 'Asia/Shanghai', 'help': '如 Asia/Shanghai'},
        }
    },
    # 内容策略
    'content': {
        'label': '内容策略',
        'settings': {
            'home_layout': {'type': 'select', 'label': '首页布局', 'default': 'grid', 'options': ['grid', 'waterfall', 'single'], 'help': 'grid=宫格, waterfall=瀑布流, single=单列大图'},
            'recommend_algorithm': {'type': 'select', 'label': '推荐算法', 'default': 'latest', 'options': ['latest', 'hot', 'random'], 'help': 'latest=最新优先, hot=最热优先, random=随机散播'},
            'max_upload_size_mb': {'type': 'int', 'label': '最大上传限制(MB)', 'default': 500, 'help': '视频文件最大允许上传大小'},
        }
    },
    # 用户注册/认证
    'auth': {
        'label': '用户认证',
        'settings': {
            'REGISTRATION_REQUIRE_CAPTCHA': {'type': 'bool', 'label': '注册需验证码', 'default': False, 'help': '开启后注册需要验证码'},
            'EMAIL_VERIFY_TOKEN_MAX_AGE': {'type': 'int', 'label': '邮箱验证有效期(秒)', 'default': 86400, 'help': '默认24小时'},
            'PASSWORD_RESET_TOKEN_MAX_AGE': {'type': 'int', 'label': '密码重置有效期(秒)', 'default': 3600, 'help': '默认1小时'},
            'EMAIL_CHECK_MX': {'type': 'bool', 'label': '检查邮箱MX记录', 'default': False, 'help': '验证邮箱域名是否有效'},
            'AVATAR_MAX_SIZE_BYTES': {'type': 'int', 'label': '头像最大大小(字节)', 'default': 2097152, 'help': '默认2MB'},
            'AVATAR_MAX_PIXELS': {'type': 'int', 'label': '头像最大像素', 'default': 25000000, 'help': '默认25MP'},
            'VIDEO_MAX_SIZE_BYTES': {'type': 'int', 'label': '视频最大大小(字节)', 'default': 524288000, 'help': '默认500MB'},
            'REFRESH_TOKEN_LIFETIME_DAYS': {'type': 'int', 'label': '刷新令牌有效期(天)', 'default': 60, 'help': 'JWT刷新令牌过期时间'},
        }
    },
    # 限流设置
    'throttle': {
        'label': '限流控制',
        'settings': {
            'throttling_enabled': {'type': 'bool', 'label': '启用限流', 'default': True, 'help': '关闭后取消所有限流'},
            'throttle_anon_rate': {'type': 'int', 'label': '匿名用户限流(次/小时)', 'default': 100, 'help': ''},
            'throttle_user_rate': {'type': 'int', 'label': '登录用户限流(次/小时)', 'default': 1000, 'help': ''},
            'throttle_recommendation_rate': {'type': 'int', 'label': '推荐接口限流(次/小时)', 'default': 1800, 'help': ''},
            'THROTTLE_REGISTER': {'type': 'string', 'label': '注册限流', 'default': '5/hour', 'help': '如 5/hour'},
            'THROTTLE_LOGIN_PASSWORD': {'type': 'string', 'label': '密码登录限流', 'default': '60/hour', 'help': ''},
            'THROTTLE_LOGIN_CODE': {'type': 'string', 'label': '验证码登录限流', 'default': '30/hour', 'help': ''},
            'THROTTLE_VIDEO_UPLOAD': {'type': 'string', 'label': '视频上传限流', 'default': '20/hour', 'help': ''},
        }
    },
    # 风控/安全
    'security': {
        'label': '安全风控',
        'settings': {
            'LOGIN_CODE_MIN_INTERVAL_SECONDS': {'type': 'int', 'label': '验证码最小间隔(秒)', 'default': 60, 'help': '发送验证码间隔'},
            'LOGIN_CODE_DAILY_LIMIT_EMAIL': {'type': 'int', 'label': '单邮箱日限', 'default': 20, 'help': '同一邮箱每天最多'},
            'LOGIN_CODE_DAILY_LIMIT_IP': {'type': 'int', 'label': '单IP日限', 'default': 200, 'help': '同一IP每天最多'},
            'LOGIN_PASSWORD_FAIL_MAX_TRIES_USERNAME': {'type': 'int', 'label': '用户名错误次数', 'default': 5, 'help': '密码错误锁定'},
            'LOGIN_PASSWORD_FAIL_MAX_TRIES_IP': {'type': 'int', 'label': 'IP错误次数', 'default': 50, 'help': ''},
            'LOGIN_PASSWORD_FAIL_COOLDOWN_SECONDS': {'type': 'int', 'label': '锁定时间(秒)', 'default': 300, 'help': '默认5分钟'},
        }
    },
    # 邮件设置
    'email': {
        'label': '邮件服务',
        'settings': {
            'EMAIL_BACKEND': {'type': 'select', 'label': '邮件后端', 'default': 'django.core.mail.backends.console.EmailBackend', 
                'options': ['django.core.mail.backends.console.EmailBackend', 'django.core.mail.backends.smtp.EmailBackend'], 'help': ''},
            'DEFAULT_FROM_EMAIL': {'type': 'string', 'label': '发件人', 'default': 'no-reply@example.com', 'help': ''},
            'EMAIL_HOST': {'type': 'string', 'label': 'SMTP主机', 'default': 'localhost', 'help': ''},
            'EMAIL_PORT': {'type': 'int', 'label': 'SMTP端口', 'default': 25, 'help': ''},
            'EMAIL_USE_TLS': {'type': 'bool', 'label': '使用TLS', 'default': False, 'help': ''},
            'EMAIL_USE_SSL': {'type': 'bool', 'label': '使用SSL', 'default': False, 'help': ''},
            'EMAIL_TIMEOUT': {'type': 'int', 'label': '超时(秒)', 'default': 10, 'help': ''},
        }
    },
    # 缓存/Redis
    'cache': {
        'label': '缓存设置',
        'settings': {
            'USE_REDIS_CACHE': {'type': 'bool', 'label': '使用Redis缓存', 'default': False, 'help': '关闭使用内存缓存'},
            'REDIS_URL': {'type': 'string', 'label': 'Redis URL', 'default': '', 'help': '如 redis://127.0.0.1:6379/0'},
            'REDIS_HOST': {'type': 'string', 'label': 'Redis主机', 'default': '127.0.0.1', 'help': ''},
            'REDIS_PORT': {'type': 'int', 'label': 'Redis端口', 'default': 6379, 'help': ''},
            'REDIS_DB': {'type': 'int', 'label': 'Redis数据库', 'default': 0, 'help': '0-15'},
            'REDIS_MAX_CONNECTIONS': {'type': 'int', 'label': '最大连接数', 'default': 50, 'help': ''},
            'CACHE_KEY_PREFIX': {'type': 'string', 'label': '缓存前缀', 'default': 'bs01', 'help': ''},
            'CACHE_DEFAULT_TIMEOUT': {'type': 'int', 'label': '缓存超时(秒)', 'default': 60, 'help': ''},
        }
    },
    # Celery
    'celery': {
        'label': '任务队列',
        'settings': {
            'CELERY_BROKER_URL': {'type': 'string', 'label': 'Broker URL', 'default': '', 'help': '消息队列地址'},
            'CELERY_RESULT_BACKEND': {'type': 'string', 'label': '结果后端', 'default': '', 'help': ''},
            'CELERY_TASK_ALWAYS_EAGER': {'type': 'bool', 'label': '同步执行', 'default': False, 'help': '开发调试用'},
            'CELERY_TASK_TIME_LIMIT': {'type': 'int', 'label': '任务硬超时(秒)', 'default': 3600, 'help': ''},
            'CELERY_TASK_SOFT_TIME_LIMIT': {'type': 'int', 'label': '任务软超时(秒)', 'default': 3300, 'help': ''},
        }
    },
}

class GlobalConfigView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        # 获取全局配置命名空间
        ns, _ = ConfigNamespace.objects.get_or_create(name='system', defaults={'description': 'System Global Settings'})
        
        # 获取所有系统配置键
        keys = ConfigKey.objects.filter(namespace=ns)
        
        # 获取全局配置项 (content_type=None)
        entries = ConfigEntry.objects.filter(key__in=keys, content_type__isnull=True)
        
        config_data = {}
        for entry in entries:
            config_data[entry.key.key] = entry.value
            
        # 补充默认值
        for key in keys:
            if key.key not in config_data:
                config_data[key.key] = key.default_value

        # 版本号用于强制客户端刷新缓存/重启逻辑
        if 'config_version' not in config_data:
            config_data['config_version'] = int(time.time())

        return Response(config_data)


class AdminConfigListView(APIView):
    """获取所有系统配置项的定义和当前值（优先从 .env 读取）"""
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    def get(self, request):
        ns, _ = ConfigNamespace.objects.get_or_create(name='system')
        
        # 获取数据库中的配置值（用户通过管理端保存的）
        keys = ConfigKey.objects.filter(namespace=ns)
        entries = ConfigEntry.objects.filter(key__in=keys, content_type__isnull=True, is_active=True)
        db_values = {e.key.key: e.value for e in entries}
        
        # 组装返回数据：优先级 .env > 数据库 > settings.py 默认值
        result = {}
        for category, data in SYSTEM_CONFIG_SCHEMA.items():
            result[category] = {
                'label': data['label'],
                'settings': {}
            }
            for key, meta in data['settings'].items():
                # 优先级：1. .env 环境变量  2. 数据库保存的值  3. settings.py 默认值
                env_value = get_env_value(key, None)
                if env_value is not None:
                    final_value = env_value
                    source = 'env'
                elif key in db_values:
                    final_value = db_values[key]
                    source = 'db'
                else:
                    final_value = meta['default']
                    source = 'default'
                
                result[category]['settings'][key] = {
                    **meta,
                    'value': final_value,
                    '_source': source  # 调试信息，前端可忽略
                }
        
        return Response(result)


class AdminConfigUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    def post(self, request):
        data = request.data
        ns, _ = ConfigNamespace.objects.get_or_create(name='system')
        
        updated_keys = []
        for k, v in data.items():
            # 自动创建 Key
            key_obj, created = ConfigKey.objects.get_or_create(
                namespace=ns, 
                key=k,
                defaults={'value_type': 'json', 'default_value': v}
            )
            
            # 更新或创建全局 Entry
            entry, _ = ConfigEntry.objects.update_or_create(
                key=key_obj,
                content_type__isnull=True,
                defaults={'value': v, 'is_active': True}
            )
            updated_keys.append(k)

        # 每次修改配置，自动更新版本号以强制客户端拉取新配置
        version_key, _ = ConfigKey.objects.get_or_create(namespace=ns, key='config_version', defaults={'value_type': 'int', 'default_value': int(time.time())})
        ConfigEntry.objects.update_or_create(
            key=version_key,
            content_type__isnull=True,
            defaults={'value': int(time.time())}
        )

        return Response({'status': 'ok', 'updated': updated_keys, 'version': int(time.time())})
