from __future__ import annotations
import os
import json
import math
import subprocess
from typing import Optional

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.videos.models import Video
from apps.configs.utils import get_system_setting


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


def _vid_key_from_rel(rel: str) -> str:
    return os.path.splitext(os.path.basename(rel))[0]


def _safe_rm(path: str) -> None:
    try:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path, topdown=False):
                for name in files:
                    try:
                        os.remove(os.path.join(root, name))
                    except Exception:
                        pass
                for name in dirs:
                    try:
                        os.rmdir(os.path.join(root, name))
                    except Exception:
                        pass
            try:
                os.rmdir(path)
            except Exception:
                pass
        elif os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _save_transcode_failure(video: Video, error: str) -> None:
    try:
        fields = ['transcode_error', 'updated_at']
        video.transcode_error = (error or '')[:200]
        if getattr(video, 'status', None) == 'processing':
            video.status = 'draft'
            fields.append('status')
        video.save(update_fields=fields)
    except Exception:
        pass


def _probe_video(file_path: str) -> tuple[int, int, int]:
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


@shared_task(bind=True, name='tasks.generate_vtt_and_thumbnail')
def generate_vtt_and_thumbnail(self, video_id: str) -> dict:
    base = (get_system_setting('SITE_URL', '') or '').rstrip('/')
    media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
    try:
        v = Video.objects.get(pk=video_id)
    except Video.DoesNotExist:
        return {'ok': False, 'error': 'video_not_found'}

    video_rel = str(v.video_file)
    if not video_rel:
        return {'ok': False, 'error': 'video_file_empty'}

    vid_key = _vid_key_from_rel(video_rel)
    video_abs = os.path.join(settings.MEDIA_ROOT, video_rel)
    if not os.path.exists(video_abs):
        return {'ok': False, 'error': 'video_file_missing'}

    try:
        vtt_rel_cleanup = f"videos/thumbs/{vid_key}.vtt"
        vtt_abs_cleanup = os.path.join(settings.MEDIA_ROOT, vtt_rel_cleanup)
        frames_rel_dir_cleanup = f"videos/thumbs/{vid_key}_vtt"
        frames_dir_cleanup = os.path.join(settings.MEDIA_ROOT, frames_rel_dir_cleanup)
        _safe_rm(vtt_abs_cleanup)
        _safe_rm(frames_dir_cleanup)
    except Exception:
        pass

    # Backfill basic metadata if missing
    width = int(getattr(v, 'width', 0) or 0)
    height = int(getattr(v, 'height', 0) or 0)
    duration = int(getattr(v, 'duration', 0) or 0)
    if width <= 0 or height <= 0 or duration <= 0:
        pw, ph, pdur = _probe_video(video_abs)
        width = width or pw
        height = height or ph
        duration = duration or pdur

    # Ensure a primary thumbnail exists and update model if needed
    thumb_rel = f"videos/thumbs/{vid_key}.jpg"
    thumb_abs = os.path.join(settings.MEDIA_ROOT, thumb_rel)
    if not os.path.exists(thumb_abs):
        try:
            seek = max(1, int((duration or 0) // 2) if duration else 1)
            cmd = [
                'ffmpeg', '-y', '-ss', str(seek), '-i', video_abs,
                '-frames:v', '1', '-vf', 'scale=480:-1', thumb_abs,
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=20)
        except Exception:
            pass

    # Persist backfilled fields
    try:
        update_fields = []
        if width and (int(getattr(v, 'width', 0) or 0) != width):
            v.width = width
            update_fields.append('width')
        if height and (int(getattr(v, 'height', 0) or 0) != height):
            v.height = height
            update_fields.append('height')
        if duration and (int(getattr(v, 'duration', 0) or 0) != duration):
            v.duration = duration
            update_fields.append('duration')
        if os.path.exists(thumb_abs) and not getattr(v, 'thumbnail', None):
            # set both char and file path fields if available
            v.thumbnail = thumb_rel[:100]
            try:
                v.thumbnail_f = thumb_rel[:200]
                update_fields.extend(['thumbnail', 'thumbnail_f'])
            except Exception:
                update_fields.append('thumbnail')
        if update_fields:
            try:
                update_fields.append('updated_at')
            except Exception:
                pass
            v.save(update_fields=list(dict.fromkeys(update_fields)))
    except Exception:
        pass

    try:
        thumb_w = 160
        max_thumbs = 100
        interval = max(1, int(math.ceil((duration or 0) / max_thumbs))) if duration else 5
        frames_rel_dir = f"videos/thumbs/{vid_key}_vtt"
        frames_dir = os.path.join(settings.MEDIA_ROOT, frames_rel_dir)
        os.makedirs(frames_dir, exist_ok=True)
        pattern = os.path.join(frames_dir, 'thumb_%04d.jpg')
        cmd = ['ffmpeg', '-y', '-i', video_abs, '-vf', f"fps=1/{interval},scale={thumb_w}:-1", pattern]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120)
        files = sorted([f for f in os.listdir(frames_dir) if f.startswith('thumb_') and f.endswith('.jpg')])
        if not files:
            return {'ok': False, 'error': 'no_frames'}
        vtt_rel = f"videos/thumbs/{vid_key}.vtt"
        vtt_abs = os.path.join(settings.MEDIA_ROOT, vtt_rel)
        with open(vtt_abs, 'w', encoding='utf-8') as f:
            f.write("WEBVTT\n\n")
            for idx, name in enumerate(files):
                start = idx * interval
                end = min((idx + 1) * interval, duration or ((idx + 1) * interval))
                f.write(f"{_format_ts(start)} --> {_format_ts(end)}\n")
                rel_to_vtt = f"{vid_key}_vtt/{name}"
                f.write(f"{rel_to_vtt}\n\n")
        return {'ok': True, 'vtt_rel': vtt_rel, 'meta_updated': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)[:200]}


@shared_task(bind=True, name='tasks.transcode_video_to_hls')
def transcode_video_to_hls(self, video_id: str) -> dict:
    base = (get_system_setting('SITE_URL', '') or '').rstrip('/')
    media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
    try:
        v = Video.objects.get(pk=video_id)
    except Video.DoesNotExist:
        return {'ok': False, 'error': 'video_not_found'}

    video_rel = str(v.video_file)
    if not video_rel:
        return {'ok': False, 'error': 'video_file_empty'}

    vid_key = _vid_key_from_rel(video_rel)
    video_abs = os.path.join(settings.MEDIA_ROOT, video_rel)
    if not os.path.exists(video_abs):
        return {'ok': False, 'error': 'video_file_missing'}

    try:
        out_dir_cleanup = os.path.join(settings.MEDIA_ROOT, 'videos', 'hls', vid_key)
        _safe_rm(out_dir_cleanup)
        low_abs_cleanup = os.path.join(settings.MEDIA_ROOT, 'videos', 'low', f"{vid_key}.mp4")
        _safe_rm(low_abs_cleanup)
    except Exception:
        pass

    width = int(v.width or 0)
    height = int(v.height or 0)
    if width <= 0 or height <= 0:
        width, height, _ = _probe_video(video_abs)

    error = None
    low_rel = None
    try:
        out_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'hls', vid_key)
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
                'ffmpeg', '-y', '-i', video_abs,
                '-vf', f"scale=-2:{p['h']}:flags=lanczos:force_original_aspect_ratio=decrease",
                '-c:v', 'libx264', '-preset', 'veryfast', '-b:v', p['br'], '-bufsize', p['buf'],
                '-c:a', 'aac', '-ar', '48000', '-b:a', '128k',
                '-hls_time', '6', '-hls_playlist_type', 'vod',
                '-hls_segment_filename', seg,
                m3u8,
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=1800)
            if os.path.exists(m3u8):
                entries.append((p['name'], p['h']))
        if not entries:
            error = 'no_variants'
            _save_transcode_failure(v, error)
            return {'ok': False, 'error': error}

        # 生成低清 MP4 供 processing 占位播放
        try:
            low_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'low')
            os.makedirs(low_dir, exist_ok=True)
            low_rel = f"videos/low/{vid_key}.mp4"
            low_abs = os.path.join(settings.MEDIA_ROOT, low_rel)
            cmd_low = [
                'ffmpeg', '-y', '-i', video_abs,
                '-vf', 'scale=-2:360', '-c:v', 'libx264', '-preset', 'superfast', '-crf', '30',
                '-c:a', 'aac', '-ar', '44100', '-b:a', '96k',
                low_abs,
            ]
            subprocess.run(cmd_low, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=900)
            if not os.path.exists(low_abs):
                low_rel = None
        except Exception:
            low_rel = None

        master_rel = f"videos/hls/{vid_key}/master.m3u8"
        master_abs = os.path.join(settings.MEDIA_ROOT, master_rel)
        with open(master_abs, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            f.write('#EXT-X-VERSION:3\n')
            for name, h in entries:
                bw = 2500000 if h >= 700 else 1200000
                # Write variant URI relative to the master playlist
                uri = f"{name}/index.m3u8"
                f.write(f"#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={1280 if h>=700 else 854}x{h}\n")
                f.write(f"{uri}\n")
        # 回写低清/错误清空
        try:
            v.low_mp4 = low_rel if low_rel else None
            v.transcode_error = None
            # 转码完成后不自动发布，但应退出 processing 状态，避免前端一直提示“处理中”
            # 这里将 processing -> draft（仍需管理员审核后再发布）
            fields = ['low_mp4', 'transcode_error', 'updated_at']
            try:
                if getattr(v, 'status', None) == 'processing':
                    v.status = 'draft'
                    fields.append('status')
            except Exception:
                pass
            v.save(update_fields=fields)
        except Exception:
            pass
        return {'ok': True, 'master_rel': master_rel, 'low_mp4': low_rel}
    except Exception as e:
        error = str(e)[:200]
        _save_transcode_failure(v, error)
        return {'ok': False, 'error': error}


@shared_task(name='tasks.cleanup_expired_upload_sessions')
def cleanup_expired_upload_sessions(hours: int = 24) -> dict:
    """
    清理超过指定时间的废弃上传会话（分片上传临时文件）。
    建议通过 Celery Beat 定时调度执行。
    """
    import os
    import shutil
    from datetime import timedelta
    from django.conf import settings
    from django.utils import timezone

    sessions_base = os.path.join(settings.MEDIA_ROOT, 'uploads', 'sessions')
    if not os.path.exists(sessions_base):
        return {'ok': True, 'cleaned': 0, 'freed_bytes': 0, 'reason': 'sessions_dir_not_exists'}

    cutoff_time = timezone.now() - timedelta(hours=hours)
    total_cleaned = 0
    total_size = 0
    errors = []

    for session_id in os.listdir(sessions_base):
        session_path = os.path.join(sessions_base, session_id)
        if not os.path.isdir(session_path):
            continue

        meta_path = os.path.join(session_path, 'meta.json')
        try:
            if os.path.exists(meta_path):
                mtime = os.path.getmtime(meta_path)
                modified_at = timezone.datetime.fromtimestamp(mtime, tz=timezone.utc)
                if modified_at > cutoff_time:
                    continue

            # 计算目录大小
            size = 0
            for dirpath, dirnames, filenames in os.walk(session_path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        size += os.path.getsize(fp)
                    except OSError:
                        pass

            try:
                shutil.rmtree(session_path)
            except Exception as e:
                errors.append(f'{session_id}: {str(e)}')
                continue

            total_cleaned += 1
            total_size += size

        except Exception as e:
            errors.append(f'{session_id}: {str(e)}')

    return {
        'ok': True,
        'cleaned': total_cleaned,
        'freed_bytes': total_size,
        'errors_count': len(errors)
    }
