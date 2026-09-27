import pytest

# mcp.knowledge_base 在模块级 import chromadb；没有该依赖时跳过而不是让整条 pytest 中断收集。
pytest.importorskip("chromadb", reason="knowledge_base 在模块级依赖 chromadb")

from mcp.knowledge_base import KnowledgeBase


class FakeCollection:
    """只实现 add/upsert 语义差异的 ChromaDB collection 替身。"""

    def __init__(self, existing_ids=()):
        self.existing = set(existing_ids)
        self.upsert_calls = []
        self.add_calls = []

    def add(self, ids, documents, metadatas):
        self.add_calls.append(list(ids))
        duplicated = self.existing & set(ids)
        if duplicated:
            # 真实 ChromaDB 在这里抛 DuplicateError，整批导入一起失败。
            raise ValueError(f"Duplicate error for ids: {sorted(duplicated)}")
        self.existing.update(ids)

    def upsert(self, ids, documents, metadatas):
        assert len(ids) == len(documents) == len(metadatas)
        assert len(set(ids)) == len(ids), "同一批内不应出现重复 id"
        self.upsert_calls.append(list(ids))
        self.existing.update(ids)


def make_knowledge_base(existing_ids=()) -> KnowledgeBase:
    # 构造器会立即连接 ChromaDB 并可能触发 embedding 模型下载，这里只测导入逻辑。
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb._collection = FakeCollection(existing_ids)
    return kb


DOC = {"title": "退款政策", "content": "订单签收后 7 天内可申请退款。超出期限需联系人工客服。"}


def test_add_documents_writes_via_upsert_not_add():
    kb = make_knowledge_base()

    count = kb.add_documents([DOC])

    assert count == 1
    assert kb._collection.add_calls == []
    assert len(kb._collection.upsert_calls) == 1


def test_re_importing_the_same_document_does_not_crash():
    # 回归点：doc_id 由「标题+切片序号+前缀」确定，重复导入同一篇文档必然命中已有 id；
    # 旧实现用 add() 会让第二次导入直接抛 DuplicateError。
    kb = make_knowledge_base()
    first = kb.add_documents([DOC])

    second = kb.add_documents([DOC])

    assert first == second
    assert kb._collection.upsert_calls[0] == kb._collection.upsert_calls[1]
    assert len(kb._collection.existing) == first


def test_duplicate_documents_in_one_batch_are_collapsed():
    kb = make_knowledge_base()

    count = kb.add_documents([DOC, dict(DOC)])

    assert count == 1
    assert len(kb._collection.upsert_calls) == 1
    assert len(kb._collection.upsert_calls[0]) == 1


def test_long_document_is_split_into_distinct_chunks():
    kb = make_knowledge_base()
    content = "这是第%d个句子，用来验证长文档会被切片。" * 60

    count = kb.add_documents([{"title": "长文档", "content": content}])

    assert count > 1
    assert len(set(kb._collection.upsert_calls[0])) == count
