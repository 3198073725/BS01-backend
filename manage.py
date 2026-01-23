#!/usr/bin/env python
"""Django 命令行工具入口。

用途：
- 运行开发服务器：python manage.py runserver 0.0.0.0:8000
- 数据迁移：python manage.py makemigrations && python manage.py migrate
- 创建超级用户：python manage.py createsuperuser
- 健康检查：python manage.py check
"""
import os
import sys


def main():
    """运行管理任务的入口函数。"""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
