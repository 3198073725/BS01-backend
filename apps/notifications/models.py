from django.db import models

# Create your models here.

import uuid


class Notification(models.Model):
    """通知 - 对应 notifications_notification 表
    仅包含 SQL 中可确定的字段（用于未读索引）。其他字段如 verb/actor/target 可在需要时补充。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='recipient_id', related_name='notifications', verbose_name="接收者")
    is_read = models.BooleanField(default=False, verbose_name="是否已读")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    notification_type = models.CharField(max_length=50, default='generic', verbose_name="通知类型")
    data = models.JSONField(default=dict, blank=True, verbose_name="通知数据")

    class Meta:
        db_table = 'notifications_notification'
        managed = True
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at'], name='idx_notification_user_unread'),
        ]
        verbose_name = "通知"
        verbose_name_plural = "通知"


class SystemAnnouncement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200, verbose_name="标题")
    content = models.TextField(blank=True, default='', verbose_name="内容")
    is_active = models.BooleanField(default=True, verbose_name="是否发布")
    pinned = models.BooleanField(default=False, verbose_name="是否置顶")
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="发布时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 'system_announcement'
        managed = True
        indexes = [
            models.Index(fields=['is_active', '-published_at'], name='idx_announce_active_pub'),
            models.Index(fields=['pinned', '-published_at'], name='idx_announce_pinned_pub'),
        ]
        verbose_name = "系统公告"
        verbose_name_plural = "系统公告"


class SystemAnnouncementRead(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    announcement = models.ForeignKey(SystemAnnouncement, on_delete=models.CASCADE, db_column='announcement_id', related_name='reads', verbose_name="公告")
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='user_id', related_name='announcement_reads', verbose_name="用户")
    read_at = models.DateTimeField(auto_now_add=True, verbose_name="已读时间")

    class Meta:
        db_table = 'system_announcement_read'
        managed = True
        indexes = [
            models.Index(fields=['user', 'announcement'], name='idx_announce_read_user'),
            models.Index(fields=['announcement', 'user'], name='idx_announce_read_ann'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['announcement', 'user'], name='uq_announce_user_read'),
        ]
        verbose_name = "系统公告已读"
        verbose_name_plural = "系统公告已读"


class WebPushSubscription(models.Model):
    """Web 推送订阅 - 对应 webpush_subscription 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='user_id', related_name='webpush_subscriptions', null=True, blank=True, verbose_name="用户")
    endpoint = models.TextField(unique=True, verbose_name="推送端点")
    p256dh = models.TextField(null=True, blank=True, verbose_name="P256DH 密钥")
    auth = models.TextField(null=True, blank=True, verbose_name="认证密钥")
    browser = models.CharField(max_length=50, null=True, blank=True, verbose_name="浏览器")
    device = models.CharField(max_length=100, null=True, blank=True, verbose_name="设备")
    is_active = models.BooleanField(default=True, verbose_name="是否激活")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    last_seen = models.DateTimeField(null=True, blank=True, verbose_name="最后活跃时间")

    class Meta:
        db_table = 'webpush_subscription'
        managed = True
        indexes = [
            models.Index(fields=['user'], name='idx_webpush_user'),
        ]
        verbose_name = "Web 推送订阅"
        verbose_name_plural = "Web 推送订阅"


class FCMDeviceToken(models.Model):
    """FCM 设备令牌 - 对应 fcm_device_token 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='user_id', related_name='fcm_tokens', null=True, blank=True, verbose_name="用户")
    token = models.TextField(unique=True, verbose_name="设备令牌")
    device_id = models.CharField(max_length=100, null=True, blank=True, verbose_name="设备ID")
    platform = models.CharField(max_length=20, null=True, blank=True, verbose_name="平台")
    is_active = models.BooleanField(default=True, verbose_name="是否激活")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    last_seen = models.DateTimeField(null=True, blank=True, verbose_name="最后活跃时间")

    class Meta:
        db_table = 'fcm_device_token'
        managed = True
        indexes = [
            models.Index(fields=['user'], name='idx_fcm_user'),
        ]
        verbose_name = "FCM 设备令牌"
        verbose_name_plural = "FCM 设备令牌"


class NotificationDelivery(models.Model):
    """通知投递记录 - 对应 notification_delivery 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, db_column='notification_id', related_name='deliveries', verbose_name="通知")
    channel = models.CharField(max_length=20, verbose_name="推送渠道")
    status = models.CharField(max_length=20, default='pending', verbose_name="状态")
    attempt_count = models.IntegerField(default=0, verbose_name="尝试次数")
    last_attempt_at = models.DateTimeField(null=True, blank=True, verbose_name="最后尝试时间")
    error = models.TextField(null=True, blank=True, verbose_name="错误信息")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name="发送时间")

    class Meta:
        db_table = 'notification_delivery'
        managed = True
        indexes = [
            models.Index(fields=['notification'], name='idx_delivery_notification'),
            models.Index(fields=['status'], name='idx_delivery_status'),
            models.Index(fields=['-created_at'], name='idx_delivery_created'),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(attempt_count__gte=0), name='chk_delivery_attempt_nonneg'),
        ]
        verbose_name = "通知投递记录"
        verbose_name_plural = "通知投递记录"
