import os
import tempfile
import unittest
import sys
from datetime import datetime, timedelta
from pathlib import Path

TMP = tempfile.TemporaryDirectory()
os.environ["XAR_STATE"] = TMP.name
sys.path.insert(0, str(Path(__file__).parents[1]))

import news_auto as N

REAL_AUTO_CIRCUIT = N._auto_circuit


class NewsAutoTest(unittest.TestCase):
    def setUp(self):
        N._configured_mode = lambda: "auto"
        N.in_active_window = lambda now=None: True
        N.policy_version = lambda: "policy-test"
        N.prompt_version = lambda: "prompt-test"
        N._auto_circuit = lambda: {}
        self.now = datetime(2026, 11, 9, 12, 0)
        N._load_state = lambda: {"stories": [{"story_id": "s1", "last_at": "2026-11-09T11:20:00"}]}
        self.intent = {
            "kind": "post",
            "mandate": {"mode": "auto", "auto_execute": True, "scope": "news-auto-post",
                        "policy_version": "policy-test", "prompt_version": "prompt-test",
                        "score": 80, "story_id": "s1", "created_at": "2026-11-09T11:30:00"},
            "news_detail": {"form": "资讯", "source_truncated": False, "source_text": "完整素材",
                            "story_id": "s1", "time": "2026-11-09 11:00"},
            "value_reviews": {"editor": {"decision": "accept"}},
        }

    def test_execute_result_routes(self):
        for status in ("pending_review", "approved", "done", "failed", "unknown"):
            self.assertEqual(N._execute_route({"status": status}), status)
        self.assertEqual(N._execute_route({"denied": {"code": "X"}}), "denied")
        self.assertEqual(N._execute_route({"error": "internal"}), "retry")

    def test_fresh_auto_allowed(self):
        self.assertTrue(N.auto_authorized(self.intent, self.now))

    def test_stale_or_missing_source_denied(self):
        stale = dict(self.intent)
        stale["news_detail"] = {**self.intent["news_detail"], "time": "2026-11-09 05:00"}
        self.assertFalse(N.auto_authorized(stale, self.now))
        missing = dict(self.intent)
        missing["news_detail"] = {**self.intent["news_detail"], "time": ""}
        self.assertFalse(N.auto_authorized(missing, self.now))

    def test_article_has_longer_window(self):
        article = dict(self.intent)
        article["news_detail"] = {**self.intent["news_detail"], "form": "精品文章",
                                   "time": "2026-11-08 13:00"}
        self.assertTrue(N.auto_authorized(article, self.now))

    def test_story_update_and_circuit_deny(self):
        N._load_state = lambda: {"stories": [{"story_id": "s1", "last_at": "2026-11-09T11:40:00"}]}
        self.assertFalse(N.auto_authorized(self.intent, self.now))
        N._load_state = lambda: {"stories": []}
        N._auto_circuit = lambda: {"blocked": True}
        self.assertFalse(N.auto_authorized(self.intent, self.now))

    def test_score_head_allows_longer_copy(self):
        item = {"id": "i1", "title": "传伯克希尔·哈撒韦发行日元债券的筹备消息"}
        expected = N._item_head(item)
        row = {"key": N._item_key(item), "head": expected + "R", "score": 20,
               "tier": "N", "event": "伯克希尔发债", "increment": "无", "publish": False}
        N._score_batch = lambda *a, **k: [row]
        out = N._score_batch_checked(None, [item], [], [])
        self.assertEqual(out[0]["score"], 20)
        bad = dict(row, head="完全无关的标题头")
        N._score_batch = lambda *a, **k: [bad]
        with self.assertRaises(ValueError):
            N._score_batch_checked(None, [item], [], [])

    def test_circuit_failure_semantics(self):
        N._auto_circuit = REAL_AUTO_CIRCUIT
        N.CIRCUIT_FILE = Path(TMP.name) / "circuit.json"
        N._record_auto_result("t1", "failed")
        self.assertFalse(N._auto_circuit().get("blocked", False))
        N._record_auto_result("t2", "failed")
        self.assertTrue(N._auto_circuit()["blocked"])
        N.CIRCUIT_FILE.unlink()
        N._record_auto_result("t3", "unknown")
        self.assertTrue(N._auto_circuit()["blocked"])


if __name__ == "__main__":
    unittest.main()
