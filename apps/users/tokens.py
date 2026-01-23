from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.conf import settings


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, user, timestamp):
        # include is_verified to invalidate token once verified
        return f"{user.pk}{user.is_verified}{timestamp}"

    def check_token(self, user, token):
        """委托给基类的校验以保证兼容性；
        令牌失效还会受 is_verified 变化影响（见 _make_hash_value）。
        当前有效期与 Django 的 PASSWORD_RESET_TIMEOUT 相同。
        """
        try:
            return super().check_token(user, token)
        except Exception:
            return False


email_verification_token = EmailVerificationTokenGenerator()
