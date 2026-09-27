"""记忆层回归：Redis 只是加速器，连不上时 /chat 仍要能正常回答。"""
import asyncio
import json

import pytest

# memory.conversation_memory 在模块级 import chromadb，缺依赖时整套记忆测试跳过。
pytest.importorskip("chromadb", reason="conversation_memory 在模块级依赖 chromadb")

from memory.conversation_memory import MemoryManager, MsgRole  # noqa: E402


class DeadRedis:
    """所有读写都抛连接异常，模拟 Docker 里的 redis 没起来。"""

    def __init__(self):
        self.writes = []

    async def lrange(self, *args, **kwargs):
        raise ConnectionError("Error 111 connecting to localhost:6379")

    async def get(self, *args, **kwargs):
        raise ConnectionError("Error 111 connecting to localhost:6379")

    async def lpush(self, key, value):
        self.writes.append((key, value))
        raise ConnectionError("Error 111 connecting to localhost:6379")

    async def expire(self, *args, **kwargs):
        raise ConnectionError("Error 111 connecting to localhost:6379")

    async def llen(self, *args, **kwargs):
        raise ConnectionError("Error 111 connecting to localhost:6379")


class BrokenThenGoodRedis:
    """列表里混着一条坏数据，读取时应跳过它而不是整轮记忆报废。"""

    def __init__(self, raws):
        self._raws = raws

    async def lrange(self, key, start, stop):
        return list(self._raws)


def _manager(redis):
    mgr = MemoryManager.__new__(MemoryManager)   # 绕开 Anthropic / ChromaDB 初始化
    mgr._redis = redis
    return mgr


def test_working_memory_falls_back_to_empty_when_redis_is_down():
    assert asyncio.run(_manager(DeadRedis())._get_working_memory("u1", "c1")) == []


def test_get_context_survives_redis_being_down():
    ctx = asyncio.run(_manager(DeadRedis()).get_context("u1", "c1", "退款多久到账"))
    assert ctx.recent_messages == []
    assert ctx.summary == ""


def test_add_message_does_not_raise_when_redis_write_fails():
    redis = DeadRedis()
    asyncio.run(_manager(redis).add_message("u1", "c1", MsgRole.USER, "我要退款"))
    assert redis.writes, "写入尝试过但失败被吞掉，而不是没走到 Redis"


def test_corrupted_working_memory_entry_is_skipped_not_fatal():
    good = json.dumps({"role": "user", "content": "第一问", "ts": "2026-09-27T10:00:00"})
    redis = BrokenThenGoodRedis([good, "{not json", good])
    msgs = asyncio.run(_manager(redis)._get_working_memory("u1", "c1"))
    assert [m.content for m in msgs] == ["第一问", "第一问"]
