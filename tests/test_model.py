"""Tests for model name resolution/shortening."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import model  # noqa: E402


def test_shorten_known():
    assert model.shorten("claude-opus-4-8") == "opus"
    assert model.shorten("claude-sonnet-4-6") == "sonnet"
    assert model.shorten("deepseek/deepseek-v4-pro") == "v4-pro"
    assert model.shorten("z-ai/glm-5") == "glm-5"


def test_shorten_unknown_passthrough():
    assert model.shorten("some-future-model") == "some-future-model"


def test_model_from_lines_latest():
    lines = [
        json.dumps({"type": "assistant", "message": {"model": "claude-opus-4-8"}}),
        json.dumps({"type": "assistant", "message": {"model": "claude-sonnet-4-6"}}),
    ]
    assert model.model_from_lines(lines) == "claude-sonnet-4-6"


def test_model_from_lines_none():
    assert model.model_from_lines([json.dumps({"type": "user", "message": {"content": "hi"}})]) == ""
