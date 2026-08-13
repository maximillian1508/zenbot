from __future__ import annotations

import unittest
from collections import deque
from dataclasses import dataclass

from zen_agent_bot.gateway.queue import drop_by_id, promote_by_id, queued_count


@dataclass
class _Job:
    job_id: str
    prompt: str


class QueueOpsTests(unittest.TestCase):
    def test_promote_moves_to_front(self) -> None:
        pending: deque[_Job] = deque(
            [_Job("a", "one"), _Job("b", "two"), _Job("c", "three")]
        )
        job = promote_by_id(pending, "c")
        assert job is not None
        self.assertEqual(job.job_id, "c")
        self.assertEqual([j.job_id for j in pending], ["c", "a", "b"])

    def test_promote_missing(self) -> None:
        pending: deque[_Job] = deque([_Job("a", "one")])
        self.assertIsNone(promote_by_id(pending, "nope"))
        self.assertEqual([j.job_id for j in pending], ["a"])

    def test_drop(self) -> None:
        pending: deque[_Job] = deque([_Job("a", "one"), _Job("b", "two")])
        job = drop_by_id(pending, "a")
        assert job is not None
        self.assertEqual(job.prompt, "one")
        self.assertEqual([j.job_id for j in pending], ["b"])

    def test_queued_count_skips_sentinel(self) -> None:
        stop = object()
        pending: deque[object] = deque([_Job("a", "one"), stop])
        self.assertEqual(queued_count(pending, stop=stop), 1)
