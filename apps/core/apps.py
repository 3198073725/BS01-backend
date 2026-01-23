from django.apps import AppConfig
from django.core.cache import caches
import logging

class CoreConfig(AppConfig):
    name = 'apps.core'

    def ready(self):
        try:
            backend = type(caches['default']).__module__
            if 'locmem' in backend:
                logging.warning('Cache backend is LocMem; dedupe may be inaccurate in multi-instance setup.')
        except Exception:
            pass
