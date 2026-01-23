"""Recommendation 视图模块。

用于实现推荐相关的 API 视图，例如推荐流、个性化召回、重排等。
可结合 DRF 的 APIView/ViewSet 来定义接口，并在 urls 中进行路由绑定。
"""

from django.conf import settings
import os
from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from apps.videos.models import Video
from apps.interactions.models import Like, Favorite, Follow
from django.db.models import Count, Q
from backend.common.pagination import StandardResultsSetPagination

# 在此编写视图，例如：
# from rest_framework.views import APIView
# class FeedView(APIView):
#     ...

class RecommendationFeedView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'recommendation_feed'

    def get(self, request):
        try:
            page = int(request.query_params.get('page', '1'))
        except Exception:
            page = 1
        page = max(1, page)
        try:
            size = int(request.query_params.get('page_size', '10'))
        except Exception:
            size = 10
        size = min(50, max(1, size))

        base = (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')

        uid = 'anon'
        try:
            if request.user and request.user.is_authenticated and getattr(request.user, 'id', None):
                uid = str(request.user.id)
        except Exception:
            uid = 'anon'
        cache_key = f"reco:feed:{uid}:{base}:{media}:{page}:{size}"
        no_cache = str(request.query_params.get('nocache', '0')).lower() in ('1', 'true', 'yes')
        cached = cache.get(cache_key) if not no_cache else None
        if (cached is not None) and (not no_cache):
            return Response(cached)

        def url_of(rel: str) -> str:
            if media.startswith('http://') or media.startswith('https://'):
                return f"{media}/{rel}"
            return f"{base}{media}/{rel}" if media.startswith('/') else f"{base}/{media}/{rel}"

        qs = (Video.objects
              .filter(status='published')
              .select_related('user')
              .only('id','title','duration','width','height','video_file','thumbnail','video_file_f','thumbnail_f',
                    'view_count','like_count','comment_count','created_at','published_at',
                    'user__id','user__username','user__nickname','user__profile_picture','user__profile_picture_f')
              .order_by('-published_at', '-created_at'))
        p = StandardResultsSetPagination()
        items = p.paginate_queryset(qs, request, view=self)

        # 当前用户已点赞/已收藏集合（未登录则为空）
        liked_ids = set()
        favorited_ids = set()
        try:
            if request.user and request.user.is_authenticated:
                vid_ids = [v.id for v in items]
                if vid_ids:
                    liked_ids = set(str(x) for x in Like.objects.filter(user=request.user, video_id__in=vid_ids).values_list('video_id', flat=True))
                    favorited_ids = set(str(x) for x in Favorite.objects.filter(user=request.user, video_id__in=vid_ids).values_list('video_id', flat=True))
        except Exception:
            liked_ids = set(); favorited_ids = set()

        # 批量收藏数
        fav_count_map = {}
        try:
            vid_ids = [v.id for v in items]
            if vid_ids:
                rows = Favorite.objects.filter(video_id__in=vid_ids).values('video_id').annotate(c=Count('id'))
                fav_count_map = {str(r['video_id']): int(r['c'] or 0) for r in rows}
        except Exception:
            fav_count_map = {}

        data = []
        for v in items:
            key = os.path.splitext(os.path.basename((getattr(v.video_file_f, 'name', None) or v.video_file or '')))[0]
            vtt_rel = f"videos/thumbs/{key}.vtt"
            vtt_abs = os.path.join(settings.MEDIA_ROOT, vtt_rel)
            master_rel = f"videos/hls/{key}/master.m3u8"
            master_abs = os.path.join(settings.MEDIA_ROOT, master_rel)
            data.append({
                'id': str(v.id),
                'title': v.title,
                'duration': v.duration,
                'width': v.width,
                'height': v.height,
                'video_url': (lambda r: (url_of(r) if (r and os.path.exists(os.path.join(settings.MEDIA_ROOT, r))) else None))((getattr(v.video_file_f, 'name', None) or v.video_file or '')),
                'thumbnail_url': (lambda t: (url_of(t) if t else None))((getattr(v.thumbnail_f, 'name', None) or v.thumbnail)),
                'view_count': v.view_count,
                'like_count': v.like_count,
                'comment_count': v.comment_count,
                'created_at': v.created_at,
                'published_at': v.published_at,
                'thumbnail_vtt_url': (url_of(vtt_rel) if os.path.exists(vtt_abs) else None),
                'hls_master_url': (url_of(master_rel) if os.path.exists(master_abs) else None),
                'author': {
                    'id': str(getattr(v.user, 'id', '') or v.user_id),
                    'name': getattr(v.user, 'display_name', None) or getattr(v.user, 'username', ''),
                    'username': getattr(v.user, 'username', ''),
                    'avatar_url': (url_of(getattr(getattr(v.user, 'profile_picture_f', None), 'name', None)) if getattr(getattr(v.user, 'profile_picture_f', None), 'name', None) else (url_of(getattr(v.user, 'profile_picture', '')) if getattr(v.user, 'profile_picture', None) else None)),
                },
                'favorite_count': fav_count_map.get(str(v.id), 0),
                'liked': (str(v.id) in liked_ids),
                'favorited': (str(v.id) in favorited_ids),
            })
        payload = {'results': data, 'page': page, 'page_size': size, 'total': p.page.paginator.count, 'has_next': p.page.has_next()}
        if not no_cache:
            cache.set(cache_key, payload, timeout=5)
        return Response(payload)


class FollowingFeedView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'following_feed'

    def get(self, request):
        try:
            page = int(request.query_params.get('page', '1'))
        except Exception:
            page = 1
        page = max(1, page)
        try:
            size = int(request.query_params.get('page_size', '10'))
        except Exception:
            size = 10
        size = min(50, max(1, size))

        base = (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')

        followed_ids = list(Follow.objects.filter(follower=request.user).values_list('followed_id', flat=True))
        if not followed_ids:
            return Response({'results': [], 'page': page, 'page_size': size, 'total': 0, 'has_next': False})

        def url_of(rel: str) -> str:
            if media.startswith('http://') or media.startswith('https://'):
                return f"{media}/{rel}"
            return f"{base}{media}/{rel}" if media.startswith('/') else f"{base}/{media}/{rel}"

        qs = (Video.objects
              .filter(status='published', user_id__in=followed_ids)
              .select_related('user')
              .only('id','title','duration','width','height','video_file','thumbnail','video_file_f','thumbnail_f',
                    'view_count','like_count','comment_count','created_at','published_at',
                    'user__id','user__username','user__nickname','user__profile_picture','user__profile_picture_f')
              .order_by('-published_at', '-created_at'))
        p = StandardResultsSetPagination()
        items = p.paginate_queryset(qs, request, view=self)

        liked_ids = set()
        favorited_ids = set()
        try:
            vid_ids = [v.id for v in items]
            if vid_ids:
                liked_ids = set(str(x) for x in Like.objects.filter(user=request.user, video_id__in=vid_ids).values_list('video_id', flat=True))
                favorited_ids = set(str(x) for x in Favorite.objects.filter(user=request.user, video_id__in=vid_ids).values_list('video_id', flat=True))
        except Exception:
            liked_ids = set(); favorited_ids = set()

        fav_count_map = {}
        try:
            vid_ids = [v.id for v in items]
            if vid_ids:
                rows = Favorite.objects.filter(video_id__in=vid_ids).values('video_id').annotate(c=Count('id'))
                fav_count_map = {str(r['video_id']): int(r['c'] or 0) for r in rows}
        except Exception:
            fav_count_map = {}

        data = []
        for v in items:
            key = os.path.splitext(os.path.basename((getattr(v.video_file_f, 'name', None) or v.video_file or '')))[0]
            vtt_rel = f"videos/thumbs/{key}.vtt"
            vtt_abs = os.path.join(settings.MEDIA_ROOT, vtt_rel)
            master_rel = f"videos/hls/{key}/master.m3u8"
            master_abs = os.path.join(settings.MEDIA_ROOT, master_rel)
            data.append({
                'id': str(v.id),
                'title': v.title,
                'duration': v.duration,
                'width': v.width,
                'height': v.height,
                'video_url': (lambda r: (url_of(r) if (r and os.path.exists(os.path.join(settings.MEDIA_ROOT, r))) else None))((getattr(v.video_file_f, 'name', None) or v.video_file or '')),
                'thumbnail_url': (lambda t: (url_of(t) if t else None))((getattr(v.thumbnail_f, 'name', None) or v.thumbnail)),
                'view_count': v.view_count,
                'like_count': v.like_count,
                'comment_count': v.comment_count,
                'created_at': v.created_at,
                'published_at': v.published_at,
                'thumbnail_vtt_url': (url_of(vtt_rel) if os.path.exists(vtt_abs) else None),
                'hls_master_url': (url_of(master_rel) if os.path.exists(master_abs) else None),
                'author': {
                    'id': str(getattr(v.user, 'id', '') or v.user_id),
                    'name': getattr(v.user, 'display_name', None) or getattr(v.user, 'username', ''),
                    'username': getattr(v.user, 'username', ''),
                    'avatar_url': (url_of(getattr(getattr(v.user, 'profile_picture_f', None), 'name', None)) if getattr(getattr(v.user, 'profile_picture_f', None), 'name', None) else (url_of(getattr(v.user, 'profile_picture', '')) if getattr(v.user, 'profile_picture', None) else None)),
                },
                'favorite_count': fav_count_map.get(str(v.id), 0),
                'liked': (str(v.id) in liked_ids),
                'favorited': (str(v.id) in favorited_ids),
            })
        return Response({'results': data, 'page': page, 'page_size': size, 'total': p.page.paginator.count, 'has_next': p.page.has_next()})


class FeaturedFeedView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'featured_feed'

    def get(self, request):
        try:
            page = int(request.query_params.get('page', '1'))
        except Exception:
            page = 1
        page = max(1, page)
        try:
            size = int(request.query_params.get('page_size', '10'))
        except Exception:
            size = 10
        size = min(50, max(1, size))

        base = (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')

        def url_of(rel: str) -> str:
            if media.startswith('http://') or media.startswith('https://'):
                return f"{media}/{rel}"
            return f"{base}{media}/{rel}" if media.startswith('/') else f"{base}/{media}/{rel}"

        # like_count > 20 或收藏数 > 10（用分组查询避免某些环境下 annotate 反向聚合引发错误）
        try:
            fav_ids = list(
                Favorite.objects.values('video_id').annotate(c=Count('id')).filter(c__gt=10).values_list('video_id', flat=True)
            )
        except Exception:
            fav_ids = []
        qs = (Video.objects
              .filter(status='published')
              .filter(Q(like_count__gt=20) | Q(id__in=fav_ids))
              .select_related('user')
              .only('id','title','duration','width','height','video_file','thumbnail','video_file_f','thumbnail_f',
                    'view_count','like_count','comment_count','created_at','published_at',
                    'user__id','user__username','user__nickname','user__profile_picture','user__profile_picture_f')
              .order_by('-published_at', '-created_at'))
        p = StandardResultsSetPagination()
        items = p.paginate_queryset(qs, request, view=self)

        liked_ids = set()
        favorited_ids = set()
        try:
            if request.user and request.user.is_authenticated:
                vid_ids = [v.id for v in items]
                if vid_ids:
                    liked_ids = set(str(x) for x in Like.objects.filter(user=request.user, video_id__in=vid_ids).values_list('video_id', flat=True))
                    favorited_ids = set(str(x) for x in Favorite.objects.filter(user=request.user, video_id__in=vid_ids).values_list('video_id', flat=True))
        except Exception:
            liked_ids = set(); favorited_ids = set()

        fav_count_map = {}
        try:
            vid_ids = [v.id for v in items]
            if vid_ids:
                rows = Favorite.objects.filter(video_id__in=vid_ids).values('video_id').annotate(c=Count('id'))
                fav_count_map = {str(r['video_id']): int(r['c'] or 0) for r in rows}
        except Exception:
            fav_count_map = {}

        data = []
        for v in items:
            key = os.path.splitext(os.path.basename((getattr(v.video_file_f, 'name', None) or v.video_file or '')))[0]
            vtt_rel = f"videos/thumbs/{key}.vtt"
            vtt_abs = os.path.join(settings.MEDIA_ROOT, vtt_rel)
            master_rel = f"videos/hls/{key}/master.m3u8"
            master_abs = os.path.join(settings.MEDIA_ROOT, master_rel)
            data.append({
                'id': str(v.id),
                'title': v.title,
                'duration': v.duration,
                'width': v.width,
                'height': v.height,
                'video_url': (lambda r: (url_of(r) if (r and os.path.exists(os.path.join(settings.MEDIA_ROOT, r))) else None))((getattr(v.video_file_f, 'name', None) or v.video_file or '')),
                'thumbnail_url': (lambda t: (url_of(t) if t else None))((getattr(v.thumbnail_f, 'name', None) or v.thumbnail)),
                'view_count': v.view_count,
                'like_count': v.like_count,
                'comment_count': v.comment_count,
                'created_at': v.created_at,
                'published_at': v.published_at,
                'thumbnail_vtt_url': (url_of(vtt_rel) if os.path.exists(vtt_abs) else None),
                'hls_master_url': (url_of(master_rel) if os.path.exists(master_abs) else None),
                'author': {
                    'id': str(getattr(v.user, 'id', '') or v.user_id),
                    'name': getattr(v.user, 'display_name', None) or getattr(v.user, 'username', ''),
                    'username': getattr(v.user, 'username', ''),
                    'avatar_url': (url_of(getattr(getattr(v.user, 'profile_picture_f', None), 'name', None)) if getattr(getattr(v.user, 'profile_picture_f', None), 'name', None) else (url_of(getattr(v.user, 'profile_picture', '')) if getattr(v.user, 'profile_picture', None) else None)),
                },
                'favorite_count': fav_count_map.get(str(v.id), 0),
                'liked': (str(v.id) in liked_ids),
                'favorited': (str(v.id) in favorited_ids),
            })
        return Response({'results': data, 'page': page, 'page_size': size, 'total': p.page.paginator.count, 'has_next': p.page.has_next()})
