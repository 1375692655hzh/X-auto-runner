
---

# X 人设号自主运营 Agent：认知层 × x-auto-run 操作层 完整嫁接方案（v3.0）

## 0. 嫁接总原则（三句话）

1. **认知层不动**：round3 的人格/记忆/自主决策/渐进放权/夜整理/防漂移全部保留，项目方向仍是"一个中文 AI/科技人设号的自主运营体"，单账号、单人格。x-auto-run 的多账号矩阵、Owen 加权、互关社群是别人的业务目标，一律不引进。
2. **手换成 x-auto-run 的手**：XClient 第一实现是一个 Node 服务（下称 **x-hand**），完整采用 x-auto-run 的实操范式——比特浏览器固定指纹窗口 + 固定代理 + puppeteer-core 接管 + probe 式 DOM 探测 + state/ 文件账本 + schtasks/.bat/resident/看门狗 + 风控 toast 熔断 + unknown 绝不重发 + --dry 演练。**不引入 XActions，不引入 MCP Host，不引入 NSSM。**
3. **约束文化同源合并**：《规范与约束.md》的"约束代码强制"与认知层"环境物理法则 fail-closed"是同一条文化，嫁接后形成**双层执法**：core 的 GuardedXClient 闸门（认知层法则）+ x-hand 入口的规范闸门（操作层法则），agent 要穿过两道门才能碰到浏览器。

---

## 一、认知层 ↔ 操作层契约：动作原语 → 真实窗口/DOM 探测

### 1.1 原语映射表

认知层的窄工具面（kimi-k3 §2.3）到 x-hand 真实窗口操作的映射：

| Agent 工具 | x-hand 操作（x-auto-run 出处） |
|---|---|
| `x_read_timeline(surface)` | 开临时页 → `gotoFeed(home/search/profile)` → `humanScroll` 分段滚动 → `collectPosts` 抓 article 卡片（planner.js/executor.js `collectPosts`） |
| `x_read_post(tweet_id)` | `gotoFeed(postUrl, tries=2, expectSid)`（落地软重定向校验，executor.js 10-22 修复）→ 按 statusId `findArticleByStatus` → 读帖停顿 ∝ 原帖字数（`dwellF`，10-21 移植） |
| `x_get_mentions` | **不走工具调用**，由 resident 常驻页 5 分钟轮询通知页（resident.js 页1 范式），结果落 state/ 后作事件推给 core |
| `x_post(text, rationale)` | `goto(compose/post)` → 等输入框 → `humanType` 拟人输入 → `submitReply()` 成功气泡判定（executor.js `postTweet`） |
| `x_reply(tweet_id, text, tier)` | `gotoFeed(postUrl, expectSid)` → statusId 定位 article（**不取 arts[0]**）→ 带媒体帖不回 → humanClick(reply) → **probeReplyComposer 绑定闸** → humanType → submitReply 二次绑定核验 → 气泡判定 |
| `x_like(tweet_id)` | gotoFeed → findArticleByStatus → 先查 unlike（已赞幂等）→ humanClick(like) → 1200ms 后复查 unlike 翻转；**只允许赞本次唤醒内 hand 真实打开过的帖**（grok"打开才算看过"，打开记录由 hand 写 state） |
| `x_browse(surface, minutes)` | 临时页 + warmUp/humanScroll 变速滚动 + 偶发回看（boost.js `humanScroll` 15% 回滚）→ 不点赞，只把"看过的帖"写打开记录 |

**刻意不开放**（认知层原纪律保留）：follow/unfollow/DM/转推/删帖/改资料。x-auto-run 的 follow 族能力（followUser/unfollowUser/followBackIfFan）代码移植进 x-hand 但**默认不注册进端口**，未来若开放关注，走"提案→人审→票据"通道，不进 agent 自主工具面。

### 1.2 TypeScript 级接口签名

```typescript
// ══ x-hand 侧（Node，操作层唯一真相实现） ══

type XActionKind = 'read_timeline' | 'read_post' | 'post' | 'reply' | 'like' | 'browse';

/** 动作意图：core 的 GuardedXClient 过闸后投递给 hand 的唯一写入口 */
interface ActionIntent {
  intentId: string;            // 幂等键 = threadId + toolCallId + requestHash（host 生成，agent 不可自选）
  kind: XActionKind;
  text?: string;               // post/reply 正文（已过 core lint + hand quality 门）
  targetStatusId?: string;     // reply/like 目标；缺失或非法 = 硬性拒发（10-22 入口守卫）
  targetUrl?: string;
  targetHandle?: string;       // reply 绑定校验用（父帖作者）
  targetLen?: number;          // 原帖字数，读帖停顿系数
  surface?: 'home' | 'search' | 'profile' | 'conversation';
  query?: string;
  limit?: number;              // read 类抓取上限（默认 20，硬顶 30）
  minutes?: number;            // browse 时长（硬顶 14min）
  ticketId?: string;           // 票据制：人审获批动作的票据号（执行时二次验票）
  rationale: string;           // agent 理由，只进审计，护栏不采信
}

/** 执行结果：四态语义与 executor.js 完全对齐 */
interface ExecResult {
  intentId: string;
  status: 'done' | 'unknown' | 'failed' | 'denied';
  tweetId?: string;            // 回读确认后补登
  note?: string;               // 失败/unknown 原因（'composer无回复绑定:…' 等）
  toast?: string | null;       // 读到的风控 toast（有即熔断，见 §四）
  denied?: DenyReason;
}

/** 结构化拒绝（cursor 格式，agent 可据此调整而非原样重试） */
interface DenyReason {
  code: 'PAUSED' | 'ABORTED_TODAY' | 'CAP_EXHAUSTED' | 'MIN_GAP'
      | 'QUALITY_GATE' | 'UNBOUND_COMPOSER' | 'MEDIA_POST' | 'BAD_STATUS_ID'
      | 'UNCERTAIN_PENDING' | 'STOP';
  detail: string;
  retryAfter?: string;         // ISO 时间
  hint?: string;
}

/** DOM 探测返回结构（probe-reply-dom.js / probeReplyComposer 范式） */
interface ReplyComposerProbe {
  ok: boolean;
  mode?: 'dialog' | 'inline';  // dialog=回复弹窗（必含父帖 article 且作者匹配）；inline=目标帖 permalink 内联框
  note?: string;               // 'dialog无父帖卡片(独立发帖弹窗)' | 'dialog父帖作者非@x' | '无绑定composer(/home)'
}

interface PostCard {
  id: string; link: string; time: string; author: string;
  text: string;                // ≤280 字截断
  hasMedia: boolean;           // tweetPhoto/videoPlayer
  locked: boolean;             // 订阅解锁帖，一律不互动
}

interface MentionItem {
  href: string; author: string; txt: string;
  kind: 'reply' | 'mention' | 'other';  // 关键词正则 ∪ 结构判定（resident.js 页1）
  seenKey: string;             // href + author 去重键
}

/** 账本只读视图（安全真相，agent 只能查不能写） */
interface LedgerStatus {
  day: string;
  rolling24hUsed: Record<'post' | 'reply' | 'like', number>;
  rolling24hCap: Record<'post' | 'reply' | 'like', number>;
  reserved: Record<string, number>;        // 票据预留占位
  abortedToday: boolean;
  abortReason?: string;
  paused: boolean;                         // state/PAUSE
  uncertainPending: number;                // 未对账 unknown 数（>0 全局停写）
  sessionOpen: boolean;                    // 窗口是否在线
}

/** XClient 端口（core 调 hand 的全部方法；loopback HTTP + bearer） */
interface XClient {
  // 感知（读类也过闸门：PAUSE 拒、熔断后只读放行）
  readTimeline(surface: string, limit?: number): Promise<PostCard[]>;
  readPost(statusId: string): Promise<{ post: PostCard; replies: PostCard[] }>;
  // 行动（写类唯一入口，intent 先落盘再执行 = exactly-once 三件套之①）
  execute(intent: ActionIntent): Promise<ExecResult>;
  // 状态（安全真相只读）
  getLedgerStatus(): Promise<LedgerStatus>;
  getHealthSignal(): Promise<{ toast: string | null; loginOk: boolean }>;
  // 会话
  openSession(): Promise<void>;   // 开比特窗口 + verifyLogin（导航链接 → twid 反查双通道）
  closeSession(): Promise<void>;  // resident-guard 裁决后才真关
  // 管理
  dryRun(intent: ActionIntent): Promise<DenyReason | null>;  // --dry：过全部闸门不点击
}
```

### 1.3 调用时序（一次写动作全链路）

```
Agent 工具调用 x_reply
  → core GuardedXClient 闸门链（STOP→熔断→阶段→审批模式→禁区→预算→间隔→防骚扰→原子预留）
  → 需人审 → 开票返回 pending_review（见 §四.4）；自动放行 ↓
  → HTTP POST 127.0.0.1:8741/execute（bearer）
  → hand 入口规范闸门（PAUSE→abortedToday→capLeftRolling fail-closed→quality 门→帖龄/媒体/订阅帖）
  → intent 落盘 queue/intent-<id>.json（崩溃可辨"可能已发出"）
  → resident-guard 查窗口占用 → 临时页隔离执行（每动作独立页用完即关）
  → probeReplyComposer 绑定闸 → humanType → submit → 气泡判定
  → 关页前读走风控 toast（有 → latchToast 当日熔断 + 飞书）
  → ExecResult 回 core → core 写审计/画像计数/打开记录
  → unknown → 登记 pending-verify，30min 后 verify-unknown 匿名核验（见 §二.3）
```

---

## 二、state/ 记账：一本账服务两层

### 2.1 两套真相（grok 原则直接采纳为总原则）

| | 安全真相（safety truth） | 叙事真相（narrative truth） |
|---|---|---|
| 内容 | 去重集合、滚动 24h 计数、熔断标记、锁、PAUSE、intent/unknown、打开记录、票据预留 | episode、画像、案例、人设版本、日记、反思、策略版本 |
| 载体 | **state/ JSON 文件**（x-auto-run 格式） | **core 侧 SQLite**（或用户既有记忆系统经 MemoryPort） |
| 写者 | **只有 x-hand 和确定性工人**（executor 原语/verify-*/health/selfcheck） | Agent（受限工具）+ 夜整理 + 人类 |
| 读者 | core 闸门（经 `getLedgerStatus`）、hand 闸门、看门狗、面板 | Agent（记忆工具）、面板 |
| 铁律 | **Agent 没有任何工具能写 state/；agent 的"我记得发过/还有额度"一律不采信，只认 `getLedgerStatus` 返回值** | 画像把某人标"可放宽"，闸门不放宽——闸门只读 state/ |

### 2.2 state/ 文件清单（单账号版，沿用 x-auto-run 语义）

```
state/
  ledger-main.json          # 滚动24h 动作时间戳数组 + meta.abortedToday/abortReason + meta.reserved
  taskstate-main.json       # repliedAuthors / repliedPosts / replyHistory(环形100) / openedPosts(本轮打开记录)
  lock-main.json            # 账号级文件锁（pid + 30min 超时接管）
  resident-main.json        # 常驻心跳 heartbeatAt/pagesOkAt/pid + seenNotifs + pendingInbox
  inbox-main.json           # mention 轮询产出（resident 写，core 读）
  intents/                  # 每动作一个 intent 文件（exactly-once 之①）
  pending-verify-main.json  # unknown 待核队列（verify-unknown 消费）
  tickets/                  # 票据落盘副本（core 主档在 SQLite，hand 侧只读副本供执行验票）
  health-snapshots/YYYY-MM-DD.json   # 自检快照（unknown 语义：任一通道失败=unknown 不计 pass）
  log-YYYY-MM.jsonl         # 月度审计流（所有进程追加）
  PAUSE                     # 全局急停旗标（存在即全停；删除不自动恢复，须面板二次确认）
```

### 2.3 state 文件 vs SQLite 的取舍裁决

- **安全账本用 state/ 文件，不上 SQLite**。理由：单写者（hand）+ 写者少（五六个确定性进程）+ 每动作全量落盘（x-auto-run 验证过的 `state.persist()` 模式），JSON 透明可人读、损坏即 `.corrupt-*` 备份后 fail-closed 终止，这比 SQLite 更符合"账本可人工审计"的文化。并发风险由**账号级文件锁 + 单飞**消除，不需要 WAL。
- **core 侧 SQLite 保留但缩编**：只装叙事记忆兜底（st/mt/lt 三层）、tickets 主档、post_metrics、策略版本、审批记录。**checkpoints 表删除**（见 §四.5）。
- **两边事实分叉时的口径**：预算/去重/熔断/已发未发，一切以 state/ 为准；SQLite 里**不复制**这些字段（防止出现第二份安全真相）。core 要这些数据就调 `getLedgerStatus`。
- **unknown 对账协议（verify-unknown 的升级版，kimi/grok SOP 落在 x-auto-run 三件套上）**：
  1. intent 残留（崩溃）→ 复跑时一律转 unknown、补记额度、**禁重试**（executor.js 原样）；
  2. unknown → 30min 后 verify-unknown.js 走 FxTwitter 匿名核验：拉自己近 20 条，正文规范化哈希 + 目标帖比对 → 命中补 tweetId 翻 done，未命中翻回 planned 补跑（≤2 次封顶）；
  3. 仍不明 → **保持 unknown、占额度、全局停写等人**（认知层 uncertain 纪律），飞书告警；同主题第二条草稿在对账完成前不得执行；
  4. reply 类额外登记 pending-verify → verify-reply.js 双端点确认是否"误发独立帖"（10-22 事故机制），双端点确认才进人工待办，不自动处置。

---

## 三、调度统一：schtasks × resident × 心跳

### 3.1 裁决总表

| 调度件 | 承载什么 | 明确裁决 |
|---|---|---|
| **schtasks + .bat** | **只管进程生命周期和确定性任务，不做业务决策**：08:55 拉起 resident+core、看门狗、夜整理兜底、自检、数据回收、备份 | 运营窗**不等于**唤醒窗。x-auto-run 的"窗=业务节拍"不照搬；唤醒节律是认知层心跳（保留时机自主），但**活跃窗对齐规范 09:00-22:00**，窗外无行动心跳（grok：安静窗即围栏——没被叫醒就没有"它决定去发"的机会） |
| **resident（x-hand 常驻进程）** | **三件：①持有比特窗口（工位）②mention 5min 轮询页 ③心跳+工位自愈**（resident.js 主循环原样：探针过才写心跳、失败清心跳重开工位 60s 冷却、连败 15 轮自灭交看门狗） | resident **不承载决策**。它是"身体的脊髓"：轮询、锁、心跳、窗口。agent 的写动作经 hand 单飞锁借这个窗口执行（临时页隔离），与轮询页互不拆工位 |
| **core 常驻（Python）** | 认知层全部：心跳唤醒（TLN(median=35min, σ=0.55, 12, 90)×日类型）、AgentRunner、闸门、票据、记忆、夜整理 | 23:59 自灭、00:05 schtasks 接替（resident.js 同款作息）；01:00-07:30 深度休息，只留数据回收/备份/critical 告警 |
| **看门狗 .bat（schtasks 每 10min）** | 双健康判定：**core 心跳文件 >3min 未更新 → 拉起 core**；**resident 心跳+页面双新鲜（resident-guard.js 的 residentAlive 语义：心跳<20min 且 pid 活）不满足 → 拉起 resident .bat**；PAUSE/PAUSE-main 存在时静默不拉起（账号级人工接管期） | 直接复用 resident.js `--watchdog` 模式 + resident-guard.js，不学 NSSM |
| **mention 轮询** | 挂 resident 常驻页1：每 5min（jitter ±60s）拉 notifications/mentions，结构判定回复类 → 去重 → 写 state/inbox-main.json → HTTP poke core `inbox_changed` 事件 | core 把多个普通 mention **合并为一次唤醒刺激**（认知层原纪律：事件不是任务，不强制回复）；自家号/实质提问转 human-todo 的规则不采用——人设号没有"光速回复"业务，mention 一律进 agent 自主裁决 |
| **夜整理（每日 reflection）** | **主挂 core 内部调度器 23:20±15min**（在 09:00-22:00 活跃期外但 core 仍在岗）；**schtasks 23:40 兜底任务**：检查今日无 reflection 记录则补 poke | 夜整理内容不变（kimi-k3 §8：日记/案例提案/画像提案/明日倾向，只提案不动线上） |
| **确定性任务（不叫醒模型）** | schtasks 独立任务：fxtwitter 采样（T+2/8/24/48h）、health.js 日检、selfcheck（四维，07:40±20min）、04:00 备份、00:05 预算摇号（重启不重摇） | 与 x-auto-run 的 health.js / 定时任务同形态；结果落 state/，core 唤醒时读 |

### 3.2 心跳与固定窗口的共存规则

1. **一扇窗一双手**：bitbrowser 窗口全天由 resident 持有（09:00 开、22:30 关或 23:59 随自灭）；**永不换窗口、代理不变更**（指纹原则，比特浏览器实践已实现，替代认知层原"专用 Chrome profile"方案）。
2. **单飞锁串行**：core 的写动作 → hand 全局单飞队列（优先级 post>reply>like>browse，浏览可被抢占）；轮询页工作时写动作排队，写动作执行时轮询跳过本轮（同一 browser 实例，临时页隔离保证互不污染）。
3. **关窗裁决**：任何执行路径收尾都走 `closeWindowIfFree`——resident 活着就绝不关窗（10-21 僵尸事故教训）。
4. **唤醒与窗的冲突**：心跳落在活跃窗外 → 不唤醒；rest_for 可推迟普通心跳，不能抑制 critical（验证码/登录墙/风控 toast/uncertain）。

---

## 四、逐部件微调/替换清单

### 4.1 XActions 侧车 / MCP Host —— 全部删除

- 删：xar-sidecar（XActions MCP）、MCP Host 代理、XActions 白名单裁决 SOP、`list_tools()` 能力裁决、MCP 安全整节（bearer/读写分连接/drafts 工具硬拒绝）、`XACTIONS_MCP_REQUIRE_APPROVAL`。
- 换：x-hand（loopback HTTP，127.0.0.1:8741 + bearer，只绑本机）。cookie 安全模型简化：cookie 不出比特浏览器 profile，core/hand 进程均不持有——hand 只经 CDP 操作页面，verifyLogin 的 twid 反查在页面上下文内完成，**不落盘、不进日志、不进 LLM 上下文**（日志中间件打码保留）。
- XActions 留作 XClient 端口的**未来第二适配器**选项，不在第一实现路径上。

### 4.2 NSSM vs schtasks+看门狗 —— 换，全删 NSSM

裁决：**采用 schtasks/.bat/resident/看门狗，NSSM 不引入**。理由：①比特浏览器窗口是真实桌面窗口，必须跑在用户会话里，NSSM 服务的 Session-0 隔离与 DPAPI/窗口交互是已知坑（认知层原方案自己都要"以当前用户运行 NSSM"绕过，那 schtasks 用户会话任务更直接）；②x-auto-run 的看门狗双健康判定（心跳+页面）已被 10-21 僵尸事故验证过，比 NSSM 的进程级重启更懂"假活"；③整套 .bat 拉起/日志重定向/23:59 自灭 00:05 接替的形态现成。core（Python）同样由 .bat 拉起（`python -m core run >> logs\core-%DATE%.log 2>&1`），不进服务化。

### 4.3 SQLite 账本 vs state 文件 —— 分治（见 §二.3）

安全账本迁 state/ 文件；SQLite 缩编为叙事记忆+tickets+指标。认知层原"LocalLedger（state.db）"概念保留但**物理载体换成 state/**，GuardedXClient 改经 `getLedgerStatus` 读。

### 4.4 interrupt 审批 → 票据制（换）

- 删：工具内 `interrupt()`、`Command(resume)` 恢复路径、"interrupt 前零副作用"整套纪律（问题随结构消失）。
- 换（cursor/glm 三分离 + x-auto-run 票据/队列文化）：
  1. 需人审的写意图 → 创建票据 `tickets` 表（intent 全文 + rationale + lint 报告 + 预算快照 + expires_at：原创 6h/回复 2h）+ 预算**预留占位**（ledger.meta.reserved）→ 立即返回 agent `{status:'pending_review', ticketId}`，**agent 循环照常继续**；
  2. 飞书卡片/面板人审（通过/改后发/驳回；改后重过 lint）；
  3. 获批 → 票据落 state/tickets/ 副本 → **hand 在下一次单飞空档执行，执行前重过全部门闸**（STOP/熔断/预算确认/间隔/幂等键=ticketId，同票最多发一次）；
  4. 驳回/过期 → 释放预留，生成事件进下次唤醒简报；旧票据不可复活，agent 可自行决定重新提案；
  5. agent 有 `cancel_ticket`（热点凉了可撤回）。
- 防重发三保险（替代 interrupt 幂等纪律）：24h 相同正文哈希拒、简报含最近动作与待审票据、票据幂等键。

### 4.5 LangGraph checkpointer —— 不保留

裁决：**删除 SqliteSaver/checkpointer**。理由：票据制后循环内无挂起，没有"审批等几小时"的图状态要冻；每次唤醒是短会话（runaway 上限 16 工具/8 分钟），崩溃恢复靠**账本对账 + 交接笔记**（cursor：持久性靠账本不靠检查点）。保留 `create_agent` 循环内核与四层 system prompt/中间件栈（WakeBudget/UntrustedContent/Audit），ActionApprovalMiddleware 改写票据逻辑。会话延续性 = handoff 笔记 + journal + 记忆检索 + 简报带"上次会话异常中断/对账结果"。**这一条是对我自己 round3 的修正，承认 LangGraph 在票据制下是负资产（抽象惯性会往画图方向拉）。**

### 4.6 拟人输入层 —— 落地在 x-hand，替换 paste 为默认

- 位置：`hand/humanize.js`，替换 `typeReply` 的默认实现。
- 参数矩阵灌入（kimi §5.5 + gemini §9.1）：中文 1-4 字分块、块间 TLN(650ms, 0.45, 250ms, 2200ms)；标点 TLN(300ms,0.4,120,900)；英文每字符 TLN(110ms,0.35,45,320)；句末停顿 P=0.6 TLN(1.2s,0.5,0.5s,3.5s)；**打错退格 P=0.025**；发前复核停顿 TLN(2.5s,0.5,1s,8s)；总时长原创 ≤240s/回复 ≤90s。
- **STOP 检查点打进每个块之间**（STOP 出现 ≤3s 停止，未提交文本丢弃）；鼠标 humanClick 保留 x-auto-run 现实现（boundingBox hover 0.3-0.8s），叠加贝塞尔轨迹为 P1 增强项。
- 保留合成 paste 为 `--input-mode=paste` 兜底（拟人输入触发异常时的降级，降级写审计）。

### 4.7 四维自检在比特浏览器下的做法（换实现）

- ①搜索建议 ②搜索封禁：沿用 x-auto-run **health.js 纯 HTTP 匿名三通道**（fxtwitter `/2/profile`、`/2/profile/statuses`、`/2/typeahead` + 已知正常对照账号），unknown 语义原样（任一通道失败=unknown，不计 pass 不计 fail，连续两天升告警）。
- ③幽灵回复 ④回复降权：**需要一个不关注本号的外部视角**——在比特浏览器里建第二个固定 profile 作"观察窗"（不登录或登录一个与本号无关系的观察者号；同样固定窗口固定指纹，指纹原则同样适用）。观察窗脚本（selfcheck.js，schtasks 07:40±20min）：无登录打开本号近 24h ≤5 条回复所在 conversation，读到覆盖位置仍无自己 id 且单帖能打开 → fail；读不完记 untestable；降权维比较 latest 与默认排序折叠区，弱证据连续 3 天才升级。
- 结果落 `state/health-snapshots/`，core 读快照执行 GREEN/YELLOW/RED 梯度（认知层原响应梯度不变：YELLOW 预算×0.5 关 Bold、RED 48h 只读、恢复需连续 3 天 GREEN 且人工确认）。
- 无观察窗可用时 ③④记 untestable，**禁止用登录态自己看自己当 pass 证据**。

### 4.8 其余部件速裁

| 部件 | 裁决 |
|---|---|
| GuardedXClient 闸门链 | 保留（认知层），但执行终点从 XActor 改为 x-hand `/execute`；XActor 单飞锁删除，由 hand 单飞队列 + 账号文件锁 + resident-guard 三重替代 |
| owner_host 租约 | 保留（防两台机器同跑），实现改为 state/owner-host.json 心跳文件（与 resident 心跳同机制），不写 DB |
| STOP/PAUSE_WRITES | 实现改为 **state/PAUSE 旗标文件**（与 x-auto-run 同款：所有入口先查、删除不自动恢复、面板二次确认）；认知层语义不变 |
| quality.js 中文质量门 | **移植进 hand 作第二道兜底**（第一道在 core lint）；不合格一律拒发不放行不重试 |
| fxtwitter 回收 | 不变（core 确定性工人，纯 HTTP） |
| 登录维护 SOP（gemini） | 新增：`登录维护.bat`——停 resident/看门狗 → 前台比特窗口人工过验证码 → 存盘 → 重启；验证码/登录墙出现即 STOP，不自动打码不刷新重试 |
| --dry | 升为强制纪律：任何新动作类型/新闸门改动，先 `--dry` 全链路演练（过闸不点击不写状态），再过"3 天半量→第 4 天全量→第 7 天复盘"灰度（规范 §8 节奏替代 14 天 warm-up 的**上线变更管理**；注意：人设号的 warm-up 阶段表本身保留，两者管不同的事） |

---

## 五、认知层取材裁决

### 5.1 裁决：以 kimi-k3.md（我自己上轮）为底，组合四份的指定部件

**理由**：①本轮题设"认知层保持不变"，而 kimi-k3 的认知层是五份中边界最完整的（三平面权责、四层 prompt、窄工具面、anti-drift 五法、三态放权、夜整理、13 项风险清单），以它为底改动面最小、退化风险最小；②kimi-k3 的短板全部集中在操作层假设（XActions/MCP/NSSM/SQLite/interrupt），恰好是本轮被要求整体替换的部分——底的缺点不影响嫁接；③cursor 的票据制和 grok 的两套真相是对"底"的手术刀式修正而非替代，部件化吸收即可。

### 5.2 部件来源表

| 部件 | 取自 | 说明 |
|---|---|---|
| 整体骨架：三平面/四决策权/形态宣言 | **kimi-k3** | 一字不动 |
| 四层 system prompt + 窄工具面 + runaway 上限 | **kimi-k3** | 工具实现改指 x-hand |
| 记忆三层 + MemoryPort + anti-drift 五法 + PersonaGym 回归集 | **kimi-k3** | 不变 |
| 预算（4/15/30 摇号、原子预留）/warm-up 14 天表/防骚扰硬约束/回复三档 | **kimi-k3** | 数值不变，执法点分两层（core 闸门 + hand 规范闸门） |
| 两套真相原则 + uncertain 对账 SOP + "打开才算看过" + 置顶风险清单 | **grok-4.7** | 两套真相成 §二总原则；对账 SOP 与 executor 三件套合并为 §二.3；置顶风险八条抄进项目《规范与约束》作"已知陷阱"节 |
| 票据制审批 + ToolGateway 中间件链 + 结构化拒绝 {code,retry_after,hint} + 防死循环（同因 3 拒下架）+ 简报只陈述事实+熵源 + 改稿 diff 回流案例 | **cursor-opus-5.5** | 替换 interrupt；中间件链顺序映射进 GuardedXClient；改稿回流进夜整理案例库 |
| 态势报告代码装配（不给原始账本给结论）+ 注入防御四件套（指令层级/untrusted 标记/工具裁剪/注入检测降级）+ "框架越少越好修" | **glm-5.3** | 装配器落地 core 简报生成；注入防御并入 UntrustedContentMiddleware |
| 拟人参数矩阵（打字分块/退格/复核停顿/贝塞尔）+ 登录维护 SOP + 自检 unknown 纪律 | **gemini-3.8** | 灌进 hand/humanize.js；SOP 成 §4.8；unknown 纪律进 health/selfcheck |
| 手的全部实现范式（固定窗口/探针/账本/锁/熔断/resident/看门狗/--dry/quality 门/verify-*） | **x-auto-run 仓库** | 本方案 §一~§四的操作层主体 |

明确**不取**的：glm 的 APScheduler/SQLite 事件溯源、cursor 的 OpenAI Agents SDK/NSSM/FakeX（降级为 --dry）、gemini 的 LangGraph 图与情绪精力模型、grok 的 LangGraph 宿主与类别信任分（保留 kimi 三态机，信任分计算仅作就绪度参考）、五份全部的多账号与互关业务语义。

---

## 六、落地目录结构与里程碑（可指导实现版）

```
xar-agent/
  core/                        # Python 认知层（kimi-k3 原设计缩编）
    agent/  (create_agent + 四层prompt + 中间件：WakeBudget/UntrustedContent/Audit/TicketApproval)
    wake/   (心跳调度/刺激合并/rest_for)
    rail/   (GuardedXClient 闸门链/预算/warm-up/防骚扰/三态放权)
    memory/ (MemoryPort/SQLite 兜底/anti-drift)
    approval/ (票据服务/飞书卡片/面板回调)
    workers/ (fxtwitter 采样/预算摇号/备份)
    panel/  (FastAPI 127.0.0.1:8710)
  hand/                        # Node 操作层（x-auto-run 范式）
    server.js                  # loopback HTTP XClient + 单飞队列(post>reply>like>browse)
    primitives.js              # 浏览器原语（移植 executor.js：openWindow/verifyLogin/gotoFeed/
                               #   findArticleByStatus/humanClick/humanScroll/submitReply/readToast）
    probe.js                   # probeReplyComposer + collectPosts + 通知页抓取
    humanize.js                # 拟人输入层（分块打字/退格/复核/STOP 块间检查）
    quality.js                 # 中文质量门（移植，第二道兜底）
    resident.js                # 窗口常驻 + mention 轮询 + 心跳自愈（--watchdog 模式同文件）
    resident-guard.js          # closeWindowIfFree / residentAlive（原样）
    verify-unknown.js verify-reply.js health.js selfcheck.js
  state/  queue/  posts-inbox/ # 见 §二.2
  deploy/
    拉起-resident.bat  拉起-core.bat  看门狗.bat  夜整理兜底.bat
    健康日检.bat  四维自检.bat  数据回收.bat  备份.bat  登录维护.bat
  config.json                  # hand 侧规范参数（activeHours/rollingCaps/pacing/limits）
  规范与约束.md                # 本项目的唯一约束依据（改约束先改文档再改代码）
```

里程碑（承接 kimi-k3 M0-M4，操作层验收项替换）：
- **M0（W1-2）**：x-hand 骨架 + FakeX/真窗口 --dry + 闸门链。**验收：--dry 全链路；杀进程三次（intent 后/气泡前/对账前）X 上只有一条；STOP ≤3s 且打字中文本不提交；单飞 1000 次并发预算不超限。**
- **M1（W3-4）**：真窗口只读（read_timeline/read_post/browse）+ resident 轮询 + health/selfcheck 上线。**验收：连续 7 天只读；cookie 不出现在任何日志与上下文；看门狗能识别僵尸并拉起。**
- **M2（W5-6）**：票据制 + 飞书 + 写工具（MANUAL）+ unknown 对账。**验收：审批等待中杀进程不重发；unknown 全局停写；probe 绑定闸拦下 /home 主发帖框误发（10-22 回归用例）；48h 快照 ≥95%。**
- **M3（W7-8）**：夜整理 + 案例提案 + 周调参 + 30 条回归集。M4（W9-12）：HYBRID 渐进放权 + 抽审。**M4 完成不代表必须开 FULL_AUTO。**

## 七、风险点（嫁接新增，原 13 项保留）

1. **比特浏览器 API 依赖**：54345 不在线 = 本次跳过不补跑（规范原纪律）；开窗失败计入健康告警，不重试风暴。
2. **软重定向 /home**：所有 goto 必带 expectSid 落地校验；reply 必过 probeReplyComposer；statusId 非法硬拒（10-22 三件套）。
3. **CDP 通道 wedge**：每动作独立临时页用完即关；模态框自动 accept；风控 toast 在关页前读走。
4. **票据执行时世界已变**：执行前重过全闸（帖龄/预算/熔断/正文哈希），目标帖已删则跳过不计失败。
5. **双写者幽灵**：state/ 只许 hand 与确定性工人写；core 任何组件直接写 state/ 视为 P0 缺陷（code review 检查项）。
6. **观察窗指纹**：自检观察窗同样固定窗口固定代理，不用它做任何写动作。
7. **拟人输入回归**：humanize.js 上线先 --dry + 半量 3 天；异常即 `--input-mode=paste` 降级并告警。

---

## 八、与认知层原设计（kimi-k3）的差异清单

**换掉**：XActions MCP 侧车 + MCP Host → x-hand（x-auto-run 范式）；interrupt 工具内审批 → 票据制异步审批；NSSM 三服务 → schtasks/.bat/resident/看门狗；专用 Chrome profile + 有头/headless → 比特浏览器固定指纹窗口 + 固定代理；state.db 安全账本 → state/ 文件账本（SQLite 缩编为叙事+tickets+指标）；XActor 单飞 → hand 单飞队列 + 文件锁 + resident-guard；STOP/PAUSE 实现 → state/PAUSE 旗标文件。

**删掉**：LangGraph checkpointer/SqliteSaver（票据制下无挂起可冻）；MCP 安全整节；`x_approve_draft` 等 drafts 工具硬拒绝条款（随 XActions 消失）；"迁 VPS"章节主体（比特窗口+住宅代理强绑本机，长期留本机成为默认且几乎是唯一选项）。

**加入**：probeReplyComposer 绑定闸与 expectSid 落地校验；verify-unknown/verify-reply 对账双件；resident mention 轮询页；看门狗心跳+页面双健康判定；--dry 强制演练与"3 天半量→第 4 天全量→第 7 天复盘"变更灰度；登录维护 SOP；quality.js 第二道质量门；风控 toast 即熔断与同类连败三次当日熔断（与认知层熔断表合并）；临时页动作隔离；grok 置顶风险八条写入规范文档。

**保留不动**：三平面与四决策权、四层 system prompt、窄工具面与 runaway 上限、三层记忆与 MemoryPort、anti-drift 五法与回归集、预算/warm-up/防骚扰/回复三档全部数值、三态渐进放权与自动降级、夜整理与周调参提案制、四维自检的判定逻辑与 GREEN/YELLOW/RED 梯度、fxtwitter 回收、KPI 口径、13 项风险清单。

**一句话总结**：认知层还是原来那个"自己决定做不做、说什么、何时说、对谁说"的运营者；只是它的手从"Python 经 MCP 使唤 XActions"换成了"票据与闸门之后，由 x-hand 在那扇永不变更的比特窗口里，用探针确认绑定、用拟人节奏输入、用 state 文件逐动作记账、用 unknown 纪律绝不重发"——像人一样想，像 x-auto-run 一样动手。
