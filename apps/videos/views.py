"""Videos 视图模块。

用于实现视频相关的 API 视图，例如视频上传、列表、详情、播放记录等。
可结合 DRF 的 APIView/ViewSet 来定义接口，并在 urls 中进行路由绑定。
"""

from __future__ import annotations
import os
import uuid
import json
import math
import subprocess
import mimetypes
try:
    import filetype as _filetype
except Exception:
    _filetype = None
try:
    import magic as _magic  # optional deeper mime check
except Exception:
    _magic = None

from django.conf import settings
from django.core.files.storage import default_storage
from django.shortcuts import get_object_or_404
from django.utils import timezone
import threading
import shutil

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError, PermissionDenied, NotAuthenticated, NotFound
from rest_framework.parsers import MultiPartParser, FormParser
from backend.common.pagination import StandardResultsSetPagination

from .models import Video, VideoTag
from apps.interactions.models import Like, Favorite
from apps.videos.models import WatchLater
from apps.content.models import Tag, Category
from apps.tasks.tasks import generate_vtt_and_thumbnail, transcode_video_to_hls
from django.db.models import Count, Q
from django.contrib.postgres.search import TrigramSimilarity
try:
    from PIL import Image as _PIL_Image  # optional, for image validation
except Exception:
    _PIL_Image = None


def _is_owner_or_admin(video: Video, viewer) -> bool:
    try:
        if not viewer or not getattr(viewer, 'id', None):
            return False
        if getattr(viewer, 'is_staff', False):
            return True
        return str(viewer.id) == str(getattr(video, 'user_id', None))
    except Exception:
        return False


def _can_view_video(video: Video, viewer) -> bool:
    try:
        st = getattr(video, 'status', 'draft')
        if st == 'banned':
            return bool(viewer and getattr(viewer, 'is_staff', False))
        if st != 'published' and not _is_owner_or_admin(video, viewer):
            return False
        vis = getattr(video, 'visibility', 'public')
        if vis == 'private' and not _is_owner_or_admin(video, viewer):
            return False
        return True
    except Exception:
        return False


def _can_edit_video(video: Video, viewer) -> bool:
    if not _is_owner_or_admin(video, viewer):
        return False
    try:
        if getattr(video, 'status', '') == 'banned' and not getattr(viewer, 'is_staff', False):
            return False
    except Exception:
        return False
    return True


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _probe_video(file_path: str) -> tuple[int, int, int]:
    """使用 ffprobe 获取 width,height,duration(秒)。任一失败时返回 0。"""
    try:
        cmd = [
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height:format=duration',
            '-of', 'json', file_path,
        ]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True, timeout=15)
        data = json.loads(p.stdout or '{}')
        width = int((data.get('streams') or [{}])[0].get('width') or 0)
        height = int((data.get('streams') or [{}])[0].get('height') or 0)
        duration = data.get('format', {}).get('duration')
        dur = int(math.floor(float(duration))) if duration is not None else 0
        return width, height, max(0, dur)
    except Exception:
        return 0, 0, 0


def _make_thumbnail(file_path: str, out_path: str, ts_sec: int | None = None) -> bool:
    """用 ffmpeg 从指定时间截取一帧生成缩略图。"""
    try:
        _ensure_dir(os.path.dirname(out_path))
        seek = max(1, int(ts_sec or 1))
        cmd = [
            'ffmpeg', '-y', '-ss', str(seek), '-i', file_path,
            '-frames:v', '1', '-vf', 'scale=480:-1', out_path,
        ]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=15)
        return p.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False


def _assert_video_file(path: str, name: str = '') -> None:
    """深度校验文件是否为视频：magic + filetype 双保险。"""
    # magic number
    if _magic is not None:
        try:
            mime = _magic.from_file(path, mime=True)
            if not (mime or '').startswith('video/'):
                raise ValidationError({'file': '文件类型不正确'})
        except ValidationError:
            raise
        except Exception:
            pass
    # python-filetype
    try:
        if _filetype:
            ft = _filetype.guess_file(path)
            if ft and not str(getattr(ft, 'mime', '') or '').startswith('video/'):
                raise ValidationError({'file': '文件类型不正确'})
    except ValidationError:
        raise
    except Exception:
        pass


def _format_ts(seconds: float) -> str:
    try:
        s = max(0.0, float(seconds or 0.0))
    except Exception:
        s = 0.0
    msec = int(round((s - int(s)) * 1000))
    si = int(s)
    h = si // 3600
    m = (si % 3600) // 60
    sec = si % 60
    return f"{h:02d}:{m:02d}:{sec:02d}.{msec:03d}"


def _build_media_url(base: str, media: str, rel: str) -> str:
    if media.startswith('http://') or media.startswith('https://'):
        return f"{media}/{rel}"
    return f"{base}{media}/{rel}" if media.startswith('/') else f"{base}/{media}/{rel}"


def _make_vtt_thumbnails(file_path: str, vid_key: str, duration: int, base: str, media: str,
                          thumb_w: int = 160, max_thumbs: int = 100) -> str | None:
    """生成按固定间隔抽帧的缩略图合集（独立图片）与 VTT 文件，返回 VTT 的相对路径。

    目录结构：
      - 图片：videos/thumbs/{vid_key}_vtt/thumb_0001.jpg
      - VTT： videos/thumbs/{vid_key}.vtt
    VTT 内使用绝对 URL，便于前端直接请求。
    """
    try:
        # 控制生成的缩略图数量上限
        interval = max(1, int(math.ceil((duration or 0) / max_thumbs))) if duration else 5
        frames_rel_dir = f"videos/thumbs/{vid_key}_vtt"
        frames_dir = os.path.join(settings.MEDIA_ROOT, frames_rel_dir)
        _ensure_dir(frames_dir)
        pattern = os.path.join(frames_dir, 'thumb_%04d.jpg')
        cmd = ['ffmpeg', '-y', '-i', file_path, '-vf', f"fps=1/{interval},scale={thumb_w}:-1", pattern]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120)

        files = sorted([f for f in os.listdir(frames_dir) if f.startswith('thumb_') and f.endswith('.jpg')])
        if not files:
            return None
        vtt_rel = f"videos/thumbs/{vid_key}.vtt"
        vtt_abs = os.path.join(settings.MEDIA_ROOT, vtt_rel)
        with open(vtt_abs, 'w', encoding='utf-8') as f:
            f.write("WEBVTT\n\n")
            for idx, name in enumerate(files):
                start = idx * interval
                end = min((idx + 1) * interval, duration or ((idx + 1) * interval))
                f.write(f"{_format_ts(start)} --> {_format_ts(end)}\n")
                url = _build_media_url(base, media, f"{frames_rel_dir}/{name}")
                f.write(f"{url}\n\n")
        return vtt_rel
    except Exception:
        return None


def _hls_output_paths(vid_hex: str) -> tuple[str, str]:
    base_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'hls', vid_hex)
    master_rel = f"videos/hls/{vid_hex}/master.m3u8"
    return base_dir, master_rel


def _start_hls_transcode(src_abs: str, vid_hex: str, width: int, height: int) -> None:
    def worker():
        try:
            out_dir, master_rel = _hls_output_paths(vid_hex)
            os.makedirs(out_dir, exist_ok=True)
            profiles = []
            if height >= 700:
                profiles.append({'name': '720p', 'h': 720, 'br': '2500k', 'buf': '5000k'})
            profiles.append({'name': '480p', 'h': 480, 'br': '1200k', 'buf': '2400k'})
            entries = []
            for p in profiles:
                sub = os.path.join(out_dir, p['name'])
                os.makedirs(sub, exist_ok=True)
                seg = os.path.join(sub, f"{p['name']}_%03d.ts")
                m3u8 = os.path.join(sub, 'index.m3u8')
                cmd = [
                    'ffmpeg', '-y', '-i', src_abs,
                    '-vf', f"scale=-2:{p['h']}:flags=lanczos:force_original_aspect_ratio=decrease",
                    '-c:v', 'libx264', '-preset', 'veryfast', '-b:v', p['br'], '-bufsize', p['buf'],
                    '-c:a', 'aac', '-ar', '48000', '-b:a', '128k',
                    '-hls_time', '6', '-hls_playlist_type', 'vod',
                    '-hls_segment_filename', seg,
                    m3u8,
                ]
                subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                entries.append((p['name'], p['h']))
            base = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
            media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
            def rel(path: str) -> str:
                return os.path.relpath(path, settings.MEDIA_ROOT).replace('\\', '/')
            master_abs = os.path.join(settings.MEDIA_ROOT, master_rel)
            with open(master_abs, 'w', encoding='utf-8') as f:
                f.write('#EXTM3U\n')
                f.write('#EXT-X-VERSION:3\n')
                for name, h in entries:
                    bw = 2500000 if h >= 700 else 1200000
                    uri = _build_media_url(base or '', media, rel(os.path.join(out_dir, name, 'index.m3u8')))
                    f.write(f"#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={1280 if h>=700 else 854}x{h}\n")
                    f.write(f"{uri}\n")
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()


class VideoUploadView(APIView):
    """视频上传接口

    - 方法：POST /api/videos/upload/
    - 表单字段：file(或 video)、title、description(可选)
    - 权限：需登录
    - 节流：video_upload
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'video_upload'
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file = request.FILES.get('file') or request.FILES.get('video')
        if not file:
            raise ValidationError({'file': '未收到文件'})
        max_bytes = int(getattr(settings, 'VIDEO_MAX_SIZE_BYTES', 524_288_000))
        if file.size and file.size > max_bytes:
            raise ValidationError({'file': '视频文件过大'})

        # 扩展名与类型校验
        name = file.name or ''
        ext = os.path.splitext(name)[1].lower()
        allowed_exts = {'.mp4', '.mov', '.m4v', '.webm', '.mkv'}
        if not ext or ext not in allowed_exts:
            raise ValidationError({'file': '不支持的文件格式'})

        # 额外的内容类型校验（在有 content_type 时才严格限制）
        ctype = getattr(file, 'content_type', None) or mimetypes.guess_type(name)[0] or ''
        if ctype and not ctype.startswith('video/'):
            raise ValidationError({'file': '文件类型不正确'})

        vid = uuid.uuid4().hex
        videos_dir = os.path.join(settings.MEDIA_ROOT, 'videos')
        thumbs_dir = os.path.join(videos_dir, 'thumbs')
        _ensure_dir(videos_dir)
        _ensure_dir(thumbs_dir)

        video_rel = f"videos/{vid}{ext}"
        video_abs = os.path.join(settings.MEDIA_ROOT, f"{video_rel}")
        thumb_rel = f"videos/thumbs/{vid}.jpg"
        thumb_abs = os.path.join(settings.MEDIA_ROOT, f"{thumb_rel}")

        # 保存上传文件（临时文件 + 原子替换）
        tmp_abs = f"{video_abs}.uploading"
        try:
            with open(tmp_abs, 'wb+') as dst:
                for chunk in file.chunks():
                    dst.write(chunk)
            _assert_video_file(tmp_abs, name)
            os.replace(tmp_abs, video_abs)
        finally:
            if os.path.exists(tmp_abs):
                try:
                    os.remove(tmp_abs)
                except Exception:
                    pass

        # 元数据探测与缩略图
        width, height, duration = 0, 0, 0
        thumb_exists = False

        # 创建视频记录（先直接发布，后续可接入转码任务）
        v = Video.objects.create(
            title=(request.data.get('title') or os.path.splitext(name)[0] or '未命名视频')[:200],
            description=request.data.get('description') or '',
            video_file=video_rel[:100],
            thumbnail=thumb_rel[:100] if thumb_exists else None,
            video_file_f=video_rel[:200],
            thumbnail_f=(thumb_rel[:200] if thumb_exists else None),
            duration=duration or 0,
            width=width or 0,
            height=height or 0,
            file_size=int(file.size or 0),
            status='processing',
            upload_status='completed',
            user=request.user,
            published_at=None,
        )

        base = (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
        t1 = generate_vtt_and_thumbnail.delay(str(v.id))
        t2 = transcode_video_to_hls.delay(str(v.id))
        return Response({
            'status': 'processing',
            'id': str(v.id),
            'task_ids': [t1.id, t2.id],
            'video_url': (f"{media}/{video_rel}" if media.startswith('http://') or media.startswith('https://') else (f"{base}{media}/{video_rel}" if media.startswith('/') else f"{base}/{media}/{video_rel}")),
            'thumbnail_url': ((f"{media}/{thumb_rel}" if media.startswith('http://') or media.startswith('https://') else (f"{base}{media}/{thumb_rel}" if media.startswith('/') else f"{base}/{media}/{thumb_rel}"))) if thumb_exists else None,
            'duration': v.duration,
            'width': v.width,
            'height': v.height,
        }, status=status.HTTP_202_ACCEPTED)


from drf_spectacular.utils import extend_schema, extend_schema_view
from .serializers import VideoListSerializer, VideoDetailSerializer

@extend_schema_view(
    get=extend_schema(responses={200: VideoListSerializer(many=True)}),
)
class VideoListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        # base/media for URL building
        base = (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
        # build queryset with proper joins and projected fields
        # NOTE: 不在此处先过滤 status，便于作者查看自己的未发布视频
        qs = Video.objects.all().select_related('user', 'category').prefetch_related('video_tags__tag')
        viewer = request.user if (request.user and request.user.is_authenticated) else None
        uid = request.query_params.get('user_id')
        if uid:
            # 统一转为字符串比较
            is_self = False
            if viewer and hasattr(viewer, 'id'):
                is_self = (str(viewer.id) == str(uid))
            
            is_staff = bool(viewer and getattr(viewer, 'is_staff', False))

            if is_self or is_staff:
                # 自己或管理员：展示该作者的全部视频（含未发布）
                qs = qs.filter(user_id=uid)
                if not is_staff:
                    qs = qs.exclude(status='banned')
            else:
                # 访问他人主页：仅展示已发布且公开的视频
                qs = qs.filter(user_id=uid, status='published', visibility='public')
        else:
            # 公共列表：仅展示已发布且公开的视频
            qs = qs.filter(status='published', visibility='public')
        # category filter
        cid = (request.query_params.get('category_id') or '').strip()
        if cid:
            qs = qs.filter(category_id=cid)
        # tags filter: support tag_id and tag_ids (comma separated)
        tid_one = (request.query_params.get('tag_id') or '').strip()
        tids_param = (request.query_params.get('tag_ids') or '').strip()
        tag_ids = []
        if tid_one:
            tag_ids.append(tid_one)
        if tids_param:
            try:
                tag_ids.extend([s.strip() for s in tids_param.split(',') if s and s.strip()])
            except Exception:
                pass
        tag_ids = [t for t in tag_ids if t]
        if tag_ids:
            match = (request.query_params.get('tag_match') or 'any').strip().lower()
            if match == 'all':
                for t in tag_ids:
                    qs = qs.filter(video_tags__tag_id=t)
            else:
                qs = qs.filter(video_tags__tag_id__in=tag_ids)
            try:
                qs = qs.distinct()
            except Exception:
                pass
        # keyword search (default: title only). Use ?in=all/desc to include description
        q = (request.query_params.get('q') or '').strip()
        if q:
            scope = (request.query_params.get('in') or '').strip().lower()
            if scope in ('all', 'desc', 'description'):
                qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
            else:
                qs = qs.filter(title__icontains=q)
        order = (request.query_params.get('order') or 'latest').lower()
        if order == 'hot':
            qs = qs.order_by('-like_count', '-view_count', '-published_at', '-created_at')
        elif order == 'relevance' and q:
            scope2 = (request.query_params.get('in') or '').strip().lower()
            if scope2 in ('all', 'desc', 'description'):
                qs = qs.annotate(sim=TrigramSimilarity('title', q) + 0.5 * TrigramSimilarity('description', q)).order_by('-sim', '-published_at', '-created_at')
            else:
                qs = qs.annotate(sim=TrigramSimilarity('title', q)).order_by('-sim', '-published_at', '-created_at')
        else:
            qs = qs.order_by('-published_at', '-created_at')
        # 不使用 .only()，避免延迟字段在序列化过程中触发异常或额外查询引发 500
        p = StandardResultsSetPagination()
        page_qs = p.paginate_queryset(qs, request, view=self)
        items = list(page_qs)
        total = p.page.paginator.count
        # helper to build abs/rel url
        def url_of(rel: str) -> str:
            if media.startswith('http://') or media.startswith('https://'):
                return f"{media}/{rel}"
            return f"{base}{media}/{rel}" if media.startswith('/') else f"{base}/{media}/{rel}"
        def to_url(rel: str):
            if not rel:
                return None
            try:
                u = default_storage.url(rel)
            except Exception:
                u = None
            if u and (u.startswith('http://') or u.startswith('https://')):
                return u
            return url_of(rel)
        def safe_exists(rel: str) -> bool:
            try:
                return bool(rel) and default_storage.exists(rel)
            except Exception:
                return False

        # 当前用户已点赞/已收藏（批量查询，未登录则为空）
        liked_ids = set()
        favorited_ids = set()
        fav_count_map = {}
        vid_ids = [v.id for v in items]
        if vid_ids:
            # 收藏数批量统计
            try:
                fav_counts = Favorite.objects.filter(video_id__in=vid_ids).values('video_id').annotate(c=Count('id'))
                fav_count_map = {str(r['video_id']): int(r['c'] or 0) for r in fav_counts}
            except Exception:
                fav_count_map = {}
        if getattr(request, 'user', None) and getattr(request.user, 'id', None):
            if vid_ids:
                liked_ids = set(str(x) for x in Like.objects.filter(user=request.user, video_id__in=vid_ids).values_list('video_id', flat=True))
                favorited_ids = set(str(x) for x in Favorite.objects.filter(user=request.user, video_id__in=vid_ids).values_list('video_id', flat=True))
        data = []
        for v in items:
            # tags
            tags = []
            try:
                for vt in list(getattr(v, 'video_tags').all()):
                    t = getattr(vt, 'tag', None)
                    if t:
                        tags.append({'id': str(t.id), 'name': t.name})
            except Exception:
                tags = []
            data.append({
                'id': str(v.id),
                'title': v.title,
                'status': getattr(v, 'status', None),
                'transcode_error': getattr(v, 'transcode_error', None),
                'duration': v.duration,
                'width': v.width,
                'height': v.height,
                'video_url': (lambda r: (to_url(r) if safe_exists(r) else None))((getattr(v.video_file_f, 'name', None) or v.video_file or '')),
                'low_mp4_url': (lambda r: (to_url(r) if safe_exists(r) else None))(getattr(v, 'low_mp4', None)),
                'thumbnail_url': (lambda t: (to_url(t) if t else None))((getattr(v.thumbnail_f, 'name', None) or v.thumbnail)),
                'view_count': v.view_count,
                'comment_count': v.comment_count,
                'like_count': v.like_count,
                'favorite_count': fav_count_map.get(str(v.id), 0),
                'thumbnail_vtt_url': (lambda key: (to_url(f"videos/thumbs/{key}.vtt") if safe_exists(f"videos/thumbs/{key}.vtt") else None))(os.path.splitext(os.path.basename((getattr(v.video_file_f, 'name', None) or v.video_file or '')))[0]),
                'hls_master_url': (lambda key: (to_url(f"videos/hls/{key}/master.m3u8") if safe_exists(f"videos/hls/{key}/master.m3u8") else None))(os.path.splitext(os.path.basename((getattr(v.video_file_f, 'name', None) or v.video_file or '')))[0]),
                'author': {
                    'id': str(getattr(v.user, 'id', '') or v.user_id),
                    'name': getattr(v.user, 'display_name', None) or getattr(v.user, 'username', ''),
                    'username': getattr(v.user, 'username', ''),
                    'avatar_url': (lambda pp: (
                        pp if str(pp).startswith(('http://', 'https://')) else (
                            f"{base}{pp}" if str(pp).startswith('/') else (url_of(str(pp)) if pp else None)
                        )
                    ))((getattr(getattr(v.user, 'profile_picture_f', None), 'name', None) or getattr(v.user, 'profile_picture', None))),
                },
                'category': ({'id': str(v.category.id), 'name': v.category.name} if getattr(v, 'category', None) else None),
                'tags': tags,
                'published_at': v.published_at,
                'liked': (str(v.id) in liked_ids),
                'favorited': (str(v.id) in favorited_ids),
            })
        return Response(p.format(data, total))


@extend_schema_view(
    get=extend_schema(responses={200: VideoDetailSerializer}),
)
class VideoDetailView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, pk):
        v = get_object_or_404(Video, pk=pk)
        viewer = request.user if (request.user and request.user.is_authenticated) else None
        if not _can_view_video(v, viewer):
            raise NotFound('资源不存在')
        can_edit = _can_edit_video(v, viewer)
        base = (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
        def url_of(rel: str) -> str:
            if media.startswith('http://') or media.startswith('https://'):
                return f"{media}/{rel}"
            return f"{base}{media}/{rel}" if media.startswith('/') else f"{base}/{media}/{rel}"
        def to_url(rel: str):
            if not rel:
                return None
            try:
                u = default_storage.url(rel)
            except Exception:
                u = None
            if u and (u.startswith('http://') or u.startswith('https://')):
                return u
            return url_of(rel)

        vtt_rel = f"videos/thumbs/{os.path.splitext(os.path.basename((getattr(v.video_file_f, 'name', None) or v.video_file or '')))[0]}.vtt"
        vtt_abs = os.path.join(settings.MEDIA_ROOT, vtt_rel)
        master_rel = f"videos/hls/{os.path.splitext(os.path.basename((getattr(v.video_file_f, 'name', None) or v.video_file or '')))[0]}/master.m3u8"
        master_abs = os.path.join(settings.MEDIA_ROOT, master_rel)
        # 当前用户已点赞/已收藏/已加入稍后看（未登录则为 False）
        liked = False
        favorited = False
        watch_later = False
        fav_count = 0
        if getattr(request, 'user', None) and getattr(request.user, 'id', None):
            try:
                liked = Like.objects.filter(user=request.user, video=v).exists()
                favorited = Favorite.objects.filter(user=request.user, video=v).exists()
                watch_later = WatchLater.objects.filter(user=request.user, video=v).exists()
            except Exception:
                liked = False; favorited = False; watch_later = False
        try:
            fav_count = Favorite.objects.filter(video=v).count()
        except Exception:
            fav_count = 0
        # collect tags for this video
        tags = []
        try:
            for vt in list(getattr(v, 'video_tags').select_related('tag').all()):
                t = getattr(vt, 'tag', None)
                if t:
                    tags.append({'id': str(t.id), 'name': t.name})
        except Exception:
            tags = []
        return Response({
            'id': str(v.id),
            'title': v.title,
            'status': getattr(v, 'status', None),
            'transcode_error': getattr(v, 'transcode_error', None),
            'description': v.description,
            'duration': v.duration,
            'width': v.width,
            'height': v.height,
            'allow_comments': bool(getattr(v, 'allow_comments', True)),
            'allow_download': bool(getattr(v, 'allow_download', False)),
            'visibility': getattr(v, 'visibility', 'public'),
            'owner_id': str(v.user_id),
            'can_edit': bool(can_edit),
            'video_url': (lambda r: (to_url(r) if (r and default_storage.exists(r)) else None))((getattr(v.video_file_f, 'name', None) or v.video_file or '')),
            'low_mp4_url': (lambda r: (to_url(r) if (r and default_storage.exists(r)) else None))(getattr(v, 'low_mp4', None)),
            'thumbnail_url': (lambda t: (to_url(t) if t else None))((getattr(v.thumbnail_f, 'name', None) or v.thumbnail)),
            'view_count': v.view_count,
            'comment_count': v.comment_count,
            'like_count': v.like_count,
            'favorite_count': int(fav_count),
            'created_at': v.created_at,
            'published_at': v.published_at,
            'thumbnail_vtt_url': (to_url(vtt_rel) if default_storage.exists(vtt_rel) else None),
            'hls_master_url': (to_url(master_rel) if default_storage.exists(master_rel) else None),
            'author': {
                'id': str(getattr(v.user, 'id', '') or v.user_id),
                'name': getattr(v.user, 'display_name', None) or getattr(v.user, 'username', ''),
                'username': getattr(v.user, 'username', ''),
                'avatar_url': (lambda pp: (
                    pp if str(pp).startswith(('http://', 'https://')) else (
                        f"{base}{pp}" if str(pp).startswith('/') else (url_of(str(pp)) if pp else None)
                    )
                ))((getattr(getattr(v.user, 'profile_picture_f', None), 'name', None) or getattr(v.user, 'profile_picture', None))),
            },
            'liked': bool(liked),
            'favorited': bool(favorited),
            'watch_later': bool(watch_later),
            'category': ({'id': str(v.category.id), 'name': v.category.name} if getattr(v, 'category', None) else None),
            'tags': tags,
        })

    def patch(self, request, pk):
        v = get_object_or_404(Video, pk=pk)
        user = getattr(request, 'user', None)
        if not (user and getattr(user, 'id', None)):
            raise NotAuthenticated('未登录')
        if not _can_edit_video(v, user):
            raise PermissionDenied('无权编辑该视频')
        data = request.data or {}
        title = data.get('title')
        description = data.get('description')
        allow_comments = data.get('allow_comments')
        allow_download = data.get('allow_download')
        visibility = data.get('visibility')
        category_id = data.get('category_id') if 'category_id' in data else None
        tag_ids = data.get('tag_ids') if 'tag_ids' in data else None
        updated = False
        tags_changed = False
        if isinstance(title, str):
            v.title = (title or '').strip()[:200]
            updated = True
        if isinstance(description, str):
            v.description = (description or '').strip()[:500]
            updated = True
        if isinstance(allow_comments, bool):
            try:
                v.allow_comments = allow_comments
                updated = True
            except Exception:
                pass
        if isinstance(allow_download, bool):
            try:
                v.allow_download = allow_download
                updated = True
            except Exception:
                pass
        if isinstance(visibility, str) and visibility in {'public','unlisted','private'}:
            try:
                v.visibility = visibility
                updated = True
            except Exception:
                pass
        # category update
        if 'category_id' in data:
            cid = str(category_id).strip() if category_id is not None else ''
            if cid in ('', 'null'):
                v.category = None
                updated = True
            else:
                try:
                    c = Category.objects.get(pk=cid)
                except Category.DoesNotExist:
                    raise ValidationError({'category_id': '分类不存在'})
                v.category = c
                updated = True
        # tags update
        if 'tag_ids' in data:
            if tag_ids is None:
                pass
            elif not isinstance(tag_ids, list):
                raise ValidationError({'tag_ids': '必须为数组'})
            else:
                ids = [str(i) for i in tag_ids if str(i)]
                # limit: max 3 tags for a video
                if len(ids) > 3:
                    raise ValidationError({'tag_ids': '最多选择 3 个标签'})
                exist_ids = set(str(tid) for tid in Tag.objects.filter(id__in=ids).values_list('id', flat=True))
                cur_ids = set(str(tid) for tid in v.video_tags.values_list('tag_id', flat=True))
                add_ids = list(exist_ids - cur_ids)
                del_ids = list(cur_ids - exist_ids)
                if add_ids:
                    VideoTag.objects.bulk_create([VideoTag(video=v, tag_id=tid) for tid in add_ids], ignore_conflicts=True)
                    tags_changed = True
                if del_ids:
                    VideoTag.objects.filter(video=v, tag_id__in=del_ids).delete()
                    tags_changed = True
        if updated:
            fields = ['updated_at']
            if isinstance(title, str):
                fields.append('title')
            if isinstance(description, str):
                fields.append('description')
            if isinstance(allow_comments, bool):
                fields.append('allow_comments')
            if isinstance(allow_download, bool):
                fields.append('allow_download')
            if isinstance(visibility, str) and visibility in {'public','unlisted','private'}:
                fields.append('visibility')
            if 'category_id' in data:
                fields.append('category')
            # 去重
            v.save(update_fields=list(dict.fromkeys(fields)))

        # 复用 GET 的返回结构
        base = (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
        def url_of(rel: str) -> str:
            if media.startswith('http://') or media.startswith('https://'):
                return f"{media}/{rel}"
            return f"{base}{media}/{rel}" if media.startswith('/') else f"{base}/{media}/{rel}"
        def to_url(rel: str):
            if not rel:
                return None
            try:
                u = default_storage.url(rel)
            except Exception:
                u = None
            if u and (u.startswith('http://') or u.startswith('https://')):
                return u
            return url_of(rel)

        key = os.path.splitext(os.path.basename((getattr(v.video_file_f, 'name', None) or v.video_file or '')))[0]
        vtt_rel = f"videos/thumbs/{key}.vtt"
        vtt_abs = os.path.join(settings.MEDIA_ROOT, vtt_rel)
        master_rel = f"videos/hls/{key}/master.m3u8"
        master_abs = os.path.join(settings.MEDIA_ROOT, master_rel)
        liked = False
        favorited = False
        # 当前方法已校验作者身份，因此可直接标记为可编辑
        can_edit = True
        try:
            from apps.interactions.models import Favorite
            fav_count = Favorite.objects.filter(video=v).count()
        except Exception:
            fav_count = 0
        # collect tags for output
        tags_out = []
        try:
            for vt in list(getattr(v, 'video_tags').select_related('tag').all()):
                t = getattr(vt, 'tag', None)
                if t:
                    tags_out.append({'id': str(t.id), 'name': t.name})
        except Exception:
            tags_out = []
        return Response({
            'id': str(v.id),
            'title': v.title,
            'description': v.description,
            'duration': v.duration,
            'width': v.width,
            'height': v.height,
            'allow_comments': bool(getattr(v, 'allow_comments', True)),
            'allow_download': bool(getattr(v, 'allow_download', False)),
            'visibility': getattr(v, 'visibility', 'public'),
            'owner_id': str(v.user_id),
            'can_edit': bool(can_edit),
            'video_url': (lambda r: (to_url(r) if (r and default_storage.exists(r)) else None))((getattr(v.video_file_f, 'name', None) or v.video_file or '')),
            'thumbnail_url': (lambda t: (to_url(t) if t else None))((getattr(v.thumbnail_f, 'name', None) or v.thumbnail)),
            'view_count': v.view_count,
            'comment_count': v.comment_count,
            'like_count': v.like_count,
            'favorite_count': int(fav_count),
            'created_at': v.created_at,
            'published_at': v.published_at,
            'thumbnail_vtt_url': (to_url(vtt_rel) if default_storage.exists(vtt_rel) else None),
            'hls_master_url': (to_url(master_rel) if default_storage.exists(master_rel) else None),
            'author': {
                'id': str(getattr(v.user, 'id', '') or v.user_id),
                'name': getattr(v.user, 'display_name', None) or getattr(v.user, 'username', ''),
                'username': getattr(v.user, 'username', ''),
                'avatar_url': (lambda pp: (
                    pp if str(pp).startswith(('http://', 'https://')) else (
                        f"{base}{pp}" if str(pp).startswith('/') else (url_of(str(pp)) if pp else None)
                    )
                ))(getattr(v.user, 'profile_picture', None)),
            },
            'liked': bool(liked),
            'favorited': bool(favorited),
            'category': ({'id': str(v.category.id), 'name': v.category.name} if getattr(v, 'category', None) else None),
            'tags': tags_out,
        })


class VideoBulkDeleteView(APIView):
    """批量删除我自己的视频

    - 方法：POST /api/videos/bulk-delete/
    - 参数：video_ids: [<uuid>, ...] 或 ids: [<uuid>, ...]
    - 权限：需登录，只能删除属于自己的视频；一次最多 200 个
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ids = request.data.get('video_ids') or request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'video_ids': '必须为非空数组'})
        ids = [str(i) for i in ids if str(i)]
        if len(ids) > 200:
            raise ValidationError({'video_ids': '一次最多处理 200 个'})
        qs = Video.objects.filter(user=request.user, id__in=ids)
        if not getattr(request.user, 'is_staff', False):
            qs = qs.exclude(status='banned')
        removed, _ = qs.delete()
        return Response({'removed': int(removed)})


class VideoThumbnailPickView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        v = get_object_or_404(Video, pk=pk)
        if not _can_edit_video(v, request.user):
            raise PermissionDenied('无权编辑该视频')
        # parse ts seconds
        raw = request.data.get('ts') if isinstance(request.data, dict) else None
        if raw is None:
            raw = request.data.get('time') if isinstance(request.data, dict) else None
        try:
            ts = float(raw)
        except Exception:
            ts = 1.0
        ts = max(0.0, ts)
        # build paths
        base = (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
        def url_of(rel: str) -> str:
            if media.startswith('http://') or media.startswith('https://'):
                return f"{media}/{rel}"
            return f"{base}{media}/{rel}" if media.startswith('/') else f"{base}/{media}/{rel}"
        vid_key = os.path.splitext(os.path.basename((getattr(v.video_file_f, 'name', None) or v.video_file or '')))[0]
        src_rel = getattr(v.video_file_f, 'name', None) or v.video_file
        src_abs = os.path.join(settings.MEDIA_ROOT, src_rel) if src_rel else ''
        thumb_rel = f"videos/thumbs/{vid_key}.jpg"
        thumb_abs = os.path.join(settings.MEDIA_ROOT, thumb_rel)
        ok = False
        try:
            ok = _make_thumbnail(src_abs, thumb_abs, int(ts))
        except Exception:
            ok = False
        if not ok:
            raise ValidationError({'detail': '生成封面失败'})
        # update model
        try:
            v.thumbnail = thumb_rel[:100]
            v.thumbnail_f = thumb_rel[:200]
            v.save(update_fields=['thumbnail', 'thumbnail_f', 'updated_at'])
        except Exception:
            pass
        return Response({'thumbnail_url': url_of(thumb_rel)})


class VideoRetryTranscodeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        v = get_object_or_404(Video, pk=pk)
        viewer = request.user
        if not _can_edit_video(v, viewer):
            raise PermissionDenied('无权操作')
        # 仅允许处理已上传完成的视频
        if not v.video_file:
            raise ValidationError({'detail': '视频文件缺失，无法重试'})
        # 清理状态与错误信息
        v.status = 'processing'
        v.transcode_error = None
        try:
            v.save(update_fields=['status', 'transcode_error', 'updated_at'])
        except Exception:
            v.save()
        # 触发转码与缩略图任务
        t1 = generate_vtt_and_thumbnail.delay(str(v.id))
        t2 = transcode_video_to_hls.delay(str(v.id))
        return Response({'status': 'processing', 'id': str(v.id), 'task_ids': [t1.id, t2.id]})


class VideoThumbnailUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        v = get_object_or_404(Video, pk=pk)
        if not _can_edit_video(v, request.user):
            raise PermissionDenied('无权编辑该视频')
        f = request.FILES.get('file') or request.FILES.get('image')
        if not f:
            raise ValidationError({'file': '未收到文件'})
        # basic checks
        name = f.name or 'cover'
        ext = os.path.splitext(name)[1].lower() or '.jpg'
        if ext not in {'.jpg', '.jpeg', '.png', '.webp'}:
            raise ValidationError({'file': '不支持的图片格式'})
        max_bytes = int(getattr(settings, 'THUMBNAIL_MAX_SIZE_BYTES', 5 * 1024 * 1024))
        if f.size and int(f.size) > max_bytes:
            raise ValidationError({'file': '图片过大'})
        vid_key = os.path.splitext(os.path.basename((getattr(v.video_file_f, 'name', None) or v.video_file or '')))[0]
        thumb_rel = f"videos/thumbs/{vid_key}_custom{ext}"
        thumb_abs = os.path.join(settings.MEDIA_ROOT, thumb_rel)
        # ensure dir
        os.makedirs(os.path.dirname(thumb_abs), exist_ok=True)
        # write file
        with open(thumb_abs, 'wb+') as dst:
            for chunk in f.chunks():
                dst.write(chunk)
        # validate dimensions/aspect ratio if Pillow available
        try:
            if _PIL_Image is not None:
                im = _PIL_Image.open(thumb_abs)
                w, h = im.size
                # minimal dimensions and 16:9 ratio tolerance
                min_w = int(getattr(settings, 'THUMBNAIL_MIN_WIDTH', 480))
                min_h = int(getattr(settings, 'THUMBNAIL_MIN_HEIGHT', 270))
                if w < min_w or h < min_h:
                    try: os.remove(thumb_abs)
                    except Exception: pass
                    raise ValidationError({'file': f'图片分辨率过低（至少 {min_w}x{min_h}）'})
                ratio = w / float(h or 1)
                target = 16.0 / 9.0
                tol = float(getattr(settings, 'THUMBNAIL_RATIO_TOL', 0.04))
                if abs(ratio - target) > tol:
                    try: os.remove(thumb_abs)
                    except Exception: pass
                    raise ValidationError({'file': '图片宽高比例必须接近 16:9'})
        except ValidationError:
            raise
        except Exception:
            # ignore validation if pillow not available or image can't be parsed
            pass
        # update model
        v.thumbnail = thumb_rel[:100]
        v.thumbnail_f = thumb_rel[:200]
        v.save(update_fields=['thumbnail', 'thumbnail_f', 'updated_at'])
        # url
        base = (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
        def url_of(rel: str) -> str:
            if media.startswith('http://') or media.startswith('https://'):
                return f"{media}/{rel}"
            return f"{base}{media}/{rel}" if media.startswith('/') else f"{base}/{media}/{rel}"
        return Response({'thumbnail_url': url_of(thumb_rel)})


class VideoBulkUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ids = request.data.get('video_ids') or request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'video_ids': '必须为非空数组'})
        ids = [str(i) for i in ids if str(i)]
        if len(ids) > 200:
            raise ValidationError({'video_ids': '一次最多处理 200 个'})
        updates = {}
        if 'allow_comments' in request.data and isinstance(request.data.get('allow_comments'), bool):
            updates['allow_comments'] = bool(request.data.get('allow_comments'))
        if 'allow_download' in request.data and isinstance(request.data.get('allow_download'), bool):
            updates['allow_download'] = bool(request.data.get('allow_download'))
        if 'visibility' in request.data:
            vis = str(request.data.get('visibility') or '')
            if vis in {'public','unlisted','private'}:
                updates['visibility'] = vis
            elif vis:
                raise ValidationError({'visibility': '取值无效'})
        if not updates:
            return Response({'updated': 0})
        qs = Video.objects.filter(user=request.user, id__in=ids)
        if not getattr(request.user, 'is_staff', False):
            qs = qs.exclude(status='banned')
        n = qs.update(**updates)
        return Response({'updated': int(n)})


class UploadInitView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'video_upload'

    def post(self, request):
        data = request.data or {}
        filename = str(data.get('filename') or '')
        filesize = int(data.get('filesize') or 0)
        if not filename or filesize <= 0:
            raise ValidationError({'detail': 'filename/filesize 无效'})
        ext = os.path.splitext(filename)[1].lower()
        allowed_exts = {'.mp4', '.mov', '.m4v', '.webm', '.mkv'}
        if ext not in allowed_exts:
            raise ValidationError({'detail': '不支持的文件格式'})
        chunk_size = int(getattr(settings, 'CHUNK_SIZE_BYTES', 5 * 1024 * 1024))
        upload_id = uuid.uuid4().hex
        sess = os.path.join(settings.MEDIA_ROOT, 'uploads', 'sessions', upload_id)
        os.makedirs(os.path.join(sess, 'chunks'), exist_ok=True)
        meta = {'filename': filename, 'filesize': filesize, 'ext': ext, 'chunk_size': chunk_size, 'user_id': str(request.user.id)}
        with open(os.path.join(sess, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f)
        total = int(math.ceil(filesize / float(chunk_size)))
        return Response({'upload_id': upload_id, 'chunk_size': chunk_size, 'total_chunks': total})


class UploadChunkView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'video_upload'
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload_id = str(request.data.get('upload_id') or '')
        index = request.data.get('index')
        try:
            idx = int(index)
        except Exception:
            raise ValidationError({'index': '必须为数字'})
        f = request.FILES.get('chunk')
        if not upload_id or f is None:
            raise ValidationError({'detail': '缺少参数'})
        sess = os.path.join(settings.MEDIA_ROOT, 'uploads', 'sessions', upload_id)
        meta_path = os.path.join(sess, 'meta.json')
        if not os.path.exists(meta_path):
            raise ValidationError({'detail': '会话不存在'})
        os.makedirs(os.path.join(sess, 'chunks'), exist_ok=True)
        out = os.path.join(sess, 'chunks', f'{idx}.part')
        with open(out, 'wb+') as dst:
            for c in f.chunks():
                dst.write(c)
        return Response({'ok': True, 'index': idx, 'size': f.size})


class UploadStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'video_upload'

    def get(self, request):
        upload_id = str(request.query_params.get('id') or '')
        if not upload_id:
            raise ValidationError({'detail': '缺少 id'})
        sess = os.path.join(settings.MEDIA_ROOT, 'uploads', 'sessions', upload_id)
        meta_path = os.path.join(sess, 'meta.json')
        if not os.path.exists(meta_path):
            raise ValidationError({'detail': '会话不存在'})
        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        chunk_dir = os.path.join(sess, 'chunks')
        have = []
        if os.path.isdir(chunk_dir):
            for name in os.listdir(chunk_dir):
                if name.endswith('.part'):
                    try:
                        have.append(int(os.path.splitext(name)[0]))
                    except Exception:
                        pass
        have = sorted(set(have))
        total = int(math.ceil(meta['filesize'] / float(meta['chunk_size'])))
        return Response({'received': have, 'total_chunks': total})


class UploadCompleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'video_upload'

    def post(self, request):
        data = request.data or {}
        upload_id = str(data.get('upload_id') or '')
        title = str(data.get('title') or '')
        description = str(data.get('description') or '')
        if not upload_id:
            raise ValidationError({'detail': '缺少 upload_id'})
        sess = os.path.join(settings.MEDIA_ROOT, 'uploads', 'sessions', upload_id)
        meta_path = os.path.join(sess, 'meta.json')
        if not os.path.exists(meta_path):
            raise ValidationError({'detail': '会话不存在'})
        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        chunk_dir = os.path.join(sess, 'chunks')
        total = int(math.ceil(meta['filesize'] / float(meta['chunk_size'])))
        missing = [i for i in range(total) if not os.path.exists(os.path.join(chunk_dir, f'{i}.part'))]
        if missing:
            return Response({'ok': False, 'missing': missing}, status=400)
        tmp_merged = os.path.join(sess, 'merged.uploading')
        with open(tmp_merged, 'wb') as out:
            for i in range(total):
                part = os.path.join(chunk_dir, f'{i}.part')
                with open(part, 'rb') as src:
                    shutil.copyfileobj(src, out)
        _assert_video_file(tmp_merged, meta['filename'])
        ext = meta['ext']
        vid = uuid.uuid4().hex
        videos_dir = os.path.join(settings.MEDIA_ROOT, 'videos')
        thumbs_dir = os.path.join(videos_dir, 'thumbs')
        _ensure_dir(videos_dir); _ensure_dir(thumbs_dir)
        video_rel = f"videos/{vid}{ext}"
        video_abs = os.path.join(settings.MEDIA_ROOT, video_rel)
        os.replace(tmp_merged, video_abs)
        thumb_rel = f"videos/thumbs/{vid}.jpg"; thumb_abs = os.path.join(settings.MEDIA_ROOT, thumb_rel)
        # 元数据探测与缩略图
        width, height, duration = 0, 0, 0
        thumb_exists = False
        v = Video.objects.create(
            title=(title or os.path.splitext(meta['filename'])[0] or '未命名视频')[:200],
            description=description or '',
            video_file=video_rel[:100],
            thumbnail=thumb_rel[:100] if thumb_exists else None,
            video_file_f=video_rel[:200],
            thumbnail_f=(thumb_rel[:200] if thumb_exists else None),
            duration=duration or 0,
            width=width or 0,
            height=height or 0,
            file_size=int(meta['filesize'] or 0),
            status='processing',
            upload_status='completed',
            user=request.user,
        )
        try:
            shutil.rmtree(sess)
        except Exception:
            pass
        base = (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
        t1 = generate_vtt_and_thumbnail.delay(str(v.id))
        t2 = transcode_video_to_hls.delay(str(v.id))
        return Response({
            'status': 'processing',
            'id': str(v.id),
            'task_ids': [t1.id, t2.id],
            'video_url': (f"{media}/{video_rel}" if media.startswith('http://') or media.startswith('https://') else (f"{base}{media}/{video_rel}" if media.startswith('/') else f"{base}/{media}/{video_rel}")),
            'thumbnail_url': ((f"{media}/{thumb_rel}" if media.startswith('http://') or media.startswith('https://') else (f"{base}{media}/{thumb_rel}" if media.startswith('/') else f"{base}/{media}/{thumb_rel}"))) if thumb_exists else None,
        }, status=status.HTTP_202_ACCEPTED)
