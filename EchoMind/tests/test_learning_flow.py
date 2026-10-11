"""学习链路回归：假外部服务验证真实业务路径，不需要 Redis/ChromaDB/模型服务。"""
import asyncio
import json
from types import SimpleNamespace as NS

import httpx
import pytest
from prometheus_client import generate_latest

from api import main
from core.intent_recognizer import IntentCategory, UrgencyLevel
from agents.agent_orchestrator import AgentType
from evaluation.evaluator import EndToEndEvaluator
from memory.conversation_memory import MemoryManager, MemoryContext
from mcp.knowledge_base import KnowledgeBase
from monitor.performance_monitor import PerformanceMonitor


class RedisList:
    def __init__(self):
        self.rows = [json.dumps({'role': 'user', 'content': str(i), 'ts': '2026-10-11T00:00:00'})
                     for i in reversed(range(15))]
        self.summary = ''

    async def lrange(self, key, start, stop): return self.rows[start:stop + 1]
    async def get(self, key): return self.summary
    async def setex(self, key, ttl, value): self.summary = value
    async def expire(self, *args): pass
    async def ltrim(self, key, start, stop): self.rows = self.rows[start:len(self.rows) + stop + 1]


@pytest.mark.parametrize('append_during_summary', [False, True])
def test_compression_keeps_chronology_and_messages_added_during_summary(append_during_summary):
    manager = MemoryManager.__new__(MemoryManager)
    manager._redis = RedisList()
    manager._model = 'fake'

    async def create(**kwargs):
        if append_during_summary:
            manager._redis.rows.insert(0, json.dumps({'role': 'user', 'content': 'new', 'ts': '2026-10-11T00:00:01'}))
        return NS(content=[NS(type='text', text='summary')])

    async def store(*args): pass
    manager._client = NS(messages=NS(create=create))
    manager._store_episodic = store

    async def run():
        await asyncio.gather(manager._compress('u', 'c'), manager._compress('u', 'c'))
        messages = await manager._get_working_memory('u', 'c')
        assert [m.content for m in messages] == list(map(str, range(10, 15))) + (['new'] if append_during_summary else [])
    asyncio.run(run())


def test_episodic_query_reaches_cross_conversation_fallback():
    manager = MemoryManager.__new__(MemoryManager)
    calls = []

    async def query(text, n_results, where):
        # ChromaDB 要求一个顶层条件/操作符；错误写法会阻止后续检索。
        if len(where) != 1: raise ValueError('invalid where')
        calls.append(where)
        return {'documents': [['current']] if '$and' in where else [['current', 'previous']]}
    manager._query_episodic = query
    assert asyncio.run(manager._search_episodic('u', 'c', 'question')) == ['current', 'previous']
    assert calls == [{'$and': [{'user_id': 'u'}, {'conv_id': 'c'}]}, {'user_id': 'u'}]


@pytest.mark.parametrize('partial', [False, True])
def test_knowledge_context_uses_only_real_hits(monkeypatch, partial):
    async def search(*args, **kwargs):
        data = [{'title': 'fallback', 'content': 'unavailable', 'fallback': True}]
        if partial: data.append({'title': 'policy', 'content': 'real policy'})
        return NS(success=True, fallback_used=True, data=data)
    monkeypatch.setattr(main, '_tool_manager', NS(search_with_rewrite=search))
    context, used = asyncio.run(main._build_knowledge_context('refund?', intent='refund'))
    assert used is partial
    assert 'unavailable' not in context
    assert ('real policy' in context) is partial


@pytest.mark.parametrize('payload', ['[1]', '[{"title":"x","content":2}]', '[]', '[{"title":"x","content":""}]'])
def test_invalid_upload_returns_actionable_client_error(monkeypatch, payload):
    kb = KnowledgeBase.__new__(KnowledgeBase)
    monkeypatch.setattr(main, '_tool_manager', NS(_tools={'knowledge_search': NS(handler=kb.search_handler)}))

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            response = await client.post('/knowledge/upload', files={'file': ('input.JSON', payload, 'application/json')})
        assert response.status_code == 400
        assert 'title' in response.json()['detail']
    asyncio.run(run())


def test_long_sentence_is_bounded_without_losing_text():
    kb = KnowledgeBase.__new__(KnowledgeBase)
    source = 'a' * 2000
    chunks = kb._chunk_text(source)
    assert ''.join(chunks) == source
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_default_metrics_measure_chat_turns_not_polling():
    orchestrator = NS(get_stats=lambda: {'general_0': {'success_rate': 1, 'avg_ms': 2000, 'total': 1, 'routing_score': 1}})
    tools = NS(get_stats=lambda: {})
    monitor = PerformanceMonitor(orchestrator, tools)
    other = PerformanceMonitor(orchestrator, tools)  # 独立注册表，重复初始化不冲突。
    monitor.record_request(2500, True)
    monitor.record_request(6000, False)
    asyncio.run(monitor._collect())
    asyncio.run(monitor._collect())
    text = generate_latest(monitor.registry).decode()
    assert 'requests_total{status="ok"} 1.0' in text
    assert 'requests_total{status="error"} 1.0' in text
    assert 'chat_latency_ms_count 2.0' in text
    assert 'chat_latency_ms_sum 8500.0' in text
    assert 'agent_avg_latency_ms{agent="general_0"} 2000.0' in text
    assert 'chat_latency_ms_count 0.0' in generate_latest(other.registry).decode()


def test_evaluation_runs_actual_memory_rag_and_entity_pipeline(monkeypatch):
    writes, requests, searches = [], [], []

    async def get_context(user_id, conv_id, query):
        return MemoryContext([], [], {}, '')
    async def add_message(user_id, conv_id, role, content): writes.append((conv_id, role.value, content))
    async def update_profile(*args): pass
    async def recognize(message, history):
        return NS(intent=IntentCategory.REFUND, intent_group='billing', urgency=UrgencyLevel.MEDIUM,
                  confidence=.9, entities={'order_id': ['order-1']}, source_scores={})
    async def search(*args, **kwargs):
        searches.append(args)
        return NS(success=True, data=[{'title': 'policy', 'content': 'policy evidence'}])
    async def run_request(req):
        requests.append(req)
        return NS(response='answer', request_id=req.request_id, intent=req.intent, agent_type=AgentType.BILLING,
                  agent_types=[AgentType.BILLING], primary_agent=AgentType.BILLING, supporting_agents=[],
                  tools_used=['search_knowledge_base'], tool_traces=[{'knowledge_hit': False}],
                  routing_reason='test', routing_confidence=.9, escalated=False)
    monkeypatch.setattr(main, '_memory', NS(get_context=get_context, add_message=add_message, update_profile=update_profile))
    monkeypatch.setattr(main, '_orchestrator', NS(recognize_intent=recognize, run=run_request))
    monkeypatch.setattr(main, '_tool_manager', NS(search_with_rewrite=search))
    monkeypatch.setattr(main, '_monitor', None)
    evaluator = EndToEndEvaluator(None, None, 'fake-key', dialog_runner=main._evaluate_chat_turn)
    async def judge(*args, **kwargs):
        return NS(overall=1, relevance=1, accuracy=1, completeness=1, helpfulness=1, judge_failed=False, error=None)
    evaluator._judge = NS(judge=judge)
    results = asyncio.run(evaluator._evaluate_dialog_case({'turns': ['refund?', 'when?']}, 0))
    assert len(results) == 2 and all(r.passed for r in results)
    assert len(writes) == 4 and len(searches) == 2
    assert requests[0].conv_id == requests[1].conv_id
    assert requests[0].entities == {'order_id': ['order-1']}
    assert 'policy evidence' in requests[0].context


def test_failed_agent_search_is_not_a_knowledge_hit(monkeypatch):
    # 沿用同一套 API 请求结构，单独验证 knowledge_used 不再由工具名决定。
    # 工具调用失败时 trace 没有 knowledge_hit；真实预检索为空。
    async def empty(*args, **kwargs): return ('', False)
    monkeypatch.setattr(main, '_build_knowledge_context', empty)
    async def context(*args, **kwargs): return MemoryContext([], [], {}, '')
    async def noop(*args): pass
    async def recognize(*args, **kwargs):
        return NS(intent=IntentCategory.REFUND, urgency=UrgencyLevel.MEDIUM, intent_group='billing',
                  confidence=.9, entities={}, source_scores={})
    async def run(req):
        return NS(response='answer', request_id=req.request_id, intent=req.intent, agent_type=AgentType.BILLING,
                  agent_types=[], primary_agent=None, supporting_agents=[], tools_used=['search_knowledge_base'],
                  tool_traces=[{'result_success': False}], routing_reason='', routing_confidence=0, escalated=False)
    monkeypatch.setattr(main, '_memory', NS(get_context=context, add_message=noop, update_profile=noop))
    monkeypatch.setattr(main, '_orchestrator', NS(recognize_intent=recognize, run=run))
    monkeypatch.setattr(main, '_monitor', None)
    assert not asyncio.run(main.chat(main.ChatRequest(message='refund?'))).knowledge_used
