# VidSprout Backend

`backend/` 是项目的 Django 后端，负责：

- REST API
- JWT 认证
- 视频、评论、关注、通知等业务接口
- ASGI WebSocket
- Celery 异步任务入口

## 当前技术栈

- Django + Django REST Framework
- ASGI: `uvicorn`
- 认证: Simple JWT
- 异步任务: Celery
- 缓存 / broker: Redis
- 数据库: PostgreSQL 优先

## 关键入口

- Django settings: [backend/settings.py](/root/BS01/backend/backend/settings.py:1)
- URL 路由: [backend/urls.py](/root/BS01/backend/backend/urls.py:1)
- 管理命令入口: [manage.py](/root/BS01/backend/manage.py:1)

## 运行方式

### 开发环境

在仓库根目录创建虚拟环境并安装依赖后：

```bash
cd /root/BS01/backend
../.venv/bin/python manage.py migrate
../.venv/bin/python manage.py runserver 0.0.0.0:8000
```

### 更接近线上

如果要验证 ASGI / WebSocket，直接跑 `uvicorn` 更接近实际部署：

```bash
cd /root/BS01
./.venv/bin/uvicorn backend.asgi:application --host 0.0.0.0 --port 8000 --workers 1
```

## 环境变量

后端默认读取：

- [../deploy/env.example](/root/BS01/deploy/env.example:1)
- `backend/.env`

最少要确认：

- `SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `SITE_URL`
- `DB_*`
- `REDIS_URL`
- `CORS_ALLOWED_ORIGINS`
- `CSRF_TRUSTED_ORIGINS`

## WebSocket

系统配置同步使用：

- `/ws/system-events/`

这要求后端必须跑 ASGI。当前仓库已经按这个方向运行，不能退回 WSGI-only 入口。

## Celery

视频转码、缩略图、部分异步处理依赖 Celery。

本地启动示例：

```bash
cd /root/BS01
./.venv/bin/celery -A backend worker -l info
```

如果使用生产环境，通常还会有：

- `bs01-celery.service`
- `bs01-celery-transcode.service`
- `bs01-celery-beat.service`

## 验证

健康检查：

```bash
curl http://127.0.0.1:8000/api/health/
```

接口文档：

- `/api/schema/swagger-ui/`
- `/api/schema/redoc/`

## 相关文档

- 仓库总览：[../README.md](/root/BS01/README.md:1)
- 通用部署：[../deploy/README.md](/root/BS01/deploy/README.md:1)
- 当前机器部署：[../2H2G3M/BACKEND_DEPLOY.md](/root/BS01/2H2G3M/BACKEND_DEPLOY.md:1)
