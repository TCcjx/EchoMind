import asyncio

from core.intent_recognizer import IntentRecognizer, IntentCategory, _TEMPLATES


class CapturingClient:
    """记录 prompt 并返回固定文本响应的假 Anthropic 客户端。"""

    def __init__(self, reply="", error=None):
        self.calls = []
        self._reply = reply
        self._error = error

        class Messages:
            async def create(inner, **kwargs):
                self.calls.append(kwargs)
                if self._error:
                    raise self._error
                return type("Response", (), {"content": [{"type": "text", "text": self._reply}]})()

        self.messages = Messages()


def make_recognizer(reply="", error=None) -> IntentRecognizer:
    recognizer = IntentRecognizer(api_key="test-key-not-used")
    # 构造期会直接创建 AsyncAnthropic，不支持注入，因此在真正发起调用前替换 client。
    recognizer.client = CapturingClient(reply, error)
    return recognizer


def test_llm_prompt_actually_carries_few_shot_examples():
    # 回归点：examples 曾只被拼接成字符串却没有插入 prompt，最高权重那路实际是 zero-shot。
    recognizer = make_recognizer('{"intent": "refund", "confidence": 0.91, "reasoning": "明确要求退款"}')

    result = asyncio.run(recognizer._llm_recognize("这一单帮我退款", history=None))
    prompt = recognizer.client.calls[0]["messages"][0]["content"]

    assert result["intent"] is IntentCategory.REFUND
    assert result["confidence"] == 0.91
    assert "参考示例:" in prompt
    for cat, templates in _TEMPLATES.items():
        assert f'消息: "{templates[0]}" → 意图: {cat.value}' in prompt
    assert "这一单帮我退款" in prompt


def test_llm_prompt_includes_recent_history_context():
    recognizer = make_recognizer('{"intent": "other", "confidence": 0.3, "reasoning": "信息不足"}')
    history = [{"role": "user", "content": "上一轮：订单还没发货"}, {"role": "assistant", "content": "正在查询"}]

    asyncio.run(recognizer._llm_recognize("那退款呢", history=history))
    prompt = recognizer.client.calls[0]["messages"][0]["content"]

    assert "最近对话:" in prompt
    assert "上一轮：订单还没发货" in prompt


def test_llm_recognize_falls_back_when_provider_fails():
    recognizer = make_recognizer(error=RuntimeError("provider down"))

    result = asyncio.run(recognizer._llm_recognize("你好", history=None))

    assert result["intent"] is IntentCategory.OTHER
    assert result["confidence"] == 0.0
    assert result["failed"] is True
