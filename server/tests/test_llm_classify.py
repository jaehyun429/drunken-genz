import json, httpx, openai
from app.llm.client import classify, LLMConnectionError, LLMModelError


def _status(code):
    req = httpx.Request("POST", "http://x/v1/chat/completions")
    return openai.APIStatusError("x", response=httpx.Response(code, request=req), body=None)


def test_connection_errors():
    req = httpx.Request("POST", "http://x")
    assert classify(openai.APIConnectionError(request=req)) is LLMConnectionError
    assert classify(openai.APITimeoutError(request=req)) is LLMConnectionError
    assert classify(_status(503)) is LLMConnectionError


def test_model_errors():
    assert classify(_status(400)) is LLMModelError
    assert classify(_status(500)) is LLMModelError
    assert classify(json.JSONDecodeError("x", "{", 0)) is LLMModelError
