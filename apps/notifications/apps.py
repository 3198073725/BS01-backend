"""Notifications 应用配置模块。

负责系统内消息/通知推送等功能的 Django 应用配置。
如需在启动阶段注册信号或回调，可重写 AppConfig.ready。
"""

from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """Notifications 应用的 AppConfig。"""
    name = 'apps.notifications'
