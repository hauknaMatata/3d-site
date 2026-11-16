from app.ai.providers.adapters import AnthropicProvider, GeminiProvider, OllamaProvider, OpenAIProvider
from app.ai.providers.base import sanitize_schema

CONVO = [
    {"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image", "media_type": "image/png", "data": "AAA"}]},
    {"role": "assistant", "text": "ok", "tool_calls": [{"id": "t1", "name": "create_box", "args": {"width": 1}}]},
    {"role": "tool", "results": [{"id": "t1", "name": "create_box", "content": "{}", "is_error": False}]},
]


def test_anthropic_format():
    m = AnthropicProvider.convert(CONVO)
    assert m[0]["content"][1]["source"]["type"] == "base64"
    assert m[1]["content"][1]["type"] == "tool_use" and m[2]["content"][0]["type"] == "tool_result"


def test_openai_format():
    m = OpenAIProvider.convert("sys", CONVO)
    assert m[0]["role"] == "system" and m[1]["content"][1]["type"] == "image_url"
    assert m[2]["tool_calls"][0]["function"]["arguments"] == '{"width": 1}' and m[3]["role"] == "tool"


def test_gemini_format_and_schema():
    m = GeminiProvider.convert(CONVO)
    assert m[1]["role"] == "model" and "functionCall" in m[1]["parts"][1] and "functionResponse" in m[2]["parts"][0]
    s = sanitize_schema({"type": "object", "properties": {"m": {"anyOf": [{"type": "string"}, {"type": "object"}]},
                                                           "s": {"description": "number or [x,y,z]"}}}, True)
    assert "anyOf" not in s["properties"]["m"] and s["properties"]["s"]["type"] == "number"


def test_ollama_format():
    m = OllamaProvider.convert("sys", CONVO)
    assert m[1]["images"] == ["AAA"] and m[2]["tool_calls"][0]["function"]["arguments"] == {"width": 1}
