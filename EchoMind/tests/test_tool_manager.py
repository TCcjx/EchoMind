import asyncio

from mcp.tool_manager import MCPToolManager, Tool


def make_manager() -> MCPToolManager:
    manager = MCPToolManager(api_key="test-key-not-used")
    # 查询改写和重排都要调 LLM，测试里直接替换成确定性实现。
    manager.rewrite_query = lambda query, n=3: _async_list([query])
    return manager


async def _async_list(items):
    return list(items)


def sub_queries(*queries):
    def rewrite(query, n=3):
        return _async_list(queries)
    return rewrite


SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}


def test_fallback_result_is_marked_degraded_and_keeps_failed_stats():
    manager = make_manager()

    async def broken(params, context):
        raise RuntimeError("chromadb down")

    manager.register(Tool(
        name="knowledge_search",
        description="stub",
        handler=broken,
        schema=SCHEMA,
        fallback=lambda params, context, error: [{"title": "降级提示", "content": "请稍后重试", "score": 0.0}],
    ))

    result = asyncio.run(manager.call("knowledge_search", {"query": "退款"}))

    # 回归点：降级结果曾经过得和真实命中完全一样，Monitor 与调用方都看不出链路已经坏了。
    assert result.success is True
    assert result.fallback_used is True
    assert "chromadb down" in result.error
    tool = manager._tools["knowledge_search"]
    assert tool.stats.success == 0 and tool.stats.failed == 1


def test_search_with_rewrite_never_mixes_fallback_text_into_real_recall():
    manager = make_manager()
    manager.rewrite_query = sub_queries("正常查询", "异常查询")

    async def handler(params, context):
        if params["query"] == "异常查询":
            raise RuntimeError("vector store timeout")
        return [{"title": "退款政策", "chunk": 0, "content": "7 天内可退", "score": 0.8}]

    manager.register(Tool(
        name="knowledge_search",
        description="stub",
        handler=handler,
        schema=SCHEMA,
        fallback=lambda params, context, error: [{"title": "降级提示", "content": "请稍后重试", "score": 0.0}],
    ))

    result = asyncio.run(manager.search_with_rewrite("knowledge_search", "退款政策", top_k=5))

    assert result.success is True
    assert result.fallback_used is True
    assert [item["title"] for item in result.data] == ["退款政策"]

    # 全部子查询都失败时，降级文案仍会返回，但必须带着降级标记。
    manager.rewrite_query = sub_queries("异常查询", "异常查询")
    degraded_only = asyncio.run(manager.search_with_rewrite("knowledge_search", "退款政策", top_k=5))
    assert degraded_only.fallback_used is True
    assert [item["title"] for item in degraded_only.data] == ["降级提示"]


def test_same_chunk_hit_by_several_sub_queries_is_deduplicated_with_best_score():
    manager = make_manager()
    manager.rewrite_query = sub_queries("q1", "q2")
    responses = {
        "q1": [{"title": "退款政策", "chunk": 0, "content": "7 天内可退", "score": 0.81}],
        "q2": [{"title": "退款政策", "chunk": 0, "content": "7 天内可退", "score": 0.93}],
    }

    async def handler(params, context):
        return responses[params["query"]]

    manager.register(Tool(name="knowledge_search", description="stub", handler=handler, schema=SCHEMA))

    result = asyncio.run(manager.search_with_rewrite("knowledge_search", "退款政策", top_k=5))

    # 回归点：去重键曾是整条 dict 的哈希，score 一变同一个片段就算两条。
    assert len(result.data) == 1
    assert result.data[0]["score"] == 0.93
    # 合并时取的是副本：写回原对象会污染工具结果缓存里的数据。
    assert responses["q1"][0]["score"] == 0.81
    assert result.data[0] is not responses["q2"][0]


def test_distinct_chunks_are_kept_apart():
    manager = make_manager()
    manager.rewrite_query = sub_queries("q1", "q2")

    async def handler(params, context):
        index = 0 if params["query"] == "q1" else 1
        return [{"title": "退款政策", "chunk": index, "content": f"片段 {index}", "score": 0.7}]

    manager.register(Tool(name="knowledge_search", description="stub", handler=handler, schema=SCHEMA))

    result = asyncio.run(manager.search_with_rewrite("knowledge_search", "退款政策", top_k=5))

    assert [item["chunk"] for item in result.data] == [0, 1]
