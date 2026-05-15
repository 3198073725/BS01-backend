import json

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.interactions.models import Comment


class Command(BaseCommand):
    help = '修复评论树历史脏数据；默认 dry-run，仅预览将要执行的调整'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='实际执行修复；默认仅预览',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=50,
            help='预览输出的最大样本数，默认 50',
        )
        parser.add_argument(
            '--format',
            choices=['text', 'json'],
            default='text',
            help='输出格式，默认 text',
        )

    def handle(self, *args, **options):
        apply_changes = bool(options['apply'])
        limit = max(1, int(options['limit']))
        fmt = options['format']

        second_level_qs = Comment.objects.filter(parent__parent__isnull=False).select_related('parent')
        second_level_rows = list(second_level_qs[:limit])
        second_level_samples = [
            {
                'id': str(comment.id),
                'video_id': str(comment.video_id),
                'old_parent_id': str(comment.parent_id),
                'new_parent_id': str(comment.parent.parent_id),
                'content': (comment.content or '')[:120],
            }
            for comment in second_level_rows
        ]
        second_level_total = second_level_qs.count()

        payload = {
            'mode': 'apply' if apply_changes else 'dry-run',
            'repairs': [
                {
                    'code': 'comment_reply_to_second_level',
                    'strategy': 'reparent_to_top_level',
                    'count': second_level_total,
                    'samples': second_level_samples,
                }
            ],
        }

        if apply_changes and second_level_total:
            with transaction.atomic():
                for comment in second_level_qs.select_related('parent'):
                    comment.parent_id = comment.parent.parent_id
                    comment.save(update_fields=['parent'])
            payload['applied'] = {'comment_reply_to_second_level': second_level_total}
        elif apply_changes:
            payload['applied'] = {'comment_reply_to_second_level': 0}

        if fmt == 'json':
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            return

        if apply_changes:
            self.stdout.write(self.style.SUCCESS('评论树修复完成'))
        else:
            self.stdout.write(self.style.SUCCESS('评论树修复预览完成'))
        item = payload['repairs'][0]
        self.stdout.write(f"[{item['code']}] {item['count']} rows; strategy={item['strategy']}")
        if item['samples']:
            self.stdout.write(f"  samples: {json.dumps(item['samples'], ensure_ascii=False, default=str)}")
