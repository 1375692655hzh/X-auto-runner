# browse.py — 日常只读例程客户端（systemd timer 3×/日 调用）
# 会话时长随机 3-6 分钟；工位不在（窗外/休眠）→ 静默跳过（exit 0，不当失败）
import json
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

TOKEN_FILE = Path("/etc/xar/hand.token")


def main():
    minutes = random.choice([3, 4, 5, 6])
    req = urllib.request.Request("http://127.0.0.1:8741/browse", method="POST")
    req.add_header("Authorization", "Bearer " + TOKEN_FILE.read_text(encoding="utf-8").strip())
    req.add_header("Content-Type", "application/json")
    req.data = json.dumps({"minutes": minutes}).encode()
    try:
        with urllib.request.urlopen(req, timeout=minutes * 60 + 150) as r:
            print(json.dumps(json.loads(r.read()), ensure_ascii=False))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:120]
        if e.code == 503:
            print(f"工位不在（窗外/休眠），跳过: {body}")
        else:
            print(f"HTTP {e.code}: {body}", file=sys.stderr)
            sys.exit(1)
    except urllib.error.URLError as e:
        # 端口未监听=窗外/夜间休眠（8741 随工位启停）——静默跳过，不算失败
        print(f"端口未开（夜间休眠），跳过: {e}")
    except Exception as e:
        print(f"browse 失败: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
