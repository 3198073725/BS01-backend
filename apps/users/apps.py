"""Users 应用配置模块。

提供用户/账户相关能力（资料、关系、认证扩展等）的 Django 应用配置。
如需在启动阶段注册信号或初始化逻辑，可重写 AppConfig.ready。
"""

from django.apps import AppConfig


class UsersConfig(AppConfig):
    """Users 应用的 AppConfig。"""
    name = 'apps.users'
