import os
import math
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.videos.models import Video
from apps.videos.views import _make_vtt_thumbnails


class Command(BaseCommand):
    help = '为历史视频批量生成缩略图 VTT 文件（WEBVTT）。'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100, help='本次最多处理的数量，默认 100')
        parser.add_argument('--resume', action='store_true', help='跳过已存在 VTT 的视频')
        parser.add_argument('--force', action='store_true', help='强制重新生成（覆盖）')
        parser.add_argument('--order', type=str, default='-created_at', help='排序字段，默认 -created_at')

    def handle(self, *args, **options):
        limit = int(options.get('limit') or 100)
        resume = bool(options.get('resume'))
        force = bool(options.get('force'))
        order = str(options.get('order') or '-created_at')

        base = (getattr(settings, 'SITE_URL', '') or 'http://localhost:8000').rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
        media_root = getattr(settings, 'MEDIA_ROOT', '')
        if not media_root:
            self.stderr.write(self.style.ERROR('MEDIA_ROOT 未配置'))
            return 1

        qs = Video.objects.all().order_by(order)
        total = qs.count()
        self.stdout.write(self.style.NOTICE(f'总计视频：{total}，本次处理上限：{limit}'))

        done = 0
        processed = 0
        for v in qs.iterator():
            if processed >= limit:
                break
            processed += 1

            rel = v.video_file or ''
            if not rel:
                self.stdout.write(self.style.WARNING(f'[skip] {v.id} 无 video_file'))
                continue

            vid_key = os.path.splitext(os.path.basename(rel))[0]
            vtt_rel = f"videos/thumbs/{vid_key}.vtt"
            vtt_abs = os.path.join(media_root, vtt_rel)
            if os.path.exists(vtt_abs) and resume and not force:
                self.stdout.write(self.style.WARNING(f'[skip] {v.id} 已存在 VTT'))
                continue

            src_abs = os.path.join(media_root, rel)
            if not os.path.exists(src_abs):
                self.stdout.write(self.style.WARNING(f'[skip] {v.id} 源文件不存在: {src_abs}'))
                continue

            # 时长用于估计抽帧间隔
            duration = int(getattr(v, 'duration', 0) or 0)
            try:
                out_rel = _make_vtt_thumbnails(src_abs, vid_key, duration, base, media)
                if out_rel and os.path.exists(os.path.join(media_root, out_rel)):
                    done += 1
                    self.stdout.write(self.style.SUCCESS(f'[ok] {v.id} -> {out_rel}'))
                else:
                    self.stdout.write(self.style.WARNING(f'[fail] {v.id} 生成失败'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'[error] {v.id} {e}'))

        self.stdout.write(self.style.SUCCESS(f'完成。本次成功 {done}/{processed}，总计 {total}。'))
        return 0
