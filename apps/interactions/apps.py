"""Interactions 应用配置模块。

负责互动相关能力（点赞、评论、收藏、关注等）的 Django 应用配置。
如需在启动阶段做信号注册或初始化，可重写 AppConfig.ready。
"""

from django.apps import AppConfig


class InteractionsConfig(AppConfig):
    """Interactions 应用的 AppConfig。"""
    name = 'apps.interactions'
    def ready(self):  # pragma: no cover
        # 注册信号处理（关注/点赞计数维护）
        from . import signals  # noqa: F401
