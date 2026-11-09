import os
import tempfile
import unittest
import sys
from pathlib import Path

TMP = tempfile.TemporaryDirectory()
os.environ["XAR_STATE"] = TMP.name
sys.path.insert(0, str(Path(__file__).parents[1]))

from inbox import InboxState, normalize_events


class InboxTest(unittest.TestCase):
    def test_normalize_dedupe_and_own_filter(self):
        rows = [
            {"kind": "reply", "actor": "Alice", "status_id": "123456", "text": "第一条"},
            {"kind": "reply", "actor": "alice", "status_id": "123456", "text": "第一条"},
            {"kind": "follow", "actor": "wcixot", "text": "自己"},
            {"kind": "follow", "actor": "bad<script>", "text": "非法handle"},
        ]
        out = normalize_events(rows, "WCixot")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["actor"], "alice")
        self.assertEqual(out[1]["actor"], "")

    def test_consumption_and_conversation_ownership(self):
        ib = InboxState("wcixot")
        self.assertFalse(ib.consumed("n1"))
        ib.mark_consumed("n1")
        self.assertTrue(ib.consumed("n1"))
        ib.register("123456", "automation", "a1")
        self.assertEqual(ib.conversation("123456")["origin"], "automation")
        ib.register("123456", "human", "m1")
        self.assertEqual(ib.conversation("123456")["origin"], "human")
        with self.assertRaises(ValueError):
            ib.register("123456", "other")


if __name__ == "__main__":
    unittest.main()
