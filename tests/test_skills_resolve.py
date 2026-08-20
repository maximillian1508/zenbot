from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zen_agent_bot.skills.loader import (
    build_prompt,
    list_discoverable_skills,
    resolve_skill_ref,
    skill_display_name,
)


class ResolveSkillRefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        personal = self.root / "personal" / "my-skill"
        personal.mkdir(parents=True)
        (personal / "SKILL.md").write_text("personal body", encoding="utf-8")
        project = self.root / "project" / "skills" / "proj-skill"
        project.mkdir(parents=True)
        (project / "SKILL.md").write_text("project body", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_name_resolves_under_custom_roots(self) -> None:
        # simulate search order via explicit path check
        path = self.root / "personal" / "my-skill" / "SKILL.md"
        self.assertTrue(path.is_file())
        self.assertEqual(
            skill_display_name("my-skill", path),
            "my-skill",
        )

    def test_absolute_path(self) -> None:
        skill = self.root / "personal" / "my-skill" / "SKILL.md"
        resolved = resolve_skill_ref(str(skill))
        self.assertEqual(resolved, skill)

    def test_missing_name(self) -> None:
        resolved = resolve_skill_ref("no-such-skill-xyz")
        self.assertEqual(resolved, Path("no-such-skill-xyz"))

    def test_build_prompt_uses_name_header(self) -> None:
        skill = self.root / "personal" / "my-skill" / "SKILL.md"
        text = build_prompt(
            agent_id="manager",
            display_name="Manager",
            backend="cursor-cli",
            workspace=Path("/tmp"),
            system_prompt="",
            skill_paths=(str(skill),),
            user_message="hi",
        )
        self.assertIn("--- my-skill ---", text)
        self.assertIn("personal body", text)


class ListSkillsTests(unittest.TestCase):
    def test_list_from_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "skills" / "alpha"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("x", encoding="utf-8")
            names = list_discoverable_skills(project_root=root)
            self.assertIn("alpha", names)


if __name__ == "__main__":
    unittest.main()
