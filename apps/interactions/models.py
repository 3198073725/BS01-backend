"""Interactions 模型定义模块。

用于定义互动相关的数据结构（如点赞、评论、收藏、关注等）。
可与用户、视频等模型建立关联以支撑互动业务。
"""

import uuid
from django.db import models


class Like(models.Model):
    """点赞 - 对应 interactions_like 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="点赞ID")
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='user_id', related_name='likes', verbose_name="用户")
    video = models.ForeignKey('videos.Video', on_delete=models.CASCADE, null=True, blank=True, db_column='video_id', related_name='likes', verbose_name="视频")
    comment = models.ForeignKey('interactions.Comment', on_delete=models.CASCADE, null=True, blank=True, db_column='comment_id', related_name='likes', verbose_name="评论")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'interactions_like'
        managed = True
        unique_together = (('user', 'video'), ('user', 'comment'),)
        indexes = [
            models.Index(fields=['user'], name='idx_like_user'),
            models.Index(fields=['video'], name='idx_like_video'),
            models.Index(fields=['comment'], name='idx_like_comment'),
        ]
        verbose_name = "点赞"
        verbose_name_plural = "点赞"


class Favorite(models.Model):
    """收藏 - 对应 interactions_favorite 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="收藏ID")
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='user_id', related_name='favorites', verbose_name="用户")
    video = models.ForeignKey('videos.Video', on_delete=models.CASCADE, db_column='video_id', related_name='favorites', verbose_name="视频")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'interactions_favorite'
        managed = True
        unique_together = (('user', 'video'),)
        indexes = [
            models.Index(fields=['user'], name='idx_favorite_user'),
            models.Index(fields=['video'], name='idx_favorite_video'),
        ]
        verbose_name = "收藏"
        verbose_name_plural = "收藏"


class Comment(models.Model):
    """评论 - 对应 interactions_comment 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="评论ID")
    content = models.TextField(verbose_name="评论内容")
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='user_id', related_name='comments', verbose_name="评论用户")
    video = models.ForeignKey('videos.Video', on_delete=models.CASCADE, db_column='video_id', related_name='comments', verbose_name="视频")
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, db_column='parent_id', related_name='replies', verbose_name="父评论")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 'interactions_comment'
        managed = True
        indexes = [
            models.Index(fields=['video', '-created_at'], name='idx_comment_video_created'),
            models.Index(fields=['user'], name='idx_comment_user'),
            models.Index(fields=['parent'], name='idx_comment_parent'),
        ]
        verbose_name = "评论"
        verbose_name_plural = "评论"


class History(models.Model):
    """观看历史 - 对应 interactions_history 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="历史ID")
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='user_id', related_name='histories', verbose_name="用户")
    video = models.ForeignKey('videos.Video', on_delete=models.CASCADE, db_column='video_id', related_name='histories', verbose_name="视频")
    watch_duration = models.IntegerField(default=0, verbose_name="观看时长(秒)")
    progress = models.FloatField(default=0, verbose_name="播放进度")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'interactions_history'
        managed = True
        unique_together = (('user', 'video'),)
        indexes = [
            models.Index(fields=['user'], name='idx_history_user'),
            models.Index(fields=['video'], name='idx_history_video'),
        ]
        constraints = [
            models.CheckConstraint(name='chk_history_progress', condition=models.Q(progress__gte=0) & models.Q(progress__lte=1)),
            models.CheckConstraint(name='chk_history_watch_duration_nonneg', condition=models.Q(watch_duration__gte=0)),
        ]
        verbose_name = "观看历史"
        verbose_name_plural = "观看历史"


class Follow(models.Model):
    """关注关系 - 对应 interactions_follow 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="关注ID")
    follower = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='follower_id', related_name='following', verbose_name="关注者")
    followed = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='followed_id', related_name='followers', verbose_name="被关注者")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="关注时间")

    class Meta:
        db_table = 'interactions_follow'
        managed = True
        unique_together = (('follower', 'followed'),)
        indexes = [
            models.Index(fields=['follower'], name='idx_follow_follower'),
            models.Index(fields=['followed'], name='idx_follow_followed'),
            models.Index(fields=['follower', 'created_at'], name='idx_follow_follower_created'),
            models.Index(fields=['followed', 'created_at'], name='idx_follow_followed_created'),
        ]
        constraints = [
            models.CheckConstraint(name='chk_not_self_follow', condition=~models.Q(follower=models.F('followed'))),
        ]
        verbose_name = "关注"
        verbose_name_plural = "关注"


class Notification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="通知ID")
    user = models.ForeignKey(
        'users.User', on_delete=models.CASCADE, db_column='user_id',
        related_name='interact_notifications', related_query_name='interact_notification',
        verbose_name="接收者"
    )
    actor = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='actor_id', related_name='activities', verbose_name="触发者")
    verb = models.CharField(max_length=30, verbose_name="类型")
    video = models.ForeignKey('videos.Video', null=True, blank=True, on_delete=models.SET_NULL, db_column='video_id', related_name='notifications', verbose_name="视频")
    comment = models.ForeignKey('interactions.Comment', null=True, blank=True, on_delete=models.SET_NULL, db_column='comment_id', related_name='notifications', verbose_name="评论")
    read = models.BooleanField(default=False, verbose_name="已读")
    hidden = models.BooleanField(default=False, verbose_name="已隐藏")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'interactions_notification'
        managed = True
        indexes = [
            models.Index(fields=['user', 'hidden', 'read', '-created_at']),
            models.Index(fields=['user', 'hidden', '-created_at']),
        ]
        verbose_name = "通知"
        verbose_name_plural = "通知"
