"""Analytics 应用配置模块。

此模块定义了 Analytics 应用的 AppConfig，用于在 Django 启动时注册应用、
并可在需要时扩展 ready 钩子执行初始化逻辑。
"""

from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    """Analytics 应用的 AppConfig。"""
    name = 'apps.analytics'
