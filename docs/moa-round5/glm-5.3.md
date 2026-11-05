本地五份方案（round3）、五份评估（round4-eval）、x-auto-run 仓库（README、规范与约束.md、executor.js、planner.js、llm.js、resident.js、probe-reply-dom.js）均已取证完毕。以下为完整嫁接方案，作为最终交付。

---

来自 GLM 原生岗（GLM-5.3）

# 《认知层 × x-auto-run 操作层》完整嫁接方案

**项目本体**：中文 AI/科技人设号自主运营智能体（单人设号，认知层主体不变）。
**嫁接两端**：认知层=round3 五份方案的记忆/画像/人设卡/自主决策/提案审批/渐进放权/夜整理/风控护栏（不许退化成管线）；操作层=x-auto-run 的实际操作方式——纯 JS 真实窗口自动化 + DOM 探测 + 比特浏览器固定窗口/固定代理 + state/ 文件记账 + schtasks/.bat/resident/看门狗 + 《规范与约束.md》“约束代码强制”文化 + --dry 演练（作为 agent 的“手”，不引入 XActions）。

## 0. 嫁接总纲：一处控制权反转，两端各自完整

x-auto-run 的运行链是“schtasks 叫起 → planner 确定性出队列 → llm.js 只写文案 → executor 点击”。嫁接的唯一结构改动是把中间两环**反转**：

- **planner 的确定性扫描逻辑降格为 agent 的“感觉器官”**（collectPosts/fetchLatestPost/notifications 收集等函数库化，变成读类工具）；
- **llm.js 从“文案函数”升格为“运营者本人”**（审议、选意图、调工具、写交接，旗舰 GLM）；
- **queue 文件从“任务队列”升格为“提案账本”**（planned→intent→done|unknown|failed 状态机原样保留，新增 rationale/approval 字段）；
- **executor.js 原样保留并加厚网关**，仍是唯一会点击的组件。

一句话：**钟不动、手不动、账本格式不动；把“谁决定这一窗做什么”从代码换成认知循环。**操作层一切落地手段（真实窗口、probe、拟人时序、文件账本、bat 调度、--dry、保号优先）原样继承；认知层的四项自主（议程/时机窗内/记忆/策略提案）全部保留。

## 1. 总体架构

```
第一圈 唤醒层（钟 = x-auto-run 原样）
  schtasks（注册-*.ps1）→ 定时-*.bat
  ├─ 运营窗任务（早/中/晚）+ 心跳任务（活跃窗内随机化间隔）
  ├─ agent-resident 常驻（bat 拉起、guard 看护、23:59 自灭、00:05 接管）
  ├─ 日历任务（夜整理 23:00±15 / 周调参周日 / 四维自检 07:40±20 / 半夜数据窗）
  └─ resident-guard / maintain-watch 看门狗

核心 认知循环 xar-agent（Node，短命会话，200-300 行薄循环）
  唤醒 → 读态势（代码装配《态势报告》）→ 审议（旗舰 GLM）→
  行动（读类工具直连真实窗口；写类生成 ActionProposal）→ 微反思（flash）→
  end_session(handoff, next_wake_hint) → 进程退出

第二圈 闸门与执行器（确定性，executor.js 加厚）
  网关中间件链 12 道（PAUSE/熔断/阶段/门禁/判分/目标/频控/预算/审批路由/幂等/执行/回执）
  → 真实窗口动作（比特浏览器 CDP + probe 双核验 + 拟人时序）
  → 票据审批（飞书卡片跳面板，GET 渲染 POST 落定；resident 到点执行 not_before 票据）

第三圈 地基
  state/（安全账本，executor 独占写）+ memory/（认知账本）+ fxtwitter 只读采样
  + 飞书 + 面板（127.0.0.1）+ 备份
```

三方契约延续我上轮（glm-5.3.md:23）：代码负责呈现世界（态势报告由代码装配）、模型负责决定行动、工具层负责守住后果——护栏长在网关里，模型无法绕过它够不到的代码。

## 2. ① 认知层 ↔ 操作层契约

### 2.1 动作原语 → 真实窗口操作映射表

| 认知层原语 | 落地实现（全部来自 x-auto-run 现有代码路径） | 类别 |
|---|---|---|
| `read_timeline(surface, screens)` | 比特浏览器 `/browser/open` → CDP connect → /home 切 For You/Following → humanScroll 变速滚动 → collectPosts 收帖 → 压缩成卡片 | 读·真实窗口 |
| `read_post(id, replies)` | permalink 打开 → 按 statusId 定位 article（不取第一条）→ 刮正文/作者/指标 → 滚动读回复层 | 读·真实窗口 |
| `read_mentions(limit)` | /notifications/mentions 轮询收集（resident 页1 逻辑库化） | 读·真实窗口 |
| `read_profile(handle)` | peek-profile 路径：主页 bio/粉丝量/近帖 | 读·真实窗口 |
| `browse(minutes, focus)` | humanScroll + 详情页 dwell + 选择性停顿，flash 出浏览摘要，**不点任何互动**（insights 纯只读纪律） | 读·行为 |
| `post(text)` | postTweet：compose 页发布，toast 判定 done/unknown | 写·提案 |
| `reply(id, text, tone)` | replyToPost：statusId 校验 → permalink → 点回复 → **probeReplyComposer 探针确认输入框绑定父帖** → 粘贴 → 提交前二次绑定核验 → toast 判定 | 写·提案 |
| `quote(id, text)` | postQuote：UI 路径（转推按钮→菜单引用，直链 404 的教训） | 写·提案 |
| `like(id)` | likePost：已赞幂等；前置 maintainDwell 停留 20-45s | 写·提案 |
| `follow(handle)` | followUser：开主页点关注，确认 unfollow 按钮出现；已关注算幂等成功 | 写·提案（默认 L0 人审） |

### 2.2 接口签名（模型可见门面 → 执行器端口）

```ts
// ── 读端口：审议循环内直接执行，返回压缩观察 ──
interface XReadPort {
  readTimeline(surface: 'for_you'|'following', screens: 1|2|3): Promise<TweetCard[]>
  readPost(id: StatusId, replies?: 0|1|2): Promise<PostDetail>
  readMentions(limit?: number): Promise<NoticeCard[]>
  readProfile(handle: string): Promise<ProfileCard>
  browse(minutes: 2|4|6|8, surface: Surface): Promise<BrowseDigest>
}
interface TweetCard { id; handle; mutual: boolean; text(≤280); metrics; age_min; has_media }
  // has_media=true 时 ContentLint 默认拒回（planner.js“带图帖回复必违感”教训升为门禁）

// ── 写端口：模型只能提案，不持有执行权 ──
type ActionKind = 'post'|'reply'|'quote'|'like'|'follow'
interface ActionProposal {          // queue/ 条目的新 schema（兼容原状态机）
  id: ULID; kind: ActionKind; account: string
  target_id?: StatusId; target_handle?: string; text?: string
  tone: 'safe'|'interesting'|'bold'
  rationale: string                 // 模型自述理由——仅供人看，围栏不采信
  risk_self: 'low'|'mid'|'high'
  not_before?: ISO8601              // 稍后发：resident 到点执行
  context_ids: StatusId[]           // 声称看过的帖，供“打开过才可动”核验
  status: 'planned'|'intent'|'done'|'unknown'|'failed'|'skipped'
  approval?: { mode: 'auto'|'ticket'; decided_by?; edited_text?; at? }
}
interface ActionReceipt { proposal_id; status: 'posted'|'already_posted'|'failed'|'unknown'|'denied'
  code?; detail?; retry_after?; hint?; tweet_id?; evidence?: {toast; url} }
// 统一拒绝格式（cursor-opus-5.5.md:401）：
// {ok:false, code:'MIN_GAP', detail:'距上一帖 1h52m，需≥2.5h', retry_after:'...', hint:'可先浏览或收尾'}
```

幂等键由代码生成：`account+kind+target_id+normalize(text) 哈希`，模型不能自选（grok-4.7.md:102）。同因拒绝连续 3 次本轮下架该工具并回告（cursor 防死循环）。

### 2.3 网关中间件链（固定顺序，executor.js 入口的一段 JS）

`KillSwitch(PAUSE) → Breaker(风控toast锁存/abortedToday/uncertain未对账停写/登录异常) → PhaseCapability(warm-up能力矩阵，工具对模型隐藏+网关拒绝双保险) → ContentLint(风险词/humanizer硬规则/长度/emoji≤2/禁链接hashtag@/近30天bigram>0.55/24h同正文) → ToneJudge(flash判AI味分+独立复核自报tone，防Bold伪报Safe) → TargetPolicy(防骚扰表：同作者24h≤1出向·7d≤3·同帖一回·对方回了才二回·hostile拒·like目标必须在seen集) → Rate(帖间隔/小时帽/相邻动作高斯间隔——参数表沿用《规范与约束.md》第3节) → Budget(滚动24h ledger+日帽原子预留，文件锁内单写者即原子性) → ReviewRouter(L0-L4授权矩阵：自动放行或开票据) → Idempotency(queue状态机：intent先落盘) → Execute(真实窗口+probe双核验+风控toast监听) → PostHook(seen集/ledger/log-jsonl/episode引用/fxtwitter采样点登记)`

关键点：**“打开过才能动”**（grok-4.7.md:50）由 browse/read 的 PostHook 写 `state/seen-<account>.json`，点赞/回复目标不在 seen 集 → TargetPolicy 拒；模型自己说“我看过了”不算数。

**DOM 探测的契约地位**：probeReplyComposer 升格为写路径的**强制闸**（提交前后各核验一次，绑定失败 → `denied:DOM_BINDING`，模型收到结构化错误可换提案但不许原样重试）；probe-reply-dom.js 式一次性探针进“健康日检/晚检”日历任务做**选择器漂移监测**——选择器失效即 PAUSE+飞书，绝不盲试三次把号打停。selector 表独立成 `config/dom-selectors.json`，X 改版只改配置。

### 2.4 会话限额（runaway 上限，kimi-k3.md:68）

单次唤醒：工具调用 ≤16、写提案 ≤3、原创 ≤1、墙钟 ≤8min、记忆写 ≤3。达限环境强制收尾，不“再看一个再回一条”。**noop 是合法结果，预算是上限不是 KPI。**

## 3. ② 两层账本：state/ × memory/

采用 grok“两套真相”（grok-4.7.md:43）为总原则，落地为 x-auto-run 文件风格：

**安全账本 `state/`（代码独占写，模型只透过态势报告看结论）——原样保留 x-auto-run 全套**：
- `ledger-<account>.json`：滚动 24h 动作时间戳 + 日帽；capLeft fail-closed
- `taskstate-<account>.json`：repliedAuthors/repliedPosts/replyHistory 跨会话去重
- `queue/<date>-<win>-<account>.json`：提案账本（exactly-once 状态机 planned→intent→done|unknown|failed）
- `pending-verify-<account>.json` + verify-unknown.js：**这就是五份方案 uncertain 对账的 x-auto-run 实现形态**（intent 崩溃残留→unknown 禁重试→30 分钟后 FxTwitter 匿名核验）。本轮升级两点：①unknown 存在时 Breaker 停新写（对人设号恢复五份方案的严格口径——x-auto-run 10-20“交付必达”拆除连败熔断是工具号拍板，precious 人设号不适用）；②对账完成前同主题第二条草稿不许进计划
- `lock-<account>.json`（账号级文件锁）、`PAUSE`/`PAUSE-<account>`、`log-YYYY-MM.jsonl`（月度审计）、`seen-<account>.json`、`handoff-<account>.json`（交接笔记）、`next-wake-<account>.json`（心跳钳制）

**认知账本 `memory/`（agent/夜整理写，人可编辑）**：
- `episodes-YYYY-MM.jsonl`：情景日志（微反思一句话 + 提案 id/tweet_id 引用，不复制安全字段）
- `profiles.json`：互动者画像（互关/warmth/兴趣/情绪EMA/do_not_engage）——存储风格参考 contacts.js 的有界设计（每主体限长、TTL、归档）
- `persona-vN.md`：人设卡版本链；`blocks.json`：三块核心记忆（今日议程/自我模型/世界观）
- `casebook.json`：案例（含 human_edit 成对样例：原稿→人改终稿→教训，cursor-opus-5.5.md:590 改稿回流）
- `journal-YYYY-MM-DD.md`：当日日志；`insights.json`：夜整理产物

**两层关系四纪律**：
1. **单向引用**：episode 引用 action 的 proposal_id/tweet_id，绝不复制计数/额度/是否已发——认知层要读额度只能走态势装配（kimi-k3.md:81“防止 Agent 用记忆代替真实状态”）。
2. **写入隔离**：外部文本（时间线/mention）只能进 log_episode 原始情景；画像写入必须经夜整理隔离上下文二次提炼（记忆投毒防线，glm-5.3.md:55）。
3. **围栏只读 state/**：外部画像把某人标成“可以放宽”，TargetPolicy 不放宽。
4. **日终对账**：夜整理先跑 log jsonl ↔ episodes 一致性检查，有动作无 episode → 补骨架 episode。

**SQLite 裁决**：不上。安全真相=state/ 文件（看文件即排障，与宿主运维同构）；认知账本=memory/ 文件（单号日几十条 episode，够用）。SQLite 仅当未来引入嵌入检索/FTS5 或多账号并发时作为 memory/ 的存储引擎升级，**永不被网关读取、永不承载安全字段**。

## 4. ③ 调度统一：钟不动，心跳物化为 schtasks + resident

**唤醒三通道全部落在现有调度形态上，不引入 APScheduler/常驻 Python**：

| 唤醒类型 | x-auto-run 落地形态 | 与认知层的融合 |
|---|---|---|
| 心跳 | schtasks 心跳任务（活跃窗 08:00-23:00 内随机化注册；错峰/不补跑/±25min 抖动文化保留） | 每次触发 `node agent.js --wake heartbeat`。先读 `next-wake` hint：未到模型建议时刻且无红灯 → 记一行 skipped 即退出（零模型成本）；否则进完整六阶段循环。模型给 15-90min 睡眠建议，代码钳制上下限与活跃窗——“调度器只负责叫醒它”（glm-5.3.md:12），时机自主收缩为**窗内自主** |
| 事件 | agent-resident 常驻进程（继承 resident.js 一窗三页骨架与 23:59 自灭/00:05 接管/探针通过才写心跳/连败15轮自灭） | 页1 保留为“感觉神经”：mentions 5min 轮询分类；新 mention → 写事件队列 → 活跃窗内且距上次旗舰审议≥15min → 拉起 event 唤醒；审批落定 → 立即唤醒；风控 toast/登录异常 → 写 PAUSE + 只读事件唤醒。**实质提问转 human-todo 不硬回**（resident 现规则，保留）。x-auto-run 的页2巡逻/页3整点互关帖是矩阵业务，不迁移；resident 新职责=事件神经 + 票据到点执行器（not_before 条目过网关落地）+ 通知摘要 |
| 日历 | 定时-*.bat 照旧 | 夜整理 23:00±15（同一身份低权模式：只写 memory 与提案，不碰 X）；周调参周日；四维自检 07:40±20（FxTwitter 免账号①② + 比特浏览器第二个无 cookie 窗口③④ + 对照账号防误判，unknown 不算 pass）；半夜数据窗只读采样与备份 |

**STOP/PAUSE 语义**：`state/PAUSE` 文件为唯一硬急停，agent 每轮第一查、网关每动作查、拟人输入块间查（kimi-k3.md:100）。升级一条：删除 PAUSE **不自动恢复**，须面板/飞书 resume 写审计——这比 x-auto-run 现状（删文件即恢复）严，对 precious 号是正确方向。看门狗保留 resident-guard（PID 探活）+ 新增 agent 心跳文件检查，拉 bat 重启 + 飞书；schtasks 禁用态漏火由 PAUSE 釜底抽薪（现机制保留）。

**执行窗护栏**：写动作只在 09:00-22:00 执行窗内（窗外语料/采样/整理照常）；凌晨深度休息窗无心跳无写——**安静窗即围栏**。

## 5. ④ 五份方案部件微调清单（嫁接下的逐一裁决）

| 部件（出处） | 嫁接裁决 |
|---|---|
| **XActions MCP 侧车**（glm/kimi/grok/gemini 均设） | **整体删除**。XClient 第一实现=executor.js 库化（真实窗口+probe+拟人时序）。XActions 仅留作未来降级备选，不进第一实现 |
| **Node 侧车进程拓扑**（core/sidecar 分离） | 删除。认知循环同为 Node，同语言单进程组（agent 短命会话 + resident 常驻），无跨语言桥 |
| **APScheduler/自研常驻唤醒器** | 删除，钟=schtasks+bat+resident（§4） |
| **NSSM 服务化** | **第一实现不用**。schtasks+bat+guard 已覆盖拉起/看护/急停/接管四要素。NSSM 列为 VPS 迁移期可选项（等价 systemd） |
| **SQLite 事件账本一统** | 拆为 state/ 文件（安全）+ memory/ 文件（认知），SQLite 降级为远期 memory 存储引擎（§3） |
| **LangGraph/interrupt/checkpoint**（kimi/gemini/grok/cursor 备选） | 全部不引入。持久性靠账本+短命会话+交接笔记（x-auto-run queue 状态机与五份“账本优先”结论在此汇合）；interrupt 的审批语义由票据制承接（glm-5.3.md:61 三分离） |
| **Python 自研薄循环**（glm-5.3:77） | **JS 化**，200-300 行照旧；GLM 双档接入 llm.js（llm.json 四通道 fallback 架构直接复用，GLM 旗舰/flash 置前，保留按通道钉死参数的纪律，如 kimi 通道 temperature=1） |
| **guardWorthiness/judgeWorthiness 预筛**（llm.js 现有） | 保留为读类工具的前置便宜档（mimo 钉通道 noFallback，失败=放行的“宁多回不漏回”口径仅适用于矩阵业务；人设号改为**失败=跳过该候选**，宁缺勿滥） |
| **FastAPI 面板** | 换为轻量 Express/静态面板（127.0.0.1，public/ 监控页增强）：待审队列/预算与授权/熔断与自检/STOP·恢复/会话回放 |
| **拟人原语**（五份各有参数系） | gemini 参数矩阵（拼音分块650ms块间TLN/手误退格2.5%/发前复核停顿/贝塞尔鼠标/甩动回滚7%）+ kimi 时序（句末停顿/思考停顿）**灌入 executor 输入层**，替换/增厚现有 gauss·humanClick·humanScroll——round4 已确认此结论有效 |
| **四维影子封禁自检** | 保留（glm 四维定义+kimi 双视角细节），载体=日历 bat + FxTwitter + 比特浏览器第二无 cookie 窗 |
| **warm-up 十四天表** | 保留（人设号是新号，认知层护栏不退化）；叠加 x-auto-run 变更管理纪律：**DRY 先行→半量→全量→第 7 天复盘，新动作类型默认关闭、先改《规范与约束.md》再改代码**（规范文档=宪法层，人写） |
| **预算 3/12/20 + 摇号** | 保留（glm-5.3.md:88 数值与“重启不重摇”），载体=ledger+config 上限，代码硬顶只能调低 |
| **关注/followers-scan/contacts/mutual-post（x-auto-run 模块）** | **不迁移**——那是多号互关矩阵业务；单人设号 follow 为低频 L0-L1 人审动作。contacts.js 的关系台账存储风格（有界/互惠分/TTL）移作 memory/profiles 设计参考 |
| **“交付必达”拆熔断**（executor 2026-10-20 拍板） | 不继承。人设号恢复五份方案熔断矩阵（同类连败3停/uncertain停写/验证码一次即停机不自动打码）；保留风控 toast 正则锁存 + abortedToday |
| **fxtwitter 采样链** | 保留并扩为 T+2/8/24/48h 四点 + 每小时粉丝快照；主 KPI=周内满48h帖加权互动中位数（grok 口径） |
| **飞书** | feishu-report.js 升级：告警照旧单向；审批卡片跳面板（GET 渲染 POST 落定防预取），TTL 原创6h/回复2h，过期即拒不补发 |

## 6. ⑤ 认知层取材裁决：以 glm-5.3 为底稿，组合四份部件

**底稿=我上轮 glm-5.3.md，理由四条**：
1. **同构性**：自研薄循环 200-300 行、无框架依赖、文件账本、短命进程——五份中唯一与 x-auto-run“纯 JS 脚本 + state/ 文件 + bat 调度”气质同构的运行时，JS 翻译成本最低（round4 评估 gemini 侧同此结论：“几乎可以 1:1 无缝翻译成纯 JavaScript”）。
2. **三分离与批处理天然契合**：提案-审批-执行三分离 + 外置票据，使每次 bat 唤醒成为一次干净会话、票据跨窗由 resident 执行、进程崩溃靠 queue 状态机不靠检查点——与 x-auto-run exactly-once 三件套在“持久性靠账本”上同源，两个设计互相印证而非互相改造。
3. **无 interrupt 问题域**：kimi/gemini/grok 都要处理“恢复时节点从头重跑”，本案从结构上不存在。
4. **坦诚修正**：我上轮的操作层（XActions 侧车、APScheduler、Python、NSSM、SQLite 一统账本）是错的那一层，本轮全部让位给 x-auto-run 实践——**我修正我自己的旧立场**（与 glm-5.3.md:121 修正 LangGraph interrupt 立场同一态度）。

**组合吸收清单**：cursor（网关 13 层链的顺序与四种返回值、结构化拒绝、票据预算预留、改稿回流成对案例、反退化纪律——心跳事件 schema 禁 task 字段、简报禁祈使句）；grok（两套真相、uncertain 对账 SOP=verify-unknown 语义升级、“打开过才能动”、ActionPlan 字段设计、置顶风险八条进规范“已知陷阱”节）；kimi（防骚扰硬约束表、精确事实不进 LLM 记忆清单、runaway 上限、四维自检双视角细节、账本原子预留思路）；gemini（拟人动力学参数矩阵——操作层唯一直接成体系的可抄资产、Tier 互动分档并入回复三档、连续冷帖自动收敛拉长间隔）。kimi 的 PersonaGym 30 场景回归集保留为夜整理周期校验（每次人设卡/模型/提示词变更后必跑，失败冻结晋升）。

## 7. 一次典型唤醒走查（全链路）

14:12 心跳 bat 触发。agent.js 预检：无 PAUSE、无 unknown 挂账、next-wake 已到 → 装配态势：预算 发1/回7/赞14（ledger）；warm-up D18、回复 L2；收件箱 3 条摘要（含互关好友新帖，画像小卡内联：mutual、warmth 2、技术派）；昨晚帖 24h likes 41 中位数之上；待审票据 0；夜整理明日提示“端侧推理延迟选题”；上次交接“下午打算实测后写一条”。

审议（旗舰）：①先回互关——read_post 拉原帖与热评（真实窗口，dwell 22s），memory_query 历史互动 3 条，写 62 字回复，tone=safe 自评低风险 → propose_reply → 网关：TargetPolicy 过（同作者 7 天 1 次）、Lint 过、ToneJudge 9.1、L2 授权自动 → queue 记 planned → executor 转 intent 落盘 → 开帖 → probe 绑定核验 → 分块拟人输入（块间查 PAUSE）→ 提交前二次核验 → toast=发布成功 → done，tweet_id 回填，episode 记一句，PostHook 记 ledger/seen/采样点。②发帖预算留 20:30——propose_post(not_before=20:30) 进票据 → L1 人审 → 飞书卡片。③like 一条真读过的长文（seen 集内）。end_session(handoff=“晚间发实测帖，素材已存 material”, next_wake_hint=38min) → 进程退出。

20:31 resident 到点：票据已人批（改了两处措辞——diff 已回流 casebook）→ 过随时间变化的网关层（KillSwitch/Breaker/Rate/Budget确认/Idempotency）→ 执行 → approval_decided 事件入下次简报。23:05 夜整理 bat：对账 log↔episodes → 蒸馏画像增量 2 条、案例 1 条（含 human_edit 对）→ 改写今日议程块 → 明日提示。杀进程演练口径：任一时刻 kill，X 上最多一条、queue 终态 done/unknown，unknown 30 分钟后 verify-unknown 匿名核销。

## 8. 嫁接后仓库结构（x-auto-run 风格增量）

```
agent.js（认知循环 CLI）+ situation.js（态势装配）+ session.js（限额/交接）
executor.js（加厚网关链）/ llm.js（GLM 双档+fallback）/ quality.js（门禁+humanizer-zh）
resident.js（事件神经+票据执行器）/ resident-guard.js / verify-unknown.js / verify-reply.js
memory.js（episodes/profiles/casebook/blocks 读写+投毒扫描器）
night-review.js / weekly-tune.js / selfcheck.js / sampler.js（fxtwitter）
config/{config.json, llm.json, dom-selectors.json, persona/(memory/persona-vN.md 同步)}
state/（安全账本，原样）+ memory/（认知账本）+ queue/ + posts-inbox/
定时-*.bat + 注册-*.ps1（心跳/运营窗/日历/常驻/看门狗/飞书报告）
tests/（杀进程三演练 + 注入用例集 + selector 漂移 fixtures + 预算并发测试）
《规范与约束.md》（宪法层：人写，先改文档再改代码——保留为最高纪律）
```

## 9. 风险与开放问题（要点）

- **选择器漂移是第一运维风险**：dom-selectors 独立配置 + 日检探针 + PAUSE 兜底；X 改版当天最多损失一个执行窗。
- **token 成本**：日 20-30 次唤醒约 15 万入/4 万出（glm-5.3.md:116 口径），GLM 套餐内；套餐耗尽停审议不自动切更贵通道（grok-4.7.md:72）。
- **提示注入**：外部文本包 untrusted 标记、外部密集轮工具带收窄为只读+提案、无 shell/文件/DM 工具、注入特征命中降级只读并计数（glm-5.3.md:93 四件套全保留）。
- **待用户补充**：人设语料（未到前禁发原创，不猜通用口吻）；记忆系统是否 API 化对接（MemoryPort 契约预留，memory/ 文件为默认实现）；面板外网方式（默认仅本机/Tailscale）；自检④维的第二无 cookie 窗口在比特浏览器的配置方式。
- **ToS 定性风险**：安全层是降量+停机+早发现，不是规避检测；LLM 层不构成风控手段，真正起作用的是错峰、熔断、拟人时序与预算这些确定性规则。

## 10. 路线图（沿用 x-auto-run 变更管理节奏）

M0 无账号演练（W1-2）：循环骨架+假窗 dry 全链路（审议→提案→网关→假执行→假回收）+杀进程三演练+注入用例集全拦+预算并发测试零超限。M1 只读真号（W3-4）：读类工具+态势装配+画像接入+四维自检+warm-up D1。M2 人审写通道（W5-6）：回复 L1+票据+幂等+verify-unknown 实战。M3 内容线与夜整理（W7-8）：发帖+门禁+改稿回流+夜整理。M4 周调参+版本链+回滚演练（W9-10）。M5 授权爬坡（W11+）：L2 类别连续 14 天零事故再议，**爬坡进度由数据决定不由日程决定**。每个 M 出发前先过 --dry；每类新动作先改《规范与约束.md》再动代码。

---

**一句话收束**：认知层提供“像人”的那一半——记忆、人设、审议、自我成长；x-auto-run 提供“操作”的那一半——真实窗口、probe、文件账本、钟与文化。嫁接缝只有一条：**态势报告进（代码装配的世界），ActionProposal 出（模型决定的意图），网关与 executor 在中间守住一切后果。**