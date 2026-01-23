from django.contrib import admin
from .models import ConfigNamespace, ConfigKey, ConfigEntry


@admin.register(ConfigNamespace)
class ConfigNamespaceAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'created_at')
    search_fields = ('name',)


@admin.register(ConfigKey)
class ConfigKeyAdmin(admin.ModelAdmin):
    list_display = ('namespace', 'key', 'value_type', 'updated_at')
    list_filter = ('namespace', 'value_type')
    search_fields = ('key', 'namespace__name')


@admin.register(ConfigEntry)
class ConfigEntryAdmin(admin.ModelAdmin):
    list_display = ('key', 'content_type', 'object_id', 'is_active', 'updated_at')
    list_filter = ('is_active', 'content_type')
    search_fields = ('key__key', 'object_id')
