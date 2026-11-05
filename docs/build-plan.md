# xar-agent 施工计划 v1（2026-10-23，PoC 通过后定稿）

## 已锁定的事实（不再重议）

- 架构：一云一号 × (Camoufox 指纹浏览器 + 静态住宅代理 + agent 全家桶)，PoC 八项全过
- 双层：认知层（轻自主 + IM 指令面）× 操作层 x-hand（探针式真实窗口操作）
- 安全共识（五轮 MoA 全收敛）：state/ 安全账本代码独占写、unknown 绝不重发、票据制审批、
  --dry 强制、熔断梯度、打开记录（只动看过的）、预算是天花板不是 KPI
- **2026-10-23 修订：互关生态正式纳入**（早期起号刚需，用户确认）——推翻 round5"互关不引进"条款；
  单账号单 VPS 不变，互关作为账号运营内的业务层（需求基线见 docs/requirements-actions.md）
- 业务需求唯一权威：docs/requirements-actions.md（用户动作矩阵 14 类 + 辅助分类）
- 票据原文：docs/moa-round5/；子项目卡：docs/subprojects/；本计划按 SP 卡批次施工

## 语言裁决（PoC 后修正）

- **x-hand = Python**：Camoufox 官方 API 是 Python，PoC 验证的就是它；浏览器操作层与
  x-auto-run 的 executor.js 逻辑一一对应（page.evaluate 里的 DOM 探针 JS 原样移植）
- **core = Node**：llm.js 四通道 fallback 模式复用、IM bot 生态成熟
- 两层只经 loopback HTTP（127.0.0.1:8741 + bearer）相连，边界即契约（XClient 端口）
- 待 M0 期间顺手评估 camoufox-js：若官方级成熟可合并为单栈 Node，不强求

## 阶段计划（每阶段有硬验收，不过不进下一段）

### P0 · 黄金镜像底座（本周，~2 天）
- bootstrap.sh 全脚本化（PoC 手工步骤固化）：apt 包、sysctl(userns)、venv、camoufox fetch、
  fontconfig、swap 核查
- 凭据抽离：/etc/xar/instance.yaml（600 权限）——代理/bot token/LLM key/人设卡路径；缺项拒启动
- 仓库立骨架：xar-agent/{core,hand,deploy,state,config}
- 《规范与约束.md》从第一天建（改约束先改文档再改代码，继承 x-auto-run 文化）
- systemd 单元骨架：xar-resident.service / xar-core.service / xar-watchdog.timer
- **验收**：全新实例跑 bootstrap.sh 一遍到底零人工；PoC 三脚本在标准化布局下复跑通过

### P1 · x-hand 操作层 = SP0（1-2 周）✅ 2026-10-24 完成并验收
- hand/primitives.py：移植 x-auto-run executor.js 探针原语（gotoFeed/expectSid、findArticleByStatus、
  humanClick、humanScroll、submitReply 气泡判定、readToast、probeReplyComposer 绑定闸、collectPosts+has_media）
- hand/resident.py：常驻 Camoufox 持久 profile + 心跳/页面双健康 + 自愈（60s 冷却/连败 15 轮自灭）+ cookie 注入兜底
- hand/ledger.py：state/ 账本全家（滚动24h/票据预留/账号锁/intents 崩溃对账/PAUSE/原子写/.corrupt 备份）
- hand/server.py：loopback HTTP 127.0.0.1:8741 + bearer + 优先级单飞队列 + SIMULATE/--dry 测试钩子；
  真实写动作网关层硬拒（WRITES_DISABLED，P3 票据就绪后放开——红线 §二）
- **验收实测（tests/p1_acceptance.py）**：A 1000 并发单飞 done=15==cap、0 INTERNAL、账本恰满 ✅
  B 幂等重放拒 ✅ C --dry 全闸零副作用 ✅ D PAUSE 拒发 ✅
  E 杀进程三次：崩溃→对账→unknown 占额→重放拒，账本每轮恰+1 ✅
  另：实机杀浏览器 95 秒自愈 + cookie 兜底实战 ✅（过程修三真 bug：backlog/额度TOCTOU/闸门顺序）

### P2 · 只读上线 + 安全护栏 = SP8 前半（1 周）
- 真窗口只读运行：readTimeline/readPost/browse 走真实 feed
- health.py：fxtwitter 匿名三通道自检（搜建议/搜封禁/幽灵回复前两维）
- 熔断：toast 即当日熔断 latch + 告警出口
- **验收**：连续 7 天只读零事故；cookie 不出现在任何日志；看门狗识别僵尸并拉起

### P3 · 票据审批 + 写动作 = SP6（1-2 周）
- core(Node)：IM = QQ 官方 bot（2026-10-26 用户拍板"agent 绑机器人"，NapCat 小号否决）——
  hand/qq_bot.py WS 桥（token 刷新 + api.sgroup.qq.com 长连接 + 被动回复），systemd xar-qqbot
- 票据全流程：开票(预算预留)→IM 人审(通过/改后发/驳回)→执行前重过全闸→幂等键=ticketId
- 写原语上线：post/reply/like（like 仅限打开记录内的帖）
- 对账双件：verify-unknown(fxtwitter 匿名核验，xar-verify.timer 30min 周期 2026-10-26 挂载) /
  verify-reply(双端点防误发独立帖)
- 质量门：hand/quality.py（quality.js 移植，14 用例；不合格一律拒发 QUALITY_REJECT，
  发送用清洗版；casual 口语豁免；SIMULATE/dry 不拦）
- **验收**：审批等待中杀进程不重发；probe 绑定闸拦下 /home 主发帖框误发（10-22 回归用例）；
  unknown 全局停写等人；票据过期预留全额释放
- **进度（2026-10-26）**：QQ「状态」指令全链路验收通过（事件解析崩溃已修：openid 字段
  三口径兼容 + 单条解析失败不拆连接）；P1 回归五组全绿；**真机首发演练 done（11:38，
  x.com/WCixot/status/2114562201893978328，QQ 票审放行 23s 完成）**。演练连修四缺陷：
  合成粘贴被 compose 忽略→键盘兜底+内容逐字校验；Deny 网关级拒发补 failed 收尾；
  票据终局释放预留+executing 僵票纳入清扫（单测 7/7）；冷启动探测盲区改耐心等待。
  P3 ✅ 收官。已知残留：键盘首字符甩尾已修（待下帖验证）；发布稿首字错位可人工删或留档

### P4 · 认知层轻自主 = SP1/SP2/SP3/SP4（2-3 周）
- 心跳唤醒：TLN(median=35min) × 日类型，活跃窗外不醒；所有动作仍走票据+预算
- 人设卡 + 四层 prompt + 双质量门（core lint + hand quality）
- **场景矩阵**（需求表 #1-#9）：原创/人工/互关贴发布 × 原创帖回复/互关帖回复/串门回复/
  互关串门评论/热门评论/高质关注评论——每场景独立提示词（普通/高级），回复三档并入
- **媒体检测闸**：collectPosts 的 hasMedia 原语策略化——默认"带媒体帖不评论"（可配）
- **多模态理解**：截图→多模态模型，供原创帖信息库/运营报告/未来放开评论时用
- humanize 拟人输入矩阵（分块打字/退格/复核停顿/STOP 块间检查，paste 降级兜底）
- 画像卡 + SQLite 叙事账本（三层记忆；绝不复制 state/ 安全字段）
- **动作质量即时评估**（能力反馈 F1/F2）：成帖/回复后即时评估进案例库
- **验收**：PersonaGym 30 场景回归集 ≥95%；盲评抽审；事件间隔分布 KS 检验

### P4b · 关系业务层 = 互关生态（1-2 周，与 P4 交错）
- 互关贴发布流水线：云端互关帖子池【接口待补】→ 互关成帖提示词 → 票据 → 发布 → F1 反馈
- 互关关注条件触发：对方关注我/有回复互动 → AI 判定 → 关注（早期 MANUAL 票据，成熟后免审档）
- 串门评论 / 互关帖下评论回复（走场景矩阵）
- 人工建议关注/取关：QQ 指令 → 主页打开 → 关注/取关（关注族原语 x-auto-run 有现成实现移植）
- 互关健康度：互关比例监测入运营报告（需求表 #13）
- **验收**：条件触发准确率抽检；关注动作 100% 过票据/审计；互关比例指标进日报

### P4c · 资讯源自动发布与分阶段自治（2026-11-02 起）
- **已上线**：固定信息库=数据站资讯+精品文章；06:30-24:00（与工位同窗）小时级增量评分，合格时1-5条、无增量可为0；题材含美股科技/美联储与重大宏观/重大政策/中国科技公司/中国模型公司；蓝V动态长度门；详细审核信息卡。当前为 review（人审执行），旧每日一票已停用。
- **自治化设计参考（以后再看，不作运行依赖）**：[deepseek-ai/dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype)（研究原型，2026-11-03 静态审读）。只借鉴授权、事件处置与恢复设计，不迁移 DSH/TypeScript 框架。
- **按当前需求裁决——现在纳入（轻量实现，2026-11-03 已落地并通过受控开票/幂等复跑）**：
  1. Source 读取位置：核实并使用数据站 cursor，重启/故障不漏稿、不重复评估。
  2. Trigger/Decision 处置记录：每条素材的评分、淘汰理由、选题、成稿、票据、执行结局可追查；沿用现有 JSONL/状态文件，不另起完整领域框架。
  3. Story 线程：同一事件的首次报道/官方确认/数字更新/市场反应串成增量历史，取代单纯 event 文本记忆。
  4. 轻量 Policy/Mandate：票据记录任务范围、规则与 prompt 版本、observe/review/auto 模式；初始不开启 auto。2026-11-03 运营者在 Anthropic v2 真发验收后曾显式开启资讯源专用 auto；同日因原生阅读质量复核降回 review，并启用 QQ 主动审稿通知与自然语言改稿，其他动作仍人审。
- **后续仍纳入**：通知类持久化 Outbox；运营台 attention-first 面板（归入 P5）。现有票据/intent/verify-unknown 已覆盖 X 写动作幂等，不重复造执行 Outbox。
- **不纳入**：每帖独立 worker/session/目录、GitHub PR/CI 模型、完整订阅路由、额外 websearch。资讯短任务无需这些复杂度。
- **验收**：同一素材只评估一次；重启后续读不漏不重；同一事件无增量不发、有增量可跟进；任一票据能还原当时规则/评分/原文/正文；auto 开关在用户未显式修改时不可达。

### P5 · 闭环 + 镜像量产（1-2 周）
- fxtwitter 采样工人(T+2/8/24/48h) + 日报/周报 + 夜整理提案
- **互动关系网**：X Circle 参考评分（回复3/引用2.5/提及1.5/转发1，30 天半衰，双向 √(a²+b²)）
  基于 agent 自有账本长期留档——关系账本本身是资产，环形图仅是运营台展示形态
- **运营台 v1（看板）**：账号数据/四维健康/互关健康/内容分析/互动分析/关系网/待审/预算/熔断
- **运营台 v2（控制台）**：十域设置面板 + 提示词 CMS（版本/回滚/回归预检）+ 模型用量监控 +
  执行流程视图（队列/票据/唤醒时间线；不做拖拽编排器）——需求基线见 docs/console-settings.md
  （权限分级：随时调/只能紧/提案制/只读/红线；含设置审计日志与生效语义标注）
- 自定义镜像制作（清 profile/state/密钥→关机→镜像）；第二台号复制演练（四配置灌入）
- **验收**：新实例从镜像到可 --dry ≤30 分钟；备份恢复演练 ≤30 分钟；关系网可出任意日期快照；
  v2 全部设置项改后 --dry 演练通过且审计留痕；红线六条在设置接口层不可达

## 节奏与红线
- 写动作永远不早于护栏：P3 之前物理上不存在写端点
- 每个新动作类型/新闸门改动：--dry → 3 天半量 → 第 4 天全量 → 第 7 天复盘
- 2C2G 现机跑 P0-P2 足够；P3 写动作上线前升 4G（浏览器长跑内存增长）
- 每阶段收尾：更新《规范与约束》+ state/ 结构快照 + 一次断电演练

## 用户侧需要提供的（按阶段）
- P2-P3 之间：一个 QQ 小号（agent 的对话身份），首次登录用户扫码一次；大号加好友即用
- P3 前：正式号的代理 + cookie（测试号继续用到 P2）
- P4：人设卡初稿（我是谁/绝不说什么/增长面）——我可以先出草稿你改

## 距离
P0+P1 约 2-3 周后：第一台机器具备「真实窗口读 X + 账本 + --dry 演练」能力；
P3 后：你的 IM 里收到第一张「这条要发了，回复发/改」的票据卡片。
