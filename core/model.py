"""Resolve and shorten the model name shown for an agent.

Source of truth is the transcript: Claude writes the model id on each assistant
message (`message.model`). We read the most recent one. This avoids poking at
live process cmdlines and works for CCR-routed sessions too.
"""
from __future__ import annotations

import json


def shorten(model: str) -> str:
    """Map a full model id to a short dashboard label."""
    m = model.lower()
    table = [
        (("deepseek-v4-pro",), "v4-pro"),
        (("deepseek-v4-flash",), "v4-flash"),
        (("kimi-k2.6",), "kimi-k2.6"),
        (("qwen3-coder",), "qwen3-coder"),
        (("glm-5",), "glm-5"),
        (("claude-sonnet", "sonnet"), "sonnet"),
        (("claude-opus", "opus"), "opus"),
        (("claude-haiku", "haiku"), "haiku"),
    ]
    for needles, label in table:
        if any(n in m for n in needles):
            return label
    return model


def model_from_lines(lines: list[str]) -> str:
    """Most recent `message.model` in the transcript, or "" if none."""
    for ln in reversed(lines):
        if '"model"' not in ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        model = (obj.get("message") or {}).get("model")
        if model:
            return str(model)
    return ""
