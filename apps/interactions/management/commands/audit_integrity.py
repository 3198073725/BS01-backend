import json

from django.core.management.base import BaseCommand
from django.db.models import F, Q

from apps.interactions.models import Comment, Follow, History, Like
from apps.videos.models import Video


class Command(BaseCommand):
    help = '审计互动与视频相关数据完整性，定位会阻塞迁移或偏离业务规则的历史脏数据'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=20,
            help='每类问题输出的示例条数，默认 20',
        )
        parser.add_argument(
            '--format',
            choices=['text', 'json'],
            default='text',
            help='输出格式，默认 text',
        )

    def handle(self, *args, **options):
        limit = max(1, int(options['limit']))
        fmt = options['format']

        findings = [
            self._build_finding(
                code='like_missing_target',
                severity='hard',
                description='Like 既没有 video 也没有 comment，会违反 chk_like_exactly_one_target',
                queryset=Like.objects.filter(video__isnull=True, comment__isnull=True),
                sample_fields=('id', 'user_id', 'video_id', 'comment_id'),
                limit=limit,
            ),
            self._build_finding(
                code='like_both_targets',
                severity='hard',
                description='Like 同时指向 video 和 comment，会违反 chk_like_exactly_one_target',
                queryset=Like.objects.filter(video__isnull=False, comment__isnull=False),
                sample_fields=('id', 'user_id', 'video_id', 'comment_id'),
                limit=limit,
            ),
            self._build_finding(
                code='video_negative_counters',
                severity='hard',
                description='Video 聚合计数为负，会违反 chk_video_counters_nonneg',
                queryset=Video.objects.filter(
                    Q(view_count__lt=0) | Q(like_count__lt=0) | Q(comment_count__lt=0)
                ),
                sample_fields=('id', 'view_count', 'like_count', 'comment_count'),
                limit=limit,
            ),
            self._build_finding(
                code='comment_self_parent',
                severity='hard',
                description='Comment.parent_id 等于自身 id，会违反 chk_comment_not_self_parent',
                queryset=Comment.objects.filter(parent_id=F('id')),
                sample_fields=('id', 'video_id', 'parent_id'),
                limit=limit,
            ),
            self._build_finding(
                code='follow_self_reference',
                severity='hard',
                description='Follow 自关注，会违反 chk_not_self_follow',
                queryset=Follow.objects.filter(follower_id=F('followed_id')),
                sample_fields=('id', 'follower_id', 'followed_id'),
                limit=limit,
            ),
            self._build_finding(
                code='history_invalid_progress',
                severity='hard',
                description='History.progress 不在 [0, 1]，会违反 chk_history_progress',
                queryset=History.objects.filter(Q(progress__lt=0) | Q(progress__gt=1)),
                sample_fields=('id', 'user_id', 'video_id', 'progress', 'watch_duration'),
                limit=limit,
            ),
            self._build_finding(
                code='history_negative_duration',
                severity='hard',
                description='History.watch_duration 为负，会违反 chk_history_watch_duration_nonneg',
                queryset=History.objects.filter(watch_duration__lt=0),
                sample_fields=('id', 'user_id', 'video_id', 'progress', 'watch_duration'),
                limit=limit,
            ),
            self._build_finding(
                code='comment_cross_video_parent',
                severity='soft',
                description='Comment 与其父评论不属于同一个 video，当前主要靠 view 拦截',
                queryset=Comment.objects.filter(parent__isnull=False).exclude(video_id=F('parent__video_id')),
                sample_fields=('id', 'video_id', 'parent_id', 'parent__video_id'),
                limit=limit,
            ),
            self._build_finding(
                code='comment_reply_to_second_level',
                severity='soft',
                description='Comment 回复了二级评论，当前主要靠 view 拦截',
                queryset=Comment.objects.filter(parent__parent__isnull=False),
                sample_fields=('id', 'video_id', 'parent_id', 'parent__parent_id'),
                limit=limit,
            ),
        ]

        payload = {
            'summary': {
                'hard_violations': sum(item['count'] for item in findings if item['severity'] == 'hard'),
                'soft_violations': sum(item['count'] for item in findings if item['severity'] == 'soft'),
                'checks': len(findings),
            },
            'findings': findings,
        }

        if fmt == 'json':
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            return

        self.stdout.write(self.style.SUCCESS('数据完整性审计完成'))
        self.stdout.write(
            f"硬违规 {payload['summary']['hard_violations']} 条；"
            f"软违规 {payload['summary']['soft_violations']} 条；"
            f"共检查 {payload['summary']['checks']} 类问题"
        )
        for item in findings:
            style = self.style.ERROR if item['count'] else self.style.SUCCESS
            self.stdout.write(style(f"[{item['severity']}] {item['code']}: {item['count']}"))
            self.stdout.write(f"  {item['description']}")
            if item['samples']:
                self.stdout.write(f"  samples: {json.dumps(item['samples'], ensure_ascii=False, default=str)}")

    def _build_finding(self, *, code, severity, description, queryset, sample_fields, limit):
        count = queryset.count()
        samples = list(queryset.values(*sample_fields)[:limit])
        return {
            'code': code,
            'severity': severity,
            'description': description,
            'count': count,
            'samples': samples,
        }
