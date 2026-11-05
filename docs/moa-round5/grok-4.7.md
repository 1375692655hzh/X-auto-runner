## 总体架构图景

一个固定比特浏览器窗口上，只跑一个运营者。它看代码拼好的世界，自己决定浏览、发帖、回复、点赞、书签、复制链接、关注、取关、记笔记或整天什么都不做。合法结果包括 noop。预算全是上限。人设卡、画像、案例、提案审批、类别信任分、夜整理、Analyst 周提案都留在认知层。图上不出现选题、生成、仿写、排期、发布这些业务节点。

三平面：

- 心智面。一次唤醒里的观察、判断、ActionPlan、反思、记忆提案。模型只经过门面工具。
- 身体与物理法则面。`XClient` 真实窗口、`probe-reply-dom.js`、高斯间隔、会话时长、去重、计数、熔断、身份校验。模型改不了这些数。
- 人类主权面。审批、放权、人设指针、改《规范与约束.md》、解除 STOP。飞书和本机面板不在模型工具表里。

```text
schtasks / .bat                resident.js                 确定性工人
早午晚窗、九槽位、±25min  →    窗内心跳、会话≤20min    →   采样 / 自检 / 备份
        │ 事件：无 task 字段           │                         │ 不叫模型，除非告警
        ▼                              ▼                         ▼
                         事件收件箱（按窗口）
                                │
                                ▼
                    态势装配（只写事实，无祈使句）
                                │
                                ▼
              认知环（薄循环，模型无关，一个窗口一条线程）
              读工具 ── 记忆工具 ── 交出 ActionPlan（可为 0 条）
                                │
                                ▼
        GuardedXClient 门链：只返回 allow / deny / needs_approval
                                │
                 allow          │          needs_approval
                    ▼           │                 ▼
            执行器调用 XClient   │          票据落 state/，进程不等待
            比特浏览器固定窗      │                 │
            拟人输入在这一层      │          人在面板/飞书决定
                    │           │                 │
                    ▼           ▼                 ▼
              回执 + 幂等终态          拒绝原因回到下一轮简报
                    │
                    ▼
     state/ 安全账本（代码写）     memory/ 叙事记忆（模型按分区写）
```

运行时第一实现是现有 JavaScript。认知环是进程内薄循环，通过 OpenAI 兼容端点调模型。配置里模型名为空时拒绝进入运营，可以进 `--dry`。不猜「科技博主」口吻。人设正文为空时锁在观察模式，写动作被拒绝。

Cookie 留在比特浏览器窗口里。运营进程不读 cookie，不把 cookie 写入日志、简报、飞书卡片。每次开窗先核对登录态和 profile handle。不匹配就不点击。窗口、指纹、代理在会话中途不换、不共享。自检用窗口外的无登录视角。没有外部视角就记 `unknown`。`unknown` 不是通过。

`--dry`：模型仍可跑完一轮并交出计划，门链仍判定，`XClient` 不点击，任何 `state/` 与 `memory/` 都不写。

### 反退化条款（写入评审清单）

1. 心跳与槽位事件的结构只有 `event` 和 `facts`。出现 `task`、`must_post`、`quota_to_fill` 字段，单测失败。
2. 态势简报只陈述事实。生成器命中「请」「应该」「现在去」「务必完成」即单测失败。
3. 门链只返回 `allow`、`deny`、`needs_approval`。禁止改写正文、禁止替模型重排候选、禁止自动降档后直接发出。档位不符就拒绝，由模型自己改。
4. 模型工具表里没有点击、输入、选择器、`probe` 脚本、审批通过、改预算、改信任分、解除熔断。写只出现在 ActionPlan 里，由执行器调用 `XClient`。
5. 技能文件写判断依据。写成步骤 1、2、3 的技能不得加载。
6. 主循环之外的小模型只做三件事：文本硬规则打分、浏览摘要、语气复核。不得规划下一动作。
7. 精力、情绪、亲密度不进入门链。它们只是模型自己的笔记。门链不读。
8. 无操作会话占比的观察区间是 5% 到 60%。连续 7 日动作序列熵下降超过 30%，或同一模板序列超过一半，只告警，不自动改成「补发」。
9. 数字约束的唯一修改口是《规范与约束.md》。先改文档，再改门链常量。配置只能调低，不能调高。

## ① 认知层与操作层契约

模型看见的是运营原语。`XClient` 看见的是固定窗口上的 DOM。两者中间是门链。模型不持有选择器，也不调用 `probe-reply-dom.js`。

### 模型侧原语

读（当轮可多次，计入工具上限）：`timeline_read`、`tweet_read`、`notifications_read`、`profile_read`、`browse`。

写（只进计划，不直接点击）：`post`、`reply`、`like`、`bookmark`、`copy_link`、`follow`、`unfollow`。

认知内部：`remember`、`noop`、`request_wake`。

第一实现不开放：转发、引用、私信、改资料、删帖、批量关注。这些要先写入规范文档，再在 `XClient` 增加方法。

`copy_link` 是手上的分享手势，有自己的日计数。正文里的「复制链接 / 关注我」诱饵仍由 lint 直接 `deny`。两件事不是同一个字段。

### 原语到窗口的映射

开窗时 `verifyIdentity` 先行。读和写都挂在已经核对过的 `WindowSession` 上。

| 原语 | 窗口行为 | DOM / 探测 | 账本副作用 |
|---|---|---|---|
| `browse` | 打开时间线或社群，变速滚动 | 只采集视口内帖卡 id | 写本轮打开记录；不点赞 |
| `timeline_read` | 同上，停在一屏 | `probe` 帖卡：id、作者、摘要、时间 | 视口内 id 进入打开记录 |
| `tweet_read` | 打开该帖 | `probe-reply-dom.js` 定位正文与回复框 | 该 `tweet_id` 记入打开记录 |
| `notifications_read` | 打开通知 | 解析提及，正文标 `untrusted` | 不因「有提及」而产生回复 |
| `profile_read` | 打开对方主页 | 读 handle、是否已关注、是否对方已关注 | 该 handle 记入本轮已开主页 |
| `like` / `bookmark` / `copy_link` | 只在已打开的帖上点对应控件 | 控件来自本轮探测结果 | 成功后记回执；失败按类计数 |
| `reply` | 已打开帖的回复框 | 探测失败则不输入 | 分块键入，提交后再对账 |
| `post` | 本号作曲框 | 同一套键入 | 提交后对账，成功后夹最小间隔 |
| `follow` / `unfollow` | 只在本轮打开过的主页上点 | 回关还是主动，由页面关系决定，不采信模型自述 | 分项计数 |

会话内第一次写之前，`XClient` 自己做 30–90 秒变速滚动热身。模型不睡眠来表演热身。

键入在真实窗口里完成，不向输入框注入 DOM 值。参数灌进输入层：中文 1–4 字一块，块间截断对数正态（中位 650ms，下限 250ms，上限 2200ms）；约 2.5% 的块先打错再退格；提交前再停一截（中位约 2.5 秒）。每个字块之间读 STOP。STOP 后 3 秒内停止，未提交文本丢弃。中观间隔用手上已在跑的量级：帖内约 5 秒±2 秒，帖间约 25 秒±10 秒，另加 40–120 秒休息。会话墙钟 ≤20 分钟。

同一会话里，同一工具因同一原因被拒 3 次，该工具从本会话拿下。这是事实，写进下一轮简报，不生成补做任务。

### 接口

```typescript
type AccountId = string;      // 与窗口绑定的 handle，小写
type WindowId = string;       // 比特浏览器固定窗口 id，配置写死
type TweetId = string;
type ActionKind =
  | "post" | "reply" | "like" | "bookmark" | "copy_link"
  | "follow" | "unfollow" | "browse" | "remember" | "noop" | "request_wake";

interface WindowSession {
  account: AccountId;
  window_id: WindowId;
  session_id: string;         // 一次唤醒一个
  opened_at: string;          // ISO +08:00
  dry: boolean;
  identity: IdentityCheck;
}

interface IdentityCheck {
  ok: boolean;
  expected_handle: AccountId;
  observed_handle: AccountId | null;
  logged_in: boolean;
  window_id: WindowId;
  proxy_fingerprint: string;  // 只比对是否与开窗时一致，不记录代理明文
  reason?: "logged_out" | "handle_mismatch" | "window_mismatch" | "proxy_changed";
}

interface XClient {
  openWindow(account: AccountId, opts: { dry: boolean }): Promise<WindowSession>;
  closeWindow(s: WindowSession): Promise<void>;
  verifyIdentity(s: WindowSession): Promise<IdentityCheck>;

  browse(s: WindowSession, req: BrowseReq): Promise<BrowseReceipt>;
  readTimeline(s: WindowSession, req: ReadSurfaceReq): Promise<TimelinePage>;
  readTweet(s: WindowSession, req: { tweet_id: TweetId }): Promise<TweetObservation>;
  readNotifications(s: WindowSession): Promise<InboxPage>;
  readProfile(s: WindowSession, req: { handle: string }): Promise<ProfileObservation>;

  like(s: WindowSession, req: TargetReq): Promise<ActionReceipt>;
  bookmark(s: WindowSession, req: TargetReq): Promise<ActionReceipt>;
  copyLink(s: WindowSession, req: TargetReq): Promise<ActionReceipt>;
  reply(s: WindowSession, req: ReplyReq): Promise<ActionReceipt>;
  post(s: WindowSession, req: PostReq): Promise<ActionReceipt>;
  follow(s: WindowSession, req: FollowReq): Promise<ActionReceipt>;
  unfollow(s: WindowSession, req: { handle: string; idem_key: string }): Promise<ActionReceipt>;
}

interface ReadSurfaceReq {
  surface: "home" | "following" | "community" | "search";
  community_id?: string;
  query?: string;
  warmup: boolean;            // 本会话第一次写前为 true，由执行器设置
}

interface TimelinePage {
  items: TweetCard[];         // 仅当前视口，5–8 条
  open_ids: TweetId[];        // 本调用新写入打开记录的 id
}

interface TweetCard {
  tweet_id: TweetId;
  author_handle: string;
  text_excerpt: string;       // ≤280 字，包裹为不可信数据
  age_min: number | null;
  surface: ReadSurfaceReq["surface"];
  community_id?: string;
}

interface TweetObservation extends TweetCard {
  reply_box_found: boolean;
  permalink: string;
}

interface TargetReq { tweet_id: TweetId; idem_key: string }
interface ReplyReq { tweet_id: TweetId; text: string; text_hash: string; idem_key: string }
interface PostReq  { text: string; text_hash: string; idem_key: string }
interface FollowReq {
  handle: string;
  idem_key: string;
  // 执行器不读模型声明的 follow_kind；由 readProfile 的页面事实写入回执
}
interface BrowseReq { surface: ReadSurfaceReq["surface"]; minutes: number; community_id?: string }
interface BrowseReceipt { open_ids: TweetId[]; dwell_ms: number; scrolled: boolean }

type ReceiptStatus =
  | "dry_skipped" | "posted" | "already_posted"
  | "failed" | "uncertain" | "deferred";

interface ActionReceipt {
  idem_key: string;
  account: AccountId;
  kind: ActionKind;
  status: ReceiptStatus;
  tweet_id?: TweetId;         // 发出内容的 id，或互动目标 id
  target_handle?: string;
  follow_kind?: "proactive" | "followback"; // 仅 follow，来自页面
  observed_hash?: string;
  error_code?: string;
  at: string;
}
```

打开记录由 `read*` / `browse` 的实现追加。模型口头说看过，不写入。`like`、`bookmark`、`copy_link`、`reply` 的目标不在本轮打开记录里，门链返回 `NOT_OPENED`。`follow` / `unfollow` 的 handle 不在本轮已开主页里，同样拒绝。

### ActionPlan

```typescript
interface ActionPlan {
  plan_id: string;            // 宿主生成的 ULID，模型不自选
  account: AccountId;
  session_id: string;
  situation_ref: string;      // 本轮简报哈希
  intent: string;             // 给人看的一句话
  actions: PlannedAction[];   // 0 条即 noop
  memory_writes: MemoryProposal[];
  next_wake_hint_minutes?: number;
}

interface PlannedAction {
  action_index: number;
  kind: ActionKind;
  target_id?: TweetId;
  target_handle?: string;
  surface?: "home" | "following" | "community" | "search" | "profile";
  text?: string;
  tone_self?: "safe" | "interesting" | "bold"; // 仅供复核，门链可改判为拒绝
  why?: string;               // 仅供人看，门链不采信
  risk_self?: string;         // 同上，不能给自己批信用
}

interface PlanVerdict {
  plan_id: string;
  items: Array<{
    action_index: number;
    result: "allow" | "deny" | "needs_approval";
    code: string;             // BUDGET、MIN_GAP、TOO_NEW、NOT_OPENED、BREAKER…
    retry_after?: string;
    ticket_id?: string;
  }>;
}
```

幂等键由宿主生成：`idem_key = account + ":" + plan_id + ":" + action_index + ":" + request_hash`。模型不能指定键。`request_hash` 覆盖种类、目标、正文规范化哈希。

单次唤醒上限，与日预算取更严者：工具调用 16、X 读 8、写意图 2、原创 1、记忆写入 3、墙钟 20 分钟。到点强制收尾。已批准票据的执行不占用新的审议额度。

### 门链顺序

对计划里的每一条按这个顺序，失败即停在该条：

1. `--dry` 则判定仍跑，执行改为 `dry_skipped`。
2. `STOP` 拒绝读写。`PAUSE_WRITES` 拒绝写、允许读。身份校验失败视同 `STOP`。
3. 阶段能力与该类信任分天花板。天花板为 0 则 `needs_approval`，不是把原语删掉。
4. 熔断：风控 toast 当日停该号；同类连续 3 次失败当日停该类；存在 `uncertain` 停该号后续写；`state/` 损坏则备份后该号全停。
5. 正文 lint：红线、机械文风、近 30 日 bigram > 0.55、诱饵语。只返回命中片段，不改写。
6. 语气复核与自报档不符则 `deny`，码为 `TONE_MISMATCH`。
7. 目标：帖龄 &lt; 45 分钟拒绝互动；陌生人帖龄 &gt; 6 小时、互关 &gt; 24 小时、任何帖 &gt; 48 小时，拒绝回复。同一帖不重复。同一作者 24 小时出向 ≤1、7 天 ≤3。社群表面另加同一作者 14 天一回。打开记录不足则 `NOT_OPENED`。
8. 间隔：互动用手上的高斯间隔；`post` 另加最小间隔 150 分钟。间隔未到返回 `retry_after`，不在门链里睡到点再发，除非剩余差距小于 3 分钟且仍在本会话墙钟内。
9. 原子预留日计数。预留失败码为 `BUDGET_EXHAUSTED`。
10. 该窗口单飞锁。
11. 写 `pending` 回执，然后才允许点击。
12. 对账，把终态写回安全账本。

`deny` 不进入审批。整份计划都被拒绝，本轮进入反思，不自动换一条任务。

### 执行结果与打开记录

```typescript
interface OpenRecord {
  account: AccountId;
  session_id: string;
  tweet_ids: TweetId[];
  profile_handles: string[];
  written_by: "xclient";     // 固定字面量，拒绝其他写入者
}

interface IdemRecord {
  idem_key: string;
  account: AccountId;
  kind: ActionKind;
  target_id?: TweetId;
  text_hash?: string;
  status: "pending" | "submitted" | "posted" | "already_posted" | "failed" | "uncertain" | "dry_skipped";
  reserved: boolean;
  tweet_id?: TweetId;
  attempts: number;          // 同一键最多 1 次重试
  updated_at: string;
}
```

`uncertain` 的对账是现有 verify-unknown 的升级，仍在同一窗口、同一指纹上做：

1. 先读本地幂等行和 `ledger`。已有 `posted` 就返回 `already_posted`。
2. 再在本窗口打开自己的近文或该对话，用 `probe` 对近 20 条正文哈希。
3. 对上则补 `tweet_id`，终态 `already_posted`，不发第二条。
4. 没有点击证据且对不上，才允许同一 `idem_key` 再试一次，间隔 3–8 分钟，且仍在执行窗和会话墙钟内。
5. 仍不清楚，或当时已经点过提交：保持 `uncertain`，该号停写，等人。不换一套浏览器，不另起一篇同主题草稿。

崩溃合同沿用三次演练：审批已挂起时杀进程；人已通过但窗口尚未返回时杀进程；回执写入前杀进程。验收都是线上至多一条，账本终态是 `posted` 或 `already_posted`。

### 现行天花板（门链加载，配置只能更低）

这些数是契约里的计数器形状。规范文档与认知层不一致时，取更严者，并先把取严结果写回规范再改代码。

| 计数器 | 硬顶 | 备注 |
|---|---|---|
| 原创 `post` | 毕业后 ≤4/日，每日只降不升地摇号 | 规范未单列时用这个数，生效前写入规范 |
| 回复 | ≤9/日 | 与认知层 15 取严 |
| 赞 | ≤15/日 | 与认知层 30 取严 |
| 书签 | ≤8/日 | |
| 复制链接 | ≤8/日 | |
| 关注 | 主动 ≤5、回关 ≤15、合计 ≤18 | 信任分天花板默认 0：未放行前每条 `needs_approval`；人调高后仍不得超过此硬顶 |
| 取关 | ≤5/日 | |
| 指定作者成组互动 | ≤2/日 | 仅当同一目标上赞、书签、复制链接、回复四件都发生才计 1 组；不含转发。不是必须完成的任务 |
| 社群回复 | ≤5/日 | 与回复总数共用更严剩余 |

单轮写意图最多 2。相邻写动作还要过间隔门。关注的「主动 / 回关」分类来自 `readProfile` 的页面事实。

## ② 两层账本

安全真相在 `state/`。叙事真相在 `memory/`（或以后接上的 MemoryPort）。画像查询由代码合并后给模型看。门链只读 `state/` 的列。叙事里把某人写成「可以放宽」，门链不放宽。外部记忆不可用时，叙事为空，安全账本继续能熔断、能停写。

两套文件分开锁。夜整理和模型记忆工具碰不到计数器文件。

### 安全账本：代码独占写，模型只读投影

| 文件 | 内容 | 写入者 |
|---|---|---|
| `state/<账号>.json` | 日计数 `used/reserved/cap`、去重集合、熔断、阶段、信任分、期望 handle、窗口 id | 门链、审批结果应用器、自检工人 |
| `state/session-<账号>.json` | 本轮 `OpenRecord`、会话起止 | 仅 `XClient` |
| `state/contacts-<账号>.json` | 出向时间、24h/7d/14d 次数、`do_not_engage`、是否互关 | 回执提交时由代码更新；模型只能提案「拉黑」，解除只有人 |
| `state/ledger-<账号>.jsonl` | `IdemRecord` 与 `ActionReceipt` 追加 | 执行器 |
| `state/tickets-<账号>.jsonl` | 审批票据 | 门链开票；人的决定由审批服务写回 |
| `state/log-YYYY-MM.jsonl` | 审计：拒绝码、开窗身份、STOP、放权 | 各确定性组件 |
| `health-snapshots/YYYY-MM-DD.json` | 四维结论，含 `unknown` | 自检工人 |
| `state/lock-<账号>` | 单写者租约 | 门链 |
| `state/backup/` | 损坏文件的副本 | 启动自检 |

模型可读的投影叫 `SafetyView`，由代码生成，不提供原始文件写入工具：

```typescript
interface SafetyView {
  account: AccountId;
  now: string;
  window_ok: boolean;
  mode: "observe" | "operate";
  phase_day: number | null;
  health: "green" | "yellow" | "red" | "unknown";
  stop: boolean;
  pause_writes: boolean;
  uncertain_open: boolean;
  breakers: Array<{ scope: "account" | "class"; kind?: ActionKind; until: string; code: string }>;
  budget: Array<{ kind: ActionKind; used: number; reserved: number; cap: number; sub?: Record<string, number> }>;
  earliest_post_at?: string;
  pending_tickets: number;
  approved_unexecuted: number;
  open_ids_this_session: TweetId[];   // 只含本轮 XClient 写下的
  last_denies: Array<{ code: string; at: string }>; // 上一轮，最多 5 条
}
```

模型只读、没有任何工具可写的字段：`cap`、`used`、`reserved`、去重集合、熔断、STOP、`uncertain`、打开记录、信任分、阶段、健康结论、已发 `tweet_id`、审批是否通过、cookie。简报里出现的剩余额度以这次投影为准。模型在日记里写「还有额度 / 已经发出」不改变任何判定。

原子预留用锁文件，不用数据库事务。顺序是：拿锁，检查 `used + reserved < cap`，`reserved += 1`，落盘，点击，对账成功则 `reserved -= 1` 且 `used += 1`，失败且确认未发出则只归还 `reserved`。进程在 `reserved` 期间死掉，下次启动先走对账，再归还或标 `uncertain`。

`state/` 解析失败：复制到 `state/backup/`，该号全停，模型不能修这个文件。

### 叙事记忆：分区与晋升

```typescript
interface MemoryPort {
  recall(q: { purpose: string; text?: string; handle?: string; scopes: MemoryScope[]; limit: number; token_budget?: number }): Promise<MemoryItem[]>;
  recentEpisodes(account: AccountId, limit: number): Promise<Episode[]>;
  profile(account: AccountId, handle: string): Promise<ProfileView>; // 代码合并安全列 + 叙事笔记
  appendEpisode(e: Episode): Promise<{ version_id: string }>;
  propose(p: MemoryProposal): Promise<{ proposal_id: string }>;
  // promote 不在此接口。只有审批服务能晋升 canonical
}

type MemoryScope = "persona" | "strategy" | "episode" | "actor_note" | "casebook" | "journal" | "handoff";

interface ProfileView {
  handle: string;
  mutual: boolean | null;          // 来自 contacts，安全列
  warmth: 0 | 1 | 2;               // 代码按真实往来维护
  outbound_24h: number;
  outbound_7d: number;
  outbound_14d: number;
  do_not_engage: boolean;
  do_not_engage_reason?: string;
  note?: string;                   // 叙事，可空
  tags: string[];
}

interface Episode {
  version_id: string;
  account: AccountId;
  at: string;
  text: string;                    // 有长度上限
  evidence_tweet_id?: TweetId;
  idem_key: string;                // 反思重跑不堆十遍
}
```

| 分区 | 谁可写 | 模型能做什么 |
|---|---|---|
| persona | 人 | 只读。空则观察模式 |
| strategy 生效指针 | 人；自动回滚只沿「变差回到父版本」这一条由代码执行 | 周提案，不直接改 |
| episode | 运营者追加 | 14 天后由夜整理蒸馏，原文保留 |
| actor 计数与拉黑 | 代码写计数；拉黑可提案 | 不能改次数，不能自己解除拉黑 |
| casebook | 追加 | 不能改旧行。只有获批文本能进 canonical |
| journal / handoff | 运营者追加，有长度上限 | 下一轮简报只读上一份 handoff |
| 信任分 | 代码按人的决定改 | 无工具 |

写入扫描器拒绝：cookie 或密钥形态、要求忽略围栏或跳过审批或提高天花板的句子、伪装成笔记的工具调用。拒绝作为工具错误返回，并写审计。外部帖文、通知、网页一律 `untrusted`，不能直接写成画像。陌生人文本先记 episode，夜整理在隔离上下文里才允许提案画像增量。

`profile()` 给模型的是合并视图。门链使用的出向次数、拉黑、预算只来自 `contacts` 与 `state/<账号>.json`。

## ③ 调度统一

两级钟，一把锁，看门狗不指挥。

外钟是 schtasks 和按账号 `.bat`。它打开早、午、晚运营窗，并在已有的九个槽位到点。计划时刻抖动 ±25 分钟。两个固定窗口错峰 35±5 分钟。外钟发出的事件没有任务字段。错过不补跑。

内钟是 `resident.js`。执行窗 09:00–22:00 内，它可以发心跳。默认间隔取截断对数正态，中位 35 分钟，下限 12 分钟，上限 90 分钟。运营者的 `next_wake_hint_minutes` 由内钟钳制到这个区间，并且落在 09:00–22:00。提示不能取消告警，不能把写会话排进安静窗，不能改 schtasks。一次 ops 会话 ≤20 分钟，到点收尾。

安静窗 22:00–09:00 不注入行动心跳。这是围栏：没被叫醒，就没有「它决定去发」的机会。采样、备份、健康探针可以在安静窗跑，它们不叫模型。

看门狗是 `resident-guard.js` 和 `maintain-watch.js`。它们只在进程死掉时拉起进程。不清除熔断，不删除 STOP，不补发错过的窗，不把心跳补成任务。STOP 还在时，拉起后仍是停写。删掉 STOP 文件不会自动开跑，要面板或飞书写一条带原因的恢复审计。

同一窗口全局只有一个会话。已有未执行写计划时，新心跳只更新收件箱事实，不并行开第二个会点击的会话。桌面锁保证两个窗口的真实点击不重叠。

### 事件表

```typescript
interface WakeEvent {
  id: string;
  account: AccountId;
  kind: WakeKind;
  at: string;
  trust: "environment" | "human";
  facts: Record<string, string | number | boolean | null>;
  // 无 task 字段
}

type WakeKind =
  | "window_open"       // 早/午/晚
  | "slot_open"         // 已有槽位，facts 只有 slot_index 与时间
  | "heartbeat"
  | "followup_due"      // 来自 request_wake，已被钳制
  | "approval_decided"
  | "approval_expired"
  | "alert"             // 验证码、登录墙、toast、uncertain、看门狗
  | "night_reflection"
  | "weekly_review"
  | "owner_message";
```

| 种类 | 谁产生 | 叫模型吗 | 允许写 X 吗 |
|---|---|---|---|
| `window_open` / `slot_open` / `heartbeat` / `followup_due` | 外钟或内钟 | 叫 ops 会话 | 由模型决定，门链逐条判 |
| `inbox_changed` | 轮询 | 不单独叫。并入下一次心跳的 facts | 否 |
| `metrics_due` / `budget_roll` / `health_due` | 工人 | 不叫 | 否 |
| `approval_decided` | 审批服务 | 安静窗只落盘；执行窗由执行器处理该票据 | 只执行这一张已批票据，不再审议新原创 |
| `approval_expired` | 工人 | 作为下一次简报事实 | 否。旧票不能复活 |
| `alert` / 熔断打开 | 门链或窗口观察 | 叫只读会话 | 否 |
| `night_reflection` | 外钟，约 23:00 | 叫反思会话 | 工具表里没有 X 写 |
| `weekly_review` | 外钟 | 只叫 Analyst | 无 X 工具，无记忆晋升工具 |
| `owner_message` | 面板 | 叫 | 写仍过同一条门链 |

提及不是回复任务。采样不是模型的任务。槽位到点只说明「现在处于规范允许活动的时刻」，不说明「要发哪一种帖」。

### 审批挂起

提案、审批、执行三分离。认知环把 `needs_approval` 写入 `tickets-<账号>.jsonl` 后就可以收尾，进程内不等待。票据字段：`ticket_id`、`idem_key`、`kind`、正文、目标、理由、lint 结果、预算快照、`expires_at`。回复类 4 小时、原创 6 小时、周提案 48 小时。超时标记 `expired`，不补发。

人在安静窗点了通过：只改票据状态，不点击。下一个执行窗由执行器在点击前重查 STOP、熔断、预算、帖龄、黑名单、正文哈希、打开记录是否仍可由本轮重新打开来满足。重查失败则票据作废，原因进入简报。通过后若进程被杀，靠同一 `idem_key` 对账，不发第二条。

飞书回调进不了这台机器时，面板批准是完整路径。飞书降级为通知。模型没有审批工具。

### 夜整理与周复盘

夜整理约 23:00，同一身份，工具表只有记忆读取和提案。它蒸馏当日 episode、起草明日提示和案例提案，不碰 X，不改天花板。明日提示进下一轮简报的事实区，不是任务。

Analyst 每周一只，读满 48 小时的样本和案例，最多 3 个白名单参数提案。预算、间隔、防骚扰、熔断阈值、拟人分布、审批条件标为 `safety_locked`，提案碰到就作废。人批准后才写入规范文档再生效。连续两个评估周变差，由代码回到父版本并冻结提案一周。这条回滚不经过模型。

四维自检每天一次，带抖动。搜索建议和搜索可见性走窗口外接口，并带对照账号。接口失败记 `unknown`。幽灵回复不用登录态自己看自己作为通过证据。`unknown` 连续两天只升告警。确认命中则该号当日只浏览，取消未执行的写，恢复必须人确认。

## ④ 五份方案在此嫁接下的微调

认知部件保留：四项决策权、三平面、ActionPlan、两套真相、打开记录、票据审批、类别信任分、防漂移五法、夜整理、PersonaGym、技能即知识、Analyst 与 Operator 分离、noop 合法、fail-closed。

下面只改和这双手接不上的部分。

### XActions 侧车：换成 `BitBrowserXClient`

五份里的手都是 XActions MCP，加 twifork、官方 API、twitterapi.io 降级链。第一实现改为本节的 `XClient`：真实窗口脚本加 `probe-reply-dom.js`，窗口是比特浏览器里那个固定 id。

启动时不做 `list_tools()` 能力裁决，也不把 152 个工具 schema 送进上下文。门面工具就是 ① 的那张表。选择器只存在于 `XClient` 和探测脚本。

失败降级改为：同一窗口、同一 `idem_key` 再试一次；仍不清楚则 `uncertain` 停该号写。不切换无头，不换代理，不新开指纹，不用第二套浏览器栈。官方 API 与第三方写接口保持不武装。读指标走 fxtwitter 一类窗口外只读，不打开自己的帖「看数据」。

Cookie 不导出到运营进程。登录是人在该固定窗口里完成。验证码出现则 STOP，不打码，不刷新重试。

### NSSM：这个手上换成现有常驻与看门狗

真实窗口要交互式桌面。第一实现的进程模型是 schtasks、`.bat`、`resident.js`、`resident-guard.js`、`maintain-watch.js`。看门狗的职责缩成 ③ 里那四条。

NSSM 不作为运营者或浏览器的宿主。若以后有人把 resident 包成服务，也必须跑在当前桌面用户下，并且仍然不许它清熔断。面板若要，只绑 `127.0.0.1`，不再占一个会写安全账本的服务。安全账本的写者只有门链和审批结果应用器。

### SQLite：不替换 `state/`，只允许可丢弃索引

安全真相用 ② 的文件。`proposals`、`actions`、`breakers`、`budget`、`audit` 都落在这些 JSON / JSONL 上。

LangGraph 的 SqliteSaver、检查点库、按帖位建图，第一实现不引入。持久性靠账本和幂等键。审批用票据，避免「恢复时节点从头跑导致再发一次」。

面板若需要查询，可以另建可随时从 `state/` 重建的索引。索引损坏不影响发帖判定。每天备份是复制 `state/` 与 `memory/` 到带日期的目录，保留 14 份。复制前先确认没有该号的写锁。

### 其他逐项

| 原稿 | 嫁接后 |
|---|---|
| grok：关注不进自动，工具侧等于没有这只手 | 原语保留。信任分天花板默认 0，所以仍是每条人批。硬顶用规范的 18（5+15）。人可以只提高天花板，不能超过硬顶 |
| grok：一个侧车、不做多号轮换 | 保留「不轮换」。一个固定窗口绑定一个 Operator 线程 `operator:<windowId>`。不换窗、不换代理、不共享会话。不按帖位建图 |
| grok：`uncertain` 全局停写 | 改为停该窗口所属账号。另一固定窗口有自己的 `state/`。机器级全停只用于 STOP.hard、宿主锁冲突，或该文件损坏 |
| grok / kimi：原创间隔 150 分钟或 3 小时；手上帖间约 25 秒 | 25 秒是会话内互动间隔。150 分钟只约束 `post`。两道门都留 |
| grok 行动窗 08:00–23:30；kimi 三段短窗；cursor 到 01:30 | 执行窗改为 09:00–22:00。安静窗即围栏这条原则保留 |
| cursor / kimi 会话 30 分钟或 8 分钟墙钟 | ops 墙钟取手上的 20 分钟。工具次数上限仍用 kimi 的 16/8/2 |
| gemini：超时免审（高分 30 分钟无异议自动发） | 改为过期即作废，不自动执行 |
| gemini：精力模型驱动外壳 | 精力只可进日记。门链不读 |
| gemini：`evaluate_posting_readiness` 前置函数 | 不建成节点。发不发由模型决定，lint 只做拒绝 |
| gemini：同 profile 无头与有头切换 | 固定有头窗口。同一 profile 不在有头无头之间切 |
| kimi：默认不开放关注原语 | 与 grok 同一修正：原语在，自动天花板从 0 起 |
| kimi：`interrupt()` 在工具内部 | 改为票据。`interrupt` 前零副作用这条纪律，变成「开票前零点击」 |
| glm：关注 / 删帖 / 私信永远 L0 | 私信与删帖第一实现仍无此原语。关注用信任分，而不是从工具表删除 |
| glm：APScheduler 另起一个钟 | 不另起。只留外钟加 resident |
| 五份：Python 主进程 | 第一实现循环用 JavaScript RawLoop。`BrainLoop` 端口留着，以后可以换大脑，手和门链不动 |
| 五份：14 天 warm-up 从 D1 零写开始 | 日历保留，作为「阶段」而不是任务单。阶段只放宽上限，不要求多发。已在跑的窗口，阶段由人写在 `state/` 里，启动时不重置到 D1 |
| 指纹与代理 | 不再设计。比特浏览器一窗一指纹、代理不换，就是这项的实现 |
| 拟人参数 | gemini / kimi 的键入、误打、提交前停顿灌进 `XClient` 输入层。宏观节奏仍由模型和执行窗决定 |
| 防骚扰 | 与规范同源，叠在同一道 TargetPolicy：24h/7d、同帖一次、社群作者 14 天、连续两次驳回则本会话不再提案回复 |

类别信任分继续是唯一放权机器。点赞、Safe 回复、原创各自有阈值和天花板。Bold 与关注的自动天花板默认 0，提高要二次确认并写审计。升级只有人能做。影子封禁命中、未对账、验证码，代码把对应类打回 0。全自动不是总开关。

周提案的白名单保持格子化。模型可以提议，不能生效。这解决「参数只能来自代码」和「运营者可以提出进化」之间的分工：提议权在认知层，生效权在人和 `safety_locked`。

## ⑤ 底稿：以 grok-4.7 的认知骨架为底，按部件吸收其余四份

用 grok-4.7 做骨架，不把五份运行时并列跑起来。

保留它的这些判决：一个决策环而不是内容图；合法输出包括整天 noop；世界摘要由代码拼；模型工具表只有读和记忆；写只存在于 ActionPlan；安全账本与叙事记忆分离，门链只认本地安全列；赞和回复必须打在本轮真正打开过的对象上；安静窗是围栏；错过不补跑；`uncertain` 就停后续写；STOP 删文件不自启；Analyst 不被运营者召唤，且没有 X 工具；人设为空就观察模式；模型名为空就拒绝运营；指标不靠自己的登录态看自己的帖；记忆扫描器挡「把围栏写松」。

上轮这份骨架有几处要自我修正，否则接不上这双手：

1. 手写成了 XActions 侧车。改为 `BitBrowserXClient`。
2. 安全真相写成了 SQLite。改为 `state/` 文件。SQLite 检查点不再当审批主机。
3. 审批挂在 LangGraph `interrupt()` 上。这双手的进程会随窗退出，节点重跑会双发。改为票据。
4. 「关注不进自动」写成了手上没有这个动作。改为原语保留、自动天花板默认 0。
5. 「不做多账号」容易读成禁止第二个固定窗口。修正为：禁止轮换指纹和代理；一个窗口一个线程。
6. 动作种类缺书签、复制链接、取关、浏览。按手补上。转发仍不在第一实现。
7. `uncertain` 写成全局停机。改为停该号，对账程序就是 verify-unknown 的升级。
8. 会话时长和执行窗改用手上的 20 分钟和 09:00–22:00。原创 150 分钟间隔保留，不拿帖间 25 秒去替换它。

其余四份只嵌入部件，不引入它们的宿主：

- cursor 提供反退化条款、门链四种判定里的前三种（allow / deny / needs_approval；不采用它改写内容的路径）、拒绝回传、三次同因拒绝就卸下该工具、交接笔记、技能写知识、大脑可替换的端口。形态 B 以后可以把同一套门链和 `XClient` 交给另一个大脑，第一阶段不这么做。
- glm 提供薄循环、态势由代码装配、提案与执行分离、夜整理不碰 X、外部文本不能在运行态直接改画像。它的 L0–L4 不另做一套状态机，只作为信任分的口语别名。
- kimi 提供精确事实不准进模型记忆、门链顺序、原子预留、幂等键由宿主生成、防漂移五法、单次唤醒上限、字块之间查 STOP、`unknown` 不算通过。
- gemini 提供输入层的拟人参数。它的认知环节点名、超时免审、精力驱动外壳、无头切换，不进入实现。

这样保留的是「谁做决定」，换掉的是「手和钟用哪一套已经在跑的代码」。

## 落地阶段

每一阶段都先有契约测试，再接到真窗口。测试挂在公共边界上：事件 schema、简报文本、门链判定、`XClient` 的 dry 回执、幂等终态。

**P0 契约与干跑。** 冻结本文的类型。Fake `XClient` 在 `--dry` 下跑通：简报、空计划、拒绝码、开票。单测锁住「事件无 task」「简报无祈使句」「门链不改写正文」。不点击，不写 `state/`。验收：模型名为空时只能 dry；人设为空时写计划被拒。

**P1 只读真窗口。** 接固定窗口的 `verifyIdentity`、`browse`、`readTimeline`、`readTweet`、`probe-reply-dom.js`。打开记录只由 `XClient` 写。连续 7 日只读。cookie 不出现在日志和简报。身份不匹配则停该号。

**P2 人审写闭环。** 开放回复、赞、书签、复制链接，信任分仍全部要人。执行器接上高斯间隔和输入层拟人参数。三次杀进程演练通过才进入下一段。`uncertain` 能停该号并完成对账升级。STOP 在字块之间 3 秒内生效。

**P3 原创、关注原语与夜整理。** 原创仍人审，并受 150 分钟间隔。关注原语可用，天花板仍是 0，每条人批，硬顶 18。夜整理和周 Analyst 以只读工具跑通。四维自检里 `unknown` 不得记成通过。

**P4 按类放权。** 人显式提高某一类天花板后，只放开那一类。抽审保留。事故时该类回 0。PersonaGym 的固定场景集（注入、要求自我放权、模仿对方口吻）失败则冻结晋升。是否进入某类自动，由人和信任分决定，不由日历决定。

P0 到 P2 期间不武装第二浏览器栈，不把 resident 换成 NSSM，不用 SQLite 替换 `state/`。