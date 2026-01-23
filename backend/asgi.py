"""
ASGI config for backend project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/

中文说明：
- 本模块提供 ASGI 启动入口，适用于 Uvicorn/Daphne 等异步服务器。
- 生产部署通常形如：
  uvicorn backend.asgi:application --host 0.0.0.0 --port 8000 --workers 4
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

application = get_asgi_application()
