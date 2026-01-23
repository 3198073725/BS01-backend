"""Videos 应用配置模块。

负责短视频上传、存储、转码、封面生成等的 Django 应用配置。
如需在启动时加载媒体处理器或注册信号，可重写 AppConfig.ready。
"""

from django.apps import AppConfig


class VideosConfig(AppConfig):
    """Videos 应用的 AppConfig。"""
    name = 'apps.videos'
