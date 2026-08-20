from __future__ import annotations

from pathlib import Path

_SKILL_FILE = "SKILL.md"


def _looks_like_path(ref: str) -> bool:
    text = ref.strip()
    return "/" in text or text.endswith(".md")


def _skill_roots(project_root: Path | None) -> list[Path]:
    roots = [
        Path.home() / ".cursor" / "skills",
        Path.home() / ".cursor" / "skills-cursor",
    ]
    if project_root is not None:
        roots.append(project_root / "skills")
    return roots


def resolve_skill_ref(ref: str, *, project_root: Path | None = None) -> Path:
    """Resolve a skill name or path to a SKILL.md file."""
    text = ref.strip()
    if not text:
        return Path(text)
    if _looks_like_path(text):
        path = Path(text).expanduser()
        if path.is_file():
            return path
        if path.is_dir():
            candidate = path / _SKILL_FILE
            if candidate.is_file():
                return candidate
        return path
    name = text.removesuffix(f"/{_SKILL_FILE}").strip("/")
    for root in _skill_roots(project_root):
        candidate = root / name / _SKILL_FILE
        if candidate.is_file():
            return candidate
    return Path(name)


def skill_display_name(ref: str, path: Path) -> str:
    if _looks_like_path(ref):
        if path.name == _SKILL_FILE and path.parent.name:
            return path.parent.name
        return path.name
    return ref.strip()


def list_discoverable_skills(*, project_root: Path | None = None) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for root in _skill_roots(project_root):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            skill = child / _SKILL_FILE
            if skill.is_file() and child.name not in seen:
                seen.add(child.name)
                names.append(child.name)
    return names


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
    project_root: Path | None = None,
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
            path = resolve_skill_ref(raw, project_root=project_root)
            name = skill_display_name(raw, path)
            body = _read_skill(path)
            parts.extend([f"--- {name} ---", body, "---"])
    parts.extend(["", "User request:", user_message])
    return "\n".join(parts)
