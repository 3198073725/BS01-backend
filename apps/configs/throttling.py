"""
动态限流配置支持

支持从数据库 ConfigEntry 读取限流配置，实时生效，无需重启服务。
"""
import logging
from rest_framework.throttling import (
    ScopedRateThrottle,
    AnonRateThrottle,
    UserRateThrottle,
)
from apps.configs.models import ConfigNamespace, ConfigKey, ConfigEntry

logger = logging.getLogger(__name__)


def get_config_value(key, default=None):
    """从数据库获取配置值"""
    try:
        ns = ConfigNamespace.objects.filter(name='system').first()
        if not ns:
            return default
        key_obj = ConfigKey.objects.filter(namespace=ns, key=key).first()
        if not key_obj:
            return default
        entry = ConfigEntry.objects.filter(key=key_obj, content_type__isnull=True, is_active=True).first()
        if entry is None:
            return default
        return entry.value
    except Exception as e:
        logger.error(f"Error reading config {key}: {e}")
        return default


class DynamicScopedRateThrottle(ScopedRateThrottle):
    """支持动态配置的 ScopedRateThrottle"""

    def get_cache_key(self, request, view):
        # 检查限流是否被全局禁用
        if not get_config_value('throttling_enabled', True):
            return None  # 返回 None 表示不限流
        return super().get_cache_key(request, view)

    def get_rate(self):
        # 先从父类获取 scope 对应的 rate
        rate = super().get_rate()
        
        # 如果是推荐相关接口，尝试读取动态配置
        if self.scope in ['recommendation_feed', 'following_feed', 'featured_feed']:
            dynamic_rate = get_config_value('throttle_recommendation_rate')
            if dynamic_rate:
                return f"{dynamic_rate}/hour"
        
        return rate


class DynamicAnonRateThrottle(AnonRateThrottle):
    """支持动态配置的 AnonRateThrottle"""

    def get_cache_key(self, request, view):
        # 检查限流是否被全局禁用
        if not get_config_value('throttling_enabled', True):
            return None
        return super().get_cache_key(request, view)

    def get_rate(self):
        # 读取动态配置
        dynamic_rate = get_config_value('throttle_anon_rate')
        if dynamic_rate:
            return f"{dynamic_rate}/hour"
        return super().get_rate()


class DynamicUserRateThrottle(UserRateThrottle):
    """支持动态配置的 UserRateThrottle"""

    def get_cache_key(self, request, view):
        # 检查限流是否被全局禁用
        if not get_config_value('throttling_enabled', True):
            return None
        return super().get_cache_key(request, view)

    def get_rate(self):
        # 读取动态配置
        dynamic_rate = get_config_value('throttle_user_rate')
        if dynamic_rate:
            return f"{dynamic_rate}/hour"
        return super().get_rate()
