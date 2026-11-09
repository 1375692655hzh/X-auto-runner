import json
import os
import tempfile
import unittest
import sys
from datetime import datetime, timezone
from pathlib import Path

TMP = tempfile.TemporaryDirectory()
os.environ["XAR_STATE"] = TMP.name
sys.path.insert(0, str(Path(__file__).parents[1]))

from feedback import normalize_reason, summary, summary_text


class FeedbackTest(unittest.TestCase):
    def setUp(self):
        d = Path(TMP.name) / "tickets"
        d.mkdir(exist_ok=True)
        for p in d.glob("*.json"):
            p.unlink()
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            {"id": "news-1", "status": "rejected", "created_at": now, "decided_at": now,
             "feedback": {"label": "事实错误"}},
            {"id": "news-2", "status": "rejected", "created_at": now, "decided_at": now,
             "feedback": {"label": "事实错误"}},
            {"id": "cog-1", "status": "done", "created_at": now, "revision_count": 1},
        ]
        for row in rows:
            (d / f"{row['id']}.json").write_text(json.dumps(row), encoding="utf-8")

    def test_labels_and_summary(self):
        self.assertEqual(normalize_reason("署名"), "署名残留")
        self.assertEqual(normalize_reason(""), "未分类")
        self.assertEqual(normalize_reason("来源截图不可核验"), "来源截图不可核验")
        s = summary("wcixot", 7)
        self.assertEqual(s["rejected"], 2)
        self.assertEqual(s["done"], 1)
        self.assertEqual(s["manual_revisions"], 1)
        self.assertEqual(s["reason_labels"]["事实错误"], 2)
        self.assertIn("事实错误", s["repeat_labels"])
        self.assertIn("事实错误", summary_text("wcixot"))


if __name__ == "__main__":
    unittest.main()
