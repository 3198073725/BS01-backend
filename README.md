# VidSprout Backend | 视频平台后端核心

VidSprout 是一个现代化的视频分享平台，后端基于 Python Django 架构，提供了从视频上传、自动化转码切片到社交互动的全栈服务。

## 架构概览

本项目采用解耦的微服务化设计思路（在单体仓库中实现）：
- **API 层**: 基于 Django REST Framework (DRF)，提供 RESTful 接口。
- **业务逻辑层**: 核心逻辑封装在 `services` 与 `serializers` 中，确保逻辑复用。
- **任务层**: 利用 Celery + Redis 实现视频转码、分发等高耗时异步操作。
- **存储层**: 
  - **数据库**: MySQL 处理结构化数据（用户、视频元数据、互动记录）。
  - **文件存储**: 支持本地或云端存储转码后的 DASH/HLS 流媒体文件。
  - **多媒体处理**: 深度集成 FFmpeg 和 Bento4 进行自动化切片与加密。

## 核心功能详解

### 1. 视频流水线 (Video Pipeline)
- **智能转码**: 自动识别上传格式并转换为 H.264 标准格式。
- **流式分发**: 自动生成 DASH (`.mpd`) 和 HLS (`.m3u8`) 列表，支持动态码率适配。
- **封面提取**: 自动从视频流中抓取高分辨率封面。

### 2. 社交互动架构
- **用户关系**: 完整的关注/粉丝/拉黑模型，支持双向动态追踪。
- **互动矩阵**: 集成点赞、踩、收藏、弹幕、评论（支持二级回复）。
- **个性化列表**: 观看历史、稍后再看（支持断点续看数据存储）。

### 3. 系统通知
- 基于长轮询或 WebSocket（可选）的系统通知分发。

## 技术栈

| 组件 | 技术 |
| :--- | :--- |
| **后端框架** | Django 4.2+, DRF 3.14+ |
| **异步任务** | Celery 5.3+ |
| **缓存/消息中间件** | Redis 7.0+ |
| **视频处理** | Bento4 (DASH 打包), FFmpeg (转码) |
| **身份认证** | Simple JWT |

## 部署指南

### 环境准备
- Python 3.9+
- MySQL 8.0+
- Redis 7.0+
- FFmpeg & Bento4 工具链（需加入系统 PATH）

### 快速启动
1. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```
2. **环境配置**
   创建 `.env` 文件（参考 `.env.example`）：
   ```ini
   DEBUG=True
   SECRET_KEY=your_secret_key
   DATABASE_URL=mysql://user:password@localhost:3306/vidsprout
   REDIS_URL=redis://localhost:6379/0
   ```
3. **初始化数据库**
   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```
4. **启动服务**
   ```bash
   # 启动 Django API
   python manage.py runserver
   
   # 启动 Celery Worker (转码任务)
   celery -A core worker -l info
   ```

## 开发规约
- **代码风格**: 遵循 PEP8，使用 `isort` 和 `black` 进行格式化。
- **接口文档**: 访问 `/api/docs/` 查看 Swagger/Redoc 自动生成的文档。
- **测试**: 运行 `python manage.py test` 确保核心逻辑覆盖。

---
*VidSprout - 开源视频平台的未来*
