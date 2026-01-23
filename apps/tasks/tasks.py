from __future__ import annotations
import os
import json
import math
import subprocess
from typing import Optional

from celery import shared_task
from django.conf import settings
from django.db import transaction

from apps.videos.models import Video


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
    base = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
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

    duration = int(v.duration or 0)
    if duration <= 0:
        _, _, duration = _probe_video(video_abs)

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
                url = _build_media_url(base, media, f"{frames_rel_dir}/{name}")
                f.write(f"{url}\n\n")
        return {'ok': True, 'vtt_rel': vtt_rel}
    except Exception as e:
        return {'ok': False, 'error': str(e)[:200]}


@shared_task(bind=True, name='tasks.transcode_video_to_hls')
def transcode_video_to_hls(self, video_id: str) -> dict:
    base = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
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

    width = int(v.width or 0)
    height = int(v.height or 0)
    if width <= 0 or height <= 0:
        width, height, _ = _probe_video(video_abs)

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
            return {'ok': False, 'error': 'no_variants'}
        master_rel = f"videos/hls/{vid_key}/master.m3u8"
        master_abs = os.path.join(settings.MEDIA_ROOT, master_rel)
        def rel(path: str) -> str:
            return os.path.relpath(path, settings.MEDIA_ROOT).replace('\\', '/')
        with open(master_abs, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            f.write('#EXT-X-VERSION:3\n')
            for name, h in entries:
                bw = 2500000 if h >= 700 else 1200000
                uri = _build_media_url(base or '', media, rel(os.path.join(out_dir, name, 'index.m3u8')))
                f.write(f"#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={1280 if h>=700 else 854}x{h}\n")
                f.write(f"{uri}\n")
        return {'ok': True, 'master_rel': master_rel}
    except Exception as e:
        return {'ok': False, 'error': str(e)[:200]}
