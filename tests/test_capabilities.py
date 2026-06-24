from __future__ import annotations

import unittest
from pathlib import Path

from crop_search_framework.capabilities import CapabilityMapWriter


REPO_ROOT = Path(__file__).resolve().parents[1]


class CapabilityMapTests(unittest.TestCase):
    def test_render_contains_statuses_and_update_rule(self) -> None:
        rendered = CapabilityMapWriter(REPO_ROOT).render()

        self.assertIn("Global tier-aware query planning", rendered)
        self.assertIn("Scientific and textbook evidence handling", rendered)
        self.assertIn("write-capability-map", rendered)
        self.assertIn("`operational`", rendered)
        self.assertIn("`partial`", rendered)


if __name__ == "__main__":
    unittest.main()
