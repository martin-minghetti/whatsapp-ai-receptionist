import pytest
from core.history import InMemoryHistory, RedisHistory, get_history

MAX = 20

def test_in_memory_add_and_get():
    h = InMemoryHistory()
    h.add("phone1", "user", "hello")
    h.add("phone1", "assistant", "hi")
    msgs = h.get("phone1")
    assert len(msgs) == 2
    assert msgs[0] == {"role": "user", "content": "hello"}
    assert msgs[1] == {"role": "assistant", "content": "hi"}

def test_in_memory_max_messages():
    h = InMemoryHistory(max_messages=MAX)
    for i in range(25):
        h.add("phone1", "user", f"msg {i}")
    assert len(h.get("phone1")) == MAX

def test_in_memory_different_phones_isolated():
    h = InMemoryHistory()
    h.add("phone1", "user", "msg for 1")
    h.add("phone2", "user", "msg for 2")
    assert len(h.get("phone1")) == 1
    assert h.get("phone1")[0]["content"] == "msg for 1"

def test_get_history_returns_in_memory_without_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    h = get_history()
    assert isinstance(h, InMemoryHistory)
