import json
import os
from collections import defaultdict

MAX_MESSAGES = 20


class InMemoryHistory:
    def __init__(self, max_messages: int = MAX_MESSAGES):
        self._store: dict[str, list] = defaultdict(list)
        self._max = max_messages

    def add(self, phone: str, role: str, content: str) -> None:
        self._store[phone].append({"role": role, "content": content})
        if len(self._store[phone]) > self._max:
            self._store[phone] = self._store[phone][-self._max:]

    def get(self, phone: str) -> list[dict]:
        return list(self._store[phone])


class RedisHistory:
    def __init__(self, redis_url: str, max_messages: int = MAX_MESSAGES, ttl_seconds: int = 7 * 24 * 3600):
        import redis
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._max = max_messages
        self._ttl = ttl_seconds

    def _key(self, phone: str) -> str:
        return f"history:{phone}"

    def add(self, phone: str, role: str, content: str) -> None:
        key = self._key(phone)
        msgs = self.get(phone)
        msgs.append({"role": role, "content": content})
        if len(msgs) > self._max:
            msgs = msgs[-self._max:]
        self._redis.setex(key, self._ttl, json.dumps(msgs))

    def get(self, phone: str) -> list[dict]:
        key = self._key(phone)
        raw = self._redis.get(key)
        if not raw:
            return []
        return json.loads(raw)


def get_history() -> InMemoryHistory | RedisHistory:
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        try:
            h = RedisHistory(redis_url)
            h._redis.ping()
            return h
        except Exception:
            pass
    return InMemoryHistory()
