# hand/ — 操作层（Python + Camoufox）

P1 填充。内容（对应 docs/build-plan.md P1 / SP0）：

- `primitives.py`：x-auto-run executor.js 探针原语移植（gotoFeed/expectSid、findArticleByStatus、
  humanClick、humanScroll、submitReply 气泡判定、readToast、probeReplyComposer、collectPosts 含 hasMedia）
- `resident.py`：常驻 Camoufox + profile 持久化 + 心跳文件 + 自愈（systemd xar-resident.service 入口）
- `server.py`：loopback HTTP (127.0.0.1:8741 + bearer)——timeline/post/profile/notifications 只读、
  execute/dry/profile/update；post 支持多图与引用，单飞队列 post>reply/comment>like/follow>browse
- `ledger.py`：state/ 账本（/opt/xar/state/，格式沿用 x-auto-run）
- `tests/poc_baseline.py` 等：PoC 三脚本迁入为回归用例

约束：写端点在 P3 前不存在（规范 §二）；cookie 不落日志（规范 §三）。
