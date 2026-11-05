# resident.py — 常驻进程（P1-3）：持有 Camoufox 持久 profile + 心跳 + 自愈
# 移植自 x-auto-run resident.js（行号对照）：
#   单实例守卫+立即声明工位   resident.js:296-311（10-22 竞态修复：守卫过立刻写 pid+startAt）
#   工位=启动+页+登录校验     resident.js:313-332 openWorkPages（全就绪才切换引用）
#   心跳+页面双健康           resident.js:269-271（10-21 僵尸事故：探针过才写心跳，失败清心跳）
#   自愈循环                 resident.js:684-713（失败清心跳→60s 冷却重开→连败 15 轮自灭交看门狗）
#   residentAlive 判据        resident-guard.js:14-22（心跳<20min 且 pid 活）
# 适配：比特浏览器开窗 → Camoufox persistent_context（profile 即登录态载体，首启自动注 cookie）；
#       23:59 自灭/00:05 接替 → systemd 管理（活跃窗外释放浏览器空转等待）
# 状态文件: state/resident-<account>.json {pid, startAt, heartbeatAt, pagesOkAt, degradedRounds}
import json
import os
import queue
import re
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, log_event  # noqa: E402
import primitives as P  # noqa: E402

import yaml  # instance.yaml
from camoufox.sync_api import Camoufox

HB_FRESH_MS = 20 * 60 * 1000      # residentAlive 判据：心跳 20min 内
CLAIM_WINDOW_MS = 5 * 60 * 1000   # 启动声明窗口（竞态修复）
PROBE_INTERVAL_S = 60             # 探针节拍
HEAL_COOLDOWN_S = 60              # 重开工位冷却
FATAL_DEGRADED = 15               # 连败自灭轮数


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


class Resident:
    def __init__(self):
        cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
        self.account = cfg["account"]["handle"].lower()
        self.handle = cfg["account"]["handle"]
        self.cookie_file = Path(cfg["account"]["cookie_file"])
        p = cfg["proxy"]
        self.proxy = {"server": f"{p['scheme']}://{p['host']}:{p['port']}",
                      "username": p["username"], "password": p["password"]}
        self.active = cfg.get("runtime", {}).get("active_window", ["09:00", "22:00"])
        self.st_path = STATE_DIR / f"resident-{self.account}.json"
        self.profile_dir = STATE_DIR / f"profile-{self.account}"
        self.cm = None          # Camoufox 上下文管理器
        self.ctx = None         # BrowserContext（持久）
        self.page = None        # 健康页（常驻 /home）
        self.browser_lock = threading.RLock()  # 浏览器非线程安全：一切页面操作经此锁
        self.task_q = queue.Queue()            # 浏览器任务递交队列：server 线程只递交闭包，
        #                                          主线程唯一执行浏览器操作（Playwright 同步 API 禁止跨线程）
        self.probe_count = 0
        self.opened_at = None                  # 浏览器回收计时（6h 强制重开）
        self._server_on = False
        self._stop = False

    def submit_browser_task(self, fn, timeout=150):
        """server 线程调用：把浏览器操作递给主线程执行并等结果"""
        box, ev = {}, threading.Event()
        self.task_q.put((fn, box, ev))
        if not ev.wait(timeout):
            box["error"] = "BROWSER_TASK_TIMEOUT"
        return box

    # ── 状态文件 ──
    def load_st(self):
        try:
            return json.loads(self.st_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    def save_st(self, **kw):
        st = self.load_st()
        st.update(kw)
        st["account"] = self.account
        self.st_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.st_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.st_path)   # 原子写（MoA 审查：半截状态文件连环损坏链）

    # ── 单实例守卫（resident.js:296）：通过后立即声明工位 ──
    def claim(self) -> bool:
        prev = self.load_st()
        hb_fresh = prev.get("heartbeatAt") and \
            time.time() * 1000 - datetime.fromisoformat(prev["heartbeatAt"]).timestamp() * 1000 < HB_FRESH_MS
        claimed = prev.get("pid") and prev["pid"] != os.getpid() and prev.get("startAt") and \
            time.time() * 1000 - datetime.fromisoformat(prev["startAt"]).timestamp() * 1000 < CLAIM_WINDOW_MS
        alive = False
        if claimed or (hb_fresh and prev.get("pid") and prev["pid"] != os.getpid()):
            alive = pid_alive(int(prev["pid"]))
        if alive:
            print(f"已有活实例(pid {prev['pid']})，本次退出（防拆工位）", flush=True)
            return False
        self.save_st(pid=os.getpid(), startAt=now_iso(), heartbeatAt=None)  # 立即声明
        return True

    # ── 工位=启动+健康页+登录校验（resident.js:313，全就绪才切引用） ──
    # 2026-10-29 R1-R4 修复（MoA 风控根因分析）：
    #   R1 固定指纹：首次生成存盘，之后每次传 fingerprint= → "同一台设备重启浏览器"
    #   R2 cookie 注入补全属性：httpOnly/expires/sameSite → 不再裸注入
    #   R3 废除 6h 回收 → 每天一次冷启动（ref 原版语义：一天一窗，中间只重连不杀进程）
    #   R4 开窗热身：冷启动后 30-90s 拟人刷 feed 再干活（ref boost.js:168 warmUp 移植）
    def _get_fingerprint(self) -> dict:
        """R1：获取或生成固定指纹。用 fingerprint_preset（纯 dict，可 JSON 持久化）。
        review B1 修正：fingerprint= 要求活对象无法序列化；preset 原生接受 dict。"""
        fp_file = STATE_DIR / f"fingerprint-{self.account}.json"
        if fp_file.exists():
            try:
                return json.loads(fp_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        from camoufox.fingerprints import get_random_preset   # 复数，review B1 实测
        fp = get_random_preset(os="windows")   # 纯 dict
        fp_file.write_text(json.dumps(fp, ensure_ascii=False, indent=1), encoding="utf-8")
        log_event("resident", self.account, action="fingerprintCreated",
                  summary=str({k: fp.get(k) for k in ("userAgent", "screen", "locale")
                               if fp.get(k)})[:120])
        return fp

    def open_workstation(self):
        # 孤儿清扫（2026-10-24 事故）——TERM 优先等 6s 再 KILL（R2 补充：-9 无法落盘 cookies.sqlite）
        try:
            import subprocess
            r = subprocess.run(["pgrep", "-c", "camoufox-bin"], capture_output=True, text=True)
            if r.stdout.strip() not in ("", "0"):
                subprocess.run(["pkill", "-TERM", "camoufox-bin"], capture_output=True)
                time.sleep(6)
                subprocess.run(["pkill", "-9", "camoufox-bin"], capture_output=True)
                log_event("resident", self.account, action="orphanSweep",
                          found=r.stdout.strip())
                time.sleep(2)
        except Exception:
            pass
        fp = self._get_fingerprint()
        self.cm = Camoufox(headless="virtual", os="windows", proxy=self.proxy,
                           geoip=True, persistent_context=True,
                           user_data_dir=str(self.profile_dir),
                           fingerprint_preset=fp)   # R1：固定指纹（preset dict）
        ctx = self.cm.__enter__()
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())   # ref 惯例：防 beforeunload 弹窗挂起
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
        if not P.verify_login(page, ctx, self.handle):
            # 取证（2026-10-28）：注入前 dump cookie 名单+属性——分清「服务端杀」vs「本地丢」
            try:
                cks = ctx.cookies("https://x.com")
                names = sorted(c["name"] for c in cks)
                log_event("resident", self.account, action="cookieForensic",
                          auth_token_present="auth_token" in names,
                          had=len(names), names=names,
                          details=[{"n": c["name"], "httpOnly": c.get("httpOnly"),
                                    "expires": bool(c.get("expires", -1) > 0)}
                                   for c in cks if c["name"] in ("auth_token", "ct0", "twid")])
            except Exception:
                pass
            self._inject_cookies(ctx)
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
            if not P.verify_login(page, ctx, self.handle):
                raise RuntimeError("登录态校验失败（cookie 注入后仍未登录）")
        # R4：开窗热身——先确保在 home 页（verify_login 通道二会跳到 intent 页），再刷 feed
        import random as _rnd
        try:
            if "x.com/home" not in (page.url or ""):
                page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
            P.sleep(int(_rnd.uniform(3000, 6000)))
            for _ in range(2 + int(_rnd.random() * 3)):
                P.human_scroll(page, 400 + int(_rnd.random() * 500))
                P.sleep(int(_rnd.uniform(2000, 5000)))
        except Exception:
            pass
        self.ctx, self.page = ctx, page  # 全就绪才切换引用
        self.opened_at = time.time()
        log_event("resident", self.account, action="workstationOpen", pid=os.getpid())

    def _inject_cookies(self, ctx):
        # 会话失活熔断（MoA 审查 2026-10-28：X 主动登出 + 无限重注入=风控升级路径）
        st = self.load_st()
        today = datetime.now().strftime("%Y-%m-%d")
        inj = st.get("session_injects") or {}
        if inj.get("date") != today:
            inj = {"date": today, "n": 0}
        if inj["n"] >= 3:
            (STATE_DIR / "PAUSE").write_text(
                f"sessionHalt: 当日登录态失活 {inj['n']} 次，停止重注入交人工"
                f"（换 cookie / 评估代理与账号状态后删本文件恢复）", encoding="utf-8")
            log_event("resident", self.account, action="sessionHalt", injects=inj["n"])
            raise RuntimeError("会话失活熔断：PAUSE 已落，人工恢复")
        inj["n"] += 1
        self.save_st(session_injects=inj)
        ck = json.loads(self.cookie_file.read_text(encoding="utf-8"))
        # R2（review B2 修正）：只有 auth_token/twid/kdt 是真实 HttpOnly；
        # ct0 必须 JS 可读（X 前端 CSRF 双提交要 document.cookie 读它）——标了 HttpOnly
        # 反而制造冷启动 403 突发=新的自动化信号。guest_id 系列也非 HttpOnly。
        far = int(time.time()) + 365 * 24 * 3600
        HTTPONLY = {"auth_token", "twid", "kdt"}
        cookies = []
        for n, v in ck.items():
            c = {"name": n, "value": v, "domain": ".x.com", "path": "/",
                 "secure": True, "sameSite": "None",
                 "expires": far,
                 "httpOnly": n in HTTPONLY}
            cookies.append(c)
        ctx.add_cookies(cookies)
        log_event("resident", self.account, action="cookieInject", n_today=inj["n"],
                  attrs="full")

    def close_workstation(self):
        if self.cm:
            try:
                self.cm.__exit__(None, None, None)
            except Exception:
                pass
            self.cm = self.ctx = self.page = None

    # ── 健康探针：真实 feed + 登录态（心跳+页面双健康）
    # 2026-10-26 修复：旧探针 arts>=0 即过——会话中途死亡后登录墙页面照样骗过心跳一整天；
    # 现要求 ①article>0（feed 真渲染）②每 30 轮查登录元素（AppTabBar）③6h 强制回收浏览器
    def probe(self) -> bool:
        try:
            r = self.page.evaluate("() => ({ok: 1+1===2, arts: document.querySelectorAll('article').length})")
            if not (r and r.get("ok")):
                return False
            if r.get("arts", 0) == 0:
                # 冷启动盲区（2026-10-26 两连发实证：开机 2 分钟 feed 仍未流出帖，探测必误判死
                # → 关工位重开留 90s 恢复窗）。改为主动回 home 重拉+耐心等 45s，仍无帖才判死。
                try:
                    if P.goto_feed(self.page, "https://x.com/home"):
                        for _ in range(15):
                            time.sleep(3)
                            n = self.page.evaluate(
                                "() => document.querySelectorAll('article').length")
                            if n and n > 0:
                                break
                    r = self.page.evaluate(
                        "() => ({ok: 1+1===2, arts: document.querySelectorAll('article').length})")
                except Exception:
                    return False
                if not (r and r.get("arts", 0) > 0):
                    return False
            self.probe_count += 1
            if self.probe_count % 30 == 0:  # ~30min 一次登录态检查
                logged = self.page.evaluate(
                    "() => !!document.querySelector('a[data-testid=\"AppTabBar_Profile_Link\"]')")
                if not logged:
                    log_event("resident", self.account, action="sessionLost")
                    return False
            toast = P.risk_toast(self.page)  # 唯一整窗刹车
            if toast:
                self._circuit_break(toast)
            return True
        except Exception:
            return False

    def _circuit_break(self, toast: str):
        from ledger import Ledger
        Ledger(self.account).abort_today(f"风控toast: {toast[:60]}")
        log_event("resident", self.account, action="CIRCUIT_BREAK", toast=toast[:80])
        print(f"⚠️ 熔断(当日停写): {toast}", file=sys.stderr, flush=True)

    # ── 时段 ──
    def in_active_window(self) -> bool:
        h = datetime.now().hour * 60 + datetime.now().minute
        a = int(self.active[0].split(":")[0]) * 60 + int(self.active[0].split(":")[1])
        b = int(self.active[1].split(":")[0]) * 60 + int(self.active[1].split(":")[1])
        return a <= h < b

    def run(self):
        if not self.claim():
            return 0
        degraded = 0
        first = True
        while not self._stop:
            if (STATE_DIR / "PAUSE").exists():
                self.close_workstation()
                self.save_st(heartbeatAt=None)
                log_event("resident", self.account, action="paused")
                time.sleep(60)
                continue
            if not self.in_active_window():
                self.close_workstation()
                self.save_st(heartbeatAt=None)
                # R4 补充：窗外随机等待（300-600s 而非固定 300s）+ 开窗随机延迟
                import random as _r
                time.sleep(300 + int(_r.random() * 300))
                continue
            if self.ctx is None:
                try:
                    self.open_workstation()
                    degraded = 0
                    self.save_st(heartbeatAt=now_iso(), pagesOkAt=now_iso(), degradedRounds=0)
                    print(f"工位就绪 @{self.handle} (pid {os.getpid()})", flush=True)
                    if not self._server_on:
                        self._server_on = True
                        import server as S
                        threading.Thread(target=S.serve, args=(self,), daemon=True).start()
                        print("XClient 端口 127.0.0.1:8741 已开", flush=True)
                except Exception as e:
                    log_event("resident", self.account, action="workstationOpenFail", note=str(e)[:120])
                    degraded += 1
                    if degraded >= FATAL_DEGRADED:
                        self.save_st(heartbeatAt=None, degradedRounds=degraded)
                        log_event("resident", self.account, action="residentFatal",
                                  note=f"开工位连败{degraded}轮自灭，交看门狗")
                        return 1
                    time.sleep(HEAL_COOLDOWN_S)
                continue
            # R3（2026-10-29）：废除 6h 进程回收——ref 原版语义是一天一窗，中间不杀浏览器。
            # 6h 回收=每天 2-3 次「token 装进新设备」的冷启动，是会话失活的主要放大器
            # （取证：两天会话都活满 6h01m，死在回收后 2-5 分钟内）。
            # 内存维护改用页面级手段：健康页定期 about:blank 释放 DOM。
            if self.opened_at and time.time() - self.opened_at > 4 * 3600:
                try:  # 每 4h 健康页回空白页释放 DOM/JS 堆（不杀进程不换指纹）
                    self.page.goto("about:blank")
                    time.sleep(3)
                    self.page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
                    time.sleep(3)
                    self.opened_at = time.time()  # 重置计时（只做维护，不算冷启动）
                    log_event("resident", self.account, action="pageRefresh")
                except Exception:
                    pass
            # ── 心跳循环（探针过才写心跳；失败清心跳→重开） ──
            if self.opened_at and time.time() - self.opened_at < 120:
                # 开工宽限期（2026-10-26 竞态修复：AppTabBar 先于帖子流渲染，立查 arts 必误判）
                self.save_st(heartbeatAt=now_iso(), pagesOkAt=now_iso(), degradedRounds=0)
                degraded = 0
            elif self._probe_with_retry():
                self.save_st(heartbeatAt=now_iso(), pagesOkAt=now_iso(), degradedRounds=0)
                degraded = 0
            else:
                degraded += 1
                self.save_st(heartbeatAt=None, degradedRounds=degraded)
                log_event("resident", self.account, action="probeFail", round=degraded)
                if degraded >= FATAL_DEGRADED:
                    self.close_workstation()
                    log_event("resident", self.account, action="residentFatal",
                              note=f"探针连败{degraded}轮自灭，交看门狗")
                    return 1
                self.close_workstation()      # 清心跳已做；60s 冷却后重开工位
                time.sleep(HEAL_COOLDOWN_S)
                continue
            first = False
            self._serve_tasks_until(time.time() + PROBE_INTERVAL_S)
        return 0

    def _probe_with_retry(self, tries=2) -> bool:
        for i in range(tries):
            if self.probe():
                return True
            if i < tries - 1:
                time.sleep(5)
        return False

    def _serve_tasks_until(self, deadline):
        """探针间隔内执行递交的浏览器任务（主线程=唯一浏览器执行者，规范注释见 __init__）"""
        while time.time() < deadline and not self._stop:
            remaining = deadline - time.time()
            try:
                fn, box, ev = self.task_q.get(timeout=max(0.1, min(remaining, 10)))
            except queue.Empty:
                continue
            try:
                box["result"] = fn()
            except Exception as e:
                box["error"] = str(e)[:160]
            ev.set()

    def stop(self, *_):
        self._stop = True


def main():
    r = Resident()
    signal.signal(signal.SIGTERM, r.stop)
    signal.signal(signal.SIGINT, r.stop)
    try:
        rc = r.run()
    finally:
        r.save_st(heartbeatAt=None)
        r.close_workstation()
        log_event("resident", r.account, action="exit")
    sys.exit(rc)


if __name__ == "__main__":
    main()
