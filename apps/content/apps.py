"""Content 应用配置模块。

用于注册内容相关功能（如话题/标签/素材等）的 Django 应用。
可在需要时通过重写 AppConfig.ready 注入启动时逻辑。
"""

from django.apps import AppConfig


class ContentConfig(AppConfig):
    """Content 应用的 AppConfig。"""
    name = 'apps.content'
