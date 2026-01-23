"""统一 API 异常处理器

- 目标：将 DRF/Django 的各种异常统一为一致的 JSON 格式，便于前端处理
- 仅对“错误响应”进行包装；成功响应保持原样（避免破坏兼容）
- 返回结构示例：
  {
    "success": false,
    "code": "validation_error",
    "detail": "参数校验失败",
    "errors": {"field": ["错误消息"]}
  }
"""
from __future__ import annotations
from typing import Any
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import ValidationError, NotAuthenticated, PermissionDenied, Throttled
import logging

logger = logging.getLogger(__name__)


def custom_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    # 先让 DRF 生成基础 Response
    resp = drf_exception_handler(exc, context)
    if resp is None:
        # 未被 DRF 识别的异常，记录堆栈并返回统一的 500 JSON（避免混杂 HTML 错误页）
        try:
            logger.error("Unhandled API exception: %s (view=%s)", exc, context.get('view'), exc_info=True)
        except Exception:
            pass
        return Response({
            'success': False,
            'code': 'server_error',
            'detail': '服务器错误',
            'errors': None,
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # 推断错误类型与 code
    code = 'error'
    detail = ''
    errors: Any = None

    if isinstance(exc, ValidationError):
        # 若视图显式提供了 code/detail，则优先透传该 code
        if isinstance(resp.data, dict) and 'code' in resp.data:
            code = str(resp.data.get('code') or 'validation_error')
            detail = str(resp.data.get('detail') or '参数校验失败')
            errors = resp.data.get('errors', None)
        else:
            code = 'validation_error'
            detail = '参数校验失败'
            errors = resp.data
    elif isinstance(exc, Throttled):
        # 429 节流统一为 cooling_down，并返回剩余秒数
        code = 'cooling_down'
        detail = str(getattr(resp, 'data', {}).get('detail') or '请求频率受限')
        errors = None
        wait = getattr(exc, 'wait', None)
        extra = {}
        if isinstance(wait, (int, float)) and wait is not None:
            extra['cool_down_seconds'] = int(wait)
        resp.data = {
            'success': False,
            'code': code,
            'detail': detail,
            'errors': errors,
            **extra,
        }
        return resp
    elif isinstance(exc, NotAuthenticated):
        code = 'not_authenticated'
        detail = '未登录或凭证无效'
    elif isinstance(exc, PermissionDenied):
        code = 'permission_denied'
        detail = '无权限执行该操作'
    else:
        # 其他错误类型根据 HTTP 状态码归类
        mapping = {
            status.HTTP_400_BAD_REQUEST: ('bad_request', '请求不合法'),
            status.HTTP_404_NOT_FOUND: ('not_found', '资源不存在'),
            status.HTTP_429_TOO_MANY_REQUESTS: ('throttled', '请求频率受限'),
            status.HTTP_500_INTERNAL_SERVER_ERROR: ('server_error', '服务器错误'),
        }
        # 若响应体中已有 code 则优先使用
        if isinstance(resp.data, dict) and 'code' in resp.data:
            code = str(resp.data.get('code') or 'error')
            detail = str(resp.data.get('detail') or '请求失败')
        else:
            code, detail = mapping.get(resp.status_code, ('error', '请求失败'))
        errors = resp.data if resp.data else None

    resp.data = {
        'success': False,
        'code': code,
        'detail': detail,
        'errors': errors,
    }
    return resp
