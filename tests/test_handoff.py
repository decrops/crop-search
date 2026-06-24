from __future__ import annotations

import unittest
from pathlib import Path

from crop_search_framework.handoff import HandoffWriter


REPO_ROOT = Path(__file__).resolve().parents[1]


class HandoffTests(unittest.TestCase):
    def test_handoff_render_contains_current_next_step_and_automation_rule(self) -> None:
        rendered = HandoffWriter(REPO_ROOT).render()

        self.assertIn("pilot-global-wheat-001", rendered)
        self.assertIn("run-exploration --run-config config/runs/pilot-global-wheat.json", rendered)
        self.assertIn("write-capability-map", rendered)
        self.assertIn("write-handoff", rendered)
        self.assertIn("docs/CAPABILITY_MAP.md", rendered)
        self.assertIn("Source tiers are implemented", rendered)
        self.assertIn("Future sessions should read `AGENTS.md`", rendered)


if __name__ == "__main__":
    unittest.main()
