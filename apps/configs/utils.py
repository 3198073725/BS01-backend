from __future__ import annotations
import threading
from typing import Any, Optional
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from .models import ConfigNamespace, ConfigKey, ConfigEntry

_cache = {}
_lock = threading.RLock()


def _cache_key(ns: str, key: str, scope_ct_id: Optional[int], scope_oid: Optional[str]):
    return (ns, key, scope_ct_id or 0, scope_oid or '')


def _coerce(value_type: str, value: Any) -> Any:
    if value is None:
        return None
    try:
        if value_type == 'string':
            return str(value)
        if value_type == 'int':
            return int(value)
        if value_type == 'bool':
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ('1', 'true', 'yes', 'y')
            return bool(value)
        # json
        return value
    except Exception:
        return value


def get_config(namespace: str, key: str, scope: Any | None = None, default=None, use_cache: bool = True):
    scope_ct_id = None
    scope_oid = None
    if scope is not None:
        ct = ContentType.objects.get_for_model(scope.__class__)
        scope_ct_id = ct.id
        scope_oid = str(getattr(scope, 'pk', getattr(scope, 'id', None)))
    ck = _cache_key(namespace, key, scope_ct_id, scope_oid)

    if use_cache:
        with _lock:
            if ck in _cache:
                return _cache[ck]

    # DB lookup with fallback chain: scope -> global -> default_value -> provided default
    try:
        ns = ConfigNamespace.objects.select_related(None).get(name=namespace)
    except ConfigNamespace.DoesNotExist:
        return default

    try:
        ckey = ConfigKey.objects.select_related('namespace').get(namespace=ns, key=key)
    except ConfigKey.DoesNotExist:
        return default

    value = None
    if scope_ct_id is not None and scope_oid:
        entry = (ConfigEntry.objects
                 .filter(key=ckey, content_type_id=scope_ct_id, object_id=scope_oid, is_active=True)
                 .order_by('-updated_at')
                 .first())
        if entry and entry.value is not None:
            value = entry.value

    if value is None:
        entry = (ConfigEntry.objects
                 .filter(key=ckey, content_type__isnull=True, object_id__isnull=True, is_active=True)
                 .order_by('-updated_at')
                 .first())
        if entry and entry.value is not None:
            value = entry.value

    if value is None:
        value = ckey.default_value if ckey.default_value is not None else default

    value = _coerce(ckey.value_type, value)

    if use_cache:
        with _lock:
            _cache[ck] = value
    return value


def invalidate_config_cache(namespace: Optional[str] = None):
    with _lock:
        if namespace is None:
            _cache.clear()
        else:
            for k in list(_cache.keys()):
                if k[0] == namespace:
                    _cache.pop(k, None)


@transaction.atomic
def set_config(namespace: str, key: str, value: Any, value_type: Optional[str] = None, scope: Any | None = None):
    ns, _ = ConfigNamespace.objects.get_or_create(name=namespace)
    if value_type is None:
        if isinstance(value, bool):
            value_type = 'bool'
        elif isinstance(value, int):
            value_type = 'int'
        elif isinstance(value, str):
            value_type = 'string'
        else:
            value_type = 'json'

    ckey, _ = ConfigKey.objects.get_or_create(namespace=ns, key=key, defaults={'value_type': value_type})
    if ckey.value_type != value_type:
        ckey.value_type = value_type
        ckey.save(update_fields=['value_type', 'updated_at'])

    if scope is None:
        ct_id = None
        oid = None
    else:
        ct = ContentType.objects.get_for_model(scope.__class__)
        ct_id = ct.id
        oid = str(getattr(scope, 'pk', getattr(scope, 'id', None)))

    entry, _ = ConfigEntry.objects.get_or_create(
        key=ckey, content_type_id=ct_id, object_id=oid, defaults={'value': value, 'is_active': True}
    )
    if entry.value != value:
        entry.value = value
        entry.is_active = True
        entry.save(update_fields=['value', 'is_active', 'updated_at'])

    invalidate_config_cache(namespace)
    return True
