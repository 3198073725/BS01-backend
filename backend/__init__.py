from __future__ import annotations

# Ensure Celery app is available as `backend.celery_app`
from .celery import app as celery_app  # noqa: F401

