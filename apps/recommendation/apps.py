"""Recommendation 应用配置模块。

负责推荐/召回/排序等相关能力的 Django 应用配置。
如需在启动阶段做模型加载或特征初始化，可重写 AppConfig.ready。
"""

from django.apps import AppConfig


class RecommendationConfig(AppConfig):
    """Recommendation 应用的 AppConfig。"""
    name = 'apps.recommendation'
