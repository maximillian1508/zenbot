from __future__ import annotations

from pathlib import Path


def _read_skill(path: Path) -> str:
    if not path.exists():
        return f"(missing: {path})"
    return path.read_text(encoding="utf-8").strip()


def build_prompt(
    *,
    agent_id: str,
    display_name: str,
    backend: str,
    workspace: Path,
    system_prompt: str,
    skill_paths: tuple[str, ...],
    user_message: str,
    model: str | None = None,
) -> str:
    model_bit = f" | Model: {model}" if model else ""
    parts: list[str] = [
        f"[Agent: {agent_id} ({display_name}) | Backend: {backend}{model_bit} | Workspace: {workspace}]",
    ]
    if system_prompt:
        parts.extend(["", "--- System ---", system_prompt, "---"])
    if skill_paths:
        parts.append("")
        parts.append("Follow skill(s):")
        for raw in skill_paths:
            path = Path(raw).expanduser()
            name = path.name
            body = _read_skill(path)
            parts.extend([f"--- {name} ---", body, "---"])
    parts.extend(["", "User request:", user_message])
    return "\n".join(parts)
