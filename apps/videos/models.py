"""Videos 模型定义模块。

用于定义短视频相关的数据结构（如视频实体、素材、封面、转码记录等）。
后续可在此定义模型类，并在 admin 中注册以便管理后台查看与运营操作。
"""

import uuid
from django.db import models
from django.db.models import Q
from django.contrib.postgres.indexes import GinIndex

class Video(models.Model):
    """视频 - 对应 videos_video 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200, verbose_name="标题")
    description = models.TextField(null=True, blank=True, verbose_name="描述")
    video_file = models.CharField(max_length=100, verbose_name="视频文件")
    thumbnail = models.CharField(max_length=100, null=True, blank=True, verbose_name="缩略图")
    video_file_f = models.FileField(upload_to='videos/', null=True, blank=True, max_length=200, verbose_name="视频文件(FileField)")
    thumbnail_f = models.ImageField(upload_to='videos/thumbs/', null=True, blank=True, max_length=200, verbose_name="缩略图(ImageField)")
    duration = models.IntegerField(default=0, verbose_name="时长(秒)")
    width = models.IntegerField(default=0, verbose_name="宽度")
    height = models.IntegerField(default=0, verbose_name="高度")
    file_size = models.BigIntegerField(default=0, verbose_name="文件大小(字节)")
    status = models.CharField(max_length=20, default='draft', verbose_name="状态")
    upload_status = models.CharField(max_length=20, default='pending', verbose_name="上传状态")
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='user_id', related_name='videos', verbose_name="用户")
    category = models.ForeignKey('content.Category', null=True, blank=True, on_delete=models.SET_NULL, db_column='category_id', related_name='videos', verbose_name="分类")
    view_count = models.BigIntegerField(default=0, verbose_name="播放次数")
    like_count = models.BigIntegerField(default=0, verbose_name="点赞数")
    comment_count = models.BigIntegerField(default=0, verbose_name="评论数")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="发布时间")

    class Meta:
        db_table = 'videos_video'
        managed = True
        indexes = [
            # 已发布视频的快速列表（部分索引）
            models.Index(fields=['-published_at'], name='idx_videos_published', condition=Q(status='published')),
            # trigram 模糊匹配索引
            GinIndex(fields=['title'], name='idx_videos_title_trgm', opclasses=['gin_trgm_ops']),
            GinIndex(fields=['description'], name='idx_videos_desc_trgm', opclasses=['gin_trgm_ops']),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=['draft', 'processing', 'published', 'banned']), name='chk_video_status'),
            models.CheckConstraint(condition=Q(upload_status__in=['pending', 'uploading', 'completed', 'failed']), name='chk_upload_status'),
            models.CheckConstraint(condition=Q(duration__gte=0) & Q(width__gte=0) & Q(height__gte=0) & Q(file_size__gte=0), name='chk_video_nonnegatives'),
        ]
        verbose_name = "视频"
        verbose_name_plural = "视频"


class VideoTag(models.Model):
    """视频标签关联 - 对应 videos_video_tags 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(Video, on_delete=models.CASCADE, db_column='video_id', related_name='video_tags', verbose_name="视频")
    tag = models.ForeignKey('content.Tag', on_delete=models.CASCADE, db_column='tag_id', related_name='tag_videos', verbose_name="标签")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'videos_video_tags'
        managed = True
        unique_together = (('video', 'tag'),)
        indexes = [
            # 标签反查索引（tag_id, video_id）
            models.Index(fields=['tag', 'video'], name='idx_video_tags_tag_video'),
        ]
        verbose_name = "视频标签关联"
        verbose_name_plural = "视频标签关联"


class VideoTranscode(models.Model):
    """视频转码记录 - 对应 videos_transcode 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(Video, on_delete=models.CASCADE, db_column='video_id', related_name='transcodes', verbose_name="视频")
    profile = models.CharField(max_length=50, verbose_name="转码规格")
    url = models.TextField(verbose_name="转码文件URL")
    status = models.CharField(max_length=20, default='pending', verbose_name="状态")
    width = models.IntegerField(null=True, blank=True, verbose_name="宽度")
    height = models.IntegerField(null=True, blank=True, verbose_name="高度")
    bitrate = models.IntegerField(null=True, blank=True, verbose_name="比特率")
    codec = models.CharField(max_length=50, null=True, blank=True, verbose_name="编码格式")
    segment_duration = models.IntegerField(default=6, verbose_name="分片时长(秒)")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 'videos_transcode'
        managed = True
        unique_together = (('video', 'profile'),)
        indexes = [
            models.Index(fields=['video'], name='idx_transcode_video'),
            models.Index(fields=['status'], name='idx_transcode_status', condition=Q(status__in=['processing', 'pending'])),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=['pending', 'processing', 'ready', 'failed']), name='chk_transcode_status'),
            models.CheckConstraint(condition=Q(segment_duration__gt=0), name='chk_transcode_segment_positive'),
        ]
        verbose_name = "视频转码记录"
        verbose_name_plural = "视频转码记录"


class VideoAsset(models.Model):
    """视频资源 - 对应 videos_asset 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(Video, on_delete=models.CASCADE, db_column='video_id', related_name='assets', verbose_name="视频")
    kind = models.CharField(max_length=20, verbose_name="资源类型")
    url = models.TextField(verbose_name="资源URL")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'videos_asset'
        managed = True
        indexes = [
            models.Index(fields=['video'], name='idx_asset_video'),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=['thumbnail','sprite','gif','cover','watermark']), name='chk_asset_kind'),
        ]
        verbose_name = "视频资源"
        verbose_name_plural = "视频资源"


class VideoSubtitle(models.Model):
    """视频字幕 - 对应 videos_subtitle 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(Video, on_delete=models.CASCADE, db_column='video_id', related_name='subtitles', verbose_name="视频")
    lang = models.CharField(max_length=16, verbose_name="语言")
    format = models.CharField(max_length=16, verbose_name="格式")
    text_content = models.TextField(null=True, blank=True, verbose_name="字幕文本")
    url = models.TextField(null=True, blank=True, verbose_name="字幕URL")
    status = models.CharField(max_length=20, default='ready', verbose_name="状态")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 'videos_subtitle'
        managed = True
        unique_together = (('video', 'lang', 'format'),)
        indexes = [
            models.Index(fields=['video'], name='idx_subtitle_video'),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=['pending','processing','ready','failed']), name='chk_subtitle_status'),
        ]
        verbose_name = "视频字幕"
        verbose_name_plural = "视频字幕"


class Playlist(models.Model):
    """播放列表 - 对应 playlists 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='user_id', related_name='playlists', verbose_name="用户")
    name = models.CharField(max_length=255, verbose_name="名称")
    description = models.TextField(null=True, blank=True, verbose_name="描述")
    visibility = models.CharField(max_length=20, default='public', verbose_name="可见性")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 'playlists'
        managed = True
        indexes = [
            models.Index(fields=['user'], name='idx_playlists_user'),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(visibility__in=['public','unlisted','private']), name='chk_playlist_visibility'),
        ]
        verbose_name = "播放列表"
        verbose_name_plural = "播放列表"


class PlaylistVideo(models.Model):
    """播放列表视频 - 对应 playlist_videos 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, db_column='playlist_id', related_name='items', verbose_name="播放列表")
    video = models.ForeignKey(Video, on_delete=models.CASCADE, db_column='video_id', related_name='in_playlists', verbose_name="视频")
    position = models.IntegerField(default=0, verbose_name="位置")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'playlist_videos'
        managed = True
        unique_together = (('playlist', 'video'),)
        indexes = [
            models.Index(fields=['playlist', 'position'], name='idx_plv_pos'),
            models.Index(fields=['playlist', 'position', 'created_at'], name='idx_plv_pos_created'),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(position__gte=0), name='chk_plv_position_nonneg'),
        ]
        verbose_name = "播放列表视频"
        verbose_name_plural = "播放列表视频"


class WatchLater(models.Model):
    """稍后看 - 对应 watch_later 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='user_id', related_name='watch_later', verbose_name="用户")
    video = models.ForeignKey(Video, on_delete=models.CASCADE, db_column='video_id', related_name='watch_later_users', verbose_name="视频")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'watch_later'
        managed = True
        unique_together = (('user', 'video'),)
        indexes = [
            models.Index(fields=['user'], name='idx_watch_later_user'),
            models.Index(fields=['video'], name='idx_watch_later_video'),
        ]
        verbose_name = "稍后看"
        verbose_name_plural = "稍后看"
