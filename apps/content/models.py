"""Content 模型定义模块。

用于定义平台内容相关数据结构（如话题、标签、素材等）。
可与视频、用户、互动等应用建立外键/多对多关系。
"""

import uuid
from django.db import models
from django.db.models import Q

class Category(models.Model):
    """视频分类 - 对应 content_category 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="分类ID")
    name = models.CharField(max_length=100, unique=True, verbose_name="分类名称")
    description = models.TextField(null=True, blank=True, verbose_name="分类描述")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'content_category'
        managed = True
        verbose_name = "视频分类"
        verbose_name_plural = "视频分类"


class Tag(models.Model):
    """视频标签 - 对应 content_tag 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="标签ID")
    name = models.CharField(max_length=50, unique=True, verbose_name="标签名称")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'content_tag'
        managed = True
        verbose_name = "视频标签"
        verbose_name_plural = "视频标签"


class Report(models.Model):
    """举报记录 - 对应 reports_report 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="举报ID")
    reporter = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='reporter_id', related_name='reports', verbose_name="举报人")
    target_type = models.CharField(max_length=50, verbose_name="目标类型")
    target_id = models.UUIDField(verbose_name="目标ID")
    reason_code = models.CharField(max_length=50, verbose_name="举报原因")
    description = models.TextField(null=True, blank=True, verbose_name="描述")
    status = models.CharField(max_length=20, default='pending', verbose_name="状态")
    handled_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, db_column='handled_by', related_name='handled_reports', verbose_name="处理人")
    handled_at = models.DateTimeField(null=True, blank=True, verbose_name="处理时间")
    moderator_notes = models.TextField(null=True, blank=True, verbose_name="审核备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 'reports_report'
        managed = True
        verbose_name = "举报"
        verbose_name_plural = "举报"
        indexes = [
            models.Index(fields=['target_type', 'target_id'], name='idx_report_target'),
            models.Index(fields=['status'], name='idx_report_status'),
            models.Index(fields=['-created_at'], name='idx_report_created'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(target_type__in=['video', 'comment', 'user']),
                name='chk_report_target_type',
            ),
        ]


class ModerationAction(models.Model):
    """审核动作 - 对应 moderation_action 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="动作ID")
    report = models.ForeignKey(Report, on_delete=models.CASCADE, db_column='report_id', related_name='actions', verbose_name="对应举报")
    moderator = models.ForeignKey('users.User', on_delete=models.CASCADE, db_column='moderator_id', related_name='moderation_actions', verbose_name="审核人")
    action = models.CharField(max_length=50, verbose_name="动作类型")
    reason = models.TextField(null=True, blank=True, verbose_name="原因")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'moderation_action'
        managed = True
        verbose_name = "审核动作"
        verbose_name_plural = "审核动作"
        indexes = [
            models.Index(fields=['report'], name='idx_moderation_report'),
        ]


class AuditLog(models.Model):
    """审计日志 - 对应 audit_log 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="日志ID")
    actor = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, db_column='actor_id', related_name='audit_logs', verbose_name="操作者")
    verb = models.CharField(max_length=100, verbose_name="操作类型")
    target_type = models.CharField(max_length=50, null=True, blank=True, verbose_name="目标类型")
    target_id = models.UUIDField(null=True, blank=True, verbose_name="目标ID")
    meta = models.JSONField(null=True, blank=True, verbose_name="元数据")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'audit_log'
        managed = True
        verbose_name = "审计日志"
        verbose_name_plural = "审计日志"
        indexes = [
            models.Index(fields=['-created_at'], name='idx_audit_log_created'),
            models.Index(fields=['actor'], name='idx_audit_log_actor'),
            models.Index(fields=['target_type', 'target_id'], name='idx_audit_log_target'),
        ]
