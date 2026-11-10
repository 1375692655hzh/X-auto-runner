# qq_bot.py — QQ 官方机器人桥（P3-4）：拉模式 IM 指令面 + 票据审批
# 协议：QQ 开放平台 v2（botpy SDK 已从 PyPI 消失，手撸 WS）
#   token: POST bots.qq.com/app/getAppAccessToken（7200s，提前 5min 刷新）
#   网关: GET api.sgroup.qq.com/gateway → WS；op10 Hello→op2 Identify(intents=1<<25)
#         →op0 Dispatch(C2C_MESSAGE_CREATE)；op1 心跳
#   回复: POST /v2/users/{openid}/messages（被动回复带 msg_id，零主动限额消耗）
# 指令集（拉模式）：
#   票 | 发 <id> | 改 <id> <正文> | 驳 <id> | 状态 | 报 | 停（二次确认） | 帮助
# 指挥官白名单：im.qq.commander_whitelist（openid）；空则首个发信人自动认领（审计留痕）
import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, log_event, file_lock, _atomic_write  # noqa: E402
from tickets import Tickets, TicketError  # noqa: E402
from ledger import Ledger  # noqa: E402

try:
    import websocket  # websocket-client
except ImportError:
    print("pip install websocket-client", file=sys.stderr)
    sys.exit(1)

import yaml

API = "https://api.sgroup.qq.com"
INTENTS_GROUP_C2C = 1 << 25
COMMANDER_FILE = Path("/opt/xar/state/commander.json")


class Bot:
    def __init__(self):
        cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
        qq = cfg["im"]["qq"]
        self.app_id = str(qq["app_id"])
        self.secret = str(qq["app_secret"])
        self.account = cfg["account"]["handle"].lower()
        self.token = None
        self.token_exp = 0
        self.seq = 0
        self.msg_seq_map = {}  # msg_id -> 已用序号（每条入站最多 5 回）
        try:
            qcfg = cfg.get("im", {}).get("qq", {})
        except Exception:
            qcfg = {}
        self.commander_whitelist = qcfg.get("commander_whitelist") or []
        self.commander_claim = bool(qcfg.get("commander_claim", False))
        self._pause_armed = {}   # openid -> 停 指令的确认窗口起点
        self.ops_cfg = cfg.get("ops") or {}
        self._last_ops_check = 0

    # ── 鉴权 ──
    def ensure_token(self):
        if self.token and time.time() < self.token_exp - 300:
            return
        req = urllib.request.Request(
            "https://bots.qq.com/app/getAppAccessToken",
            data=json.dumps({"appId": self.app_id, "clientSecret": self.secret}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        self.token, self.token_exp = d["access_token"], time.time() + int(d["expires_in"])
        log_event("qqbot", self.account, action="tokenRefresh")

    def _auth_headers(self):
        self.ensure_token()
        return {"Authorization": f"QQBot {self.token}", "Content-Type": "application/json"}

    # ── REST：发 C2C 消息（被动回复） ──
    def reply(self, openid, text, msg_id=None):
        seq = self.msg_seq_map.get(msg_id, 0)
        if msg_id and seq >= 5:
            return False  # 每条入站最多 5 连回，超出静默（防触发风控）
        body = {"content": text[:2000], "msg_type": 0}
        if msg_id:
            body["msg_id"] = msg_id
            body["msg_seq"] = seq + 1
            self.msg_seq_map[msg_id] = seq + 1
        req = urllib.request.Request(f"{API}/v2/users/{openid}/messages",
                                     data=json.dumps(body, ensure_ascii=False).encode(),
                                     headers=self._auth_headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                log_event("qqbot", self.account, action="reply", rc=r.status,
                          to=openid[-6:], text=str(text)[:80])
                return True
        except urllib.error.HTTPError as e:
            log_event("qqbot", self.account, action="replyFail",
                      rc=e.code, note=e.read().decode("utf-8", "replace")[:120])
            return False
        except Exception as e:
            log_event("qqbot", self.account, action="replyFail", note=str(e)[:120])
            return False

    # ── 新待审稿主动通知（active C2C；不消耗被动回复序号） ──
    def _commander_openid(self):
        try:
            d = json.loads(COMMANDER_FILE.read_text(encoding="utf-8"))
            if d.get("openids"):
                return d["openids"][0]
        except Exception:
            pass
        return self.commander_whitelist[0] if self.commander_whitelist else None

    def _notify_loop(self):
        first = True
        while True:
            try:
                self._notify_pending(first)
            except Exception as e:
                log_event("qqbot", self.account, action="notifyError", note=str(e)[:120])
            first = False
            time.sleep(15)

    def _notify_pending(self, first=False):
        oid = self._commander_openid()
        if not oid:
            return
        path = STATE_DIR / "qq-notified.json"
        with file_lock(path):
            try:
                notified = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                notified = {}
            rows = Tickets(Ledger(self.account)).list_open()
            changed = False
            if first:
                # 启动时不补推历史待审，避免旧稿批量轰炸；只跟踪启动后的新票
                for d in rows:
                    if d["id"] not in notified:
                        notified[d["id"]] = "baseline"
                        changed = True
            else:
                for d in rows:
                    tid = d.get("id")
                    if not tid or notified.get(tid):
                        continue
                    p = d.get("payload") or {}
                    body = p.get("text") or ""
                    text = (f"我想发 {tid}\n\n正文：\n{body}\n\n"
                            f"回复「发{tid}」发布；「驳{tid} 标签 备注」放弃。\n"
                            f"也可以直接说：把{tid}改得更自然/删掉某段/换个角度。\n"
                            f"回复「票 {tid}」看原始素材与审核。")
                    if self.reply(oid, text, None):
                        notified[tid] = "sent"
                        changed = True
                        log_event("qqbot", self.account, action="notify", ticket=tid)
            if self._ops_alerts(oid, notified):
                changed = True
            if changed:
                _atomic_write(path, notified)

    def _ops_alerts(self, oid, notified):
        """低频运营告警：auto 熔断按原因去重，token 日超阈按日去重。"""
        if time.time() - self._last_ops_check < 300:
            return False
        self._last_ops_check = time.time()
        changed = False
        try:
            c = json.loads((STATE_DIR / "news-auto-circuit.json").read_text(encoding="utf-8"))
            reason = str(c.get("reason") or "blocked")
            key = "alert:auto:" + reason
            if c.get("blocked") and not notified.get(key):
                if self.reply(oid, f"⚠️ 资讯 auto 已熔断：{reason}\n新资讯转为人工审核；现有待审稿不受影响。", None):
                    notified[key] = "sent"
                    changed = True
        except Exception:
            pass
        try:
            limit = int(self.ops_cfg.get("daily_token_alert", 2000000))
        except (TypeError, ValueError):
            limit = 2000000
        if limit > 0:
            day = datetime.now().strftime("%Y-%m")
            log = STATE_DIR / f"log-{day}.jsonl"
            total = 0
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                with log.open(encoding="utf-8") as f:
                    for line in f:
                        try:
                            row = json.loads(line)
                            if row.get("action") == "chat" and str(row.get("at", "")).startswith(today):
                                total += int(row.get("prompt_tokens") or 0) + int(row.get("completion_tokens") or 0)
                        except Exception:
                            continue
            except FileNotFoundError:
                pass
            key = "alert:tokens:" + datetime.now().strftime("%Y-%m-%d")
            if total >= limit and not notified.get(key):
                if self.reply(oid, f"⚠️ 今日 LLM token 已达 {total:,}（告警线 {limit:,}），请检查任务量与供应商。", None):
                    notified[key] = "sent"
                    changed = True
        # 生产线沉默告警：活跃窗内超 6h 无成功节拍且过开局宽限 → 按日去重。
        # 背景：2026-11-06~11-10 上游停摆 4 天无人发现，直到运营者追问产量。
        try:
            from pathlib import Path
            cfg = __import__("yaml").safe_load(
                Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
            win = ((cfg.get("runtime") or {}).get("active_window") or ["06:30", "24:00"])
        except Exception:
            win = ["06:30", "24:00"]

        def _to_min(hhmm):
            h, m = str(hhmm).split(":")[:2]
            return int(h) * 60 + int(m)

        now_min = datetime.now().hour * 60 + datetime.now().minute
        if _to_min(win[0]) <= now_min < _to_min(win[1]):
            stale_h = None
            last = ""
            try:
                st = json.loads((STATE_DIR / "news-auto.json").read_text(encoding="utf-8"))
                last = str(st.get("last_run_at") or "")
                if last:
                    lt = datetime.fromisoformat(last[:19])
                    stale_h = (datetime.now() - lt).total_seconds() / 3600
                else:
                    stale_h = 99.0
            except Exception:
                stale_h = None
            if stale_h is not None and stale_h > 6 and now_min >= _to_min(win[0]) + 360:
                key = "alert:silence:" + datetime.now().strftime("%Y-%m-%d")
                if not notified.get(key):
                    msg = (f"⚠️ 资讯生产线已沉默 {stale_h:.0f} 小时"
                           f"（最后成功节拍 {last[:16] or '无记录'}），请检查 datastation 与评分链。")
                    if self.reply(oid, msg, None):
                        notified[key] = "sent"
                        changed = True
        return changed

    # ── 指挥官白名单 ──
    def is_commander(self, openid) -> bool:
        try:
            d = json.loads(COMMANDER_FILE.read_text(encoding="utf-8"))
            if openid in d.get("openids", []):
                return True
        except Exception:
            pass
        wl = self.commander_whitelist
        if str(openid) in [str(x) for x in wl]:
            return True
        # 首个发信人认领：仅当白名单为空且配置显式允许（MoA 审查：commander.json
        # 损坏/被删时任意 QQ 用户可接管指令通道——默认关闭认领，白名单为唯一入口）
        if not wl and not COMMANDER_FILE.exists() and self.commander_claim:
            COMMANDER_FILE.write_text(json.dumps(
                {"openids": [openid], "claimed_at": datetime.now(timezone.utc).isoformat()},
                ensure_ascii=False), encoding="utf-8")
            log_event("qqbot", self.account, action="commanderClaimed", openid=openid[-6:])
            return True
        return False

    # ── XClient 调用 ──
    def xc(self, method, path, body=None):
        tok = Path("/etc/xar/hand.token").read_text(encoding="utf-8").strip()
        req = urllib.request.Request(f"http://127.0.0.1:8741{path}", method=method,
                                     headers={"Authorization": f"Bearer {tok}",
                                              "Content-Type": "application/json"})
        if body is not None:
            req.data = json.dumps(body, ensure_ascii=False).encode()
        try:
            with urllib.request.urlopen(req, timeout=timeout_for(path)) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return {"_http": e.code, **json.loads(e.read() or b"{}")}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    # ── 指令路由 ──
    def handle(self, text, openid, msg_id):
        if not self.is_commander(openid):
            self.reply(openid, "未授权（指挥官白名单外）", msg_id)
            return
        t = text.strip()
        led = Ledger(self.account)
        tk = Tickets(led)
        try:
            if t in ("帮助", "help", "HELP", "/help"):
                self.reply(openid, "指令：\n票 —— 待审列表\n票 <id> —— 资讯原始信息卡\n发 <id> —— 批准；驳 <id> [标签] [备注] —— 驳回留反馈\n取消 <id> —— 取消待审票\n"
                                   "把 <id> 改成… —— 主脑按自然语言改稿并复审\n"
                                   "发帖 <正文> / 回复 <帖子ID> <正文> —— 人工写动作\n"
                                   "改 <id> <新正文> —— 改后发\n驳 <id> —— 驳回\n"
                                   "关注/取关 <handle> —— 人工关注操作\n"
                                   "状态 —— 工位/账本\n报 —— 今日报告\n反馈/效果 —— 近7天摘要\n"
                                   "池 —— 互动账号池\n互关/大V/解黑 <handle> —— 设档\n"
                                   "黑 <handle> <原因> —— 拉黑\n停 —— 急停(需二次确认)", msg_id)
            elif t == "票":
                tk.sweep_expired()
                rows = tk.list_open()
                if not rows:
                    self.reply(openid, "无待审票据 ✅", msg_id)
                    return
                out = [f"待审 {len(rows)} 张（回「票 <id>」看原始信息卡）："]
                for d in rows:
                    p = d["payload"]
                    out.append(f"[{d['id']}] {d['kind']} @{p.get('target_handle') or '-'}")
                    out.append(f"  {(p.get('text') or '')[:80]}")
                    out.append(f"  理由:{(d.get('rationale') or '')[:50]} 回复「发 {d['id']}」执行")
                self.reply(openid, "\n".join(out), msg_id)
            elif t.startswith("票 "):
                tid = t[2:].strip().split()[0]
                d = tk.get(tid)
                if not d:
                    self.reply(openid, f"票据不存在: {tid}", msg_id)
                    return
                p = d["payload"]
                nd = p.get("news_detail")
                if not nd:
                    text = (f"[{d['id']}] {d['kind']}｜{d['status']}\n"
                            f"正文：{p.get('text') or ''}\n理由：{d.get('rationale') or ''}")
                else:
                    contract = (p.get("analysis_pack") or {}).get("contract") or {}
                    analysis_txt = ""
                    if contract:
                        analysis_txt = (f"【主脑立论】\n问题：{contract.get('question')}\n"
                                        f"观点：{contract.get('claim')}\n"
                                        f"读者价值：{contract.get('reader_value')}\n\n")
                    reviews = p.get("value_reviews") or {}
                    review_txt = ""
                    if reviews.get("editor"):
                        er = reviews["editor"]
                        review_txt = (f"【编辑审核】{er.get('decision')}｜"
                                      f"{json.dumps(er.get('issues') or [], ensure_ascii=False)[:180]}\n\n")
                    elif reviews:
                        review_txt = (f"【独立审核】盲评:{reviews.get('blind', {}).get('code')}｜"
                                      f"证据/合约:{reviews.get('evidence', {}).get('code')}\n\n")
                    text = (f"【资讯审核信息卡】{d['id']}｜{d['status']}\n\n"
                            f"【来源】{nd.get('form')}｜{nd.get('source')}｜{nd.get('time')}\n"
                            f"【原文标题】{nd.get('title') or '（无标题）'}\n\n"
                            + analysis_txt +
                            f"【素材原文】\n{nd.get('source_text') or '（无正文）'}\n\n"
                            f"【信息增量】{nd.get('increment')}\n"
                            f"【价值判断】{nd.get('tier')}档｜{nd.get('score')}分｜{nd.get('selection_reason')}\n\n"
                            + review_txt +
                            f"【拟发布正文】\n{p.get('text') or ''}\n\n"
                            f"回复「发 {d['id']}」执行；「驳 {d['id']}」驳回")
                for i in range(0, min(len(text), 6500), 1300):
                    self.reply(openid, text[i:i + 1300], msg_id)
            elif re.match(r"^发\s*\S+$", t) and not t.startswith("发帖"):
                tid = re.sub(r"^发\s*", "", t).strip()
                tk.decide(tid, True, by=f"qq:{openid[-6:]}")
                r = self.xc("POST", "/execute", tk.get(tid)["payload"])
                if r.get("status") == "approved":
                    self.reply(openid, f"{tid} 已批准，工位暂不可用；将在有效期内自动重试，未发布", msg_id)
                elif r.get("status") == "pending_review":
                    self.reply(openid, f"{tid} 已批（等待执行）", msg_id)
                elif r.get("status") in ("done", "unknown", "failed", "dry"):
                    self.reply(openid, f"{tid} 执行:{r['status']} {r.get('note','')[:80]}", msg_id)
                elif "denied" in r:
                    self.reply(openid, f"{tid} 被拒:{r['denied'].get('code')} "
                               f"{r['denied'].get('detail','')[:60]}", msg_id)
                else:
                    self.reply(openid, f"{tid} 结果:{json.dumps(r, ensure_ascii=False)[:120]}", msg_id)
            elif t.startswith("改 "):
                parts = t[2:].strip().split(None, 1)
                if len(parts) < 2:
                    self.reply(openid, "用法: 改 <id> <新正文>", msg_id)
                    return
                tk.decide(parts[0], True, by=f"qq:{openid[-6:]}", edited_text=parts[1])
                r = self.xc("POST", "/execute", tk.get(parts[0])["payload"])
                self.reply(openid, f"{parts[0]} 改后执行:{r.get('status') or r}", msg_id)
            elif re.match(r"^驳\s*\S+", t):
                m = re.match(r"^驳\s*(\S+)(?:\s+(\S+)(?:\s+([\s\S]*))?)?$", t)
                tid, label, note = m.group(1), m.group(2) or "", m.group(3) or ""
                tk.decide(tid, False, by=f"qq:{openid[-6:]}", reason=label, note=note)
                doc = tk.get(tid)
                self.reply(openid, f"{tid} 已驳回（{(doc.get('feedback') or {}).get('label')}，额度已释放）", msg_id)
            elif t.startswith("取消 "):
                parts = t.split(None, 2)
                if len(parts) < 2:
                    self.reply(openid, "用法：取消 <id> [备注]", msg_id)
                    return
                tk.cancel(parts[1], by=f"qq:{openid[-6:]}",
                          note=parts[2] if len(parts) > 2 else "")
                self.reply(openid, f"{parts[1]} 已取消，未执行，额度已释放", msg_id)
            elif t == "状态":
                h = self.xc("GET", "/health")
                l = self.xc("GET", "/ledger")
                used = l.get("rolling24h_used", {})
                cap = l.get("rolling24h_cap", {})
                self.reply(openid,
                           f"工位:{'在岗' if h.get('workstation') else '离岗'} "
                           f"心跳:{h.get('heartbeat_age_s')}s\n"
                           f"24h: " + " ".join(f"{k}:{used.get(k,0)}/{cap.get(k)}" for k in ("post","reply","comment","like")) +
                           f"\n熔断:{'是' if l.get('aborted_today') else '否'} "
                           f"PAUSE:{'是' if l.get('paused') else '否'}", msg_id)
            elif t == "池":
                from contacts import Contacts
                s = Contacts(self.account).summary()
                if not s:
                    self.reply(openid, "互动池为空。用「互关 <handle>」「大V <handle>」「黑 <handle> 原因」录入", msg_id)
                    return
                out = [f"互动池（{sum(len(v) for v in s.values())} 人）："]
                zh = {"mutual": "互关", "bigv": "大V", "normal": "正常", "blacklist": "黑名单"}
                for k in ("mutual", "bigv", "normal", "blacklist"):
                    if s.get(k):
                        row = "、".join(f"@{x['handle']}({x['fam']}·{x['n']})" for x in s[k][:6])
                        out.append(f"[{zh[k]}] {row}" + ("…" if len(s[k]) > 6 else ""))
                self.reply(openid, "\n".join(out), msg_id)
            elif t.startswith(("互关 ", "大V ", "黑 ", "解黑 ")):
                from contacts import Contacts
                pool = Contacts(self.account)
                parts = t.split(None, 1)
                who = (parts[1] if len(parts) > 1 else "").strip().split()[0] if len(parts) > 1 else ""
                if not who:
                    self.reply(openid, "用法: 互关/大V/解黑 <handle>；黑 <handle> <原因>", msg_id)
                    return
                try:
                    if t.startswith("互关 "):
                        pool.set_tier(who, "mutual")
                        self.reply(openid, f"@{who.lstrip('@')} → 互关档（串门评论已开启）", msg_id)
                    elif t.startswith("大V "):
                        pool.set_tier(who, "bigv")
                        self.reply(openid, f"@{who.lstrip('@')} → 大V档", msg_id)
                    elif t.startswith("解黑 "):
                        pool.set_tier(who, "normal")
                        self.reply(openid, f"@{who.lstrip('@')} 已解黑回正常档", msg_id)
                    else:
                        reason = t[2:].strip()[len(who):].strip()
                        pool.set_tier(who, "blacklist", reason)
                        self.reply(openid, f"@{who.lstrip('@')} → 黑名单（不互动）", msg_id)
                except ValueError as e:
                    self.reply(openid, f"失败: {e}", msg_id)
            elif t.startswith("回复 "):
                parts = t.split(None, 2)
                if len(parts) < 3 or not parts[1].isdigit():
                    self.reply(openid, "用法：回复 <帖子ID> <正文>", msg_id)
                    return
                sid, body = parts[1], parts[2].strip()
                detail = self.xc("POST", "/read/post", {"status_id": sid})
                if not detail.get("post"):
                    self.reply(openid, "原帖读取失败，未创建回复", msg_id)
                    return
                author = detail["post"].get("author") or ""
                if not author:
                    self.reply(openid, "原帖作者读取失败，未创建回复", msg_id)
                    return
                iid = f"manual-r-{int(time.time())}"
                payload = {"kind": "reply", "intent_id": iid, "text": body,
                           "status_id": sid, "post_url": f"https://x.com/{author}/status/{sid}",
                           "target_handle": author,
                           "target_len": len(detail["post"].get("text") or ""),
                           "rationale": "运营者 QQ 人工回复；线程转人工接管"}
                r = self.xc("POST", "/execute", payload)
                if r.get("status") == "pending_review":
                    tk.decide(iid, True, by="qq:运营者人工回复")
                    from inbox import InboxState
                    InboxState(self.account).register(sid, "human", iid)
                    r = self.xc("POST", "/execute", payload)
                self.reply(openid, f"{iid} 回复结果:{r.get('status') or json.dumps(r, ensure_ascii=False)[:120]}", msg_id)
            elif t.startswith("发帖 "):
                # 人工帖（#2 人工输入→发布器）：开票→运营者自动批→立即执行；质量门照过
                text = t[3:].strip()
                if not text:
                    self.reply(openid, "用法: 发帖 <正文>", msg_id)
                    return
                iid = f"manual-post-{int(time.time())}"
                payload = {"kind": "post", "intent_id": iid, "text": text,
                           "rationale": "运营者直发（QQ 人工帖）"}
                r = self.xc("POST", "/execute", payload)
                if r.get("status") == "pending_review":
                    tk.decide(iid, True, by="qq:运营者直发")
                    r = self.xc("POST", "/execute", payload)
                if r.get("status") == "done":
                    self.reply(openid, f"已发布:done {r.get('note', '')[:60]}", msg_id)
                elif r.get("status") == "unknown":
                    self.reply(openid, "执行结果不明（unknown），禁止重发，等待核验", msg_id)
                elif r.get("denied"):
                    d = r["denied"]
                    self.reply(openid, f"被拒:{d.get('code')} {d.get('detail', '')[:80]}", msg_id)
                else:
                    self.reply(openid, f"结果:{json.dumps(r, ensure_ascii=False)[:120]}", msg_id)
            elif t.startswith(("关注 ", "取关 ")):
                # 人工关注/取关（#10/#12）：同直发口径，开票自动批执行
                follow = t.startswith("关注 ")
                who = (t[3:] if follow else t[3:]).strip().split()[0] if t[3:].strip() else ""
                if not who:
                    self.reply(openid, "用法: 关注 <handle> / 取关 <handle>", msg_id)
                    return
                who = who.strip("<>").lstrip("@")   # 容错：<@handle>/@handle/<> 包裹皆归一
                iid = f"manual-{'fo' if follow else 'unfo'}-{int(time.time())}"
                payload = {"kind": "follow" if follow else "unfollow",
                           "intent_id": iid, "target_handle": who,
                           "rationale": f"运营者指令{'关注' if follow else '取关'}"}
                r = self.xc("POST", "/execute", payload)
                if r.get("status") == "pending_review":
                    tk.decide(iid, True, by="qq:运营者指令")
                    r = self.xc("POST", "/execute", payload)
                if r.get("status") in ("done",):
                    self.reply(openid, f"@{who} 已关注:{r['status']} "
                               f"{r.get('note', '')[:60]}", msg_id)
                    if follow:
                        from contacts import Contacts
                        Contacts(self.account).set_follow(who, True)
                elif r.get("status") == "unknown":
                    # 结果不明不落 we_follow（review 低11：未确认关注可能虚升 mutual）
                    self.reply(openid, f"@{who} 关注结果不明（unknown），请上 X 手动核实后"
                               f"再「互关 {who}」", msg_id)
                elif r.get("denied"):
                    d = r["denied"]
                    self.reply(openid, f"被拒:{d.get('code')} {d.get('detail', '')[:80]}", msg_id)
                else:
                    self.reply(openid, f"结果:{json.dumps(r, ensure_ascii=False)[:120]}", msg_id)
            elif t == "反馈":
                from feedback import summary_text
                self.reply(openid, summary_text(self.account, 7), msg_id)
            elif t == "效果":
                from effects import summary
                s = summary(self.account, 7)
                self.reply(openid, s.get("text") or json.dumps(s, ensure_ascii=False)[:1600], msg_id)
            elif t == "报":
                # P4-3：优先 LLM 日推报告（cognitive 落 state/daily-report-*.md），无则回落快照
                rp = sorted(STATE_DIR.glob("daily-report-*.md"))
                today = datetime.now().strftime("%Y-%m-%d")
                if rp and rp[-1].stem == f"daily-report-{today}":
                    body = rp[-1].read_text(encoding="utf-8")[:1800]
                    self.reply(openid, f"【今日运营报告】\n{body}", msg_id)
                    return
                p = sorted((STATE_DIR / "health-snapshots").glob("*.json"))
                if not p:
                    self.reply(openid, "尚无健康快照", msg_id)
                    return
                a = json.loads(p[-1].read_text(encoding="utf-8"))["accounts"][self.account]
                pr = a.get("profile", {})
                self.reply(openid, f"{p[-1].stem} 快照：{a.get('verdict')}\n"
                           f"粉丝:{pr.get('followers')} 帖:{pr.get('statuses')}\n"
                           f"24h自发:{a.get('statuses',{}).get('ownCount24h','?')}", msg_id)
            elif t == "停":
                armed = self._pause_armed.get(openid, 0)
                if time.time() - armed > 60:
                    self._pause_armed[openid] = time.time()   # 二次确认（MoA 审查：文档说确认、实现直接落）
                    self.reply(openid, "确认急停？60 秒内再发「确认停」生效。", msg_id)
                else:
                    (STATE_DIR / "PAUSE").write_text("qqbot", encoding="utf-8")
                    self.reply(openid, "已急停（PAUSE）。恢复须 SSH 删除 state/PAUSE（红线：不自动恢复）", msg_id)
            elif t == "确认停":
                if time.time() - self._pause_armed.get(openid, 0) <= 60:
                    (STATE_DIR / "PAUSE").write_text("qqbot", encoding="utf-8")
                    self.reply(openid, "已急停（PAUSE）。恢复须 SSH 删除 state/PAUSE（红线：不自动恢复）", msg_id)
                else:
                    self.reply(openid, "未在 60 秒确认窗口内。先发「停」再确认。", msg_id)
            elif t in ("直接发吧", "直接发", "发布吧", "发吧", "直接发布"):
                # 发布动词不交给自由对话模型编回执；必须回到确定性的票据审批指令
                self.reply(openid, "主脑不能直接批准或发布。确认具体票据请回复「发 <票号>」；仍未发布。", msg_id)
            else:
                # B 路对话主脑 v2（agent）：未命中固定指令 → 记忆+多轮工具推理
                shadow_id = None
                try:
                    from decision import new_id, qq_shadow
                    from brain import _active_ticket
                    shadow_id = new_id()
                    candidates = [r["id"] for r in tk.list_open()]
                    threading.Thread(target=qq_shadow,
                                     args=(t, self.account, _active_ticket(openid), candidates, shadow_id),
                                     daemon=True).start()
                except Exception as e:
                    log_event("qqbot", self.account, action="shadowError", note=str(e)[:100])
                try:
                    from brain import chat as brain_chat
                    reply_text = brain_chat(t, self.account, None, openid)
                    if shadow_id:
                        from decision import log_shadow_outcome
                        log_shadow_outcome(shadow_id, reply_text)
                    # 长回复分片（≤1400×3，被动额度 5 条/msg_id 内）防超长被 QQ 拒收
                    for i in range(0, min(len(reply_text), 4200), 1400):
                        self.reply(openid, reply_text[i:i + 1400], msg_id)
                except ImportError:
                    self.reply(openid, "未知指令，回「帮助」看指令集", msg_id)
                except Exception as e:
                    log_event("qqbot", self.account, action="brainFail",
                              note=str(e)[:120])
                    self.reply(openid, "主脑暂时不可用，回「帮助」看固定指令", msg_id)
        except TicketError as e:
            self.reply(openid, f"票据错误: {e}", msg_id)
        except Exception as e:
            self.reply(openid, f"内部错误: {type(e).__name__} {str(e)[:100]}", msg_id)
        log_event("qqbot", self.account, action="cmd", cmd=t[:20])

    # ── WS 主循环 ──
    def run(self):
        import urllib.error  # noqa: F401
        threading.Thread(target=self._notify_loop, daemon=True).start()
        while True:
            try:
                self._ws_once()
            except Exception as e:
                log_event("qqbot", self.account, action="wsDrop", note=str(e)[:120])
                time.sleep(10)

    def _ws_once(self):
        self.ensure_token()
        req = urllib.request.Request(f"{API}/gateway", headers=self._auth_headers())
        with urllib.request.urlopen(req, timeout=15) as r:
            url = json.loads(r.read())["url"]
        ws = websocket.create_connection(url, timeout=20)
        hello = json.loads(ws.recv())
        assert hello["op"] == 10, hello
        hb_int = hello["d"]["heartbeat_interval"]
        ws.send(json.dumps({"op": 2, "d": {
            "token": f"QQBot {self.token}", "intents": INTENTS_GROUP_C2C, "shard": [0, 1]}}))
        log_event("qqbot", self.account, action="wsIdentified")
        last_hb = 0
        while True:
            if time.time() - last_hb >= hb_int / 1000:
                ws.send(json.dumps({"op": 1, "d": self.seq or None}))
                last_hb = time.time()
            try:
                ws.settimeout(max(5, hb_int / 1000))
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue   # 非 JSON 帧噪声（当日 10 次 wsDrop 的根因）——丢弃不拆连接
            if msg.get("op") == 0:
                self.seq = msg.get("s") or self.seq
                t = msg.get("t")
                try:
                    if t == "C2C_MESSAGE_CREATE":
                        d = msg.get("d") or {}
                        a = d.get("author") or {}
                        # 字段名随版本漂移：author.user_openid / author.id / author.member_openid
                        oid = (a.get("user_openid") or a.get("id")
                               or a.get("member_openid") or d.get("openid"))
                        if not oid:
                            log_event("qqbot", self.account, action="evParseFail",
                                      note=json.dumps(d, ensure_ascii=False)[:160])
                            continue
                        content = (d.get("content") or "").strip()
                        log_event("qqbot", self.account, action="c2c",
                                  msg=content[:20] or "<空>", frm=oid[-6:])
                        thr = threading.Thread(
                            target=self.handle, args=(content, oid, d.get("id")),
                            daemon=True)
                        thr.start()
                    elif t is not None:
                        log_event("qqbot", self.account, action="event", kind=t)
                except Exception as e:  # 单条解析失败不拆连接（10-26 事故：KeyError 拆了 WS 丢消息）
                    log_event("qqbot", self.account, action="evParseFail",
                              note=f"{type(e).__name__}: {e}"[:120])
            elif msg.get("op") == 11:
                pass  # 心跳 ack


def timeout_for(path):
    return 300 if path.startswith("/execute") or path.startswith("/browse") else 30


if __name__ == "__main__":
    Bot().run()
