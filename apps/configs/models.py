import uuid
from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType


class ConfigNamespace(models.Model):
    """配置命名空间 - 对应 configs_namespace 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="命名空间ID")
    name = models.CharField(max_length=64, unique=True, verbose_name="命名空间名称")
    description = models.CharField(max_length=255, null=True, blank=True, verbose_name="描述")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = 'configs_namespace'
        managed = True
        verbose_name = "配置命名空间"
        verbose_name_plural = "配置命名空间"

    def __str__(self) -> str:  # pragma: no cover
        return self.name


class ConfigKey(models.Model):
    """配置键 - 对应 configs_key 表"""
    VALUE_TYPES = (
        ('string', 'string'),
        ('int', 'int'),
        ('bool', 'bool'),
        ('json', 'json'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="配置键ID")
    namespace = models.ForeignKey(ConfigNamespace, on_delete=models.CASCADE, related_name='keys', verbose_name="命名空间")
    key = models.CharField(max_length=64, verbose_name="配置键")
    value_type = models.CharField(max_length=16, choices=VALUE_TYPES, default='json', verbose_name="值类型")
    default_value = models.JSONField(null=True, blank=True, verbose_name="默认值")
    description = models.CharField(max_length=255, null=True, blank=True, verbose_name="描述")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 'configs_key'
        managed = True
        unique_together = (('namespace', 'key'),)
        indexes = [
            models.Index(fields=['namespace', 'key'], name='idx_cfg_key_ns_key'),
        ]
        verbose_name = "配置键"
        verbose_name_plural = "配置键"

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.namespace.name}.{self.key}"


class ConfigEntry(models.Model):
    """配置条目 - 对应 configs_entry 表"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="配置条目ID")
    key = models.ForeignKey(ConfigKey, on_delete=models.CASCADE, related_name='entries', verbose_name="配置键")

    # 通用外键作用域：允许绑定任意模型，支持全局（content_type/object_id 为空）
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE, verbose_name="关联模型")
    object_id = models.CharField(max_length=64, null=True, blank=True, verbose_name="对象ID")
    scope = GenericForeignKey('content_type', 'object_id')

    value = models.JSONField(null=True, blank=True, verbose_name="配置值")
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 'configs_entry'
        managed = True
        unique_together = (('key', 'content_type', 'object_id'),)
        indexes = [
            models.Index(fields=['key'], name='idx_cfg_entry_key'),
            models.Index(fields=['key', 'content_type', 'object_id'], name='idx_cfg_entry_scope'),
            models.Index(fields=['-updated_at'], name='idx_cfg_entry_updated'),
        ]
        verbose_name = "配置条目"
        verbose_name_plural = "配置条目"

    def __str__(self) -> str:  # pragma: no cover
        scope = 'global' if not self.content_type else f"{self.content_type.app_label}.{self.content_type.model}:{self.object_id}"
        return f"{self.key} -> {scope}"
