from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from zen_agent_bot.outbound import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILES,
    describe_unsupported,
    extract_outbound,
    limits_from_env,
)


class ExtractOutboundTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def _file(self, name: str, size: int = 16) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        return path

    def _extract(self, text: str, **kwargs):
        return extract_outbound(text, allowed_roots=(self.root,), **kwargs)

    def test_no_marker_returns_text_unchanged(self) -> None:
        out = self._extract("just a normal reply")
        self.assertEqual(out.text, "just a normal reply")
        self.assertFalse(out.has_files)
        self.assertEqual(out.notes, [])

    def test_valid_file_attached_and_marker_stripped(self) -> None:
        shot = self._file("shot.png")
        out = self._extract(f"Here is the page.\n\n[[attach: {shot}]]")
        self.assertEqual([f.path for f in out.files], [shot])
        self.assertEqual([f.name for f in out.files], ["shot.png"])
        self.assertEqual(out.text, "Here is the page.")
        self.assertNotIn("attach", out.text)
        self.assertEqual(out.notes, [])

    def test_marker_is_case_and_space_tolerant(self) -> None:
        shot = self._file("a.png")
        out = self._extract(f"[[ATTACH:   {shot}   ]]")
        self.assertEqual([f.path for f in out.files], [shot])

    def test_quoted_path_accepted(self) -> None:
        shot = self._file("b.png")
        out = self._extract(f'[[attach: "{shot}"]]')
        self.assertEqual([f.path for f in out.files], [shot])

    def test_path_outside_roots_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "secret.txt"
            outside.write_text("nope")
            out = self._extract(f"[[attach: {outside}]]")
        self.assertFalse(out.has_files)
        self.assertTrue(any("outside the allowed" in n for n in out.notes))

    def test_traversal_rejected(self) -> None:
        out = self._extract(f"[[attach: {self.root}/../../etc/passwd]]")
        self.assertFalse(out.has_files)
        self.assertTrue(any("outside the allowed" in n for n in out.notes))

    def test_symlink_escape_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            target = Path(other) / "outside.txt"
            target.write_text("data")
            link = self.root / "link.txt"
            link.symlink_to(target)
            out = self._extract(f"[[attach: {link}]]")
        self.assertFalse(out.has_files)
        self.assertTrue(any("outside the allowed" in n for n in out.notes))

    def test_missing_file_noted(self) -> None:
        out = self._extract(f"[[attach: {self.root}/ghost.png]]")
        self.assertFalse(out.has_files)
        self.assertTrue(any("not found" in n for n in out.notes))

    def test_directory_rejected(self) -> None:
        sub = self.root / "sub"
        sub.mkdir()
        out = self._extract(f"[[attach: {sub}]]")
        self.assertFalse(out.has_files)

    def test_oversize_rejected(self) -> None:
        big = self._file("big.bin", size=2048)
        out = self._extract(f"[[attach: {big}]]", max_bytes=1024)
        self.assertFalse(out.has_files)
        self.assertTrue(any("limit" in n for n in out.notes))

    def test_secret_names_rejected(self) -> None:
        for name in (".env", ".env.local", "id_rsa", "server.pem", "creds.key"):
            with self.subTest(name=name):
                path = self._file(name)
                out = self._extract(f"[[attach: {path}]]")
                self.assertFalse(out.has_files, f"{name} should be blocked")
                self.assertTrue(any("secret" in n for n in out.notes))

    def test_secret_directories_rejected(self) -> None:
        path = self._file(".ssh/known_hosts")
        out = self._extract(f"[[attach: {path}]]")
        self.assertFalse(out.has_files)
        self.assertTrue(any("secret" in n for n in out.notes))

    def test_max_files_cap(self) -> None:
        paths = [self._file(f"f{i}.png") for i in range(4)]
        markers = "\n".join(f"[[attach: {p}]]" for p in paths)
        out = self._extract(markers, max_files=2)
        self.assertEqual(len(out.files), 2)
        self.assertTrue(any("limit" in n for n in out.notes))

    def test_duplicates_collapsed(self) -> None:
        shot = self._file("dup.png")
        out = self._extract(f"[[attach: {shot}]]\n[[attach: {shot}]]")
        self.assertEqual(len(out.files), 1)

    def test_text_survives_with_markers_between_prose(self) -> None:
        a = self._file("one.png")
        b = self._file("two.png")
        out = self._extract(f"before\n[[attach: {a}]]\nmiddle\n[[attach: {b}]]\nafter")
        self.assertEqual(len(out.files), 2)
        self.assertEqual(out.text, "before\nmiddle\nafter")

    def test_marker_only_reply_yields_empty_text(self) -> None:
        shot = self._file("solo.png")
        out = self._extract(f"[[attach: {shot}]]")
        self.assertEqual(out.text, "")
        self.assertEqual(len(out.files), 1)


class LimitsFromEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k) for k in ("OUTBOUND_MAX_BYTES", "OUTBOUND_MAX_FILES")
        }
        self.addCleanup(self._restore)
        for key in self._saved:
            os.environ.pop(key, None)

    def _restore(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_defaults(self) -> None:
        self.assertEqual(limits_from_env(), (DEFAULT_MAX_BYTES, DEFAULT_MAX_FILES))

    def test_override(self) -> None:
        os.environ["OUTBOUND_MAX_BYTES"] = "1024"
        os.environ["OUTBOUND_MAX_FILES"] = "3"
        self.assertEqual(limits_from_env(), (1024, 3))

    def test_garbage_and_zero_fall_back(self) -> None:
        os.environ["OUTBOUND_MAX_BYTES"] = "not-a-number"
        os.environ["OUTBOUND_MAX_FILES"] = "0"
        self.assertEqual(limits_from_env(), (DEFAULT_MAX_BYTES, DEFAULT_MAX_FILES))


class DescribeUnsupportedTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(describe_unsupported([]), "")

    def test_lists_paths(self) -> None:
        from zen_agent_bot.outbound import OutboundFile

        text = describe_unsupported(
            [OutboundFile(path=Path("/tmp/a.png"), name="a.png")]
        )
        self.assertIn("/tmp/a.png", text)
        self.assertIn("can't upload", text)


class SendFilesReplyTests(unittest.IsolatedAsyncioTestCase):
    """Upload helper: batching + reply shape, without touching Discord."""

    async def test_batches_and_replies_with_files(self) -> None:
        from zen_agent_bot.transports.discord import send_files_reply

        calls: list[list[str]] = []
        kwargs_seen: list[dict] = []

        class FakeMsg:
            async def reply(self, *, files, mention_author):
                calls.append([f.filename for f in files])
                kwargs_seen.append({"mention_author": mention_author})

        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(5):
                path = Path(tmp) / f"s{i}.png"
                path.write_bytes(b"\x89PNG\r\n\x1a\n")
                paths.append(path)
            await send_files_reply(FakeMsg(), paths, per_message=2)

        self.assertEqual(
            calls, [["s0.png", "s1.png"], ["s2.png", "s3.png"], ["s4.png"]]
        )
        self.assertTrue(all(k["mention_author"] is False for k in kwargs_seen))

    async def test_no_paths_no_calls(self) -> None:
        from zen_agent_bot.transports.discord import send_files_reply

        class FakeMsg:
            async def reply(self, **kwargs):
                raise AssertionError("should not be called")

        await send_files_reply(FakeMsg(), [])


class OutboundRootsTests(unittest.TestCase):
    def test_roots_cover_workspace_data_and_tmp(self) -> None:
        from types import SimpleNamespace

        from zen_agent_bot.gateway.router import Gateway

        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as data:
            gw = Gateway.__new__(Gateway)
            gw.config = SimpleNamespace(data_dir=Path(data))
            roots = Gateway._outbound_roots(gw, Path(ws))
            self.assertIn(Path(ws).resolve(), roots)
            self.assertIn(Path(data).resolve(), roots)
            self.assertIn(Path("/tmp").resolve(), roots)
            # no duplicates
            self.assertEqual(len(roots), len(set(roots)))


if __name__ == "__main__":
    unittest.main()
