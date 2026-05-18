from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from django.shortcuts import get_object_or_404
from .models import ConfigNamespace, ConfigKey, ConfigEntry
import time
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
from apps.adminapi.permissions import IsAdmin
from apps.configs.utils import invalidate_config_cache
from backend.system_events import publish_config_updated
from apps.content.comment_moderation_rules import (
    COMMENT_PATTERN_RULES,
    COMMENT_TEXT_CANONICAL_RULES,
    DEFAULT_COMMENT_BLOCKED_KEYWORDS,
)

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
    if isinstance(value, str) and value == '':
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
            'featured_video_ids': {'type': 'string', 'label': '热门推荐视频ID列表', 'default': '', 'help': '每行一个视频ID，按优先级排序'},
            'featured_limit': {'type': 'int', 'label': '热门推荐显示数量', 'default': 10, 'help': '热门推荐区最多显示的视频数量（1-20）'},
            'AUTO_MODERATION_ENABLED': {'type': 'bool', 'label': '启用自动质控', 'default': True, 'help': '命中文本规则时自动拦截评论并阻止视频进入正常发布流'},
            'COMMENT_AUTOMOD_ENABLED': {'type': 'bool', 'label': '评论自动质控', 'default': True, 'help': '评论发布前执行敏感词拦截'},
            'COMMENT_BLOCKED_KEYWORDS': {'type': 'string', 'label': '评论敏感词', 'default': ','.join(DEFAULT_COMMENT_BLOCKED_KEYWORDS), 'help': '逗号或换行分隔，命中后拒绝发表评论；留空时使用系统内置兜底词表'},
            'COMMENT_CANONICAL_RULES': {'type': 'string', 'label': '评论归一化规则', 'default': '\n'.join(f'{src}={dst}' for src, dst in COMMENT_TEXT_CANONICAL_RULES), 'help': '每行一条，格式为 变体=标准词，例如 草拟吗=操你妈；用于同音字、空格符号绕过归一化'},
            'COMMENT_PATTERN_RULES': {'type': 'string', 'label': '评论正则规则', 'default': '\n'.join(f'{pattern.pattern}={label}' for pattern, label in COMMENT_PATTERN_RULES), 'help': '每行一条，格式为 正则=标签，例如 n[1i]m[a4]=你妈；用于缩写、字母数字混写等高级匹配'},
            'VIDEO_AUTOMOD_ENABLED': {'type': 'bool', 'label': '视频自动质控', 'default': True, 'help': '视频标题/描述/文件名发布前执行敏感词拦截'},
            'VIDEO_BLOCKED_KEYWORDS': {'type': 'string', 'label': '视频敏感词', 'default': '', 'help': '逗号或换行分隔，命中后视频保持草稿并记录审计'},
            'AUTOMOD_REJECT_MESSAGE': {'type': 'string', 'label': '自动质控提示语', 'default': '内容未通过自动质控，请修改后重试', 'help': '评论或视频命中规则时返回给用户的提示'},
            'ZHIPU_MODERATION_ENABLED': {'type': 'bool', 'label': '启用智谱AI质控', 'default': True, 'help': '开启后优先调用智谱AI内容安全 API 做真实质控'},
            'ZHIPU_API_KEY': {'type': 'string', 'label': '智谱AI API Key', 'default': '', 'help': '服务端审核使用，建议通过 .env 配置'},
            'ZHIPU_BASE_URL': {'type': 'string', 'label': '智谱AI Base URL', 'default': 'https://open.bigmodel.cn/api/paas/v4', 'help': '兼容代理或网关时可覆盖'},
            'ZHIPU_MODERATION_MODEL': {'type': 'string', 'label': '智谱AI 审核模型', 'default': 'moderation', 'help': '智谱官方内容安全模型'},
            'ZHIPU_MODERATION_TIMEOUT_SECONDS': {'type': 'int', 'label': '智谱AI 审核超时(秒)', 'default': 15, 'help': '评论和视频文本审核调用超时'},
            'ZHIPU_MODERATION_FAIL_CLOSED': {'type': 'bool', 'label': '审核故障时拒绝', 'default': False, 'help': '开启后审核服务故障会直接拦截内容'},
            'ZHIPU_MODERATION_BLOCKED_CATEGORIES': {'type': 'string', 'label': '拦截分类', 'default': 'porn,abuse,violence,contraband,politics,crime', 'help': '逗号分隔，命中这些风险类型则拦截'},
            'MODERATION_MEDIA_PUBLIC_BASE_URL': {'type': 'string', 'label': '媒体审核公网地址', 'default': '', 'help': '智谱审核图片/视频时需要可公网访问的媒体地址基址，留空时回退到 SITE_URL'},
        }
    },
    # 用户注册/认证
    'auth': {
        'label': '用户认证',
        'settings': {
            'REGISTRATION_REQUIRE_CAPTCHA': {'type': 'bool', 'label': '注册需验证码', 'default': False, 'help': '开启后注册需要验证码'},
            'EMAIL_VERIFY_TOKEN_MAX_AGE': {'type': 'int', 'label': '邮箱验证有效期(秒)', 'default': 86400, 'help': '默认24小时'},
            'PASSWORD_RESET_TOKEN_MAX_AGE': {'type': 'int', 'label': '密码重置有效期(秒)', 'default': 3600, 'help': '默认1小时'},
            'EMAIL_CHANGE_TOKEN_MAX_AGE': {'type': 'int', 'label': '邮箱改绑确认有效期(秒)', 'default': 86400, 'help': '默认24小时'},
            'EMAIL_CHECK_MX': {'type': 'bool', 'label': '检查邮箱MX记录', 'default': False, 'help': '验证邮箱域名是否有效'},
            'AVATAR_MAX_SIZE_BYTES': {'type': 'int', 'label': '头像最大大小(字节)', 'default': 2097152, 'help': '默认2MB'},
            'AVATAR_MAX_PIXELS': {'type': 'int', 'label': '头像最大像素', 'default': 25000000, 'help': '默认25MP'},
            'VIDEO_MAX_SIZE_BYTES': {'type': 'int', 'label': '视频最大大小(字节)', 'default': 524288000, 'help': '默认500MB'},
            'CHUNK_SIZE_BYTES': {'type': 'int', 'label': '分片上传大小(字节)', 'default': 5242880, 'help': '默认5MB'},
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
            'LOGIN_CODE_LOGIN_FAIL_WINDOW_SECONDS': {'type': 'int', 'label': '验证码登录失败统计窗口(秒)', 'default': 600, 'help': '默认10分钟'},
            'LOGIN_CODE_LOGIN_FAIL_MAX_TRIES_EMAIL': {'type': 'int', 'label': '验证码登录单邮箱失败上限', 'default': 5, 'help': '超过后进入冷却'},
            'LOGIN_CODE_LOGIN_FAIL_MAX_TRIES_IP': {'type': 'int', 'label': '验证码登录单IP失败上限', 'default': 50, 'help': '超过后进入冷却'},
            'LOGIN_CODE_LOGIN_FAIL_COOLDOWN_SECONDS': {'type': 'int', 'label': '验证码登录冷却时间(秒)', 'default': 300, 'help': '默认5分钟'},
            'USERNAME_CHANGE_COOLDOWN_DAYS': {'type': 'int', 'label': '改名冷却天数', 'default': 30, 'help': '两次修改用户名之间的最短间隔'},
            'LOGIN_PASSWORD_FAIL_WINDOW_SECONDS': {'type': 'int', 'label': '密码登录失败统计窗口(秒)', 'default': 600, 'help': '默认10分钟'},
            'LOGIN_PASSWORD_FAIL_MAX_TRIES_USERNAME': {'type': 'int', 'label': '用户名错误次数', 'default': 5, 'help': '密码错误锁定'},
            'LOGIN_PASSWORD_FAIL_MAX_TRIES_IP': {'type': 'int', 'label': 'IP错误次数', 'default': 50, 'help': ''},
            'LOGIN_PASSWORD_FAIL_COOLDOWN_SECONDS': {'type': 'int', 'label': '锁定时间(秒)', 'default': 300, 'help': '默认5分钟'},
            'POPUP_STATS_CACHE_SECONDS': {'type': 'int', 'label': '个人弹窗统计缓存(秒)', 'default': 120, 'help': '默认2分钟'},
        }
    },
    'media': {
        'label': '媒体限制',
        'settings': {
            'THUMBNAIL_MAX_SIZE_BYTES': {'type': 'int', 'label': '封面最大大小(字节)', 'default': 5242880, 'help': '默认5MB'},
            'THUMBNAIL_MIN_WIDTH': {'type': 'int', 'label': '封面最小宽度(像素)', 'default': 480, 'help': '默认480'},
            'THUMBNAIL_MIN_HEIGHT': {'type': 'int', 'label': '封面最小高度(像素)', 'default': 270, 'help': '默认270'},
            'THUMBNAIL_RATIO_TOL': {'type': 'string', 'label': '封面比例容差', 'default': '0.04', 'help': '与16:9比例允许的偏差，例如0.04'},
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

ADMIN_OVERRIDE_KEYS = {'SITE_URL', 'FRONTEND_URL'}
RELOAD_REQUIRED_KEYS = {
    'SITE_URL',
    'FRONTEND_URL',
    'LANGUAGE_CODE',
    'TIME_ZONE',
    'EMAIL_BACKEND',
    'DEFAULT_FROM_EMAIL',
    'EMAIL_HOST',
    'EMAIL_PORT',
    'EMAIL_USE_TLS',
    'EMAIL_USE_SSL',
    'EMAIL_TIMEOUT',
    'REDIS_URL',
    'REDIS_HOST',
    'REDIS_PORT',
    'REDIS_DB',
    'REDIS_MAX_CONNECTIONS',
    'CACHE_KEY_PREFIX',
    'CACHE_DEFAULT_TIMEOUT',
    'USE_REDIS_CACHE',
    'CELERY_BROKER_URL',
    'CELERY_RESULT_BACKEND',
    'CELERY_TASK_ALWAYS_EAGER',
    'CELERY_TASK_TIME_LIMIT',
    'CELERY_TASK_SOFT_TIME_LIMIT',
}


def config_requires_reload(changed_keys):
    return any(str(key or '') in RELOAD_REQUIRED_KEYS for key in (changed_keys or []))


def iter_system_schema_settings():
    for category, data in SYSTEM_CONFIG_SCHEMA.items():
        for key, meta in data['settings'].items():
            yield category, key, meta


def get_admin_writable(key: str, env_value):
    if key in ADMIN_OVERRIDE_KEYS:
        return True
    return env_value is None

class GlobalConfigView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        # 获取全局配置命名空间
        ns, _ = ConfigNamespace.objects.get_or_create(name='system', defaults={'description': 'System Global Settings'})
        
        keys = ConfigKey.objects.filter(namespace=ns)
        key_defaults = {item.key: item.default_value for item in keys}
        entries = ConfigEntry.objects.filter(key__in=keys, content_type__isnull=True, object_id__isnull=True, is_active=True)
        db_values = {entry.key.key: entry.value for entry in entries}

        config_data = {}
        for _, key, meta in iter_system_schema_settings():
            env_value = get_env_value(key, None)
            if key in ADMIN_OVERRIDE_KEYS and key in db_values:
                config_data[key] = db_values[key]
            elif env_value is not None:
                config_data[key] = env_value
            elif key in db_values:
                config_data[key] = db_values[key]
            elif key in key_defaults and key_defaults[key] is not None:
                config_data[key] = key_defaults[key]
            else:
                config_data[key] = meta['default']

        # 版本号用于强制客户端刷新缓存/重启逻辑
        if 'config_version' not in config_data:
            config_data['config_version'] = int(time.time())

        return Response(config_data)


class AdminConfigListView(APIView):
    """获取所有系统配置项的定义和当前值（优先从 .env 读取）"""
    permission_classes = [IsAdmin]

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
                # 大多数配置优先级：.env > 数据库 > 默认值
                # 但地址类配置允许管理端覆盖，以数据库值优先。
                env_value = get_env_value(key, None)
                prefer_db = key in ADMIN_OVERRIDE_KEYS
                writable = get_admin_writable(key, env_value)
                if prefer_db and key in db_values:
                    final_value = db_values[key]
                    source = 'db'
                elif env_value is not None:
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
                    '_source': source,  # 调试信息，前端可忽略
                    '_writable': writable,
                }
        
        return Response(result)


class AdminConfigUpdateView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request):
        data = request.data
        ns, _ = ConfigNamespace.objects.get_or_create(name='system')
        
        updated_keys = []
        for k, v in data.items():
            env_value = get_env_value(k, None)
            if not get_admin_writable(k, env_value):
                continue
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
        version = int(time.time())
        version_key, _ = ConfigKey.objects.get_or_create(namespace=ns, key='config_version', defaults={'value_type': 'int', 'default_value': version})
        ConfigEntry.objects.update_or_create(
            key=version_key,
            content_type__isnull=True,
            defaults={'value': version}
        )
        invalidate_config_cache('system')
        publish_config_updated(
            version=version,
            changed_keys=updated_keys,
            reload_required=config_requires_reload(updated_keys),
        )

        return Response({'status': 'ok', 'updated': updated_keys, 'version': version})
