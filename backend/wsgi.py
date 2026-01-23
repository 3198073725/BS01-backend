"""
WSGI config for backend project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/

中文说明：
- 本模块提供 WSGI 启动入口，适用于 gunicorn/uwsgi 等同步服务器。
- 生产部署例如：gunicorn backend.wsgi:application -b 0.0.0.0:8000 -w 4
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

application = get_wsgi_application()
