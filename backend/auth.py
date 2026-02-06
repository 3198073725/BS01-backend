from datetime import datetime, timezone, timedelta
import time
from django.core.cache import cache
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


class JWTAuthenticationWithRevoke(JWTAuthentication):
    """JWT auth that supports per-user force logout via cache.

    Mechanism:
    - Admin sets cache key logout_after:{user_id} = unix_ts (seconds)
    - On each request, if token.iat < logout_after_ts, reject the token
    - TTL of the cache entry should cover the refresh token lifetime
    """

    def get_validated_token(self, raw_token):
        token = super().get_validated_token(raw_token)
        try:
            user_id = str(token.get('user_id') or token.get('user') or '')
            iat = int(token.get('iat') or 0)
        except Exception:
            user_id = ''
            iat = 0
        if user_id:
            key = f"logout_after:{user_id}"
            val = cache.get(key)
            if val:
                try:
                    cutoff = int(val)
                except Exception:
                    cutoff = 0
                if iat and iat < cutoff:
                    raise AuthenticationFailed('凭证已失效，请重新登录')
        return token
