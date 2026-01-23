"""Analytics 管理后台注册模块。

用于将本应用模型注册到 Django 管理后台，便于数据查看与运维操作。
后续在 models.py 中定义模型后，可通过 admin.site.register 进行注册。
"""

from django.contrib import admin

# 示例：
# from .models import AnalyticsEvent
# admin.site.register(AnalyticsEvent)
