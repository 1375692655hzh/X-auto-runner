# server.py — XClient loopback 端口（P1-4）：127.0.0.1:8741 + bearer
# 设计出处：kimi-k3 round5 §1.2 XClient 接口 / glm 中间件链 / cursor 三态结果
# 端点：
#   GET  /health                存活/工位/心跳年龄
#   GET  /ledger                账本只读投影（LedgerStatus）
#   POST /read/timeline         {surface, limit}    → PostCard[]（临时页，用完即关）
#   POST /read/post             {status_id, url}    → {post, replies}
#   POST /dry                   {intent}            → 过全闸不落盘不点击 {would_deny}
#   POST /execute               {intent}            → ExecResult（P1 真实写=WRITES_DISABLED 硬拒，
#                                                      SIMULATE/--dry 走全状态机——验收用）
# 红线落实：
#   §一.3 幂等：intent_id 重复 → IDEMPOTENCY_CONFLICT
#   §一.6 fail-closed：未知类型/无上限 → CAP_EXHAUSTED 口径
#   §二   写护栏先行：真实浏览器写路径在 P3 前于网关层拒发（WRITES_DISABLED）
#   §一.1 exactly-once：intent 落盘→执行→崩溃→重启 reconcile→unknown 占额度禁重试
# 单飞：写动作进优先级队列（post>reply/comment>like/follow>browse/read），工作线程串行；
#       读操作直取浏览器锁。浏览器非线程安全，一切页面操作经 browser_lock。
import json
import os
import queue
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import (STATE_DIR, Ledger, Intents, SIMULATE, log_event, check_pause,  # noqa: E402
                    PauseActive, AccountLocked)
from tickets import Tickets, TicketError  # noqa: E402
from quality import check as quality_check  # noqa: E402

WRITE_KINDS = {"post", "reply", "comment", "like", "follow", "unfollow"}
PRIORITY = {"post": 0, "reply": 1, "comment": 1, "like": 2, "follow": 2, "unfollow": 2, "browse": 3}


class Deny(Exception):
    def __init__(self, code, detail="", retry_after=None):
        super().__init__(f"{code}: {detail}")
        self.body = {"code": code, "detail": detail}
        if retry_after:
            self.body["retry_after"] = retry_after


class Gateway:
    """入口规范闸门链 + 单飞队列 + intent 生命周期。resident 进程内实例化。"""

    def __init__(self, ledger: Ledger, intents: Intents, resident=None,
                 writes_enabled=False, approval=None, tickets=None):
        self.ledger = ledger
        self.intents = intents
        self.resident = resident            # 真实写派发需工位（浏览器任务递交）
        self.writes_enabled = writes_enabled  # runtime.writes.enabled（P3 开关，默认关）
        self.approval = approval or {}        # runtime.approval：kind→required|none，default=required
        self.tickets = tickets or Tickets(ledger)
        self.q = queue.PriorityQueue()
        self.seq = 0
        self.worker = threading.Thread(target=self._work, daemon=True)
        self.worker.start()
        # 启动对账：崩溃残留 intent → unknown + 占额度 + 禁重试（红线 §一.1）
        n = self.intents.reconcile()
        if n:
            log_event("server", ledger.account, action="startupReconcile", unknown_count=n)
        # 仅生产工位启动重试线程；测试/无resident实例不产生后台副作用
        if resident is not None and writes_enabled:
            threading.Thread(target=self._retry_loop, daemon=True).start()

    def _retry_loop(self):
        while True:
            try:
                self._retry_due()
            except Exception as e:
                log_event("server", self.ledger.account, action="retryLoopError", note=str(e)[:100])
            time.sleep(30)

    def _retry_due(self):
        """只重试“已批准且因工位不可用被requeue”的票；unknown/其他失败永不自动重试。"""
        now = time.time()
        for p in self.tickets.dir.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if d.get("status") != "approved" or not d.get("next_retry_at"):
                continue
            if now < float(d["next_retry_at"]):
                continue
            if datetime.now(timezone.utc) > datetime.fromisoformat(d["expires_at"]):
                self.tickets.expire(d["id"])
                continue
            try:
                idoc = json.loads((self.intents.dir / f"{d['id']}.json").read_text(encoding="utf-8"))
                if idoc.get("status") != "requeued":
                    continue
            except Exception:
                continue
            try:
                r = self.execute(d["payload"], timeout=180)
                log_event("server", self.ledger.account, action="retryResult",
                          ticket=d["id"], status=r.get("status"))
            except Exception as e:
                log_event("server", self.ledger.account, action="retryError",
                          ticket=d["id"], note=str(e)[:100])

    # ── 闸门链（execute 与 dry 共用；dry 在闸门后、副作用前返回） ──
    def _gates(self, intent: dict):
        try:
            check_pause()
        except PauseActive:
            raise Deny("PAUSED", "state/PAUSE 急停生效中")
        if self.ledger.aborted_today:
            raise Deny("ABORTED_TODAY", self.ledger.data["meta"].get("abort_reason") or "今日已熔断")
        kind = intent.get("kind")
        if kind not in WRITE_KINDS:
            raise Deny("BAD_REQUEST", f"未知动作类型: {kind}")
        iid = intent.get("intent_id")
        if not iid:
            raise Deny("BAD_REQUEST", "缺 intent_id（幂等键）")
        p = self.intents.dir.joinpath(f"{iid}.json")
        if p.exists():
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                doc = {}
            if doc.get("status") == "requeued":
                p.unlink()  # 核验翻回可重执行（verify-unknown"翻回 planned"同义）
            else:
                # 协议错误优先于额度错误（权威判定仍在串行段）
                raise Deny("IDEMPOTENCY_CONFLICT", f"幂等键已存在: {iid}（同票最多一次，红线 §一.3）")
        # 本票自身预留不双重占额——但只认持有活跃预留的票据状态（grok S2：被复活的
        # 过期票预留早已释放，own=1 会让 cap_left=0 时 0+1 放行 → 真实超额）
        t = self.tickets.get(iid)
        own = 1 if (t or {}).get("kind") == kind and (t or {}).get("status") in (
            "pending_review", "approved", "executing") else 0
        if self.ledger.cap_left(kind) + own <= 0:
            raise Deny("CAP_EXHAUSTED", f"{kind} 滚动24h额度用尽（fail-closed）")
        if not self.writes_enabled and not SIMULATE and not intent.get("dry"):
            raise Deny("WRITES_DISABLED", "写动作未启用（runtime.writes.enabled，红线 §二）")
        return kind, iid

    # ── P3-5 质量门：ref quality.js 语义（不合格一律拒发；发送用清洗版） ──
    def _quality_gate(self, kind: str, intent: dict) -> dict:
        text = str(intent.get("text") or "")
        qopts = {}
        if kind == "post":
            # 蓝 V 长文权限由账号实时状态+24h缓存决定；非蓝 V 保持 280 硬上限
            from account_status import post_max_len
            qopts["max_len"] = post_max_len(self.ledger.account)
        r = quality_check(text, history=self._history_texts(),
                          casual=bool(intent.get("casual")), **qopts)
        if not r["ok"]:
            log_event("server", self.ledger.account, action="qualityReject",
                      kind=kind, reason=r["reason"])
            raise Deny("QUALITY_REJECT", r["reason"])
        out = dict(intent)
        out["text"] = r["cleaned"]
        return out

    def _history_texts(self):
        """近 7 天已发出文案（查重历史；intents 目录即事实源）"""
        cutoff = time.time() - 7 * 86400
        out = []
        try:
            for p in self.intents.dir.glob("*.json"):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if d.get("status") != "done" or d.get("kind") not in ("post", "reply", "comment"):
                    continue
                try:
                    ts = datetime.fromisoformat(d["created_at"]).timestamp()
                except Exception:
                    ts = 0
                if ts >= cutoff:
                    t = (d.get("payload") or {}).get("text")
                    if t:
                        out.append(str(t))
        except Exception:
            pass
        return out[-100:]

    def dry_run(self, intent: dict):
        """过全闸、不落盘、不点击"""
        try:
            kind, iid = self._gates(intent)
            return {"dry": True, "kind": kind, "would_deny": None}
        except Deny as d:
            return {"dry": True, "would_deny": d.body}

    def execute(self, intent: dict, timeout=300):
        kind, iid = self._gates(intent)  # Deny 向上抛给 HTTP 层
        # ── P3-5 质量门（真实文本写：不合格一律拒发，票据不生成、额度不动）──
        # 只拦真实路径：SIMULATE 验收与 --dry 探路不受文案质量干扰
        if not SIMULATE and not intent.get("dry") and kind in ("post", "reply", "comment"):
            intent = self._quality_gate(kind, intent)
        # ── P3 票据路由（真实写；SIMULATE/dry 不走） ──
        if not SIMULATE and not intent.get("dry"):
            # 资讯源 scoped auto：仅满足当前Policy/Mandate/双审核的资讯帖可自动批准；
            # 仍创建票据并走同一执行链。其他人审票与其他动作不受此开关影响。
            auto = False
            if kind == "post":
                from news_auto import auto_authorized
                auto = auto_authorized(intent)
            if self.approval.get(kind, self.approval.get("default", "required")) == "required" or auto:
                self.tickets.sweep_expired()
                t = self.tickets.get(iid)
                if t is None:
                    tk = self.tickets.create(kind, intent, intent.get("rationale", ""))
                    log_event("server", self.ledger.account, action="ticketCreate",
                              kind=kind, ticket=iid)
                    if auto:
                        self.tickets.decide(iid, True, by="policy:news-auto")
                        t = self.tickets.get(iid)
                        log_event("server", self.ledger.account, action="autoApprove",
                                  kind=kind, ticket=iid,
                                  policy=(intent.get("mandate") or {}).get("policy_version"))
                    else:
                        return {"status": "pending_review", "ticket_id": iid,
                                "expires_at": tk["expires_at"]}
                if t["status"] == "pending_review":
                    # 旧review票不能被后来的auto配置追溯放行；须票据自身也满足当前auto授权
                    if auto and auto_authorized(t["payload"]):
                        self.tickets.decide(iid, True, by="policy:news-auto")
                        t = self.tickets.get(iid)
                        log_event("server", self.ledger.account, action="autoApprove",
                                  kind=kind, ticket=iid)
                    else:
                        return {"status": "pending_review", "ticket_id": iid,
                                "expires_at": t["expires_at"]}
                if t["status"] in ("rejected", "expired", "cancelled"):
                    raise Deny(f"TICKET_{t['status'].upper()}",
                               "旧票终态不可复活；新提案请换 intent_id")
                # approved / executing → 落队列执行
        ev = threading.Event()
        box = {}
        with self.q.mutex:
            self.seq += 1
            seq = self.seq
        self.q.put((PRIORITY.get(kind, 3), seq, (intent, ev, box)))
        if not ev.wait(timeout):
            raise Deny("QUEUE_TIMEOUT", "单飞队列等待超时")
        if box.get("deny"):
            raise Deny(box["deny"]["code"], box["deny"]["detail"])
        return box["result"]

    def _work(self):
        while True:
            _, _, (intent, ev, box) = self.q.get()
            try:
                box["result"] = self._perform(intent)
            except Deny as d:
                box["deny"] = d.body
            except Exception as e:  # 工作线程永不死
                box["deny"] = {"code": "INTERNAL", "detail": str(e)[:160]}
            ev.set()

    def _perform(self, intent: dict):
        """单飞工作线程内执行（唯一串行段——权威额度/幂等检查在此，入口闸只是快路径）
        SIMULATE=状态机全走；dry=过闸即返零副作用；真实写=票据执行段"""
        kind, iid = intent["kind"], intent["intent_id"]
        t = self.tickets.get(iid)
        own = 1 if (t or {}).get("kind") == kind and (t or {}).get("status") in (
            "pending_review", "approved", "executing") else 0
        if self.ledger.cap_left(kind) + own <= 0:
            # 权威检查（防并发 TOCTOU 超额）；本票自身活跃预留不双重计（同 _gates 口径）
            raise Deny("CAP_EXHAUSTED", f"{kind} 滚动24h额度用尽（fail-closed）")
        if intent.get("dry"):
            # dry 零副作用：不落 intent、不占额度（P1 版误占额度，P3 修正）
            return {"intent_id": iid, "status": "dry", "note": "过全闸未点击未写状态"}
        try:
            self.intents.put(iid, kind, {k: v for k, v in intent.items()
                                         if k not in ("dry", "rationale")})
        except ValueError:
            raise Deny("IDEMPOTENCY_CONFLICT", f"幂等键已存在: {iid}（同票最多一次）")
        self.intents.mark_intent(iid)  # exactly-once ①：执行前落 intent + 占额度
        if SIMULATE:
            self.intents.mark(iid, "done", "")
            return {"intent_id": iid, "status": "done", "simulated": True}

        # ── 真实写路径（P3）：票据执行段 ──
        tdoc = self.tickets.claim_for_execution(iid)
        if tdoc is None:
            self.intents.mark(iid, "failed", "票据不可执行")
            raise Deny("TICKET_NOT_APPROVED",
                       f"票据状态不可执行: {(self.tickets.get(iid) or {}).get('status')}")
        if kind == "like":
            from ledger import TaskState
            if not TaskState(self.ledger.account).opened_today(str(tdoc["payload"].get("status_id") or "")):
                self.intents.mark(iid, "failed", "NOT_OPENED")
                self.tickets.finish(iid, "failed", "未在今日打开记录（红线 §一.4）")
                raise Deny("NOT_OPENED", "只能互动今日真实打开过的帖")
        # 确定性拒发（WORKSTATION_DOWN 等网关级 Deny）≠ 结果不明：动作没到浏览器，
        # 记 failed 收尾（2026-10-26 首演实证：Deny 直抛会留 intent=意图态 + 票据卡 executing）
        try:
            payload = dict(tdoc["payload"])
            if kind in ("post", "reply", "comment"):
                from quality import clean
                canonical = clean(str(payload.get("text") or ""))[0]
                # 只执行“已批准票据的规范正文”，绝不采用同票号请求里的另一份文本
                if str(intent.get("text") or "") != canonical:
                    raise Deny("TICKET_TEXT_MISMATCH", "请求正文与已批准票据规范正文不一致")
                payload["text"] = canonical
            r = self._dispatch_real(kind, payload)
        except Deny as d:
            note = str(d.body.get("detail") or d.body.get("code") or "")[:80]
            if d.body.get("code") == "WORKSTATION_DOWN":
                # 确定性未执行：保留批准与预算，intent=requeued 允许同工位恢复后重试；
                # 不释放预留、不改成failed。unknown走另一条路径，绝不自动重发。
                self.intents.mark(iid, "requeued", note)
                self.tickets.requeue(iid, note)
                return {"intent_id": iid, "status": "approved",
                        "note": "工位暂不可用，已保留批准；有效期内自动重试"}
            self.intents.mark(iid, "failed", note)
            self.tickets.finish(iid, "failed", note)
            raise
        if r.get("ok"):
            status, note = "done", r.get("note", "")
        elif r.get("unknown"):
            status, note = "unknown", r.get("note", "")
            self._queue_verify(payload)
        else:
            status, note = "failed", r.get("note", "未知失败")
        self.intents.mark(iid, status, note)
        self.tickets.finish(iid, status, note)
        log_event("server", self.ledger.account, action="execute", kind=kind,
                  intent_id=iid, status=status)
        return {"intent_id": iid, "status": status, "note": note}

    def _dispatch_real(self, kind, payload):
        """真实动作派发到工位（浏览器任务递交制——主线程唯一执行者）+ 事后风控 toast 检查"""
        if self.resident is None or self.resident.ctx is None:
            # 工位可能在 6h 回收/探测恢复的秒级窗口内（2026-10-27 18:19 实证：发帖撞上
            # 回收后 3 秒被判死）。确定性未发=可安全等待：最多等 75s 等它重开再判死。
            deadline = time.time() + 75
            while time.time() < deadline and (self.resident is None or self.resident.ctx is None):
                time.sleep(5)
        if self.resident is None or self.resident.ctx is None:
            raise Deny("WORKSTATION_DOWN", "工位不在（窗外/休眠/故障；已等待75s）")
        import primitives as P

        def impl():
            with self.resident.browser_lock:
                page = self.resident.ctx.new_page()
                try:
                    if kind in ("reply", "comment"):
                        # comment=在他人帖下发言，与 reply 同管线（P4-4 接线）
                        r = P.reply_pipeline(page, payload)
                    elif kind == "post":
                        r = P.post_tweet(page, payload["text"])
                    elif kind == "like":
                        r = P.like_post(page, payload)
                    elif kind == "follow":
                        r = P.follow_user(page, payload["target_handle"])
                    elif kind == "unfollow":
                        r = P.unfollow_user(page, payload["target_handle"])
                    else:
                        r = {"ok": False, "note": f"kind {kind} 未接线"}
                    toast = P.risk_toast(page)  # 唯一整窗刹车：动作后读到风控 toast 即当日熔断
                    if toast:
                        self.ledger.abort_today(f"风控toast:{toast[:50]}")
                    return r
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass

        box = self.resident.submit_browser_task(impl, timeout=300)
        if "result" in box:
            return box["result"]
        return {"ok": False, "unknown": True, "note": f"浏览器任务失败:{box.get('error')}"}

    def _queue_verify(self, payload):
        """unknown → 待核清单（审计流；核验消费走 verify_unknown 的 intents 扫描，
        本文件只作运维面展示——曾是无消费者的死通道，review/grok S5）"""
        f = STATE_DIR / f"pending-verify-{self.ledger.account}{'_test' if SIMULATE else ''}.json"
        try:
            lst = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            lst = []
        lst.append({"at": datetime.now(timezone.utc).isoformat(),
                    "text": str(payload.get("text") or "")[:80],
                    "status_id": payload.get("status_id"), "kind": payload.get("kind")})
        import os
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(lst[-50:], ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, f)


def make_handler(gw: Gateway, resident):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _auth(self):
            tok = _token()
            return self.headers.get("Authorization") == f"Bearer {tok}"

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            if self.path == "/health":
                ws = resident is not None and resident.ctx is not None
                hb_age = None
                if resident:
                    st = resident.load_st()
                    hb = st.get("heartbeatAt")
                    if hb:
                        hb_age = int(time.time() - _parse(hb))
                return self._send(200, {"ok": True, "workstation": ws,
                                        "heartbeat_age_s": hb_age, "simulate": SIMULATE})
            if self.path == "/ledger":
                led = gw.ledger
                return self._send(200, {
                    "account": led.account,
                    "rolling24h_used": {k: led.used(k) for k in WRITE_KINDS},
                    "rolling24h_cap": {k: led.caps.get(k) for k in WRITE_KINDS},
                    "reserved": led.data["meta"].get("reserved", {}),
                    "aborted_today": led.aborted_today,
                    "paused": (Path(led.path.parent) / "PAUSE").exists(),
                })
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            try:
                body = self._body()
            except json.JSONDecodeError:
                return self._send(400, {"error": "bad json"})
            try:
                if self.path == "/dry":
                    return self._send(200, gw.dry_run(body))
                if self.path == "/execute":
                    return self._send(200, gw.execute(body))
                if self.path == "/profile/update":
                    if resident is None or resident.ctx is None:
                        return self._send(503, {"error": "workstation_down"})
                    return self._send(200, _update_profile(resident, body))
                if self.path in ("/read/timeline", "/read/post", "/read/profile"):
                    if resident is None or resident.ctx is None:
                        return self._send(503, {"error": "workstation_down"})
                    return self._send(200, _read(resident, self.path, body))
                if self.path == "/browse":
                    if resident is None or resident.ctx is None:
                        return self._send(503, {"error": "workstation_down"})
                    mins = min(max(float(body.get("minutes", 4)), 1), 8)
                    return self._send(200, _browse(resident, mins, body.get("surface", "home")))
                self._send(404, {"error": "not found"})
            except Deny as d:
                # Deny 必留审计（MoA 审查：全月 qualityReject=0、拒因不可复原的盲区）
                log_event("server", gw.ledger.account, action="deny",
                          path=self.path, code=d.body.get("code"),
                          detail=str(d.body.get("detail"))[:120])
                return self._send(200, {"denied": d.body})
            except Exception as e:
                # 非 Deny 异常兜底（review 低19：炸 handler 留无响应连接）
                try:
                    log_event("server", gw.ledger.account, action="handlerError",
                              path=self.path, note=f"{type(e).__name__}: {e}"[:160])
                except Exception:
                    pass
                return self._send(500, {"error": "internal"})
    return H


def _read(resident, path, body):
    """读操作：server 线程只递交闭包，主线程执行（Playwright 同步 API 禁止跨线程）；
    临时页用完即关（kimi §1.1 范式）"""
    import primitives as P

    def impl():
        with resident.browser_lock:
            page = resident.ctx.new_page()
            try:
                if path == "/read/profile":
                    page.goto("https://x.com/settings/profile",
                              wait_until="domcontentloaded", timeout=60000)
                    P.sleep(6000)
                    return page.evaluate("""() => ({
                        url: location.href, title: document.title,
                        text: (document.body.innerText || '').slice(0, 2500),
                        buttons: [...document.querySelectorAll('button,[role="button"]')]
                            .map(x => ({text: (x.innerText || '').slice(0, 80),
                                       label: x.getAttribute('aria-label'),
                                       testid: x.getAttribute('data-testid')})).slice(0, 50),
                        inputs: [...document.querySelectorAll('input')].map(x => ({
                            name: x.name, type: x.type,
                            value: x.type === 'file' ? '' : x.value,
                            label: x.getAttribute('aria-label')})),
                        files: document.querySelectorAll('input[type=file]').length
                    })""")
                if path == "/read/timeline":
                    surface = body.get("surface", "home")
                    url = {"home": "https://x.com/home",
                           "explore": "https://x.com/explore"}.get(surface, "https://x.com/home")
                    if not P.goto_feed(page, url,
                                       must_have_article=(surface != "explore")):
                        diag = {}
                        try:  # 失败取证（与 _browse 同款：feed_load_failed 必须留现场）
                            diag = page.evaluate(
                                "() => ({url: location.pathname, title: (document.title||'').slice(0,50),"
                                " arts: document.querySelectorAll('article').length,"
                                " loginBtn: !!document.querySelector('[data-testid=\"loginButton\"]'),"
                                " tabs: [...document.querySelectorAll('[role=\"tab\"]')].map(t=>t.textContent).slice(0,5),"
                                " head: (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,100)})"
                            )
                        except Exception:
                            pass
                        return {"error": "feed_load_failed", "diag": diag}
                    P.sleep(2500)
                    # explore 趋势壳要靠滚动逼懒加载（home 一屏即有内容）
                    for _ in range(3 if surface == "explore" else 1):
                        P.human_scroll(page, 600)
                        P.sleep(1200)
                    return {"posts": P.collect_posts(page, min(body.get("limit", 20), 30))}
                # /read/post
                url = body.get("url") or f"https://x.com/i/web/status/{body.get('status_id')}"
                sid = str(body.get("status_id") or "")
                if not P.goto_feed(page, url, 2, sid or None):
                    return {"error": "post_load_failed"}
                P.sleep(2500)
                arts = P.collect_posts(page, 30)
                main = next((a for a in arts if a["id"] == sid), None)
                return {"post": main, "replies": [a for a in arts if a["id"] != sid]}
            finally:
                try:
                    page.close()
                except Exception:
                    pass

    box = resident.submit_browser_task(impl)
    if "result" in box:
        return box["result"]
    return {"error": box.get("error", "browser_task_failed")}


def _update_profile(resident, body):
    """本机 Token 资料维护：显示名/头像/横幅/简介。只允许运维临时上传的本地图片，
    不接受 URL 或任意路径；仍由工位主线程串行操作。蓝V可能进入复审。"""
    import primitives as P
    display_name = str(body.get("display_name") or "").strip()
    avatar_path = body.get("avatar_path")
    banner_path = body.get("banner_path")
    bio = str(body.get("bio") or "").strip()
    if display_name and not (1 <= len(display_name) <= 50):
        return {"error": "display_name 长度须为1-50字"}
    if bio and len(bio) > 160:
        return {"error": "bio 长度须为1-160字"}
    for key, label in (("avatar_path", "avatar_path"), ("banner_path", "banner_path")):
        p = body.get(key)
        if p:
            f = Path(str(p))
            if f.parent != Path("/tmp") or not f.is_file() \
                    or f.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                return {"error": f"{label} 仅允许 /tmp 下的 png/jpg 临时文件"}
    if not display_name and not avatar_path and not banner_path and not bio:
        return {"error": "未提供 display_name/avatar_path/banner_path/bio"}

    def _apply_crop(page, stage):
        """头像/横幅上传后的裁剪应用弹窗；无弹窗或按钮不可用时如实报错并附现场。"""
        state = page.evaluate("""() => ({
            dialogs: document.querySelectorAll('[role="dialog"]').length,
            buttons: [...document.querySelectorAll('[role="dialog"] button')]
                .map(x => ({text: x.innerText, label: x.getAttribute('aria-label'),
                           testid: x.getAttribute('data-testid')})),
            text: (document.querySelector('[role="dialog"]')?.innerText || '').slice(0, 700)
        })""")
        print(f"PROFILE_{stage.upper()}_DIALOG", json.dumps(state, ensure_ascii=False), flush=True)
        apply_button = page.locator('[data-testid="applyButton"]')
        if not apply_button.count():
            apply_button = page.locator('[role="dialog"] button').filter(has_text="应用")
        if not apply_button.count():
            return {"ok": False, "stage": stage, "note": f"未找到{stage}应用按钮", "dialog": state}
        if apply_button.first.is_disabled():
            return {"ok": False, "stage": stage, "note": f"{stage}应用按钮不可用", "dialog": state}
        apply_button.first.click()
        P.sleep(4000)
        return None

    def impl():
        with resident.browser_lock:
            page = resident.ctx.new_page()
            try:
                page.goto("https://x.com/settings/profile",
                          wait_until="domcontentloaded", timeout=60000)
                P.sleep(6000)
                if display_name:
                    field = page.locator('input[name="displayName"]')
                    field.fill(display_name)
                    if field.input_value() != display_name:
                        return {"ok": False, "stage": "display_name",
                                "note": "显示名写入后回读不一致，未保存"}
                if banner_path:
                    banner_button = None
                    for label in ("添加横幅照片", "编辑横幅照片", "更改横幅照片"):
                        btn = page.locator(f'[aria-label="{label}"]')
                        if btn.count():
                            banner_button = btn.first
                            break
                    if banner_button is None:
                        return {"ok": False, "stage": "banner",
                                "note": "未找到横幅上传按钮（添加/编辑/更改横幅照片均无）"}
                    with page.expect_file_chooser(timeout=15000) as chooser:
                        banner_button.click()
                    chooser.value.set_files(str(banner_path))
                    P.sleep(5000)
                    bad = _apply_crop(page, "banner_crop")
                    if bad:
                        return bad
                if avatar_path:
                    avatar_button = page.locator('[aria-label="添加头像照片"]')
                    if avatar_button.count():
                        with page.expect_file_chooser(timeout=15000) as chooser:
                            avatar_button.click()
                        chooser.value.set_files(str(avatar_path))
                    else:
                        # 已有头像时 X 可能呈现“编辑照片”，通过其文件选择器进入上传。
                        with page.expect_file_chooser(timeout=15000) as chooser:
                            page.get_by_role("button", name="编辑照片", exact=True).click()
                        chooser.value.set_files(str(avatar_path))
                    P.sleep(5000)
                    bad = _apply_crop(page, "avatar_crop")
                    if bad:
                        return bad
                if bio:
                    bio_field = page.locator('textarea[name="description"]')
                    if not bio_field.count():
                        return {"ok": False, "stage": "bio", "note": "未找到简介输入框"}
                    bio_field.fill(bio)
                    if bio_field.input_value() != bio:
                        return {"ok": False, "stage": "bio",
                                "note": "简介写入后回读不一致，未保存"}
                save = page.locator('[data-testid="Profile_Save_Button"]')
                if not save.count():
                    return {"ok": False, "stage": "save", "note": "未找到资料保存按钮"}
                if save.is_disabled():
                    return {"ok": False, "stage": "save", "note": "资料保存按钮不可用"}
                save.click()
                P.sleep(2500)
                # Premium 确认框不是 [role=dialog]（2026-11-09 实证），直接按文本节点
                # 找“保存更改”容器内的保存按钮；不能把表单回显当成功。
                confirmed = page.evaluate("""() => {
                    const holders = [...document.querySelectorAll('body *')].filter(el =>
                        [...el.childNodes].some(n => n.nodeType === 3
                            && (n.textContent || '').includes('保存更改')));
                    let node = holders[holders.length - 1];
                    for (let up = 0; node && up < 5; up++) {
                        const btn = [...node.querySelectorAll('button')].find(b =>
                            (b.innerText || '').trim() === '保存' && !b.disabled);
                        if (btn) { btn.click(); return {clicked: true, up}; }
                        node = node.parentElement;
                    }
                    return {clicked: false};
                }""")
                P.sleep(7000)
                still_confirming = page.evaluate(
                    "() => (document.body.innerText || '').includes('保存更改')")
                result = {"ok": not still_confirming, "url": page.url,
                          "confirmed": confirmed,
                          "display_name": page.locator('input[name="displayName"]').input_value()
                          if page.locator('input[name="displayName"]').count() else display_name,
                          "bio_set": bool(bio),
                          "toast": P.read_toast(page),
                          "text": page.evaluate(
                              "() => (document.body.innerText || '').slice(0, 700)")}
                log_event("profile", resident.account, action="update",
                          display_name=display_name, avatar=bool(avatar_path),
                          banner=bool(banner_path), bio=bool(bio),
                          ok=result["ok"], toast=result["toast"])
                return result
            except Exception as e:
                log_event("profile", resident.account, action="updateError",
                          note=f"{type(e).__name__}: {e}"[:160])
                return {"ok": False, "error": f"{type(e).__name__}: {e}"[:180]}
            finally:
                try:
                    page.close()
                except Exception:
                    pass

    box = resident.submit_browser_task(impl, timeout=150)
    return box.get("result") or {"ok": False, "error": box.get("error", "browser_task_failed")}


def _parse(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()


def _browse(resident, minutes, surface="home"):
    """P2 日常只读例程：拟人刷 feed（变速滚动+15% 回看+偶发长停顿），不点赞不发帖，
    只把真实展示过的帖写入打开记录（TaskState sightings——红线 §一.4 数据面）"""
    import random
    import primitives as P
    from ledger import TaskState

    def impl():
        deadline = time.time() + minutes * 60
        ts = TaskState(resident.account)
        seen = {}
        with resident.browser_lock:
            page = resident.ctx.new_page()
            try:
                url = {"home": "https://x.com/home", "explore": "https://x.com/explore"}.get(surface, "https://x.com/home")
                if not P.goto_feed(page, url, must_have_article=(surface != "explore")):
                    diag = {}
                    try:  # 失败取证（2026-10-26：feed_load_failed 一整天无现场可查的教训）
                        diag = page.evaluate(
                            "() => ({url: location.pathname, title: (document.title||'').slice(0,50),"
                            " arts: document.querySelectorAll('article').length,"
                            " loginBtn: !!document.querySelector('[data-testid=\"loginButton\"]'),"
                            " head: (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,100)})")
                    except Exception:
                        pass
                    return {"error": "feed_load_failed", "diag": diag}
                P.sleep(2000 + random.random() * 2000)  # 进场先看一会儿
                while time.time() < deadline:
                    P.human_scroll(page, 500 + random.random() * 700)
                    P.sleep(900 + random.random() * 2200)  # 帖间停顿
                    for p in P.collect_posts(page, 30):
                        if p.get("id") and p["id"] not in seen:
                            seen[p["id"]] = p
                    if random.random() < 0.12:  # 偶发长停顿（读到长帖/图片）
                        P.sleep(3000 + random.random() * 5000)
                ts.record_opened(list(seen.values()))
                summary = {"action": "browse", "minutes": minutes, "surface": surface,
                           "opened": len(seen), "authors": len({p["author"] for p in seen.values() if p.get("author")})}
                ts.record_session(summary)
                log_event("browse", resident.account, **{k: v for k, v in summary.items() if k != "action"})
                return {"opened": summary["opened"], "authors": summary["authors"],
                        "duration_s": int(minutes * 60)}
            finally:
                try:
                    page.close()
                except Exception:
                    pass

    box = resident.submit_browser_task(impl, timeout=minutes * 60 + 120)
    return box.get("result") or {"error": box.get("error", "browser_task_failed")}


_TOKEN_PATH = None


def _token():
    global _TOKEN_PATH
    p = _TOKEN_PATH or Path("/etc/xar/hand.token")
    try:
        return p.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        tok = secrets.token_hex(32)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(tok, encoding="utf-8")
        os.chmod(tmp, 0o600)   # 先收紧再落地（review 低17：0644 可读窗口）
        os.replace(tmp, p)
        return tok


class _Server(ThreadingHTTPServer):
    request_queue_size = 256   # 默认 5 会被并发突发打爆（连接重置）
    daemon_threads = True
    allow_reuse_address = True  # TIME_WAIT 残留连接会让重绑 8741 失败（p1 连环假象根因）


def serve(resident, port=8741):
    """resident 进程内调用：常驻工位就绪后启动本函数（线程）"""
    import yaml
    cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
    rt = cfg.get("runtime", {})
    led = Ledger(resident.account)
    gw = Gateway(led, Intents(led), resident=resident,
                 writes_enabled=bool((rt.get("writes") or {}).get("enabled")),
                 approval=(rt.get("approval") or {}))
    httpd = _Server(("127.0.0.1", port), make_handler(gw, resident))
    log_event("server", resident.account, action="listen", port=port,
              writes=rt.get("writes", {}).get("enabled", False))
    httpd.serve_forever()


def main_simulate(port=8742):
    """SIMULATE 自检模式：无浏览器全状态机（验收：单飞并发/幂等/崩溃对账/--dry）
    运行: XAR_SIMULATE=1 XAR_STATE=/tmp/xar-sim python server.py"""
    global _TOKEN_PATH
    _TOKEN_PATH = Path(os.environ.get("XAR_STATE", "/tmp/xar-sim")) / "hand.token"
    _token()  # 急切创建，避免测试端等文件/懒创建互锁
    led = Ledger(os.environ.get("XAR_ACCOUNT", "wcixot"))
    gw = Gateway(led, Intents(led), writes_enabled=os.environ.get("XAR_WRITES") == "1")
    httpd = _Server(("127.0.0.1", port), make_handler(gw, None))
    print(f"SIMULATE server on {port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main_simulate()
