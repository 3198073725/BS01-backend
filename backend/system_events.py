import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs

import redis
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed

logger = logging.getLogger(__name__)

SYSTEM_EVENTS_CHANNEL = 'system-events'
CONFIG_UPDATED_EVENT = 'config.updated'
INSTANCE_ID = f'{os.getpid()}:{id(object())}'


@dataclass(frozen=True)
class _Client:
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop


class SystemEventBroker:
    def __init__(self):
        self._clients = set()
        self._lock = threading.Lock()
        self._subscriber_started = False
        self._subscriber_thread: Optional[threading.Thread] = None

    def register(self, client: _Client):
        with self._lock:
            self._clients.add(client)
            self._ensure_subscriber_locked()

    def unregister(self, client: _Client):
        with self._lock:
            self._clients.discard(client)

    def emit_local(self, payload: dict):
        message = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.loop.call_soon_threadsafe(client.queue.put_nowait, message)
            except Exception:
                logger.debug('Failed to enqueue local system event', exc_info=True)

    def publish(self, payload: dict):
        message_payload = dict(payload)
        message_payload.setdefault('instance_id', INSTANCE_ID)
        self.emit_local(message_payload)
        redis_url = getattr(settings, 'REDIS_URL', '') or ''
        if not redis_url:
            return
        try:
            client = redis.Redis.from_url(redis_url)
            client.publish(SYSTEM_EVENTS_CHANNEL, json.dumps(message_payload, ensure_ascii=False))
        except Exception:
            logger.warning('Failed to publish system event to redis', exc_info=True)

    def _ensure_subscriber_locked(self):
        redis_url = getattr(settings, 'REDIS_URL', '') or ''
        if self._subscriber_started or not redis_url:
            return
        self._subscriber_started = True
        self._subscriber_thread = threading.Thread(
            target=self._subscriber_main,
            name='system-events-subscriber',
            daemon=True,
        )
        self._subscriber_thread.start()

    def _subscriber_main(self):
        redis_url = getattr(settings, 'REDIS_URL', '') or ''
        if not redis_url:
            return
        while True:
            try:
                client = redis.Redis.from_url(redis_url)
                pubsub = client.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(SYSTEM_EVENTS_CHANNEL)
                for item in pubsub.listen():
                    if not item or item.get('type') != 'message':
                        continue
                    data = item.get('data')
                    if isinstance(data, bytes):
                        data = data.decode('utf-8', errors='ignore')
                    try:
                        payload = json.loads(str(data or '{}'))
                    except Exception:
                        logger.warning('Invalid system event payload from redis: %r', data)
                        continue
                    if payload.get('instance_id') == INSTANCE_ID:
                        continue
                    self.emit_local(payload)
            except Exception:
                logger.warning('System event redis subscriber stopped unexpectedly', exc_info=True)
                threading.Event().wait(1.0)


broker = SystemEventBroker()


def publish_config_updated(*, version: int, changed_keys=None, reload_required: bool = False):
    broker.publish({
        'type': CONFIG_UPDATED_EVENT,
        'version': version,
        'changed_keys': list(changed_keys or []),
        'reload_required': bool(reload_required),
    })


def _extract_token(scope):
    query = parse_qs((scope.get('query_string') or b'').decode('utf-8', errors='ignore'))
    token = ''
    if query.get('token'):
        token = str(query['token'][0] or '').strip()
    if token:
        return token
    for raw_name, raw_value in (scope.get('headers') or []):
        if raw_name.lower() != b'authorization':
            continue
        text = raw_value.decode('utf-8', errors='ignore').strip()
        if text.lower().startswith('bearer '):
            return text[7:].strip()
    return ''


def authenticate_scope(scope):
    token = _extract_token(scope)
    if not token:
        return None
    from backend.auth import JWTAuthenticationWithRevoke

    auth = JWTAuthenticationWithRevoke()
    try:
        validated = auth.get_validated_token(token)
        return auth.get_user(validated)
    except AuthenticationFailed:
        raise
    except Exception as exc:
        raise AuthenticationFailed('无效的认证凭证') from exc


async def websocket_application(scope, receive, send):
    try:
        authenticate_scope(scope)
    except AuthenticationFailed:
        await send({'type': 'websocket.close', 'code': 4401})
        return

    await send({'type': 'websocket.accept'})
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    client = _Client(queue=queue, loop=loop)
    broker.register(client)

    async def sender():
        while True:
            message = await queue.get()
            await send({'type': 'websocket.send', 'text': message})

    async def receiver():
        while True:
            event = await receive()
            if event['type'] == 'websocket.disconnect':
                break
            if event['type'] == 'websocket.receive' and event.get('text') == 'ping':
                await send({'type': 'websocket.send', 'text': '{"type":"pong"}'})

    sender_task = asyncio.create_task(sender())
    receiver_task = asyncio.create_task(receiver())
    done, pending = await asyncio.wait(
        [sender_task, receiver_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    for task in done:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug('System event websocket task stopped with error', exc_info=True)
    broker.unregister(client)
