#!/usr/bin/env python3
# p1_acceptance.py — P1 验收（SIMULATE 全状态机，不开浏览器）
# 覆盖：A 单飞1000并发额度不超限 | B 幂等重放拒 | C --dry 全闸零副作用 | D PAUSE 拒发
#       E 杀进程三次：崩溃→重启对账→unknown占额→重放拒（"X上只有一条"的账本等价证明）
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PY = "/opt/xar/venv/bin/python"
HAND = "/opt/xar/hand"
STATE = "/tmp/xar-p1-acc"
PORT = 8742
TOKEN_FILE = Path(STATE) / "hand.token"


def call(method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    req.add_header("Authorization", "Bearer " + TOKEN_FILE.read_text().strip())
    data = json.dumps(body).encode() if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
        req.data = data
    try:
        with urllib.request.urlopen(req, timeout=200) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_http": e.code, **json.loads(e.read() or b"{}")}


def start_server():
    env = dict(os.environ, XAR_SIMULATE="1", XAR_STATE=STATE)
    p = subprocess.Popen([PY, HAND + "/server.py"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(100):
        time.sleep(0.2)
        # 只认 health 探活（token 文件先于端口绑定产生——bind 失败时它照样存在，假阳性）
        if TOKEN_FILE.exists():
            try:
                if call("GET", "/health").get("ok"):
                    return p
            except Exception:
                pass
    p.terminate()
    raise SystemExit("sim server 未起来（bind 失败或启动崩溃）")


CRASH = """
import os, sys
sys.path.insert(0, "/opt/xar/hand")
from ledger import Ledger, Intents
from server import Gateway
led = Ledger("wcixot"); gw = Gateway(led, Intents(led))
gw.execute({"kind": "like", "intent_id": os.environ["IID"], "text": "x"})
print("UNEXPECTED-NO-CRASH")
"""


def main():
    shutil.rmtree(STATE, ignore_errors=True)
    Path(STATE).mkdir(parents=True)
    srv = start_server()

    # A: 单飞 1000 并发
    def one(i):
        return call("POST", "/execute", {"kind": "reply", "intent_id": f"acc-{i}", "text": "t"})
    with ThreadPoolExecutor(50) as ex:
        rs = list(ex.map(one, range(1000)))
    done = sum(1 for r in rs if r.get("status") == "done")
    denied = sum(1 for r in rs if r.get("denied", {}).get("code") == "CAP_EXHAUSTED")
    internal = sum(1 for r in rs if r.get("denied", {}).get("code") == "INTERNAL" or r.get("_http"))
    led = call("GET", "/ledger")
    used, cap = led["rolling24h_used"]["reply"], led["rolling24h_cap"]["reply"]
    assert done == cap and denied == 1000 - cap and internal == 0 and used == cap, \
        (done, denied, internal, used, cap)
    print(f"PASS A: 1000 并发单飞 done={done}==cap{cap} 拒={denied} INTERNAL={internal} 账本={used}/{cap}")

    # B: 幂等重放（挑一个真正 done 的）
    done_iid = next(r["intent_id"] for r in rs if r.get("status") == "done")
    r = call("POST", "/execute", {"kind": "reply", "intent_id": done_iid, "text": "t"})
    assert r.get("denied", {}).get("code") == "IDEMPOTENCY_CONFLICT", r
    print(f"PASS B: 幂等键重放拒绝（{done_iid}，同票最多一次）")

    # C: --dry 全闸零副作用
    ndir = Path(STATE) / "intents-test"
    before = len(list(ndir.glob("*.json")))
    d1 = call("POST", "/dry", {"kind": "like", "intent_id": "dry-1"})
    d2 = call("POST", "/dry", {"kind": "reply", "intent_id": "dry-2"})
    after = len(list(ndir.glob("*.json")))
    assert d1["would_deny"] is None and d2["would_deny"]["code"] == "CAP_EXHAUSTED" and before == after, (d1, d2)
    print("PASS C: --dry 过全闸、额度判断正确、零 intent 落盘")

    # D: PAUSE
    (Path(STATE) / "PAUSE").write_text("")
    r = call("POST", "/execute", {"kind": "like", "intent_id": "p-1"})
    assert r.get("denied", {}).get("code") == "PAUSED", r
    (Path(STATE) / "PAUSE").unlink()
    print("PASS D: PAUSE 旗标拒发")

    srv.terminate(); srv.wait()

    # E: 杀进程三次（崩溃钩子：写完 intent 即 exit(70)）
    for rnd in range(3):
        iid = f"crash-{rnd}"
        srv = start_server()
        u0 = call("GET", "/ledger")["rolling24h_used"]["like"]
        srv.terminate(); srv.wait()
        env = dict(os.environ, XAR_SIMULATE="1", XAR_STATE=STATE,
                   XAR_CRASH_AFTER_INTENT="1", IID=iid)
        cp = subprocess.run([PY, "-c", CRASH], env=env, capture_output=True, text=True)
        assert cp.returncode == 70, (cp.returncode, cp.stdout, cp.stderr[-200:])
        srv = start_server()  # 启动即对账
        led = call("GET", "/ledger")
        assert led["rolling24h_used"]["like"] == u0 + 1, (u0, led)
        r = call("POST", "/execute", {"kind": "like", "intent_id": iid, "text": "x"})
        assert r.get("denied", {}).get("code") == "IDEMPOTENCY_CONFLICT", r
        doc = json.loads((Path(STATE) / "intents-test" / f"{iid}.json").read_text())
        assert doc["status"] == "unknown" and "禁重试" in doc["result"], doc
        srv.terminate(); srv.wait()
        print(f"PASS E{rnd + 1}: 崩溃→unknown占额→重放拒（like 账本={led['rolling24h_used']['like']}，恰+1）")

    print("P1 验收（SIMULATE 态）五组全部通过 ✅")


if __name__ == "__main__":
    main()
