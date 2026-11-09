import os
import tempfile
import unittest
import sys
from pathlib import Path

TMP = tempfile.TemporaryDirectory()
os.environ["XAR_STATE"] = TMP.name
sys.path.insert(0, str(Path(__file__).parents[1]))

from server import Deny, _validate_post_options


class PostOptionsTest(unittest.TestCase):
    def test_plain_post_ok(self):
        _validate_post_options({"kind": "post", "text": "hello"})

    def test_quote_id_validation(self):
        _validate_post_options({"kind": "post", "quoted_status_id": "123456789"})
        for bad in ("abc", "123;rm", "https://x.com/a/status/123"):
            with self.assertRaises(Deny):
                _validate_post_options({"kind": "post", "quoted_status_id": bad})

    def test_media_not_allowed_on_reply(self):
        with self.assertRaises(Deny):
            _validate_post_options({"kind": "reply", "media_paths": ["/tmp/a.png"]})

    def test_media_path_and_count_guard(self):
        with self.assertRaises(Deny):
            _validate_post_options({"kind": "post", "media_paths": []})
        with self.assertRaises(Deny):
            _validate_post_options({"kind": "post", "media_paths": ["/etc/passwd"]})

    @unittest.skipIf(os.name == "nt", "/tmp 白名单在 Linux 生产验证")
    def test_media_magic(self):
        p = Path("/tmp/xar-test-image.png")
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        try:
            _validate_post_options({"kind": "post", "media_paths": [str(p)]})
            p.write_bytes(b"not-a-png")
            with self.assertRaises(Deny):
                _validate_post_options({"kind": "post", "media_paths": [str(p)]})
        finally:
            p.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
