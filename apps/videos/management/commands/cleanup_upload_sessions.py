import os
import json
import shutil
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone


class Command(BaseCommand):
    help = '清理超过指定时间的废弃上传会话（分片上传临时文件）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=24,
            help='会话保留时间（小时），超过此时间的未完成会话将被清理，默认24小时'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='仅预览将要清理的文件，不实际删除'
        )
        parser.add_argument(
            '--verbose',
            '-v',
            action='store_true',
            help='显示详细信息'
        )

    def handle(self, *args, **options):
        hours = options['hours']
        dry_run = options['dry_run']
        verbose = options['verbose']

        sessions_base = os.path.join(settings.MEDIA_ROOT, 'uploads', 'sessions')
        if not os.path.exists(sessions_base):
            self.stdout.write(self.style.SUCCESS('会话目录不存在，无需清理'))
            return

        cutoff_time = timezone.now() - timedelta(hours=hours)
        total_cleaned = 0
        total_size = 0
        errors = []

        for session_id in os.listdir(sessions_base):
            session_path = os.path.join(sessions_base, session_id)
            if not os.path.isdir(session_path):
                continue

            # 检查 meta.json 的修改时间
            meta_path = os.path.join(session_path, 'meta.json')
            try:
                if os.path.exists(meta_path):
                    mtime = os.path.getmtime(meta_path)
                    modified_at = timezone.datetime.fromtimestamp(mtime, tz=timezone.utc)
                    
                    # 如果会话在 cutoff_time 之后还有活动，跳过
                    if modified_at > cutoff_time:
                        continue

                # 计算目录大小
                size = self._get_dir_size(session_path)
                
                if dry_run:
                    self.stdout.write(f'[预览] 将清理会话: {session_id} ({self._human_size(size)})')
                else:
                    try:
                        shutil.rmtree(session_path)
                        if verbose:
                            self.stdout.write(f'已清理: {session_id} ({self._human_size(size)})')
                    except Exception as e:
                        errors.append(f'{session_id}: {str(e)}')
                        continue

                total_cleaned += 1
                total_size += size

            except Exception as e:
                errors.append(f'{session_id}: {str(e)}')

        # 输出结果
        action = '预览' if dry_run else '清理'
        self.stdout.write(
            self.style.SUCCESS(
                f'{action}完成: 共处理 {total_cleaned} 个会话，释放空间 {self._human_size(total_size)}'
            )
        )

        if errors:
            self.stdout.write(self.style.WARNING(f'警告: {len(errors)} 个会话清理失败'))
            for err in errors:
                self.stdout.write(self.style.ERROR(f'  - {err}'))

    def _get_dir_size(self, path):
        """递归计算目录大小"""
        total = 0
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
        return total

    def _human_size(self, size_bytes):
        """将字节转换为人类可读格式"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f'{size_bytes:.2f} {unit}'
            size_bytes /= 1024.0
        return f'{size_bytes:.2f} TB'
