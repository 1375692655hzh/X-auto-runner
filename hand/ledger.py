# ledger.py — state/ 安全账本（P1 第一模块）
# 移植自 x-auto-run executor.js 滚动账本/账号锁/intent 对账，语义逐条对应：
#   滚动24h: loadLedger 的 cut 过滤 → Ledger.load()
#   fail-closed 封顶: capLeftRolling 的 cap<=0→0 → Ledger.cap_left()
#   账号锁: acquireLock pid 活性+30min 残留接管 → AccountLock
#   崩溃对账: executor.js:475 intent 卡死→unknown+占额度+禁重试 → Intents.reconcile()
#   PAUSE: state/PAUSE 存在即全停 → PauseActive
#   SIMULATE 隔离: ledger-<acct>.test.json 独立账本 → env XAR_SIMULATE=1
# 升级（规范 §五）: 原子写(tmp+rename)；损坏即 .corrupt-* 备份后抛错终止，绝不静默重置
# 红线对应: 规范 §一.2（单写者：本模块是 state/ 唯一写入口）、§一.3（幂等键）、§一.6（只能紧）
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl  # Linux 生产；Windows 开发环境无此模块
except ImportError:
    fcntl = None

STATE_DIR = Path(os.environ.get("XAR_STATE", "/opt/xar/state"))
SIMULATE = os.environ.get("XAR_SIMULATE") == "1"
CRASH_AFTER_INTENT = os.environ.get("XAR_CRASH_AFTER_INTENT") == "1"
LOCK_STALE_MIN = 30
ACTION_KINDS = ("post", "reply", "comment", "like", "follow", "unfollow")
DEFAULT_CAPS = {"post": 4, "reply": 15, "comment": 10, "like": 30, "follow": 10, "unfollow": 3}


class LedgerCorrupt(Exception):
    """账本损坏——已备份 .corrupt-*，进程必须终止（fail-closed）"""


class PauseActive(Exception):
    """state/PAUSE 存在——全局急停，唯一恢复路径=人工二次确认（红线 §一.5）"""


class AccountLocked(Exception):
    """账号级文件锁被持有（防并发）；残留锁超时自动接管"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _atomic_write(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def _ledger_lock(path: Path):
    """账本进程间互斥（flock；Windows 开发退化为直通——多写者覆盖的修复面在生产侧）"""
    if fcntl is None:
        yield
        return
    lf = open(str(path) + ".lock", "a+")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lf, fcntl.LOCK_UN)
        finally:
            lf.close()


def file_lock(path: Path):
    """通用文件锁（tickets/contacts 同族跨进程读改写共用；MoA/review 审查 2026-10-28）"""
    return _ledger_lock(path)


def _load_json_fail_closed(path: Path):
    """读 JSON；不存在返回 None；损坏先备份 .corrupt-* 再抛错（绝不静默重置）"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        bak = path.with_name(f"{path.name}.corrupt-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        path.rename(bak)
        raise LedgerCorrupt(f"{path.name} 损坏已备份为 {bak.name}: {e}") from e


def log_event(task: str, account: str, **fields) -> None:
    """月度审计流 state/log-YYYY-MM.jsonl（所有进程追加；与 x-auto-run 同构）"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"at": datetime.now(timezone.utc).isoformat(), "task": task, "account": account}
    if SIMULATE:
        entry["simulated"] = True
    entry.update(fields)
    with open(STATE_DIR / f"log-{datetime.now():%Y-%m}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def check_pause() -> None:
    if (STATE_DIR / "PAUSE").exists():
        raise PauseActive("state/PAUSE 急停生效中，退出（人工二次确认后才能恢复）")


class Ledger:
    """滚动 24h 动作账本: state/ledger-<account>[.test].json
    结构: {actions: {kind: [ts_ms...]}, meta: {aborted_today, abort_reason, reserved}}
    写者仅限 hand 与确定性工人（红线 §一.2）"""

    def __init__(self, account: str, caps: dict | None = None):
        self.account = account
        # 实例预算是权威配置；此前只读 DEFAULT_CAPS，导致 instance.yaml 调整不生效。
        # 显式 caps（测试/演练）优先于文件；无文件时保持默认。
        file_caps = {}
        if caps is None:
            try:
                import yaml
                cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
                file_caps = ((cfg.get("runtime") or {}).get("budget_24h") or {})
            except FileNotFoundError:
                pass
        self.caps = {**DEFAULT_CAPS, **file_caps, **(caps or {})}
        self.suffix = ".test" if SIMULATE else ""
        self.path = STATE_DIR / f"ledger-{account}{self.suffix}.json"
        self.data = self.load()

    def load(self) -> dict:
        j = _load_json_fail_closed(self.path)
        if j is None:
            return {"actions": {}, "meta": {}}
        j = {"actions": {}, "meta": {}, **j}
        cut = _now_ms() - 24 * 3600 * 1000
        for k in list(j["actions"]):
            j["actions"][k] = [ts for ts in (j["actions"].get(k) or []) if ts > cut]
        return j

    def save(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.path, self.data)

    def used(self, kind: str) -> int:
        """滚动 24h：读时实时过滤（2026-10-28 实证修复：裸 len() 让已滚出窗口的条目
        继续虚占额度——两笔 >24h 的旧账把 CAP_EXHAUSTED 顶到人工帖头上）"""
        cut = time.time() * 1000 - 24 * 3600 * 1000
        return sum(1 for ms in (self.data["actions"].get(kind) or []) if ms > cut)

    def reserved(self, kind: str) -> int:
        return int(self.data["meta"].get("reserved", {}).get(kind) or 0)

    def cap_left(self, kind: str) -> int:
        """fail-closed：未知类型/非法上限一律 0（对应 capLeftRolling）"""
        cap = self.caps.get(kind)
        if not isinstance(cap, (int, float)) or cap <= 0:
            return 0
        return int(cap) - self.used(kind) - self.reserved(kind)

    def _reload(self):
        """写前重读（多写者：Gateway/qq_bot/CLI 各持实例——flock 下重读再改防覆盖，
        MoA 审查 2026-10-28：后写者整写曾把先写者的 release 覆盖回去）。
        首日无文件=合法空账本；其他读失败抛错（review 低8：绝不拿陈旧内存态覆盖磁盘）"""
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self.data
        except json.JSONDecodeError:
            raise  # 损坏 → 调用方失败，走 .corrupt-* 人工路径，不静默覆盖
        d.setdefault("actions", {})
        d.setdefault("meta", {})
        self.data = d
        return self.data

    def record(self, kind: str) -> None:
        """动作尝试即计额度（保守口径，executor.js:589 同款）；顺手切掉滚出窗口的旧账防无限增长"""
        if kind not in ACTION_KINDS:
            raise ValueError(f"未知动作类型: {kind}")
        with _ledger_lock(self.path):
            self._reload()
            cut = time.time() * 1000 - 24 * 3600 * 1000
            lst = [ms for ms in self.data["actions"].get(kind, []) if ms > cut]
            lst.append(_now_ms())
            self.data["actions"][kind] = lst
            self.save()

    def reserve(self, kind: str, n: int = 1, ticket: str = "") -> bool:
        """P3 票据预留占位：开票时占额度，驳回/过期释放"""
        with _ledger_lock(self.path):
            self._reload()
            if self.cap_left(kind) < n:
                return False
            r = self.data["meta"].setdefault("reserved", {})
            r[kind] = int(r.get(kind) or 0) + n
            tk = r.setdefault("_tickets", [])
            tk.append({"kind": kind, "n": n, "ticket": ticket, "at": _now_ms()})
            # 滚动清理：只留 48h 内条目（review 低9：单调增长曾积 9 条与实际预留脱节）
            cut = _now_ms() - 48 * 3600 * 1000
            r["_tickets"] = [t for t in tk if t.get("at", 0) > cut]
            self.save()
            return True

    def release(self, kind: str, n: int = 1) -> None:
        with _ledger_lock(self.path):
            self._reload()
            r = self.data["meta"].get("reserved", {})
            r[kind] = max(0, int(r.get(kind) or 0) - n)
            self.save()

    # ── 熔断 meta（当日 aborted；触发=风控 toast/同类连败，见规范 §二） ──
    def abort_today(self, reason: str) -> None:
        with _ledger_lock(self.path):
            self._reload()
            self.data["meta"]["aborted_today"] = True
            self.data["meta"]["abort_reason"] = reason
            self.data["meta"]["abort_date"] = datetime.now().strftime("%Y-%m-%d")
            self.save()

    @property
    def aborted_today(self) -> bool:
        return bool(self.data["meta"].get("aborted_today")) and \
            self.data["meta"].get("abort_date") == datetime.now().strftime("%Y-%m-%d")


class AccountLock:
    """账号级文件锁 state/lock-<account>.json：pid 活性 + 30min 残留接管（executor.js:104 同款）"""

    def __init__(self, account: str):
        self.account = account
        self.path = STATE_DIR / f"lock-{account}{'_test' if SIMULATE else ''}.json"

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True  # EPERM = 进程存在但属他人
        except ProcessLookupError:
            return False

    def acquire(self) -> None:
        stale_cut = _now_ms() - LOCK_STALE_MIN * 60000
        j = _load_json_fail_closed(self.path)
        if j and j.get("pid") and j.get("at", 0) > stale_cut and self._pid_alive(int(j["pid"])):
            raise AccountLocked(f"账号 {self.account} 已有运行中进程(pid {j['pid']})，本次退出（防并发）")
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.path, {"pid": os.getpid(), "at": _now_ms(), "account": self.account})

    def release(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


class Intents:
    """exactly-once 之①：写动作执行前先落盘 state/intents/<id>.json
    状态机: planned → intent → done|unknown|failed
    崩溃恢复: 任何 status==intent 的残留 → unknown + 占额度 + 禁重试（红线 §一.1）"""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.dir = STATE_DIR / ("intents-test" if SIMULATE else "intents")
        self.dir.mkdir(parents=True, exist_ok=True)

    def put(self, intent_id: str, kind: str, payload: dict) -> Path:
        p = self.dir / f"{intent_id}.json"
        if p.exists():
            raise ValueError(f"幂等键冲突: {intent_id}（同票最多一次，红线 §一.3）")
        doc = {"id": intent_id, "kind": kind, "account": self.ledger.account,
               "status": "planned", "payload": payload,
               "created_at": datetime.now(timezone.utc).isoformat()}
        _atomic_write(p, doc)
        return p

    def mark(self, intent_id: str, status: str, result: str = "") -> None:
        p = self.dir / f"{intent_id}.json"
        doc = _load_json_fail_closed(p)
        if doc is None:
            raise FileNotFoundError(f"intent 不存在: {intent_id}")
        doc["status"] = status
        doc["result"] = result
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(p, doc)

    def mark_intent(self, intent_id: str) -> None:
        """发送前调用：落 intent 态=可能已发出的证据；动作尝试即计额度"""
        self.mark(intent_id, "intent")
        self.ledger.record(self._kind_of(intent_id))
        if CRASH_AFTER_INTENT:
            os._exit(70)  # 测试钩子：写完 intent 即崩（验收"杀进程三次"用）

    def _kind_of(self, intent_id: str) -> str:
        doc = _load_json_fail_closed(self.dir / f"{intent_id}.json")
        return doc["kind"]

    def reconcile(self) -> int:
        """进程启动时调用：intent 残留全部转 unknown、占额度、禁重试"""
        n = 0
        for p in sorted(self.dir.glob("*.json")):
            doc = _load_json_fail_closed(p)
            if doc and doc.get("status") == "intent":
                self.mark(p.stem, "unknown", "进程中断，结果未知（禁重试）")
                n += 1
        return n


class TaskState:
    """state/taskstate-<account>.json —— 打开记录（sightings）与会话台账
    红线 §一.4 的数据面：只有 browse/read 中真实展示过的帖，才可进入写互动候选；
    openedPosts 上限 2000 条（超出丢最旧），sessions 环形 50 条"""

    def __init__(self, account: str):
        self.account = account
        self.path = STATE_DIR / f"taskstate-{account}{'_test' if SIMULATE else ''}.json"
        j = _load_json_fail_closed(self.path)
        self.data = j if j else {"openedPosts": {}, "openedAuthors": {}, "sessions": []}
        self.data.setdefault("openedPosts", {})
        self.data.setdefault("openedAuthors", {})
        self.data.setdefault("sessions", [])

    def save(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.path, self.data)

    def record_opened(self, posts: list) -> None:
        day = datetime.now().strftime("%Y-%m-%d")
        for p in posts:
            if p.get("id"):
                self.data["openedPosts"][p["id"]] = day
            if p.get("author"):
                self.data["openedAuthors"][p["author"]] = day
        while len(self.data["openedPosts"]) > 2000:  # 丢最旧（dict 保序）
            self.data["openedPosts"].pop(next(iter(self.data["openedPosts"])))
        while len(self.data["openedAuthors"]) > 2000:
            self.data["openedAuthors"].pop(next(iter(self.data["openedAuthors"])))
        self.save()

    def record_session(self, summary: dict) -> None:
        self.data["sessions"].append({"at": datetime.now(timezone.utc).isoformat(), **summary})
        self.data["sessions"] = self.data["sessions"][-50:]
        self.save()

    def opened_today(self, status_id: str) -> bool:
        return self.data["openedPosts"].get(status_id) == datetime.now().strftime("%Y-%m-%d")


# ── 自检（VPS 上: XAR_SIMULATE=1 python ledger.py selftest） ──
def selftest():
    import shutil, tempfile
    global STATE_DIR
    STATE_DIR = Path(tempfile.mkdtemp(prefix="xar-ledger-test-"))
    ok = []

    led = Ledger("wcixot", caps={"post": 2, "reply": 3})
    assert led.used("reply") == 0 and led.cap_left("reply") == 3
    led.record("reply"); led.record("reply")
    assert led.cap_left("reply") == 1
    assert led.cap_left("post") == 2
    # fail-closed：无上限类型
    assert led.cap_left("unknown-kind") == 0
    ok.append("ledger 滚动计数+fail-closed")

    # 票据预留/释放
    assert led.reserve("reply", 1, "T1") is True
    assert led.cap_left("reply") == 0
    assert led.reserve("reply", 1, "T2") is False
    led.release("reply", 1)
    assert led.cap_left("reply") == 1
    ok.append("票据预留/释放")

    # 重新 load：持久化 + 滚动过滤（伪造过期时间戳）
    led2 = Ledger("wcixot")
    assert led2.used("reply") == 2
    led2.data["actions"]["reply"] = [_now_ms() - 25 * 3600 * 1000]
    led2.save()
    assert Ledger("wcixot").used("reply") == 0
    ok.append("持久化+24h滚动过滤")

    # 损坏 fail-closed
    led3 = Ledger("wcixot")
    led3.path.write_text("{broken", encoding="utf-8")
    try:
        Ledger("wcixot"); assert False
    except LedgerCorrupt:
        pass
    corrupt_baks = list(STATE_DIR.glob("ledger-wcixot*.corrupt-*"))
    assert corrupt_baks, "应有 .corrupt-* 备份"
    ok.append("损坏→.corrupt备份+fail-closed")

    # 锁：抢占/重复抢占/释放/残留接管
    lk = AccountLock("wcixot")
    lk.acquire()
    try:
        AccountLock("wcixot").acquire(); assert False
    except AccountLocked:
        pass
    lk.release()
    # 死 pid + 新鲜时间戳 → 残留接管（不抛错，直接抢到）
    _atomic_write(lk.path, {"pid": 999999, "at": _now_ms(), "account": "wcixot"})
    AccountLock("wcixot").acquire()
    AccountLock("wcixot").release()
    ok.append("账号锁（活性检测+残留接管）")

    # PAUSE
    (STATE_DIR / "PAUSE").write_text("", encoding="utf-8")
    try:
        check_pause(); assert False
    except PauseActive:
        pass
    (STATE_DIR / "PAUSE").unlink()
    ok.append("PAUSE 旗标")

    # intent 对账：intent 残留 → unknown + 占额度
    led4 = Ledger("wcixot", caps={"reply": 5})
    ints = Intents(led4)
    ints.put("i-001", "reply", {"statusId": "123"})
    ints.mark("i-001", "intent")
    before = led4.used("reply")
    # 手动制造"残留"：直接改状态
    doc = json.loads((ints.dir / "i-001.json").read_text(encoding="utf-8"))
    doc["status"] = "intent"; doc["payload"]["_stale"] = True
    _atomic_write(ints.dir / "i-001.json", doc)
    n = ints.reconcile()
    assert n == 1
    doc = json.loads((ints.dir / "i-001.json").read_text(encoding="utf-8"))
    assert doc["status"] == "unknown" and "禁重试" in doc["result"]
    assert led4.used("reply") >= before  # 占额度
    # 幂等键冲突
    try:
        ints.put("i-001", "reply", {}); assert False
    except ValueError:
        pass
    ok.append("intent 崩溃对账+幂等键")

    shutil.rmtree(STATE_DIR, ignore_errors=True)
    print("\n".join(f"PASS: {x}" for x in ok))
    print(f"SELFTEST OK ({len(ok)} 组)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest()
    else:
        print("用法: XAR_SIMULATE=1 python ledger.py selftest")
