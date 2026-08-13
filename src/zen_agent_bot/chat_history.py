"""Sliding chat-turn window for stateless backends (OpenRouter)."""

from __future__ import annotations

CHAT_TURN_MAX = 20
CHAT_TURN_MAX_CHARS = 24_000
CHAT_TURN_CLIP = 4_000


def clip_turn(content: str, limit: int = CHAT_TURN_CLIP) -> str:
    body = (content or "").strip()
    if len(body) <= limit:
        return body
    return body[: limit - 1] + "…"


def window_turns(
    turns: list[dict[str, str]],
    *,
    max_turns: int = CHAT_TURN_MAX,
    max_chars: int = CHAT_TURN_MAX_CHARS,
) -> list[dict[str, str]]:
    """Keep newest turns under count + char budget. Oldest dropped first."""
    cleaned: list[dict[str, str]] = []
    for turn in turns:
        role = (turn.get("role") or "").strip()
        content = clip_turn(turn.get("content") or "")
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content})
    while len(cleaned) > max_turns:
        cleaned.pop(0)
    while len(cleaned) > 1 and sum(len(t["content"]) for t in cleaned) > max_chars:
        cleaned.pop(0)
    return cleaned


def build_openrouter_messages(
    *,
    system: str,
    history: list[dict[str, str]],
    prompt: str,
) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system}]
    messages.extend(window_turns(history))
    messages.append({"role": "user", "content": prompt})
    return messages
