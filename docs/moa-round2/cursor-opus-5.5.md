方案已由 cursor-agent（claude-opus-5-5-high，Opus 5.5 1M High，--mode plan 只读模式，耗时约 17 分钟）完整产出，127.5 KB / 1886 行，覆盖全部 16 项要求 + 假设清单 + 待补充接口。我已通读核验：11 条冻结决策全部只细化、未推翻；项目目录零写入（唯一落盘为项目外的临时捕获文件 `%TEMP%\xrunner_plan_opus55.txt`）。以下为方案全文。

---

# X(Twitter) 中文 AI/科技人设号：全自动运营 Agent 技术方案

项目代号 `xar`（x-auto-runner）· v1.0 · 2026-10-22

**说明**：我尝试联网核验 XActions 的接口和 fxtwitter 的字段，请求被拒绝。所以文中涉及第三方接口细节的地方，都基于已有知识设计，并用“假设 A*n*”标出，完整清单见第 17 节。第 1 周的开工任务里包含“契约测试核验全部假设”。所有已冻结决策（①到⑪）只细化落地，不做改动。

---

## 1. 总体架构

### 1.1 分层架构

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ L0 触发层  APScheduler(AsyncIOScheduler, Asia/Shanghai)                  │
│   ├ 基础设施 Cron（数据回收/复盘/自检/调参/备份）                          │
│   ├ 日计划器 DayPlanner：每日 06:00 把"行为类"任务物化为 DateTrigger      │
│   │   （带作息/情绪/抖动/跳过概率，保证发帖间隔≥2.5h）                    │
│   └ 外部触发：面板 control_commands / 审批结果轮询 / STOP 监视器           │
├──────────────────────────────────────────────────────────────────────────┤
│ L1 编排层  LangGraph                                                     │
│   主图 dispatcher → guard → 子图路由                                      │
│   子图：content_prepare / publish / reply_scan / reply_one / browse /     │
│         collect / daily_review / weekly_tune / selfcheck                  │
│   AsyncSqliteSaver(checkpoints.db) + interrupt 审批 + RetryPolicy          │
├──────────────────────────────────────────────────────────────────────────┤
│ L2 执行层                                                                │
│   ├ GuardedXClient（安全守卫装饰器）→ XClientRouter（降级链/熔断）          │
│   │     ├ XActionsAdapter ──MCP stdio──► Node 侧车(XActions+xar扩展工具)   │
│   │     ├ TwikitAdapter（GraphQL+cookie，备选）                            │
│   │     ├ OfficialApiAdapter（按量付费，仅发帖/回复逃生）                   │
│   │     ├ TwitterApiIoAdapter（只读逃生/观察者视角）                        │
│   │     └ FxTwitterClient（免登录只读：自有帖指标/用户粉丝数）              │
│   ├ Humanize 行为引擎（打字/阅读/滚动/浏览路径/作息）                       │
│   ├ 内容管线（选题→草稿→风格化→分句→去AI味门禁→预评分）                   │
│   └ LLM 网关（GLM 分档路由/Prompt 注册表/重试降级）                        │
├──────────────────────────────────────────────────────────────────────────┤
│ L3 记忆层  state.db（自建三层账本）                                       │
│   短期 st_*（当日上下文）｜中期 mt_*（互动者画像+互动历史）｜              │
│   长期 lt_*（人设卡/风格库/案例库/洞察）｜MemoryProvider 外部记忆适配占位   │
├──────────────────────────────────────────────────────────────────────────┤
│ L4 优化层  数据回收(fxtwitter 48h 快照) → 日复盘 → 周调参(参数版本库/A-B/  │
│   连续两周期变差自动回滚) ← 算法知识库 algo_kb（x-algorithm 信号蒸馏）     │
├──────────────────────────────────────────────────────────────────────────┤
│ L5 安全层（横切，所有 X 写操作强制经过）                                    │
│   KillSwitch(STOP 文件) ｜ BudgetGuard(日预算±30%/小时上限/最小间隔) ｜     │
│   PhaseMachine(warm-up→ramp→normal→throttled→red) ｜ ShadowbanMonitor(四维)│
│   ｜ 凭据管理(keyring+Fernet) ｜ 异常熔断(锁号/验证码→自动 STOP)           │
└──────────────────────────────────────────────────────────────────────────┘
              审核旁路：飞书 webhook 卡片 ⇄ FastAPI 面板 ⇄ approval_requests 表
```

为什么这样分层：
- 安全层不是最底下的一层，而是横切层：它以装饰器的形式包住 XClient，任何写操作都绕不过去，这样做是“失败即关闭”（fail-closed）。
- 触发层只负责“什么时候”，编排层负责“做什么”，执行层负责“怎么做得像人”。三者解耦，单独替换任何一层（比如把 XActions 换成 twikit）都不影响其余层。

### 1.2 进程拓扑

```text
Windows 主机（后期迁 Linux VPS）
┌─────────────────────────────── NSSM 服务 xar-core ───────────────────────────────┐
│ Python 3.12 主进程（单 asyncio 事件循环）                                         │
│  ├ APScheduler（任务定义每次启动按 config 重建，不持久化 pickle）                │
│  ├ GraphRunner（LangGraph 执行器，LLM 类任务可并发）                              │
│  ├ XActor（全局唯一"手"：asyncio 优先级队列+锁，串行执行所有浏览器动作，         │
│  │         发帖 > 回复 > 点赞 > 浏览；浏览会话可被抢占）                         │
│  ├ SidecarSupervisor ──spawn/stdio──► Node 20 子进程：sidecar/start.mjs          │
│  │                                     ├ XActions MCP Server（152+ 工具）       │
│  │                                     ├ xar 扩展工具（human_type/scroll/dwell）│
│  │                                     └ Chrome（持久 userDataDir）             │
│  ├ Pollers：审批恢复(15s)、控制指令(5s)、STOP 监视(2s)、心跳文件(30s)            │
│  └ 写库：state.db / checkpoints.db（WAL，主写者）                                │
└──────────────────────────────────────────────────────────────────────────────────┘
┌──────────── NSSM 服务 xar-panel ────────────┐   ┌──── 计划任务 xar-watchdog ────┐
│ FastAPI + Jinja2 + HTMX，127.0.0.1:8710      │   │ 每 5 分钟检查心跳文件年龄，   │
│ 读 state.db；写入仅限：审批决定、开关、       │   │ 超过 3 分钟就 nssm restart 并  │
│ 记忆 CRUD、control_commands                   │   │ 发飞书告警                     │
│ 外网访问经 Tailscale / Cloudflare Tunnel(A11) │   └───────────────────────────────┘
└──────────────────────────────────────────────┘
```

关键取舍：
1. **侧车作为 core 的子进程，走 stdio，不单独做成服务。** MCP 原生支持 stdio，不占端口，生命周期跟 core 绑定，core 退出时浏览器一定一起关掉，不会留下无主 Chrome 继续操作账号。
2. **面板单独一个进程。** 面板崩溃或被攻击都不影响运行中的 agent。两个进程之间只通过 SQLite 表（`approval_requests`、`control_commands`）通信，没有 RPC，迁移简单。
3. **XActor 只有一把“手”，全部串行。** 真人不会同时刷两个页面，单浏览器实例也不允许并发。LLM 生成、fxtwitter 回收这些不碰浏览器的任务可以并发。

---

## 2. 项目目录结构

```text
x-auto-runner/
├─ pyproject.toml                 # uv 管理；Python 3.12
├─ uv.lock
├─ .env.example                   # 只放非敏感占位；真实密钥进 keyring
├─ .gitignore                     # data/ .env sidecar/node_modules *.db browser_profile/
├─ .pre-commit-config.yaml        # ruff + gitleaks（防密钥入库）
├─ README.md
├─ docs/
│  ├─ DECISIONS.md                # 冻结决策①-⑪及后续决策留痕
│  ├─ RUNBOOK.md                  # 运维手册：登录/STOP/回滚/迁移/故障
│  └─ ALGO_SIGNALS.md             # x-algorithm 蒸馏笔记（人读版）
├─ config/
│  ├─ settings.yaml               # 全局：时区、data_dir、账号 handle、面板地址
│  ├─ schedule.yaml               # 第5节时刻表（锚点/抖动/跳过概率）
│  ├─ budget.yaml                 # 硬上限、小时上限、最小间隔、阶段乘数
│  ├─ warmup.yaml                 # 两周 warm-up 分段计划
│  ├─ xclient.yaml                # 适配器链、熔断参数、XActions tool-map
│  ├─ llm.yaml                    # 模型档位映射、超时、并发、降级链
│  ├─ humanize.yaml               # 第8节行为参数分布
│  ├─ review.yaml                 # 审核开关初值、TTL、飞书 webhook 引用名
│  ├─ shadowban.yaml              # 四维检测参数与判定阈值
│  ├─ sources.yaml                # 【占位】内容源列表（待用户补充）
│  └─ strategy_defaults.yaml      # 策略参数注册表与初值（第9节）
├─ persona/                       # 【占位】待用户补充人设语料
│  ├─ persona_card.yaml           # 人设卡模板（第7.1节）
│  ├─ corpus/                     # 人设语料原文（txt/md/json）
│  ├─ style_seeds.yaml            # 风格库种子
│  ├─ kol_targets.yaml            # 主动回复目标账号池
│  └─ humanizer_rules.yaml        # 去AI味规则库（基于 humanizer-zh 扩展）
├─ prompts/                       # Jinja2 + YAML front-matter，带版本号
│  ├─ _partials/persona.md.j2  _partials/style.md.j2  _partials/cases.md.j2
│  ├─ topic/score.md.j2  topic/summarize.md.j2
│  ├─ content/draft_post.md.j2  content/draft_thread.md.j2  content/stylize.md.j2
│  ├─ content/rewrite.md.j2
│  ├─ reply/classify.md.j2  reply/safe.md.j2  reply/interesting.md.j2  reply/bold.md.j2
│  ├─ gate/ai_flavor_judge.md.j2  gate/compliance.md.j2  gate/persona_consistency.md.j2
│  ├─ memory/profile_update.md.j2  memory/case_lesson.md.j2
│  └─ optimize/daily_review.md.j2  optimize/weekly_propose.md.j2
├─ src/xar/
│  ├─ __init__.py
│  ├─ __main__.py                 # python -m xar → cli
│  ├─ cli.py                      # typer: run / panel / init-db / migrate / login /
│  │                              #   selfcheck / backup / restore / export-cookies /
│  │                              #   secrets set|import / replay-thread / dry-run
│  ├─ settings.py                 # pydantic-settings，合并 yaml+env+keyring
│  ├─ runtime/
│  │  ├─ app.py                   # 生命周期：启动顺序/优雅退出/启动恢复
│  │  ├─ actor.py                 # XActor 优先级队列+抢占
│  │  ├─ sidecar.py               # SidecarSupervisor（拉起/健康/退避重启）
│  │  ├─ pollers.py               # 审批恢复/控制指令/心跳
│  │  ├─ logging.py               # structlog JSON + 脱敏
│  │  └─ clock.py                 # 时区/日期工具（统一 Asia/Shanghai）
│  ├─ scheduler/
│  │  ├─ jobs.py                  # 基础设施 cron 注册
│  │  ├─ day_planner.py           # 行为任务物化（作息/情绪/抖动）
│  │  └─ catchup.py               # 基于 job_runs 的漏跑补偿
│  ├─ graphs/
│  │  ├─ state.py                 # TypedDict 状态与 reducer
│  │  ├─ checkpointer.py          # AsyncSqliteSaver 工厂、线程清理
│  │  ├─ main_graph.py            # dispatcher
│  │  ├─ content_prepare.py  publish.py  reply_scan.py  reply_one.py
│  │  ├─ browse.py  collect.py  daily_review.py  weekly_tune.py  selfcheck.py
│  │  ├─ nodes/                   # 可复用节点：guard/approval/record/fail_handler
│  │  └─ errors.py                # Transient/Fatal/Policy 错误分类
│  ├─ xclient/
│  │  ├─ base.py                  # XClient ABC
│  │  ├─ models.py                # Tweet/User/Notification/PostResult/...
│  │  ├─ errors.py                # AuthExpired/RateLimited/AccountLocked/...
│  │  ├─ mcp_bridge.py            # Python MCP 客户端（stdio）
│  │  ├─ xactions_adapter.py
│  │  ├─ twikit_adapter.py
│  │  ├─ official_api_adapter.py
│  │  ├─ twitterapiio_adapter.py
│  │  ├─ fxtwitter.py
│  │  ├─ router.py                # 按操作的降级链+熔断器
│  │  └─ guarded.py               # 安全守卫装饰器（STOP/阶段/预算/间隔/幂等）
│  ├─ humanize/
│  │  ├─ distributions.py         # 截断对数正态/截断正态等
│  │  ├─ typing_plan.py  reading.py  scrolling.py  browse_path.py
│  │  └─ circadian.py             # 作息/情绪/日类型（参考 Pulse/erii）
│  ├─ content/
│  │  ├─ sources/base.py  sources/rss.py  sources/x_seen.py  sources/manual.py
│  │  ├─ topic.py  draft.py  stylize.py
│  │  ├─ splitter.py              # 分句短句节奏 + twitter-text 加权长度
│  │  ├─ gate.py                  # 去AI味门禁（规则层+LLM 判官层）+ 合规
│  │  ├─ prescore.py              # 算法信号预评分
│  │  ├─ dedup.py
│  │  └─ reply_tier.py            # 回复分档决策树
│  ├─ memory/
│  │  ├─ db.py                    # 连接/PRAGMA/迁移执行器
│  │  ├─ short_term.py  mid_term.py  long_term.py
│  │  ├─ retrieval.py             # 组装 MemoryContext
│  │  ├─ embeddings.py            # GLM embedding / 本地备选，numpy 余弦
│  │  ├─ expiry.py                # 过期与蒸馏
│  │  └─ external.py              # 【占位】MemoryProvider：接用户现有记忆系统
│  ├─ optimizer/
│  │  ├─ collector.py  metrics.py  attribution.py
│  │  ├─ daily_review.py  weekly_tuner.py
│  │  ├─ params.py                # 参数注册表/版本/回滚
│  │  ├─ ab.py                    # 分臂/贝叶斯判定
│  │  └─ drift.py                 # 人设漂移检测
│  ├─ algo_kb/
│  │  ├─ signals.yaml             # 信号→设计约束映射（机器读）
│  │  └─ loader.py
│  ├─ safety/
│  │  ├─ killswitch.py  budget.py  phase.py  rate.py
│  │  ├─ shadowban.py             # 四维检测
│  │  ├─ anomaly.py               # 锁号/验证码/失败率/粉丝骤降
│  │  └─ credentials.py           # keyring + Fernet
│  ├─ review/
│  │  ├─ feishu.py                # 签名、卡片构建、发送
│  │  ├─ cards.py                 # 卡片模板
│  │  ├─ approval.py              # 申请创建/TTL/决定落库
│  │  ├─ tokens.py                # HMAC 一次性链接令牌
│  │  └─ policy.py                # content/reply 双开关与升降级
│  └─ llm/
│     ├─ client.py                # AsyncOpenAI(base_url=智谱)
│     ├─ router.py                # 档位路由与降级
│     ├─ prompts.py               # 注册表/渲染/版本哈希
│     ├─ json_guard.py            # pydantic 校验 + json_repair
│     └─ usage.py                 # 用量记账
├─ panel/
│  ├─ app.py
│  ├─ auth.py
│  ├─ routers/ dashboard.py approvals.py switches.py posts.py interactors.py
│  │          memory.py params.py shadowban.py threads.py reports.py jobs.py
│  ├─ templates/                  # Jinja2 + HTMX
│  └─ static/                     # htmx.min.js chart.min.js pico.css
├─ sidecar/
│  ├─ package.json                # xactions 固定到 commit；puppeteer-extra-stealth
│  ├─ start.mjs                   # 启动 XActions MCP(stdio) + 注册 xar 扩展工具
│  ├─ xar-tools/ human_type.mjs human_scroll.mjs dwell.mjs export_cookies.mjs
│  │            probe.mjs         # 健康探针（是否登录/是否出现验证码）
│  └─ tool-map.json               # 逻辑操作 → MCP 工具名/参数映射（A1）
├─ migrations/
│  ├─ 0001_core.sql  0002_memory.sql  0003_optimizer.sql  0004_safety_review.sql
├─ deploy/
│  ├─ windows/ install_services.ps1 uninstall_services.ps1 watchdog.ps1 power_setup.ps1
│  ├─ linux/ xar-core.service xar-panel.service xar-backup.timer setup_vps.sh
│  └─ backup/ backup.py restore.py
├─ tests/
│  ├─ unit/                       # splitter/gate/budget/tier/params/ab
│  ├─ contract/                   # XClient 契约测试（同一套用例跑所有适配器）
│  ├─ graphs/                     # 图路由/中断恢复/重启恢复
│  ├─ simulation/                 # 4 周合成数据跑优化器
│  └─ fixtures/                   # fxtwitter 样本 JSON、通知样本、AI味样本集
└─ data/                          # 运行时（gitignore）
   ├─ state.db  checkpoints.db
   ├─ browser_profile/            # Chrome userDataDir（不可跨 OS 搬迁，见14.3）
   ├─ cookies.enc                 # Fernet 加密 cookie 导出（迁移/twikit 用）
   ├─ STOP  PAUSE_WRITES          # kill switch 文件（存在即生效）
   ├─ heartbeat
   ├─ logs/  backups/  media/
```

技术栈：Python 3.12、uv、langgraph 1.x + langgraph-checkpoint-sqlite（A17）、apscheduler 3.11.x、fastapi、uvicorn、jinja2、httpx、openai、tenacity、pydantic v2、aiosqlite、numpy、json-repair、twikit、mcp（Python SDK）、keyring、cryptography、twitter-text-parser、typer、structlog、pytest-asyncio。Node 端：Node 20/22 LTS、xactions（固定 commit，并 fork 到自己的 GitHub 兜底）、puppeteer、puppeteer-extra-plugin-stealth、@modelcontextprotocol/sdk。

---

## 3. XClient 统一抽象接口

### 3.1 数据模型（`xclient/models.py`）

```python
class Tweet(BaseModel):
    id: str
    author_id: str | None
    author_handle: str
    author_name: str | None = None
    author_followers: int | None = None
    text: str
    created_at: datetime
    lang: str | None = None
    in_reply_to_id: str | None = None
    conversation_id: str | None = None
    quoted_id: str | None = None
    metrics: TweetMetrics | None = None      # views/likes/replies/retweets/quotes/bookmarks
    media: list[MediaInfo] = []               # type/url/duration_sec
    url: str | None = None
    raw: dict | None = None                   # 原始 payload 便于排障

class Notification(BaseModel):
    id: str
    kind: Literal["reply", "mention", "quote", "like", "follow", "retweet", "other"]
    actor: UserBrief
    tweet: Tweet | None
    our_tweet_id: str | None
    created_at: datetime

class PostResult(BaseModel):
    ok: bool
    tweet_id: str | None
    url: str | None
    adapter: str
    verified: bool               # 是否经回读确认已发出
    latency_ms: int

class SelfStats(BaseModel):
    user_id: str; handle: str
    followers: int; following: int; tweets: int
    fetched_at: datetime; source: str

class BrowsePlan(BaseModel):     # 由 humanize 生成，交适配器执行
    surface: Literal["for_you", "following", "notifications", "search", "profile"]
    query: str | None = None
    duration_s: int
    scroll_profile: dict          # 滚动分布参数
    dwell_rule: dict              # 停留规则（相关度阈值→全文阅读）
    like_policy: dict             # 会话内点赞概率/上限/最小间隔
    max_tweets: int

class BrowseReport(BaseModel):
    seen: list[SeenTweet]         # 含 dwell_ms, liked, opened_detail
    likes_done: int
    duration_s: int
    anomalies: list[str]          # 验证码/登录墙/空页面
```

### 3.2 抽象基类（`xclient/base.py`）

```python
class Capability(StrEnum):
    POST = "post"; REPLY = "reply"; LIKE = "like"; BROWSE = "browse"
    TIMELINE = "timeline"; NOTIFICATIONS = "notifications"; SEARCH = "search"
    TYPEAHEAD = "typeahead"; GET_TWEET = "get_tweet"; REPLIES = "replies"
    SELF_STATS = "self_stats"; SELF_TWEETS = "self_tweets"; DELETE = "delete"

class XClient(ABC):
    name: ClassVar[str]
    capabilities: set[Capability]

    # 生命周期
    @abstractmethod
    async def start(self) -> None: ...
    @abstractmethod
    async def close(self) -> None: ...
    @abstractmethod
    async def health(self) -> HealthStatus: ...          # logged_in / challenge / degraded
    @abstractmethod
    async def whoami(self) -> UserBrief: ...

    # 写操作：必须带幂等键；typing_plan 由 humanize 生成（不支持的适配器忽略）
    @abstractmethod
    async def post(self, text: str, *, idem_key: str,
                   media: list[MediaRef] | None = None,
                   thread: list[str] | None = None,
                   typing_plan: TypingPlan | None = None) -> PostResult: ...
    @abstractmethod
    async def reply(self, tweet_id: str, text: str, *, idem_key: str,
                    typing_plan: TypingPlan | None = None) -> PostResult: ...
    @abstractmethod
    async def like(self, tweet_id: str, *, idem_key: str) -> ActionResult: ...
    async def delete(self, tweet_id: str, *, idem_key: str) -> ActionResult:
        raise UnsupportedOperation

    # 浏览（拟人行为在侧车内执行）
    @abstractmethod
    async def browse(self, plan: BrowsePlan) -> BrowseReport: ...

    # 读操作
    @abstractmethod
    async def get_timeline(self, kind: TimelineKind, *, query: str | None = None,
                           limit: int = 40, cursor: str | None = None) -> Page[Tweet]: ...
    @abstractmethod
    async def get_notifications(self, *, kinds: set[str] = {"reply", "mention", "quote"},
                                since_id: str | None = None, limit: int = 50) -> list[Notification]: ...
    @abstractmethod
    async def get_tweet(self, tweet_id: str) -> Tweet: ...
    async def get_replies(self, tweet_id: str, *, limit: int = 50) -> list[Tweet]:
        raise UnsupportedOperation
    async def search(self, query: str, *, mode: Literal["latest", "top"] = "latest",
                     limit: int = 20) -> list[Tweet]:
        raise UnsupportedOperation
    async def search_typeahead(self, q: str) -> list[UserBrief]:
        raise UnsupportedOperation

    # 自身数据
    @abstractmethod
    async def get_self_stats(self) -> SelfStats: ...
    @abstractmethod
    async def get_self_tweets(self, *, limit: int = 20, include_replies: bool = False) -> list[Tweet]: ...
```

错误分类（`xclient/errors.py`）决定后面的降级行为：

| 错误类 | 典型来源 | 路由器行为 |
|---|---|---|
| `TransientError` | 超时、5xx、DOM 未就绪 | 同适配器指数退避重试，最多 2 次 |
| `UnsupportedOperation` / `CapabilityMissing` | tool-map 缺工具 | 立刻降级到下一适配器 |
| `SidecarDown` | Node 进程退出 | 读操作降级；写操作等侧车重启（最长 120s），之后再降级 |
| `RateLimited` | 429、“操作过于频繁”提示 | **不降级**，全局冷却 30–120 分钟，跨适配器换号硬冲会更可疑 |
| `AuthExpired` | 登录墙、cookie 失效 | 暂停写操作并告警“需要重新登录”；读操作降级到 twitterapi.io |
| `AccountLocked` / `ChallengeRequired` / `Suspended` | 验证码、锁号页 | **写 STOP 文件**，红色告警，全面停机 |
| `ContentRejected` | 重复内容、长度超限 | 不重试，标记草稿失败 |
| `UnknownWriteOutcome` | 写操作超时、结果不明 | 先回读校验，禁止盲目重试（见 3.5） |

### 3.3 XActions 适配器（MCP stdio 桥接）

**桥接方式**：core 用 Python `mcp` SDK 的 `stdio_client` 拉起 `node sidecar/start.mjs`。`start.mjs` 做两件事：①启动 XActions 的 MCP Server，transport 用 stdio（A3）；②在同一个 MCP Server 上注册 xar 扩展工具（human_type、human_scroll、dwell、probe、export_cookies），和 XActions 共享同一个 Puppeteer Browser 实例（A2）。

```python
# xclient/mcp_bridge.py（要点）
class McpBridge:
    async def start(self):
        params = StdioServerParameters(
            command=settings.sidecar.node_bin, args=["sidecar/start.mjs"],
            env={"XAR_PROFILE_DIR": str(settings.data_dir / "browser_profile"),
                 "XAR_HEADLESS": settings.sidecar.headless,   # 服务模式 "new"
                 "XAR_TZ": "Asia/Shanghai", "XAR_LANG": "zh-CN"})
        self._cm = stdio_client(params)
        read, write = await self._cm.__aenter__()
        self.session = ClientSession(read, write); await self.session.__aenter__()
        await self.session.initialize()
        tools = {t.name for t in (await self.session.list_tools()).tools}
        self.capabilities = ToolMap.load("sidecar/tool-map.json").validate(tools)  # 缺失即降级

    async def call(self, logical_op: str, args: dict, timeout: float) -> dict:
        tool, mapped = self.toolmap.resolve(logical_op, args)
        res = await asyncio.wait_for(self.session.call_tool(tool, mapped), timeout)
        if res.isError: raise classify_mcp_error(res)
        return parse_content(res.content)          # TextContent(JSON) → dict
```

`tool-map.json` 示例（工具名是占位，第 1 周用 `list_tools()` 实测后回填，A1）：

```json
{
  "post":          {"tool": "x_post_tweet",      "args": {"text": "$text"}, "via": "xar.human_type"},
  "reply":         {"tool": "x_reply",           "args": {"tweetId": "$tweet_id", "text": "$text"}, "via": "xar.human_type"},
  "like":          {"tool": "x_like",            "args": {"tweetId": "$tweet_id"}},
  "timeline":      {"tool": "x_get_timeline",    "args": {"type": "$kind", "count": "$limit"}},
  "notifications": {"tool": "x_get_notifications","args": {"count": "$limit"}},
  "get_tweet":     {"tool": "x_get_tweet",       "args": {"tweetId": "$tweet_id"}},
  "replies":       {"tool": "x_get_replies",     "args": {"tweetId": "$tweet_id"}},
  "search":        {"tool": "x_search",          "args": {"query": "$query", "mode": "$mode"}},
  "self_stats":    {"tool": "x_get_profile",     "args": {"username": "$self"}},
  "browse":        {"tool": "xar_browse_session","args": {"plan": "$plan"}},
  "probe":         {"tool": "xar_probe",         "args": {}}
}
```

**为什么写操作要走 `xar.human_type`**：XActions 原生的发帖工具多半是直接填值，或者用固定延迟打字。真人感要求按输入法分块上屏、句末停顿、偶尔删改（见第 8 节），所以写操作分成三步：xar 扩展工具负责“打开编辑框、拟人打字、停顿检查”，然后调用 XActions 的提交动作，或者由扩展工具自己点“发布”。如果 A2 不成立（XActions 无法共享浏览器实例），就退一步：侧车自己管理 Puppeteer，把 XActions 当选择器和动作函数库来 import。

**侧车监管**：SidecarSupervisor 每 60 秒调一次 `probe`（是否登录、有没有验证码或锁号页、页面是否卡死）。进程退出后按 5s、15s、45s、120s 退避重启；10 分钟内重启超过 4 次，就熔断 XActions 适配器 30 分钟并告警。

### 3.4 twikit 适配器（备选实现）

- **会话来源**：由侧车的 `export_cookies` 从当前 Chrome 会话导出 `auth_token`、`ct0` 等 cookie，Fernet 加密写入 `data/cookies.enc`；twikit 加载时解密，调 `client.set_cookies()`。不在 twikit 里用账号密码登录，避免两套登录行为触发验证。
- **用途定位**：默认只读（时间线、通知、搜索、推文详情、用户信息）。写操作只在 XActions 不可用、且 `xclient.yaml: twikit.allow_write: true` 时启用。理由是 twikit 的 HTTP 指纹和浏览器不同，同一个 cookie 在两种指纹之间频繁切换写操作，风险更高。
- **同步与异步**：twikit 本身是 async 接口，直接 await；所有调用套一层 `asyncio.wait_for` 超时。
- **拟人**：twikit 不打字，但守卫层照样执行“阅读停留”和“写前等待”（纯时间延迟），节奏不变。

### 3.5 逃生通道与降级链（`xclient/router.py`）

```yaml
# config/xclient.yaml
chains:
  post:          [xactions, twikit?, official_api$]   # ?=需 allow_write  $=需 allow_paid
  reply:         [xactions, twikit?, official_api$]
  like:          [xactions, twikit?]                   # 点赞不走付费通道，不可用就跳过
  browse:        [xactions]                            # 只有浏览器能"浏览"，失败就跳过本次会话
  timeline:      [xactions, twikit, twitterapi_io$]
  notifications: [xactions, twikit, twitterapi_io$]
  search:        [xactions, twikit, twitterapi_io$]
  get_tweet:     [fxtwitter, xactions, twikit]
  self_tweets:   [xactions, twikit, twitterapi_io$]
  self_stats:    [fxtwitter, xactions, twikit]
circuit_breaker: {fail_threshold: 3, window: 10, error_rate: 0.5, open_minutes: 30, half_open_probes: 1}
paid:
  official_api: {enabled: false, monthly_cap_usd: 5.0}      # $0.015/帖
  twitterapi_io: {enabled: true, monthly_cap_usd: 5.0}       # 观察者+读逃生（A21）
```

路由算法（伪代码）：

```python
async def execute(op, **kw):
    for adapter in chain(op):
        if not adapter.has(op) or breaker(adapter, op).is_open: continue
        if adapter.paid and not paid_budget.allow(adapter): continue
        try:
            return await adapter.call(op, **kw)
        except (UnsupportedOperation, CapabilityMissing, SidecarDown) as e:
            breaker.record_fail(); continue            # 降级
        except TransientError:
            breaker.record_fail(); continue            # 适配器内已重试过
        except RateLimited:
            cooldown.global_set(op_class(op)); raise   # 不降级
        except AuthExpired:
            if is_write(op): raise                     # 写不降级，等重新登录
            continue
        except (AccountLocked, ChallengeRequired, Suspended):
            killswitch.trip(reason=...); raise
    raise AllAdaptersFailed(op)
```

**写操作幂等**：`GuardedXClient` 在写操作前先往 `action_log` 插一条 `idem_key`（UNIQUE，状态 pending）。幂等键由 `thread_id + 节点名 + 内容哈希` 生成。如果出现 `UnknownWriteOutcome`（超时或结果不明），先用 `get_self_tweets(limit=5)` 加 fxtwitter 回读，按文本哈希比对：查到了就补记成功，查不到才重试。这样从根上避免同一帖发两次（重复内容本身也是风控信号）。

**手动切换**：面板“适配器”页可以强制设置主适配器、关闭某个适配器、打开付费通道，写入 `system_kv.xclient_overrides`，路由器每次调用都会读取。

---

## 4. LangGraph 图设计

### 4.1 通用约定

- **线程 ID 规范**：`{graph}:{本地日期}:{槽位或对象ID}`，例如 `prepare:2026-10-22:S3`、`reply:1849…`、`tune:2026-W43`。每次运行一个独立线程，可以单独审批、单独恢复。
- **`graph_runs` 登记表**：记录每个线程的状态（running、interrupted、done、failed、expired），供面板展示和启动恢复使用。
- **主状态**（`graphs/state.py`）：

```python
class RunState(TypedDict, total=False):
    trigger: Trigger                     # kind, slot, scheduled_at, payload
    day: str
    phase: Phase                         # warmup_a/b/c, ramp, normal, throttled, red
    guard: GuardVerdict                  # allowed_ops, reasons
    params: dict                         # 当前生效策略参数快照(版本号+AB臂)
    memory_ctx: MemoryContext | None
    topic: TopicCandidate | None
    candidates: list[Draft]
    chosen: Draft | None
    gate: GateReport | None
    rewrite_count: int
    regen_count: int
    approval_id: str | None
    decision: ReviewDecision | None      # approve/edit/regenerate/reject/expire
    result: PostResult | None
    errors: Annotated[list[str], operator.add]
```

### 4.2 主图（dispatcher）

```text
START → guard ──(STOP/PAUSE_WRITES 且需写)──────────────────► log_skip → END
          │──(静默时段/阶段不允许/预算耗尽/RED)─────────────► log_skip → END
          └─ route_by_kind ─┬─ content_prepare（子图）
                            ├─ publish（子图）
                            ├─ reply_scan（子图）
                            ├─ browse（子图）
                            ├─ collect（子图）
                            ├─ daily_review（子图）
                            ├─ weekly_tune（子图）
                            └─ selfcheck（子图）
                            → finalize（写 graph_runs/st_events）→ END
任一子图抛出非重试错误 → fail_handler（落库+告警+必要时 trip STOP）→ END
```

`guard` 节点做六项检查：kill switch、当前阶段允许的操作集、当日预算余量、静默时段、ShadowbanMonitor 等级、XClient 健康。只有读操作的任务（collect、review）不受写开关影响。

### 4.3 content_prepare 子图（发帖前 120 分钟运行，产出“待发队列”）

把“生成”和“发布”拆开，是为了让审批延迟不影响发帖时刻：内容提前备好，到点由 publish 子图取用；没有待发内容就跳过这个槽位，不硬发。

```text
load_ctx → pick_topic ──(无合格选题)──► record_skip → END
              │
              ▼
        retrieve_memory → draft_candidates(N=3, pro档) → stylize → rhythm_split
              ▼
            gate ──(fail & rewrite<2)──► rewrite(fast档, 带门禁反馈) → rhythm_split → gate
              │──(fail & rewrite≥2)──► discard(写失败样本) → END
              ▼(pass)
        prescore_select → dedup ──(重复 & 未重选)──► pick_topic
              ▼
        review_route ──(auto 且低风险)──► enqueue_ready → END
              ▼(需审核)
        create_approval（落库+发飞书卡片，幂等 upsert）
              ▼
        human_review【interrupt】
              ▼
        apply_decision ─┬ approve/edit → enqueue_ready → END
                        ├ regenerate(regen<2) → draft_candidates
                        └ reject/expire → record_reject(写入失败案例候选) → END
```

### 4.4 publish 子图（到槽位时刻触发）

```text
pick_ready（按新鲜度/优先级；资讯类超过 fresh_until 作废）──(空)──► record_skip → END
   → final_guard（再次检查 STOP/预算/距上帖≥2.5h，不满足就顺延到下一可用时刻或丢弃）
   → acquire_actor(priority=HIGH, 抢占浏览)
   → pre_browse(30–120s 浏览热身) → publish(GuardedXClient.post, 拟人打字)
   → verify（回读确认+写 posts）
   → link_followup（如有外链：1–3 分钟后自回复贴链接，A8）
   → linger(1–4 分钟浏览/看自己帖下评论) → schedule_collect（登记快照时间点）→ END
```

### 4.5 reply_scan 与 reply_one

reply_scan（每 30 分钟左右一次）：

```text
fetch_notifications(since_id) → upsert_inbound（写 mt_interactions/mt_interactors）
  → filter（已处理/黑名单/机器人/垃圾/敌意升级）
  → score_and_pick（价值×时效×关系，取 ≤ min(小时余量, 3) 条）
  → spawn_reply_tasks（每条写 reply_tasks，并起一个独立线程 reply:{tweet_id}，
                      计划执行时刻 = 通知时间 + 拟人回复延迟）→ END
```

主动回复（outbound）的候选来自 browse 子图标记的 `st_seen_tweets.candidate='outbound_reply'`，同样生成 reply_task。

reply_one（每条回复一个线程，独立审批）：

```text
load_ctx（对方画像+与其最近5次互动+线程上下文+相似回复案例）
  → classify（意图/情绪/风险，flash档）
  → tier_decide（决策树，见7.6）─┬ skip → record → END
                                 ├ like_only → act_like → record → END
                                 └ safe/interesting/bold → generate(2候选) → gate(rewrite≤1)
  → review_route ──(auto)──► act
       └─(需审核) create_approval → human_review【interrupt】→ apply_decision
  → act（final_guard → 阅读原帖与热评停留 → 拟人打字回复 → 回读确认；可附带点赞）
  → record（mt_interactions/out + 画像更新计数；每满3次触发画像重写）→ END
```

### 4.6 其余子图

- **browse**：`plan_session(humanize) → acquire_actor(LOW, 可抢占) → run_session(侧车 xar_browse_session) → ingest_seen（写 st_seen_tweets，计算相关度，标记主动回复和选题候选）→ log → END`。
- **collect**：`due_snapshots（查询到期的 1/3/6/12/24/48h 快照）→ fetch_fx（fxtwitter 并发数 1）→ write_metrics → follower_snapshot → END`。
- **daily_review**：`aggregate → score_posts → reply_tier_stats → extract_cases(LLM) → insights(LLM) → update_style_perf → distill_short_term → report_card(飞书) → END`。
- **weekly_tune**：`weekly_kpi → rollback_check ─(触发)→ do_rollback → notify → END`；否则 `→ ab_conclude → propose(≤3) → create_approval → human_review【interrupt】→ apply_version → start_ab → notify → END`。
- **selfcheck**：`run_4_dimensions → aggregate_level → apply_throttle（改 phase、throttle_level）→ notify → END`。

### 4.7 SQLite checkpointer 配置

```python
# graphs/checkpointer.py
async def make_saver(path: Path) -> AsyncSqliteSaver:
    conn = await aiosqlite.connect(path, timeout=30)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return saver

saver = await make_saver(settings.data_dir / "checkpoints.db")
main_graph = build_main().compile(checkpointer=saver)        # 子图作为节点时继承父检查点
reply_one  = build_reply_one().compile(checkpointer=saver)   # 独立线程的顶层图
cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 60}
```

- 检查点放独立的 `checkpoints.db`，和业务账本 `state.db` 分开，避免 checkpoint 高频写入跟业务写锁竞争，而且可以单独清理。
- **清理**：每周日 04:30，删除状态为 done/failed/expired 且超过 14 天的线程检查点（按 thread_id 删 `checkpoints`、`writes` 表），然后 `VACUUM`。

### 4.8 interrupt 审批与恢复流程

```python
# nodes/approval.py
async def create_approval(state):              # 独立节点：有副作用，必须幂等
    req_id = f"{state['trigger'].kind}-{thread_id()}-{state.get('regen_count',0)}"
    await approvals.upsert(req_id, payload=..., ttl=review_cfg.ttl(kind))
    await feishu.send_once(req_id, cards.review_card(...))   # 按 req_id 去重发送
    return {"approval_id": req_id}

def human_review(state):
    decision = interrupt({"approval_id": state["approval_id"]})   # 恢复时整个节点会重跑
    return {"decision": ReviewDecision.model_validate(decision)}
```

**要点**：节点恢复时会从头重新执行，所以“落库加发卡片”必须放在 interrupt 之前的独立节点里，`human_review` 节点内除了 interrupt 不放任何副作用。

恢复流程：
1. 面板或飞书链接上的审批决定写入 `approval_requests.status = approved/edited/rejected/regenerate`，同时写 `decision_json`。
2. core 的审批轮询（每 15 秒）扫描已决定但 `resumed_at IS NULL` 的记录，执行：

```python
await graph.ainvoke(Command(resume=req.decision_json),
                    config={"configurable": {"thread_id": req.thread_id}})
```

然后写入 `resumed_at`。
3. 超时：轮询同时检查 `expires_at < now` 且仍为 pending 的记录，标记 expired，用 `{"action":"expire"}` 恢复，图走到 reject 分支。TTL 取值：content 为“槽位时刻 + 60 分钟”；reply 为 120 分钟（回复时效性强，过期就不回了）；weekly_tune 为 48 小时，超时视为拒绝、保持现版本。
4. 重启安全：interrupt 状态在 SQLite 里，进程重启后只要轮询还在跑，审批照样能恢复。

### 4.9 错误重试策略

| 层级 | 策略 |
|---|---|
| LLM 节点 | `RetryPolicy(max_attempts=3, initial_interval=2, backoff_factor=3, jitter=True, retry_on=TransientLLMError)`，LLM 网关内部另有档位降级（第 13 节） |
| fxtwitter / 读节点 | `RetryPolicy(max_attempts=4, initial_interval=5, backoff_factor=2)` |
| 写操作节点（post/reply/like） | 图级 `max_attempts=1`，重试只在 XClient 内部“回读校验后”进行，防止重复发帖 |
| 非重试错误 | 写入 `errors`，条件边进 `fail_handler`：落 `graph_runs.status=failed`，按错误类告警；锁号类直接 trip STOP |
| 启动恢复 | `runtime/app.py` 启动时扫描 `graph_runs.status='running'`：最后节点在写操作之前的，`ainvoke(None, cfg)` 从检查点续跑；最后节点就是写操作的，先查 `action_log`，再回读确认，再决定标记完成还是续跑；时效已过（比如槽位过去超过 90 分钟）的标记 expired |
| 面板手动 | “从上一检查点重跑”、“放弃线程”两个按钮，经 `control_commands` 执行 |

---

## 5. APScheduler 触发计划表

### 5.1 实现方式

- `AsyncIOScheduler(timezone="Asia/Shanghai", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600})`，jobstore 用 MemoryJobStore。每次启动按 `schedule.yaml` 重建任务，规避 pickle 和模块路径在跨机迁移时的兼容问题。漏跑补偿由 `job_runs` 表加 `catchup.py` 完成：启动时 collect、review、tune、backup 这类关键任务如果错过了，就补跑一次；行为类任务不补跑，错过就算了，真人也会错过。
- **两类任务**：
  - **基础设施类**：直接用 `CronTrigger(..., jitter=J)`。APScheduler 3.x 的 jitter 语义第 1 周实测确认（A6）。
  - **行为类**（发帖、浏览、回复扫描）：由 DayPlanner 每天 06:00 按“锚点 + 截断正态抖动”物化成 `DateTrigger`。这样能统一施加作息、情绪、跳过概率和最小间隔约束，分布也比均匀抖动更像人。下表里的 cron 是锚点。

### 5.2 全天时刻表（工作日）

| 任务 | 锚点 cron | 抖动 | 说明与约束 |
|---|---|---|---|
| day_planner | `0 6 * * *` | 0 | 生成当日画像（日类型、情绪、精力、当日预算、物化时刻表），写 `st_day_context` |
| content_prepare S1 | `20 6 * * *` | ±10m | 为 S1 备稿（槽位前 120 分钟） |
| publish S1 | `20 8 * * *` | σ=10m，截断 ±25m | 早通勤 |
| content_prepare S2 | `10 10 * * *` | ±10m | |
| publish S2 | `10 12 * * *` | σ=8m，±20m | 午休 |
| content_prepare S3 | `30 16 * * *` | ±10m | |
| publish S3 | `30 18 * * *` | σ=10m，±25m | 晚通勤 |
| content_prepare S4 | `50 19 * * *` | ±10m | |
| publish S4 | `50 21 * * *` | σ=8m，±20m | 晚高峰，优先保留 |
| reply_scan | `*/30 7-23 * * *` 和 `0,30 0 * * *` | ±10m | 07:00–00:30；单次最多选 3 条；night_owl 日延到 01:30 |
| browse B1 | `45 7 * * *` | ±20m | 跳过概率 0.20 |
| browse B2 | `30 10 * * *` | ±30m | 跳过概率 0.25 |
| browse B3 | `50 12 * * *` | ±15m | 跳过概率 0.15 |
| browse B4 | `0 16 * * *` | ±30m | 跳过概率 0.30 |
| browse B5 | `15 20 * * *` | ±20m | 跳过概率 0.10 |
| browse B6 | `30 22 * * *` | ±20m | 跳过概率 0.15；点赞和主动回复主要发生在浏览会话内 |
| collect_metrics | `7 * * * *` | ±5m | 处理到期快照（1/3/6/12/24/48h） |
| follower_snapshot | `3 * * * *` | ±4m | fxtwitter 用户端点，用于粉丝转化归因 |
| collect_full | `10 3 * * *` | ±10m | 夜间补齐 72 小时内全部帖子 |
| daily_review | `40 0 * * *` | ±10m | 复盘对象：过去 24 小时内满 24h 龄的帖子，加 48h 终值更新 |
| memory_distill | `10 1 * * *` | ±10m | 画像重写、短期记忆蒸馏、风格分更新 |
| shadowban_check | `15 10 * * *` | ±60m | 每日一次随机时刻；YELLOW/RED 期间加跑 `15 22 * * *` |
| session_health | `0 */6 * * *` | ±15m | 登录态、cookie 导出刷新（供 twikit 使用） |
| weekly_tune | `30 1 * * 1` | ±10m | 周一凌晨：周 KPI、回滚检查、A/B 判定、提案 |
| persona_drift | `0 2 * * 1` | ±10m | 人设漂移检测（并入周报） |
| backup | `0 4 * * *` | ±10m | SQLite 在线备份，加密 cookie 一并备份 |
| expiry_purge | `30 4 * * *` | ±5m | 三层记忆过期清理 |
| checkpoint_purge | `30 4 * * 0` | ±5m | 清理 14 天前已完成线程 |
| algo_kb_watch | `0 3 1 * *` | 0 | 每月检查 x-algorithm 仓库更新，提醒人工重新蒸馏 |
| 审批恢复轮询 | interval 15s | 0 | 常驻 asyncio 任务 |
| 控制指令轮询 | interval 5s | 0 | |
| STOP 监视 | interval 2s | 0 | 不经 APScheduler，独立 asyncio 任务 |
| 侧车探针 | interval 60s | 0 | |

**最小间隔校验**（worst case）：S1 最晚 08:45 到 S2 最早 11:50，间隔 3 小时 05 分；S2 最晚 12:30 到 S3 最早 18:05；S3 最晚 18:55 到 S4 最早 21:30，间隔 2 小时 35 分。全部满足 ≥2.5h。publish 子图的 `final_guard` 再兜底一次。

**日计划器规则**：
- **槽位选择**：当日发帖上限 cap 小于 4 时，按权重保留槽位（S4 0.95、S3 0.85、S2 0.7、S1 0.6），抽样不放回。
- **日类型**：normal（0.70）、lazy（0.10，各类预算乘 0.6，浏览会话减 2 个）、night_owl（0.10，最后活动顺延 60–90 分钟，次日起床晚 30 分钟）、busy（0.10，白天浏览减少、晚上集中）。周六日所有锚点后移 40 分钟，B2/B4 跳过概率加 0.15。
- **静默时段**：01:00–07:00（night_owl 日从 01:30 开始），只允许 collect 和备份这类不碰浏览器的任务。

---

## 6. 三层记忆 SQLite Schema

**约定**：
- 时间戳 `ts` 用 INTEGER（UTC 秒）；`day` 用 TEXT（本地日期 YYYY-MM-DD）。
- 向量以 float32 BLOB 存储，用 numpy 暴力余弦检索。规模在数万条以内时延迟低于 50ms，零扩展依赖，跨 OS 可移植；`sqlite-vec` 作为后续可选项。
- 所有连接统一设置：

```sql
PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
```

### 6.1 元数据

```sql
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at INTEGER NOT NULL);
CREATE TABLE system_kv(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL);
-- phase / phase_started_day / auto_content_mode / auto_reply_mode / throttle_level /
-- self_user_id / self_handle / notif_since_id / xclient_overrides / warmup_start_day
```

### 6.2 短期：当日上下文（st_*）

```sql
CREATE TABLE st_day_context(
  day TEXT PRIMARY KEY,
  phase TEXT NOT NULL,
  day_type TEXT NOT NULL,             -- normal/lazy/night_owl/busy/weekend
  mood TEXT, energy REAL,             -- energy∈[0.6,1.0] 缩放会话时长与数量
  caps_json TEXT NOT NULL,            -- {"post":3,"reply":12,"like":24}
  plan_json TEXT NOT NULL,            -- 物化时刻表 [{kind,slot,at}]
  summary TEXT,                       -- 日终摘要（daily_review 写入）
  created_at INTEGER NOT NULL
);

CREATE TABLE st_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL, day TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- post/reply/like/browse/hot_seen/topic/alert/review/approval
  ref_id TEXT, summary TEXT NOT NULL, payload_json TEXT
);
CREATE INDEX idx_st_events_day_kind ON st_events(day, kind, ts);

CREATE TABLE st_seen_tweets(
  tweet_id TEXT PRIMARY KEY,
  day TEXT NOT NULL, seen_at INTEGER NOT NULL,
  source TEXT NOT NULL,               -- for_you/following/search/notif/profile
  author_id TEXT, author_handle TEXT, author_followers INTEGER,
  text TEXT, tweet_created_at INTEGER, metrics_json TEXT,
  dwell_ms INTEGER, liked INTEGER NOT NULL DEFAULT 0, opened_detail INTEGER NOT NULL DEFAULT 0,
  relevance REAL, embedding BLOB,
  candidate TEXT NOT NULL DEFAULT 'none',   -- none/outbound_reply/topic
  consumed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_seen_day_rel ON st_seen_tweets(day, relevance DESC);
CREATE INDEX idx_seen_candidate ON st_seen_tweets(candidate, consumed, seen_at DESC);
CREATE INDEX idx_seen_author ON st_seen_tweets(author_id, seen_at DESC);

CREATE TABLE st_topic_pool(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  source TEXT NOT NULL, source_url TEXT, title TEXT NOT NULL, summary TEXT,
  pillar TEXT,                        -- 内容支柱
  published_at INTEGER, fresh_until INTEGER,
  heat REAL, relevance REAL, novelty REAL, score REAL,
  embedding BLOB,
  status TEXT NOT NULL DEFAULT 'new', -- new/drafting/used/discarded/expired
  created_at INTEGER NOT NULL,
  UNIQUE(source_url)
);
CREATE INDEX idx_topic_status_score ON st_topic_pool(status, score DESC);
```

### 6.3 中期：互动者画像 + 互动历史（mt_*）

```sql
CREATE TABLE mt_interactors(
  user_id TEXT PRIMARY KEY,
  handle TEXT NOT NULL, display_name TEXT, bio TEXT,
  followers INTEGER, following INTEGER, verified INTEGER, account_created_at INTEGER,
  relationship TEXT NOT NULL DEFAULT 'stranger',
      -- stranger/acquaintance(≥2次)/regular(≥6次且近30天活跃)/kol/target_kol/hostile/blocked
  is_follower INTEGER NOT NULL DEFAULT 0, we_follow INTEGER NOT NULL DEFAULT 0,
  inbound_count INTEGER NOT NULL DEFAULT 0, outbound_count INTEGER NOT NULL DEFAULT 0,
  last_inbound_at INTEGER, last_outbound_at INTEGER,
  first_seen_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
  sentiment_ema REAL NOT NULL DEFAULT 0,     -- [-1,1]，α=0.3
  toxicity_ema REAL NOT NULL DEFAULT 0, bot_score REAL,
  topics_json TEXT,                          -- 兴趣标签 ["agent","开源","芯片"]
  tier_stats_json TEXT,                      -- {"safe":{"n":5,"pos":3},"interesting":{...}}
  profile_summary TEXT,                      -- LLM 画像 ≤200字
  profile_version INTEGER NOT NULL DEFAULT 0, profile_updated_at INTEGER,
  pending_profile_updates INTEGER NOT NULL DEFAULT 0,
  notes TEXT                                  -- 人工备注（面板可编辑）
);
CREATE INDEX idx_inter_handle ON mt_interactors(handle);
CREATE INDEX idx_inter_rel_last ON mt_interactors(relationship, last_seen_at DESC);

CREATE TABLE mt_interactions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  user_id TEXT NOT NULL REFERENCES mt_interactors(user_id),
  direction TEXT NOT NULL,            -- in/out
  type TEXT NOT NULL,                 -- reply/mention/quote/like/follow/retweet
  tweet_id TEXT,                      -- 对方推文
  our_tweet_id TEXT,                  -- 我方相关推文（被回复的原帖或我方回复）
  text TEXT, intent TEXT, sentiment REAL, risk REAL,
  tier TEXT,                          -- safe/interesting/bold/like_only/skip
  outcome_json TEXT,                  -- {"likes":3,"author_replied":true,"followed_back":false}
  UNIQUE(direction, type, tweet_id, our_tweet_id)
);
CREATE INDEX idx_interactions_user_ts ON mt_interactions(user_id, ts DESC);
CREATE INDEX idx_interactions_tweet ON mt_interactions(tweet_id);
CREATE INDEX idx_interactions_tier ON mt_interactions(direction, tier, ts);
```

### 6.4 长期：人设卡 + 风格库 + 案例库 + 洞察（lt_*）

```sql
CREATE TABLE lt_persona_cards(
  version INTEGER PRIMARY KEY,
  card_yaml TEXT NOT NULL, card_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0,
  corpus_centroid BLOB,               -- 人设语料嵌入质心（漂移检测基准）
  note TEXT, created_by TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX idx_persona_one_active ON lt_persona_cards(is_active) WHERE is_active = 1;

CREATE TABLE lt_style_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,   -- hook/closing/catchphrase/sentence_pattern/emoji_habit/forbidden/tone_example/reply_example
  tier TEXT,            -- 回复类条目适用档位
  pillar TEXT,
  content TEXT NOT NULL, tags TEXT,
  source TEXT NOT NULL,               -- corpus/manual/learned
  weight REAL NOT NULL DEFAULT 1.0,   -- 抽样权重
  perf_ema REAL,                      -- 使用该条目的帖子复合分 EMA
  use_count INTEGER NOT NULL DEFAULT 0, last_used_at INTEGER,
  enabled INTEGER NOT NULL DEFAULT 1,
  locked INTEGER NOT NULL DEFAULT 0,  -- 人工锁定：优化器不可降权/禁用
  created_at INTEGER NOT NULL
);
CREATE INDEX idx_style_kind ON lt_style_items(kind, enabled, weight DESC);

CREATE TABLE lt_cases(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  polarity TEXT NOT NULL,             -- success/failure
  content_type TEXT NOT NULL,         -- post/thread/reply/outbound_reply/rejected_draft
  tweet_id TEXT, text TEXT NOT NULL,
  topic TEXT, pillar TEXT, tier TEXT, hour_slot TEXT,
  param_version INTEGER, prompt_version TEXT,
  metrics_json TEXT, score REAL,      -- 复合分 z
  lesson TEXT,                        -- LLM 提炼 ≤60字
  tags TEXT, embedding BLOB,
  pinned INTEGER NOT NULL DEFAULT 0,
  expires_at INTEGER,                 -- 默认 180 天；pinned 不过期
  created_at INTEGER NOT NULL
);
CREATE INDEX idx_cases_lookup ON lt_cases(polarity, content_type, score DESC);

CREATE TABLE lt_insights(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  period TEXT NOT NULL,               -- day/week
  period_key TEXT NOT NULL,           -- 2026-10-22 / 2026-W43
  insight TEXT NOT NULL, evidence_json TEXT, confidence REAL,
  status TEXT NOT NULL DEFAULT 'active',  -- active/superseded/rejected
  created_at INTEGER NOT NULL
);
CREATE INDEX idx_insights_active ON lt_insights(status, created_at DESC);
```

### 6.5 业务与运维表（记忆之外，同库）

```sql
CREATE TABLE drafts(
  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- post/thread/reply
  topic_id INTEGER, reply_task_id TEXT,
  candidates_json TEXT, chosen_text TEXT, segments_json TEXT,
  tier TEXT, gate_json TEXT, prescore REAL,
  param_version INTEGER, ab_experiment_id INTEGER, ab_arm TEXT,
  prompt_versions_json TEXT,
  status TEXT NOT NULL,               -- drafted/pending_review/ready/published/rejected/expired/failed/discarded
  ready_slot TEXT, fresh_until INTEGER,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE INDEX idx_drafts_status ON drafts(status, kind, fresh_until);

CREATE TABLE posts(
  tweet_id TEXT PRIMARY KEY, draft_id TEXT REFERENCES drafts(id),
  kind TEXT NOT NULL,                 -- post/thread_head/thread_part/reply/outbound_reply/link_followup
  text TEXT NOT NULL, posted_at INTEGER NOT NULL, day TEXT NOT NULL, hour_slot TEXT,
  in_reply_to_id TEXT, pillar TEXT, topic TEXT, tier TEXT,
  weighted_len INTEGER, n_lines INTEGER, has_media INTEGER NOT NULL DEFAULT 0, has_link INTEGER NOT NULL DEFAULT 0,
  param_version INTEGER, ab_experiment_id INTEGER, ab_arm TEXT,
  adapter TEXT, deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_posts_day ON posts(day, kind);

CREATE TABLE post_metrics(
  tweet_id TEXT NOT NULL REFERENCES posts(tweet_id),
  age_bucket INTEGER NOT NULL,        -- 1/3/6/12/24/48（小时）
  fetched_at INTEGER NOT NULL,
  views INTEGER, likes INTEGER, replies INTEGER, retweets INTEGER, quotes INTEGER, bookmarks INTEGER,
  source TEXT NOT NULL,               -- fxtwitter/xactions/twikit
  http_status INTEGER,                -- 404=已删/不可见（异常信号）
  PRIMARY KEY(tweet_id, age_bucket)
);

CREATE TABLE follower_snapshots(ts INTEGER PRIMARY KEY, followers INTEGER, following INTEGER, tweets INTEGER, source TEXT);

CREATE TABLE action_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  idem_key TEXT NOT NULL UNIQUE,
  ts INTEGER NOT NULL, day TEXT NOT NULL,
  action TEXT NOT NULL,               -- post/reply/like/delete/browse
  adapter TEXT, target_id TEXT, result_id TEXT,
  status TEXT NOT NULL,               -- pending/ok/failed/unknown/verified
  error_class TEXT, error_msg TEXT, latency_ms INTEGER
);
CREATE INDEX idx_action_day ON action_log(day, action, status);

CREATE TABLE budget_ledger(
  day TEXT NOT NULL, action TEXT NOT NULL,
  cap INTEGER NOT NULL, reserved INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(day, action)
);

CREATE TABLE reply_tasks(
  id TEXT PRIMARY KEY, source TEXT NOT NULL,          -- inbound/outbound
  tweet_id TEXT NOT NULL UNIQUE, user_id TEXT, thread_id TEXT,
  value_score REAL, risk_score REAL, tier TEXT,
  due_at INTEGER NOT NULL, status TEXT NOT NULL,      -- queued/running/pending_review/done/skipped/expired/failed
  created_at INTEGER NOT NULL
);
CREATE INDEX idx_reply_tasks_due ON reply_tasks(status, due_at);

CREATE TABLE approval_requests(
  request_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, graph TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- content/reply/tune
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,               -- pending/approved/edited/regenerate/rejected/expired
  decision_json TEXT, decided_by TEXT, decided_at INTEGER,
  expires_at INTEGER NOT NULL, resumed_at INTEGER,
  feishu_sent INTEGER NOT NULL DEFAULT 0,
  shadow_auto_verdict TEXT,           -- 影子模式：系统若自动会怎么判
  created_at INTEGER NOT NULL
);
CREATE INDEX idx_approval_status ON approval_requests(status, expires_at);

CREATE TABLE graph_runs(
  thread_id TEXT PRIMARY KEY, graph TEXT NOT NULL, status TEXT NOT NULL,
  trigger_json TEXT, last_node TEXT, error TEXT,
  started_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE INDEX idx_graph_runs_status ON graph_runs(status, updated_at);

CREATE TABLE job_runs(job_id TEXT NOT NULL, run_at INTEGER NOT NULL, status TEXT NOT NULL, detail TEXT,
  PRIMARY KEY(job_id, run_at));

CREATE TABLE control_commands(
  id INTEGER PRIMARY KEY AUTOINCREMENT, cmd TEXT NOT NULL,   -- run_job/retry_thread/abandon_thread/rollback/selfcheck_now/reload_config
  args_json TEXT, status TEXT NOT NULL DEFAULT 'queued', result TEXT,
  created_by TEXT, created_at INTEGER NOT NULL, done_at INTEGER
);

CREATE TABLE llm_calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
  task TEXT NOT NULL, model TEXT NOT NULL, tier TEXT NOT NULL,
  prompt_name TEXT, prompt_version TEXT,
  tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER,
  status TEXT NOT NULL, error_code TEXT, fallback_from TEXT
);
CREATE INDEX idx_llm_ts ON llm_calls(ts, task);

CREATE TABLE shadowban_checks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, day TEXT NOT NULL,
  dimension TEXT NOT NULL,            -- suggestion/search/ghost/reply_deboost/view_anomaly
  result TEXT NOT NULL,               -- pass/fail/inconclusive
  vantage TEXT NOT NULL,              -- twitterapi_io/observer_browser/fxtwitter
  evidence_json TEXT
);
CREATE INDEX idx_sb_day ON shadowban_checks(day, dimension);

CREATE TABLE alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, level TEXT NOT NULL,
  kind TEXT NOT NULL, message TEXT NOT NULL, ack INTEGER NOT NULL DEFAULT 0);
```

优化器相关表（`param_*`、`ab_*`、`weekly_kpis`）见第 9.4 节。

### 6.6 写入、检索、过期策略

**写入**（事件驱动，加夜间蒸馏）：

| 事件 | 写入 |
|---|---|
| 浏览会话结束 | `st_seen_tweets`；热点摘要写 `st_events(kind=hot_seen)` |
| 选题抓取 | `st_topic_pool` |
| 每次发帖、回复、点赞 | `posts`、`action_log`、`st_events`、`mt_interactions(out)`，并更新 `mt_interactors` 计数和 EMA |
| 通知入库 | `mt_interactions(in)`、`mt_interactors` upsert，`pending_profile_updates += 1` |
| 画像重写 | 每满 3 次新互动，或夜间批处理；flash 档生成 ≤200 字画像，`profile_version += 1` |
| 日复盘 | `lt_cases`（top 20%、bottom 20%，被拒草稿作 failure）、`lt_insights(day)`、`lt_style_items.perf_ema`、`st_day_context.summary` |
| 周调参 | `lt_insights(week)`，旧洞察标记 superseded |
| 人设卡与风格库 | 只能人工在面板修改，或通过 `xar persona import` 导入；优化器只能调 `weight`，不能改 `content`，`locked` 的条目连 `weight` 也不能调 |

**检索**（`memory/retrieval.py` 组装 `MemoryContext`，带 token 预算）：
- **发帖**：
  - 激活的人设卡全文（约 1.2k tokens）。
  - 风格条目：按支柱和标签匹配取 hook 2 条、句式 3 条、口头禅 2 条、禁用词全量，用 `weight × perf_ema` 加权抽样，避免每次都用同样的句式。
  - 成功案例 3 条、失败案例 2 条：按话题嵌入相似度取 top-k，加入时间衰减项 `score × exp(-age/60d)`。
  - 近 7 天已发帖子标题列表（防重复），活跃洞察最多 5 条，今日 `st_events` 摘要。
- **回复**：对方画像、与其最近 5 次互动、线程上下文（原帖 + 前 3 条热评）、同档位相似回复案例 2 条、人设卡里的“回复风格”小节。
- **外部记忆**：`MemoryProvider` 协议预留给用户现有的记忆系统（`retrieve(query, scope, k)`、`write(event)`）。启用后作为额外片段并入 `MemoryContext`，有独立 token 配额；双写开关由配置控制，默认关。

**过期策略**：

| 数据 | 保留 | 处理方式 |
|---|---|---|
| `st_events`、`st_topic_pool` | 3 天 | 先蒸馏进 `st_day_context.summary`，再删除 |
| `st_seen_tweets` | 7 天 | 去重用；embedding 3 天后置空 |
| `mt_interactions` | 180 天 | 超期按用户汇总进 `profile_summary` 后删除 |
| `mt_interactors` | 不活跃 90 天且互动少于 2 次 | 删除；kol、target_kol、hostile、blocked 永久保留 |
| `lt_cases` | 180 天 | pinned 不过期；每类最多保留 300 条，超出淘汰 \|score\| 最小的 |
| `lt_insights` | 被新洞察覆盖就标记 superseded，90 天后删除 | |
| `post_metrics`、`posts` | 永久 | 体量很小 |
| `llm_calls` | 60 天 | 先按日聚合，再删明细 |

---

## 7. 内容生成管线

### 7.1 人设卡结构（占位，待用户补语料）

```yaml
# persona/persona_card.yaml
version: 1
identity: {name: "<待补>", one_liner: "<待补：如 一线AI工程师，爱折腾开源工具>", location_hint: "<可选>"}
bio_for_profile: "<待补>"
pillars:            # 内容支柱及初始配比（配比本身是可调策略参数）
  - {id: news_take,  name: "AI 资讯快评",   weight: 0.35}
  - {id: tool_test,  name: "工具/模型实测", weight: 0.25}
  - {id: opinion,    name: "行业观点",      weight: 0.20}
  - {id: dev_life,   name: "开发者日常",    weight: 0.12}
  - {id: ask,        name: "互动提问",      weight: 0.08}
values: ["<待补：真诚、不吹不黑、动手派>"]
tone: {formality: 0.3, humor: 0.5, sharpness: 0.4, emoji_rate: 0.15, self_reference: "我"}
signature_habits: ["<待补口头禅>"]
taboo_topics: ["政治时事", "地缘冲突", "投资荐股", "医疗建议", "人身攻击", "未经证实的爆料"]
reply_style:
  safe: "<待补：礼貌、有信息量、一两句>"
  interesting: "<待补：个人视角、轻梗>"
  bold: "<待补：反常识观点、点到为止，不攻击人>"
sample_posts: ["<待补 10-30 条真实样例>"]
```

### 7.2 管线全流程

```text
[内容源 sources.yaml(占位) + 浏览热点 st_seen_tweets + 人工投喂 manual]
      │ 抓取/摘要（flash，英文源顺便翻译）
      ▼
① 选题  score = 0.30·新鲜度 + 0.30·人设相关度 + 0.20·新颖度 + 0.20·热度（权重为策略参数）
        硬过滤：taboo_topics、近14天同话题嵌入相似度>0.85、来源不可信
        支柱配额：按 pillar_mix 和本周已发分布做缺口补偿
      ▼
② 草稿（pro 档，N=3 个角度不同的候选；注入人设卡+案例+洞察+algo_kb 约束）
      ▼
③ 人设风格化（pro 档；抽样风格条目；强制第一人称、口语化、观点明确）
      ▼
④ 分句节奏（规则引擎，不用 LLM，可复现可测）
      ▼
⑤ 去AI味门禁（规则层 → LLM 判官层 → 合规层 → 人设一致性），不通过就带反馈重写，最多 2 次
      ▼
⑥ 预评分与择优（algo 信号打分 + 门禁分），去重（vs 近30天已发 sim<0.85；vs 来源原文 5-gram 重合<30%）
      ▼
⑦ 审核路由（开关模式 × 风险）→ 飞书卡片/面板 →【interrupt】
      ▼
⑧ 进入待发队列 → 槽位到点 → 拟人发布 → 外链放到自回复 → 登记数据回收
```

### 7.3 分句短句节奏规则（`content/splitter.py`，参考 WeClone）

- **长度上限**：用 twitter-text 加权长度（`twitter-text-parser`）计算。非 Premium 账号上限 280 加权单位，CJK 字符每个计 2，相当于约 140 个汉字（A7）。目标区间是策略参数 `content.length_target`，初值 60–120 汉字。
- **断句**：先按“。！？；\n”切句，再对超过 22 字的长句在“，、”处切开。合并少于 4 字的碎句，保留刻意的短句作为“冲击句”。
- **节奏模板**（按概率抽样，策略参数可调）：
  - 钩子句（≤15 字）+ 空行 + 2–4 行展开（每行 8–20 字）+ 收尾（观点或反问）。
  - 一句话短评型（≤40 字，资讯快评用）。
  - 列点型，但不用 markdown 符号，改用“1/2/3”或换行；每周占比 ≤20%，太多会有 AI 味。
- **句长“突发度”**：统计句长的变异系数，要求 CV ≥ 0.35，过于整齐就打回重写。
- **标点**：中文全角；“——”每帖 ≤1 个，“！”每帖 ≤1 个；emoji 按 `emoji_rate` 抽样，每帖 ≤2 个；hashtag 每帖 ≤1 个，默认 0 个。
- **长内容**：超过上限且支柱是 tool_test 或 opinion 时，转为 thread（≤3 条）。整个 thread 计 1 次发帖预算；后续条目间隔 20–60 秒打字发出。

### 7.4 去AI味门禁（`content/gate.py`，以 humanizer-zh 为规则底座）

**规则层**（确定性，命中即扣分，按严重程度分级）：

| 类别 | 示例规则（`persona/humanizer_rules.yaml` 维护） |
|---|---|
| 套话连接词 | 首先/其次/最后、值得注意的是、总而言之、综上所述、不仅…而且…、在当今…的时代、让我们一起 |
| 空泛大词 | 赋能、闭环、抓手、深度解析、全方位、颠覆性、革命性、划时代 |
| AI 句式 | “这不仅仅是X，更是Y”、“X，Y，Z——”三连排比、“你是否也…？”开头、结尾“你怎么看？”的使用频率超出配额 |
| 结构 | markdown 标题、加粗符号、项目符号、冒号小标题；段落数和句长过于对称 |
| 语气 | 过度谦逊或客服腔（“希望对你有帮助”）、感叹号堆叠、过度肯定（“绝对”“毫无疑问”） |
| 统计 | 句长 CV < 0.35；平均句长 > 22 字；重复用词率过高 |

**LLM 判官层**（flash 档，`gate/ai_flavor_judge.md.j2`）：输出 JSON `{"ai_score":0-10,"reasons":[],"suggest":""}`，ai_score ≤ 3 才通过，判官温度 0。

**合规层**：taboo 话题、事实性断言（“据报道”“已确认”）没有来源时打回或改为观点式表述，涉及具体公司的负面断言、医疗或投资建议直接打回。智谱侧内容安全拦截（A5 中的错误码）视同不通过，话题标记 sensitive。

**人设一致性**：草稿嵌入与人设语料质心的余弦相似度 ≥ 阈值（初值 0.55，第 3 周用真实语料校准），不足就打回。

不通过时把 reasons 和命中规则作为反馈交给 rewrite（fast 档），最多 2 次。仍不通过就丢弃，记为 `lt_cases(polarity=failure, content_type=rejected_draft)`，作为反例积累。

### 7.5 算法信号与设计约束映射（`algo_kb/signals.yaml`，对应决策⑦）

| x-algorithm 信号 | 落地约束 |
|---|---|
| 停留时长是显式正向信号 | 节奏模板追求“读完需要 5–15 秒”：钩子加 3–5 行展开；避免过短的空帖，一句话短评只给资讯快评用。预评分包含“信息密度”项 |
| 视频秒数是正向信号 | tool_test 支柱预留“录屏或 GIF 演示”槽（`data/media/`，由用户投喂素材，A12 之后迭代）；`has_media` 进入 A/B 候选参数 |
| 关注转化单独建模 | 粉丝转化 FC 是独立 KPI；主页优化（bio、置顶帖）列入 warm-up 人工清单；系列化内容（固定栏目名）进入候选参数 |
| 同作者连续发帖衰减 | 发帖间隔 ≥2.5h（守卫硬约束）；thread 计 1 次；外链自回复不计发帖（A15） |
| 举报、拉黑、静音为负向（agatha） | Bold 档配额和风险门槛；不回复陌生人的敌意内容；主动回复只做高相关度帖；禁止批量相似回复（回复间文本相似度 <0.6）；监控负向代理指标（9.5 节） |
| 新账号流量加权 | warm-up 结束后的前两周（ramp 期）优先发高质量常青内容，pillar 配比向 tool_test 和 opinion 倾斜（A12） |
| 48h 相关性窗口 | 数据回收窗口 48h；资讯类选题新鲜度 ≤24h，fresh_until 默认发布后 24h；终值以 48h 快照为准 |

**预评分**（`content/prescore.py`）：`prescore = Σ w_i·f_i`。特征包括钩子强度（flash 打分）、信息密度、加权长度落在目标区间、是否结尾提问、话题热度、槽位历史表现、是否带媒体。权重初值手工设定，第 6 周起由日复盘用 48h 复合分做岭回归更新（只更新预评分权重，不算“调参次数”）。

### 7.6 回复分档决策树（参考 notcringe）

```text
输入：通知/主动回复候选 + 对方画像 + 线程上下文
│
├─ 硬过滤 → SKIP
│    · 对方 relationship ∈ {blocked, hostile}，或 bot_score > 0.7，或广告/引流
│    · 命中 taboo 话题或政治敏感；toxicity > 0.6（同一人 3 次 → 标记 hostile）
│    · 我方已回复过该线程 ≥2 次（防"对线"）
│
├─ 意图分类（flash）：question / praise / agree / disagree / joke / share_info / attack / other
├─ 风险 r∈[0,1] = f(话题敏感度, 对方毒性EMA, 对方影响力(粉丝越多越谨慎), 当前阶段, 影子封禁等级)
├─ 价值 v∈[0,1] = f(对方影响力, 关系等级, 线程可见度, 互关可能, 是否目标KOL)
│
├─ praise/agree 且内容 <10 字 ──► LIKE_ONLY（30% 概率另加一句 Safe 短回复）
├─ attack ──► SKIP（r 高时可静默；永不对骂）
├─ r ≥ 0.6 ──► SAFE 或 SKIP（v < 0.3 时 SKIP）
├─ question ──► SAFE（准确、有信息量）；若关系 ≥ regular 且 r < 0.3 ──► INTERESTING
├─ disagree ──► SAFE（冷静陈述一个论点）；关系 ≥ acquaintance 且 r < 0.3 ──► INTERESTING
├─ joke/share_info ──► INTERESTING（关系 ≥ acquaintance 或 主动回复目标KOL）
└─ BOLD 仅当同时满足：
     r < 0.2 ∧ 阶段 = normal ∧ 影子封禁 GREEN ∧ 当日 bold 已用 < bold_quota
     ∧ （关系 = regular 或 目标KOL的开放讨论帖）∧ 非陌生人的反对观点
     （bold_quota = 当日回复上限 × reply.tier_mix.bold，初值 10%）
```

- **档位定义**：Safe 是稳妥有用、1–2 句；Interesting 是个人视角、轻梗、具体细节；Bold 是反常识观点或犀利点评，点到为止，只针对观点、不针对人。
- **生成**：选定档位后生成 2 个候选（fast 档；Bold 用 pro 档），经门禁后由 flash 判官择优。回复长度 ≤ 60 汉字；99% 是单段，偶尔两句换行。
- **主动回复**（引流）：只回目标 KOL 池（`persona/kol_targets.yaml`，占位）和高相关帖；帖龄 ≤ 60 分钟优先，早回复的位置更好；占当日回复预算的比例是策略参数 `reply.outbound_share`，初值 0.5。

---

## 8. 真人感行为模型

所有分布参数放在 `config/humanize.yaml`，由 `humanize/distributions.py` 实现。主要用截断对数正态（`TLN(median, σ, min, max)`），比均匀分布更接近人类反应时间的长尾。每天的 energy 系数（0.6–1.0）缩放会话时长和数量。

### 8.1 打字（`typing_plan.py` 生成，侧车 `human_type` 执行）

| 项 | 参数 |
|---|---|
| 中文输入方式 | 模拟拼音输入法分块上屏：按分词切成 1–4 字的块，用 `document.execCommand('insertText')` 或 CDP `Input.insertText` 整块插入 |
| 块间延迟 | TLN(median 650ms, σ 0.45, 250ms, 2200ms) |
| 中文标点 | TLN(300ms, 0.4, 120, 900) |
| 英文和数字 | 逐字符 `keyboard.type`，每字 TLN(110ms, 0.35, 45, 320) |
| 句末停顿 | 概率 0.6，TLN(1.2s, 0.5, 0.5s, 3.5s) |
| 思考停顿 | 每句概率 0.08，U(3s, 8s) |
| 输入法选错词 | 每块概率 0.025：插入错误块，停 0.4–1.0s，退格删除，重打正确块 |
| 英文拼写错误 | 每词概率 0.02：错一个字母，0.2–0.6s 后退格修正 |
| 打完复核 | 发送前停 TLN(2.5s, 0.5, 1s, 8s)；概率 0.15 光标回移、小改一处标点 |
| 粘贴模式 | 长帖（>80 汉字）概率 0.35 走“粘贴”（模拟在别处写好）：聚焦 0.8–2s 后一次插入，复核停顿翻倍 |
| 总时长上限 | 发帖 ≤ 240s、回复 ≤ 90s，超出就等比压缩延迟（下限保持块间 ≥200ms） |

### 8.2 阅读时间

| 场景 | 停留时长 |
|---|---|
| 时间线扫过（相关度低于阈值） | TLN(1.1s, 0.5, 0.4s, 3s) |
| 感兴趣的帖（相关度 ≥ 阈值，初值 0.55） | 全文阅读：汉字数 × U(130, 200)ms + 1.0s 起步；带图加 U(2, 5)s/张 |
| 视频 | 观看 min(视频时长， U(4, 20)s)；短于 15 秒的视频概率 0.5 看完。这是给别人的正向信号，也是我们作为“真实用户”的特征 |
| 回复前 | 打开详情页读原帖，加滚动读前 3–6 条热评：TLN(18s, 0.5, 8s, 60s) |
| 打开通知页 | 每条通知 TLN(1.5s, 0.4, 0.6s, 4s) |
| 点赞前 | 已阅读 ≥ 2s，且点赞后继续停留 0.5–2s |

### 8.3 滚动

- **滚动由“甩动”组成**：每次甩动 2–6 个滚轮事件，单次 deltaY 为 U(80, 400)px，事件间隔 U(30, 80)ms；甩动之间的停顿 TLN(1.4s, 0.6, 0.5s, 6s)，遇到感兴趣的帖就切换为阅读停留。
- **回滚**：每次甩动概率 0.07 回滚 1–2 屏（“刚才那条再看一眼”）。
- **鼠标**：贝塞尔曲线移动，15–35 个点，总耗时 200–700ms；点击位置在元素包围盒中心 ±30% 随机；按下到抬起 60–140ms。
- **视口**：固定一组真实分辨率（如 1536×864），整个生命周期不变；窗口偶尔失焦（概率 0.03，持续 5–40s），模拟切走去看别的东西。

### 8.4 浏览路径（`browse_path.py`，加权状态机）

```text
入口概率：For You 0.50 | Following 0.20 | Notifications 0.12 | Search(兴趣词) 0.10 | Profile(互动者/KOL) 0.08
状态内动作：滚动阅读 → 打开详情(相关帖 p=0.10) → 打开作者主页(p=0.03) → 返回
转移：每停留 1–3 分钟以 p=0.25 切换到另一入口；会话结束条件为时长到期或被高优先级任务抢占
会话时长：TLN(6min, 0.5, 3min, 14min) × energy
单会话浏览帖数：15–80（由时长和停留自然决定，不强制）
点赞策略：P(like) = clip(0.04 + 0.25·(relevance−0.55), 0, 0.2)；单会话上限 U{3..8}；相邻点赞 ≥ 20s
```

### 8.5 作息与操作节奏（参考 Pulse/erii）

- 日类型和情绪见 5.2 节。情绪（平静、兴奋、疲惫）通过 prompt 的轻微语气调节注入（`mood_hint`），只影响用词活跃度，不改观点。
- **写操作间隔**：任意两次写操作 ≥ TLN(90s, 0.5, 45s, 300s)；回复每小时 ≤ 5 条，点赞每小时 ≤ 10 个。
- 发帖后停留 1–4 分钟（看时间线和自己帖下的评论），不发完就走。
- **入站回复延迟**：从通知到回复 TLN(12min, 0.8, 3min, 90min)；白天活跃时段 median 乘 0.7，深夜乘 1.5。
- **指纹一致性**：持久化 userDataDir；Chrome 正式版而非 Chromium；时区 Asia/Shanghai，语言 zh-CN；UA 不手工伪造，用真实 Chrome 自带的；加 stealth 插件；IP 固定为家宽（本机阶段），VPS 阶段见 14.3。

---

## 9. 优化引擎闭环

### 9.1 数据回收（fxtwitter）

```text
GET https://api.fxtwitter.com/{screen_name}/status/{tweet_id}
Headers: User-Agent: xar/0.1 (+contact)；Accept: application/json
返回(A4)：{"code":200,"message":"OK","tweet":{
   "id","url","text","created_at","created_timestamp","lang",
   "author":{"screen_name","name","followers","following","tweets",...},
   "replies","retweets","likes","bookmarks","views",      ← 指标（views/bookmarks 可能为 null）
   "quote":{...}|null, "media":{"photos":[],"videos":[{"duration":..}]}|null,
   "replying_to","replying_to_status" }}
code 404 → 推文已删/不可见（自有帖出现 404 视为异常，进入告警和自检）；401 → 受保护；5xx → 重试

GET https://api.fxtwitter.com/{screen_name}  → {"code":200,"user":{"followers","following","tweets","likes",...}}
```

- **客户端**：httpx，并发数 1，请求间隔 U(1.5, 3)s；5xx 或 429 时退避 5s、15s、45s；单帖失败 3 次就降级到 XClient 的 `get_tweet`（xactions、twikit）。
- **快照节奏**：每帖在 1、3、6、12、24、48 小时各采一次，写入 `post_metrics(tweet_id, age_bucket)`；48h 快照是终值，和算法的 48h 窗口对齐。
- **粉丝快照**：每小时一次，写 `follower_snapshots`。
- **契约测试**：fixtures 存一份真实响应样本；每天首次调用校验字段集合，缺字段就告警并切换降级源。

### 9.2 指标体系

| 指标 | 定义 | 用途 |
|---|---|---|
| 曝光 V | 48h views | 主指标；用中位数和 log 平滑长尾 |
| 互动率 ER | (likes + replies + retweets + quotes + bookmarks) / V | 内容质量 |
| 加权互动 WER | (0.5·like + 1·rt + 13.5·reply + 1·quote + 1·bookmark) / V，权重初值来自 2023 年开源 heavy ranker 配置（A16） | 向算法偏好对齐 |
| 粉丝转化 FC | 每千次曝光带来的净增粉：Δfollowers[发帖后 0–24h] 按窗口内各帖 views 占比分摊 | 关注转化单独建模 |
| 回复效果 RQ | 我方回复的 (likes + 作者回复×3 + 回关×5) / 回复数，按档位分别统计 | 回复分档调参 |
| 负向代理 NEG | 粉丝单日净减 ≥ 1% 或 ≥ 5 人；views/followers 比低于 7 日中位数的 50%；自有帖 fxtwitter 404；回复被折叠（自检第 4 维）；收到敌意回复比例 | 负向信号（X 不直接暴露拉黑和举报数） |
| 健康 H | 影子封禁等级、写操作失败率、验证码次数 | 安全闸门 |

**单帖复合分**（相对基线归一）：

S = 0.40·z(log V) + 0.30·z(WER) + 0.30·z(FC) − λ·NEG_post

其中 z 相对过去 14 天同槽位基线计算，以消除时段混杂。

**周复合分**：

C_w = 0.35·Ṽ + 0.25·WER̃ + 0.25·FC̃ + 0.15·RQ̃ − λ·NEG_w

各项以过去 4 周均值归一。

### 9.3 每日复盘算法（00:40）

1. **收集**：过去 24 小时内刚满 24h 龄的帖子（初步）+ 刚满 48h 的帖子（终值）+ 昨日全部回复。
2. **打分**：计算每帖 S。回复按档位聚合 RQ，更新 `mt_interactors.tier_stats_json` 和全局档位统计。
3. **案例入库**：样本数 ≥5 时，S 前 20% 记 success、后 20% 记 failure；样本不足时，S 高于 +1σ 或低于 −1σ 的才入库。pro 档为每个案例提炼 ≤60 字教训，写 `lt_cases`。
4. **洞察**：pro 档读取今日数据、近 7 天活跃洞察和案例，输出 ≤3 条新洞察（JSON 格式，带证据帖 ID 和置信度）。和旧洞察矛盾的，旧的标记 superseded。
5. **风格分更新**：对每帖用到的风格条目，`perf_ema ← 0.8·perf_ema + 0.2·S`；`weight = clip(1 + 0.5·perf_ema, 0.3, 2.0)`，locked 条目不动。
6. **预评分权重更新**：累计 ≥30 帖后，每日用岭回归拟合 prescore 权重，限制单日变化 ≤10%。
7. **蒸馏**：短期记忆写日终摘要。
8. **日报卡片**（飞书）：今日 KPI 与 7 日对比、最好和最差各 1 帖、预算使用、档位效果、异常、影子封禁状态。
9. **日复盘不改策略参数**（只在周调参时改）。唯一例外是安全收紧：NEG 触发阈值时立即 throttle，这属于安全层动作，不计入调参次数。

### 9.4 参数库表结构（版本化、回滚、A/B）

```sql
CREATE TABLE param_registry(
  key TEXT PRIMARY KEY,               -- content.pillar_mix / reply.tier_mix / ...
  type TEXT NOT NULL,                 -- float/int/enum/weights/time_offset
  default_json TEXT NOT NULL, min_json TEXT, max_json TEXT, step_json TEXT,
  tunable INTEGER NOT NULL DEFAULT 1,
  safety_locked INTEGER NOT NULL DEFAULT 0,   -- 预算/间隔/阶段类参数=1，优化器永不可改
  ab_unit TEXT,                       -- post/reply
  description TEXT
);

CREATE TABLE param_versions(
  version INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_version INTEGER REFERENCES param_versions(version),
  params_json TEXT NOT NULL,          -- 完整快照（非增量，回滚即切换指针）
  diff_json TEXT NOT NULL,            -- 相对 parent 的变更
  reason TEXT NOT NULL,
  source TEXT NOT NULL,               -- init/weekly_tune/ab_adopt/rollback/manual
  status TEXT NOT NULL,               -- active/retired/rolled_back/rejected
  approved_by TEXT,
  effective_from INTEGER NOT NULL, effective_to INTEGER,
  week_key TEXT                       -- 2026-W43
);
CREATE UNIQUE INDEX idx_param_one_active ON param_versions(status) WHERE status='active';

CREATE TABLE param_changes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version INTEGER NOT NULL REFERENCES param_versions(version),
  key TEXT NOT NULL REFERENCES param_registry(key),
  old_json TEXT, new_json TEXT,
  hypothesis TEXT,                    -- "提问式结尾比例 0.2→0.3 预计提高 reply 率"
  expected_metric TEXT,
  observed_delta REAL, verdict TEXT   -- improved/neutral/worse（下周回填）
);

CREATE TABLE ab_experiments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, key TEXT NOT NULL REFERENCES param_registry(key),
  unit TEXT NOT NULL,                 -- post/reply
  arm_a_json TEXT NOT NULL, arm_b_json TEXT NOT NULL,
  base_version INTEGER NOT NULL,
  metric TEXT NOT NULL,               -- S / RQ
  min_samples_per_arm INTEGER NOT NULL,
  started_at INTEGER NOT NULL, ends_at INTEGER NOT NULL,   -- 最长 2 周
  status TEXT NOT NULL,               -- running/concluded/aborted
  p_b_better REAL, effect REAL, winner TEXT, result_json TEXT
);

CREATE TABLE ab_assignments(
  experiment_id INTEGER NOT NULL REFERENCES ab_experiments(id),
  unit_id TEXT NOT NULL,              -- draft_id / reply_task_id
  arm TEXT NOT NULL, assigned_at INTEGER NOT NULL,
  PRIMARY KEY(experiment_id, unit_id)
);

CREATE TABLE weekly_kpis(
  week_key TEXT PRIMARY KEY, version INTEGER NOT NULL,
  n_posts INTEGER, n_replies INTEGER,
  v_median REAL, wer_median REAL, fc_per_1k REAL, rq REAL, neg REAL,
  composite REAL, composite_ci_low REAL, composite_ci_high REAL,
  vs_baseline REAL, verdict TEXT       -- better/neutral/worse
);
```

**可调参数初始注册表**（`strategy_defaults.yaml`）：
- 内容类：`content.pillar_mix`、`content.length_target`、`content.rhythm_template_mix`、`content.question_ending_prob`、`content.thread_ratio`、`content.media_ratio`、`content.hook_style_mix`、`style.emoji_rate`、`topic.score_weights`、`post.slot_weights`。
- 回复类：`reply.tier_mix`、`reply.outbound_share`、`reply.max_len`。
- `safety_locked`（不可调）：`budget.*`、`interval.*`、`warmup.*`、`humanize.*`。

### 9.5 每周调参流程（周一 01:30）

```text
1. 计算本周 C_w（bootstrap 1000 次得 CI）→ verdict：
   worse  ⇔ C_w < 基线·(1−5%) 且 P(C_w < 基线) ≥ 0.7
   基线 = 当前版本生效前 2 周的 C 均值
2. 回滚检查（优先级最高）：
   · 当前版本连续两个周期 verdict=worse → 自动回滚到 parent_version（切换 active 指针），
     source=rollback，并锁定被回滚的 key 2 周不再调；直接通知，不需审批
   · 版本生效后出现影子封禁 RED 或 NEG 激增 → 立即回滚（安全回滚，不等两周期）
   · 回滚当周不再提新变更
3. A/B 判定：运行 ≥1 周且每臂 ≥ min_samples（post 8 / reply 30）的实验：
   贝叶斯 bootstrap 比较两臂 S（或 RQ），P(B>A) ≥ 0.8 且效应 ≥ 5% → B 胜；
   P(B>A) ≤ 0.2 → A 胜；否则延长至 2 周上限，仍不显著就判 neutral，保持 A
4. 提案（≤3 项/周，硬约束；A/B 胜出采纳也计入）：
   候选来源：A/B 胜者 > 洞察驱动（pro 档读 4 周 KPI+洞察+param_changes 的 verdict，
            只能在 registry 的 min/max/step 内提议，每项改动 ≤1 step）> 探索（随机 1 项 1 step）
   规则：同一 key 不得连续两周改动（A/B 证实的除外）；safety_locked 永不入选；
        每项必须带 hypothesis 和 expected_metric
5. 审批：初期一律 interrupt 人工审批（飞书卡片显示 diff+理由），content 切到 auto 后可改为"自动生效+通知"
6. 生效：写新 param_versions（完整快照）+ param_changes；旧版 retired
7. 启动下周 A/B：每周最多 1 个 post 实验 + 1 个 reply 实验，二者参数不重叠
```

**A/B 分臂**（`optimizer/ab.py`）：
- **post**：发帖样本少（约 20 帖/周），用“槽位轮转平衡”代替纯随机。按日交替 ABBA/BAAB 序列分配槽位，保证每臂在各槽位出现次数相同，消除时段混杂。
- **reply**：按 `hash(reply_task_id) % 2` 分配。
- 分臂结果写入 `drafts.ab_arm` 和 `posts.ab_arm`。
- **统计功效**：周样本量小，post 实验的功效有限。所以设计上默认 2 周上限、判定阈值宽松（0.8），并优先在样本多的 reply 上做实验，在方案层面明确接受“慢而稳”。

---

## 10. 影子封禁四维自检

**前提**（A9）：X 已关闭访客搜索，而且账号自己看不到自己被屏蔽的效果，所以必须用**外部观察者视角**：
- **主观察者**：twitterapi.io。第三方服务，用自己的账号池查询，与我方账号零关联，按量计费，每日自检成本是美分级（A21）。
- **辅助观察者**（可选）：一个独立的“观察号”，使用独立 Chrome profile。初期复用同 IP 有关联风险，默认关闭，迁 VPS 后可以放到另一出口。
- 第 4 维“回复降权”需要看渲染后的折叠区，没有浏览器观察者时判定为 inconclusive，再用代理指标补充。

| 维度 | 检测步骤 | 判定 |
|---|---|---|
| ① 搜索建议屏蔽（Search Suggestion Ban） | 观察者调用 typeahead（twitterapi.io 用户搜索或观察号的 typeahead 接口）查询完整 handle，以及去掉最后一个字符的前缀；当天在不同时刻做 2 次 | 完整 handle 查询结果里没有我方账号记为 fail；2 次中 ≥1 次 fail 记 fail；接口异常记 inconclusive |
| ② 搜索封禁（Search Ban） | (a) 观察者 Latest 搜索 `from:{handle}`，取发布超过 30 分钟、7 天内的帖；(b) 取最近一帖中 6–10 字的独特短语加引号精确搜索 | (a) 结果为 0 但 fxtwitter 证实近期有帖 → fail；(a) 通过但 (b) 搜不到 → 部分 fail（计 fail，在 evidence 里标注 partial） |
| ③ 幽灵回复（Ghost Ban） | 抽样我方过去 2–24 小时发出的 3 条回复（优先回复给中小账号的）；观察者拉取父帖的完整回复列表（twitterapi.io 回复端点，或观察号浏览器展开全部） | 3 条中 ≥2 条不在列表里 → fail；1 条缺失 → inconclusive（需复测） |
| ④ 回复降权（Reply Deboosting） | 同样抽 3 条回复；观察号浏览器打开父帖，判断我方回复是否只出现在“显示更多回复”或“可能的垃圾内容”折叠区里，或者排在同等互动回复的最后 20% | 3 条中 ≥2 条被折叠 → fail；没有浏览器观察者 → inconclusive，并用代理指标（我方回复的平均 views 低于 7 日中位数的 30%）给出 soft_fail |
| 附加：曝光异常 | 近 3 帖 48h views / followers 低于 14 日中位数的 40% | warn，不单独定级，作为佐证 |

**定级与处置**（`safety/shadowban.py` 写 `system_kv.throttle_level`）：

| 等级 | 条件 | 自动处置 |
|---|---|---|
| GREEN | 全部 pass，或只有 inconclusive | 正常 |
| YELLOW | 任一维度单日 fail，或曝光异常加任一 soft_fail | 预算 ×0.5；禁用 Bold 和主动回复；暂停外链；当晚 22:15 复测 |
| RED | 同一维度连续 2 次 fail，或单日 ≥2 维 fail | phase 设为 red：48 小时不发帖，只浏览加 ≤3 条 Safe 入站回复；暂停调参；如果在新参数版本生效后 7 天内出现，自动回滚该版本；飞书红色告警 |
| 恢复 | 连续 3 天 GREEN | RED 降到 YELLOW，再降到 GREEN，每次升一级观察 3 天 |

---

## 11. 安全层全景

### 11.1 预算执行器（`safety/budget.py`）

**日上限计算**（A13）：冻结的 4、15、30 视为**绝对上限**。基准值取上限的 1/1.3，日值 = 基准 × U(0.7, 1.3)，再和硬上限取较小值，最后乘阶段乘数：

cap_day = min(HARD, round(HARD/1.3 · U(0.7, 1.3))) × phase_mult

结果是发帖 2–4、回复 8–15、点赞 16–30（normal 阶段）。

**硬上限写死在代码常量里**，配置只能更低不能更高（加载时取较小值，越界会被拒绝）。代码就是最后一道防线。

**原子预留**：写操作前在一个事务里执行：

```sql
BEGIN IMMEDIATE;
UPDATE budget_ledger SET reserved = reserved + 1
 WHERE day=? AND action=? AND used + reserved < cap;
-- changes()==0 → 拒绝
COMMIT;
```

成功后 `used += 1, reserved -= 1`；失败就释放。启动时把残留的 reserved 清零。

**多维限速**（`safety/rate.py`）：小时上限（回复 5、点赞 10、发帖 1）、最小间隔（发帖 2.5h、回复 45s 起、点赞 20s）、写操作全局最小间隔、滚动 24 小时窗口复核（防跨日突刺）。

**关注**：冻结预算没覆盖关注操作，默认 `follow.enabled=false`；开启后上限 ≤3/天，normal 阶段才允许（A14 之外的扩展项）。

### 11.2 warm-up 两周计划（`config/warmup.yaml`，phase 状态机）

| 阶段 | 天数 | 浏览会话/日 | 点赞/日（A14） | 回复/日 | 发帖 | 审核 |
|---|---|---|---|---|---|---|
| warmup_a | D1–D3 | 3–4 | 0–5 | 0–1（只 Safe，只入站） | 0 | 全人工 |
| warmup_b | D4–D7 | 4–5 | 5–10 | 2–4（Safe，入站加中小号主动回复） | 0 | 全人工 |
| warmup_c | D8–D14 | 4–6 | 8–15 | 4–8（Safe/Interesting） | 0 | 全人工 |
| ramp_1 | D15–D21 | 5–6 | ≤ 0.7×cap | ≤ 0.7×cap，Bold 禁用 | 1–2/日 | 全人工 |
| ramp_2 | D22–D28 | 正常 | ≤ 0.85×cap | ≤ 0.85×cap | 2–3/日 | reply 可进影子模式 |
| normal | D29 起 | 正常 | cap | cap，Bold 开放 | cap | 按 12.3 节升级 |

- **人工前置清单**（D0，面板列出并逐项勾选）：头像、横幅、bio（人设卡的 `bio_for_profile`）、置顶帖（ramp_1 第一帖发出后人工置顶）、两步验证、绑定邮箱和手机、关注 20–40 个领域账号（人工完成，避免早期自动关注）。
- **阶段推进条件**：按日期自动推进，但需要满足“过去 3 天没有 YELLOW/RED、没有验证码、写操作失败率 <10%”，否则在当前阶段停留并告警。面板可以手动改阶段（记录操作人）。

### 11.3 STOP 文件 kill switch（`safety/killswitch.py`）

- **文件**：`data/STOP` 表示全停（写和浏览都停）；`data/PAUSE_WRITES` 表示只读（继续浏览和数据回收，禁止任何写操作）。
- **四重检查点**：
  1. 独立 asyncio 监视任务每 2 秒 `stat` 一次。检测到 STOP 就设置全局 `stop_event`，`scheduler.pause()`，向侧车发送 `abort`（清空编辑框、不提交、停止滚动），并发飞书告警。
  2. `GuardedXClient` 在每次写操作前、拟人打字每一块之间、浏览每次甩动之间检查 `stop_event`。
  3. 主图的 guard 节点检查。
  4. publish 子图的 final_guard 再查一次。
- **触发方式**：手动创建文件（本机、远程 SSH 都行）、面板“紧急停止”按钮、自动触发（AccountLocked、ChallengeRequired、Suspended；10 分钟内连续 3 次写失败；预算账本异常，比如 used > cap，按代码 bug 处理；影子封禁 RED 加人工未确认超过 24 小时，只写 PAUSE_WRITES）。
- **恢复**：删除文件后不会自动恢复，还需要在面板点“恢复”（二次确认、填写原因）。目的是防止误删文件后无人值守地继续跑。
- **验收要求**：STOP 生效到动作停止 ≤3 秒，正在输入的文本不得发出（第 2 周验收项）。

### 11.4 凭据管理（`safety/credentials.py`）

| 凭据 | 存放方式 |
|---|---|
| GLM API Key、飞书 webhook URL 和签名密钥、twitterapi.io key、官方 API key、面板 admin 密码哈希、HMAC 链接密钥、Fernet 主密钥 | `keyring`（Windows 凭据管理器；Linux 用 SecretService，或 `keyrings.alt` 加密文件、权限 600）；`xar secrets set <name>` 交互写入 |
| X 账号登录态 | 不存储密码。`xar login` 以有界面模式打开侧车的 Chrome profile，由人工登录（含两步验证），会话保存在 userDataDir |
| cookie 导出 | `data/cookies.enc`（Fernet 加密），供 twikit 使用和迁移；每 6 小时刷新一次 |
| `.env` | 只放非敏感配置；`.gitignore` 排除 `data/`、`.env`；pre-commit 跑 gitleaks |
| 日志 | structlog 处理器对 token、cookie、key、Authorization 字段脱敏 |

**服务账户注意**（A19）：DPAPI 和凭据管理器绑定 Windows 用户，所以 NSSM 服务必须以**当前用户身份**运行（不能用 LocalSystem），否则读不到 keyring 和 Chrome profile。

---

## 12. 审核流

### 12.1 飞书 webhook 卡片

**发送**：用自定义机器人 webhook，开启签名校验：`string_to_sign = f"{timestamp}\n{secret}"`，`sign = base64(hmac_sha256(key=string_to_sign, msg=b""))`。审核和告警分两个机器人（两个群），避免告警淹没审核。

**按钮限制**（A10）：自定义机器人卡片的按钮只支持 URL 跳转，不支持回调。所以按钮跳转到面板的确认页，链接带 HMAC 一次性令牌（2 小时有效，绑定 request_id 和动作）。**落地页的 GET 只渲染确认页，不改任何状态**，防止飞书或浏览器预取链接时误触发审批；点击确认后用 POST 提交。后续如果需要卡片内一键操作，可以升级为飞书自建应用加卡片回调。

内容审核卡片示例：

```json
{
  "msg_type": "interactive",
  "timestamp": "1761098400",
  "sign": "<签名>",
  "card": {
    "config": {"wide_screen_mode": true},
    "header": {"template": "blue",
      "title": {"tag": "plain_text", "content": "待审·发帖｜S3 18:30｜工具实测"}},
    "elements": [
      {"tag": "div", "fields": [
        {"is_short": true, "text": {"tag": "lark_md", "content": "**预评分**\n0.72（候选 1/3）"}},
        {"is_short": true, "text": {"tag": "lark_md", "content": "**AI味**\n2/10 ✓"}},
        {"is_short": true, "text": {"tag": "lark_md", "content": "**参数版本**\nv12 · A/B 臂 B"}},
        {"is_short": true, "text": {"tag": "lark_md", "content": "**加权长度**\n214/280"}}
      ]},
      {"tag": "hr"},
      {"tag": "div", "text": {"tag": "lark_md", "content": "<草稿正文，保留换行>"}},
      {"tag": "note", "elements": [{"tag": "plain_text",
        "content": "选题来源：<url>｜门禁：规则 0 命中｜过期：19:30｜req: content-prepare:2026-10-22:S3-0"}]},
      {"tag": "action", "actions": [
        {"tag": "button", "type": "primary", "text": {"tag": "plain_text", "content": "通过"},
         "url": "https://<panel>/a/<req>?act=approve&t=<token>"},
        {"tag": "button", "type": "default", "text": {"tag": "plain_text", "content": "编辑"},
         "url": "https://<panel>/approvals/<req>?t=<token>"},
        {"tag": "button", "type": "default", "text": {"tag": "plain_text", "content": "重写"},
         "url": "https://<panel>/a/<req>?act=regenerate&t=<token>"},
        {"tag": "button", "type": "danger", "text": {"tag": "plain_text", "content": "拒绝"},
         "url": "https://<panel>/a/<req>?act=reject&t=<token>"}
      ]}
    ]
  }
}
```

**其他卡片**：
- **回复审核**（绿色）：上半部分是原帖作者（粉丝数、关系、画像摘要）和原帖正文；下半部分是档位、意图、风险值、两个候选回复。按钮：“通过①”“通过②”“编辑”“跳过”。
- **调参审核**（紫色）：参数 diff 表、假设、上周 KPI 和 CI，按钮“批准”“驳回”。
- **告警**（红色）：STOP、锁号、影子封禁 RED、侧车反复崩溃、预算异常。
- **日报**（灰色）：日报内容见 9.3 节第 8 步。
- **聚合**：同一时段待审超过 3 条时合并为一张“待审 N 条”摘要卡片，直接链接到面板队列，避免刷屏。

### 12.2 FastAPI 面板

技术选型：FastAPI + Jinja2 + HTMX + Chart.js，不需要前端构建链。只绑定 127.0.0.1，外网通过 Tailscale 或 Cloudflare Tunnel 访问（A11）。会话认证用 bcrypt 密码加 HttpOnly cookie；写操作带 CSRF token。

**页面**：

| 页面 | 内容 |
|---|---|
| 总览 `/` | 阶段、throttle 等级、双开关状态、今日预算进度、XClient 各适配器健康和熔断状态、侧车状态、最近告警、STOP 大按钮 |
| 审批队列 `/approvals` | 待审列表（倒计时）、详情（编辑框实时显示加权长度和门禁复检）、批量通过、影子模式一致率 |
| 发布与日历 `/posts` | 已发帖时间线、每帖快照曲线、待发队列、槽位日历 |
| 互动者 `/interactors` | 画像列表和筛选（关系、毒性）、详情（互动史）、打标签、拉黑、加入目标 KOL |
| 记忆 `/memory` | 人设卡版本（diff、激活）、风格库 CRUD 和锁定、案例库（置顶、删除）、洞察 |
| 数据 `/analytics` | 日和周 KPI 曲线、档位效果、支柱表现、粉丝曲线 |
| 策略 `/params` | 参数注册表、版本列表、版本 diff、一键回滚、A/B 实验状态 |
| 安全 `/safety` | 影子封禁历史、warm-up 进度和人工清单、预算配置（只读显示硬上限）、凭据状态（只显示是否已配置） |
| 运行 `/runs` | LangGraph 线程列表（状态筛选）、线程状态快照、重跑、放弃；任务表、立即运行；日志尾部 |
| 设置 `/settings` | 双开关模式、审核 TTL、适配器优先级覆盖、付费通道开关和月上限 |

**API 清单**（JSON；页面通过 HTMX 调用同一组接口）：

```text
POST /api/auth/login                    POST /api/auth/logout
GET  /api/status                        GET  /healthz
GET  /api/approvals?status=&kind=       GET  /api/approvals/{id}
POST /api/approvals/{id}/decision       {action: approve|edit|regenerate|reject, text?, candidate?, reason?}
GET  /a/{id}?act=&t=                    （飞书落地确认页，仅渲染）
POST /a/{id}/confirm                    （令牌校验后写决定）
GET  /api/switches                      PUT /api/switches {auto_content, auto_reply, phase?}
POST /api/killswitch {mode: stop|pause_writes|resume, reason}
GET  /api/budget/today
GET  /api/posts?from=&to=&kind=         GET  /api/posts/{tweet_id}/metrics
GET  /api/drafts?status=ready
GET  /api/interactors?rel=&q=           GET  /api/interactors/{id}   PATCH /api/interactors/{id}
GET  /api/persona/cards                 POST /api/persona/cards     POST /api/persona/cards/{v}/activate
GET/POST/PATCH/DELETE /api/style[/{id}]
GET/PATCH/DELETE /api/cases[/{id}]      GET  /api/insights
GET  /api/params/registry               GET  /api/params/versions    GET  /api/params/versions/{v}/diff
POST /api/params/rollback/{v}           GET  /api/ab                 POST /api/ab/{id}/abort
GET  /api/shadowban/checks?days=        POST /api/shadowban/run
GET  /api/kpi/daily?days=               GET  /api/kpi/weekly?weeks=  GET  /api/reports/daily/{date}
GET  /api/runs?status=&graph=           GET  /api/runs/{thread_id}   POST /api/runs/{thread_id}/retry
POST /api/runs/{thread_id}/abandon
GET  /api/jobs                          POST /api/jobs/{job_id}/run-now
GET  /api/xclient/health                PUT /api/xclient/overrides
GET  /api/alerts?ack=0                  POST /api/alerts/{id}/ack
```

需要 core 执行的动作（立即运行、重跑、回滚应用、立即自检、重载配置）由面板写入 `control_commands`，core 每 5 秒轮询执行并回写结果。面板只会直接改审批决定、开关、记忆条目这类数据。

### 12.3 content/reply 双开关渐进切换

两个开关各自有四档：

| 档位 | 行为 |
|---|---|
| `manual` | 全部人工审核 |
| `shadow` | 仍然全部人工审核，但系统同时给出“如果自动，是否通过”的判定（写 `shadow_auto_verdict`），用来统计和人工决定的一致率 |
| `partial` | 低风险项自动执行，其余人工审核 |
| `auto` | 全部自动；每天随机抽 10% 事后审计（面板“已自动发出”列表可一键删除），删除视为严重事件 |

各档位的低风险定义：
- **reply partial 自动范围**：Safe 档，风险 r < 0.3，对方关系 ≥ acquaintance 或纯入站问题；Interesting 和 Bold 仍走人工。
- **content partial 自动范围**：门禁 ai_score ≤ 2，预评分高于 7 日中位数，支柱 ∈ {news_take, tool_test}，不含外链和具体公司负面；opinion 和 ask 仍走人工。
- **Bold 档**：在 reply 进入 auto 后还要再满 4 周、并经人工单独开启，才允许自动。

**升级条件**（同时满足；面板显示进度，人工点击升级，系统不自动升级）：

| 升级路径 | reply | content |
|---|---|---|
| manual → shadow | 进入 ramp_2 | 进入 normal |
| shadow → partial | 最近 50 条一致率 ≥ 90%，人工编辑率 < 15% | 最近 20 帖一致率 ≥ 90%，编辑率 < 15% |
| partial → auto | partial 连续 2 周没有严重事件，影子封禁全 GREEN | 同左，且 reply 已在 auto 稳定 ≥ 2 周 |

**自动降级**：一旦出现严重事件（事后被人工删除、收到明显敌意或负面反馈、NEG 激增、影子封禁 YELLOW 及以上、门禁漏判被人工标记），对应开关立即降一档，并告警。

**顺序**：reply 先放开（量大、单条风险小、统计快），content 后放开。

---

## 13. GLM 调用层

### 13.1 接入

用 `openai.AsyncOpenAI(base_url=..., api_key=keyring("glm_api_key"))`。base_url 有两个候选：通用接口 `https://open.bigmodel.cn/api/paas/v4/`，或套餐专属接口（如 Coding Plan 的 `.../api/coding/paas/v4`）。具体取哪个由用户套餐决定，配置项是 `llm.base_url`（A5）。模型名只在 `config/llm.yaml` 里出现，代码只引用档位别名。

```yaml
tiers:
  pro:   {model: "glm-4.7",       timeout_s: 90, max_tokens: 1500, thinking: enabled}
  fast:  {model: "glm-4.5-airx",  timeout_s: 40, max_tokens: 800,  thinking: disabled}
  flash: {model: "glm-4.7-flash", timeout_s: 20, max_tokens: 500,  thinking: disabled}
  embed: {model: "embedding-3",   dims: 1024}
fallback: {pro: [fast, flash], fast: [flash, pro], flash: [fast]}
concurrency: 3            # 套餐并发上限（以实际为准）
daily_soft_cap_calls: 1500
```

如果套餐里有更新的旗舰（如 GLM-5.x），只需改 `pro.model`，第 1 周跑一次 prompt 回归集来对比效果。

### 13.2 模型分工

| 步骤 | 档位 | 理由 |
|---|---|---|
| 选题摘要和翻译、相关度打分 | flash | 量大、结构化、不需要创造力 |
| 发帖草稿（3 候选）、风格化 | pro | 内容质量决定一切，每天只有个位数次调用 |
| 门禁失败后重写 | fast | 有明确反馈，中档足够 |
| 回复意图、情绪、风险分类 | flash | 结构化分类，温度 0 |
| Safe/Interesting 回复生成 | fast | 量中等，要求自然度 |
| Bold 回复生成 | pro | 分寸感要求高 |
| 去AI味判官、候选择优、合规判定 | flash | 判别任务，温度 0，可复现 |
| 人设一致性 | embed + flash | 先用向量过阈值，边界样本再由 flash 判断 |
| 互动者画像重写 | flash | 摘要任务 |
| 案例教训提炼、日复盘洞察 | pro（开 thinking） | 需要归纳推理 |
| 周调参提案 | pro（开 thinking） | 需要在约束内推理，输出严格 JSON |
| 嵌入 | embed（不可用时退到本地 `bge-small-zh-v1.5`） | 套餐不一定包含 embedding，可能另计费（A5） |

### 13.3 Prompt 模板管理

- **文件格式**：`prompts/**/*.md.j2`，YAML front-matter 声明元数据：

```yaml
---
name: reply.interesting
version: 3
tier: fast
temperature: 0.85
response_format: json        # 输出 {"candidates":[{"text":"..."}]}
schema: ReplyCandidates      # pydantic 模型名
---
{% include "_partials/persona.md.j2" %}
...
```

- **注册表**：启动时加载全部模板，计算内容哈希作为 `prompt_version`（例如 `reply.interesting@3#a1b2`），写入 `llm_calls` 和 `drafts.prompt_versions_json`，表现可以追溯到具体 prompt 版本；prompt 本身也可以作为 A/B 参数（registry 里的 enum 类型）。
- **共享片段**：人设卡、风格条目、案例、洞察、algo_kb 约束、`mood_hint` 统一由 `_partials` 注入，各任务模板只写任务部分。
- **输出约束**：要求 JSON 的模板同时使用 `response_format={"type":"json_object"}`（如果接口支持，A5）、在 prompt 里给出 schema 示例、用 pydantic 校验；校验失败先 `json_repair`，再失败就用 flash 跑一次“修复为合法 JSON”。
- **回归集**：`tests/fixtures/prompt_cases/` 放 30 条典型输入。改 prompt 或换模型后运行 `xar eval prompts`，输出门禁通过率、平均 ai_score、长度分布，作为上线前的检查。

### 13.4 失败重试与降级

| 情况 | 处理 |
|---|---|
| 超时、5xx、网络错误 | tenacity 指数退避 2s、4s、8s，最多 3 次，带抖动；然后按 fallback 链换档位 |
| 429 或并发超限 | 尊重 Retry-After，否则退避 10s、30s、60s；信号量降并发 |
| 内容安全拦截（A5 中的错误码） | 不重试、不降级，话题标记 sensitive，草稿丢弃 |
| 余额或套餐额度耗尽 | 全局切到 flash；内容生成暂停（不发低质帖），回复只保留 Safe；飞书告警 |
| JSON 解析失败 | json_repair，然后 flash 修复一次，再失败按 TransientLLMError 处理并重试 |
| 降级产出 | 草稿标记 `quality_degraded=true`，即使在 auto 模式下也强制人工审核 |

用量记账：每次调用写 `llm_calls`，日报汇总调用数、token 数、各档位失败率。成本约为 0（套餐内），外部成本单独列在 16 节。

---

## 14. 部署方案

### 14.1 Windows 本机（NSSM 服务）

**准备**（`deploy/windows/power_setup.ps1`）：
- 电源计划禁用睡眠和休眠；Windows Update 设置活动时段，重启后服务自动拉起。
- 安装 Python 3.12（uv）、Node 20 LTS、Chrome 正式版。
- 运行 `uv sync`，在 sidecar 目录 `npm ci`，`xar init-db`，`xar secrets set ...`，`xar login`（交互登录，在用户会话里以有界面模式进行）。

**服务安装**（`deploy/windows/install_services.ps1` 要点）：

```powershell
$root = "E:\x-auto-runner"; $py = "$root\.venv\Scripts\python.exe"
nssm install xar-core $py "-m xar run"
nssm set xar-core AppDirectory $root
nssm set xar-core AppEnvironmentExtra PYTHONUTF8=1 XAR_ENV=prod XAR_SIDECAR_HEADLESS=new
nssm set xar-core ObjectName ".\hzh" "<密码>"        # 以当前用户运行：keyring/DPAPI/Chrome profile 可用
nssm set xar-core Start SERVICE_DELAYED_AUTO_START   # 开机延迟自启，等网络就绪
nssm set xar-core AppStdout "$root\data\logs\core.out.log"
nssm set xar-core AppStderr "$root\data\logs\core.err.log"
nssm set xar-core AppRotateFiles 1; nssm set xar-core AppRotateOnline 1; nssm set xar-core AppRotateBytes 10485760
nssm set xar-core AppExit Default Restart            # 崩溃自动重启
nssm set xar-core AppRestartDelay 10000              # 10s 后重启
nssm set xar-core AppThrottle 30000                  # 运行 <30s 就退出视为启动失败，NSSM 延长重启间隔
nssm set xar-core AppStopMethodConsole 20000         # 先发 Ctrl+C，给 20s 优雅退出
nssm set xar-core AppStopMethodWindow 5000
nssm set xar-core AppKillProcessTree 1               # 连同 Node 侧车和 Chrome 一起结束

nssm install xar-panel $py "-m xar panel --host 127.0.0.1 --port 8710"
nssm set xar-panel AppDirectory $root
nssm set xar-panel ObjectName ".\hzh" "<密码>"
nssm set xar-panel Start SERVICE_AUTO_START
nssm set xar-panel AppExit Default Restart
nssm set xar-panel AppRestartDelay 5000
# 外部看门狗：计划任务每 5 分钟运行 watchdog.ps1（心跳文件超过 3 分钟未更新 → nssm restart xar-core + 飞书告警）
schtasks /Create /TN xar-watchdog /SC MINUTE /MO 5 /RU hzh /TR "powershell -File $root\deploy\windows\watchdog.ps1"
```

**有界面与无界面**（A19）：服务运行在 Session 0，看不到界面，所以服务模式下 Chrome 用 `headless: "new"`（新版无头模式的指纹接近有界面模式）。需要人工介入时（登录、验证码），运行 `xar login`：先 `nssm stop xar-core`，在用户会话里用同一个 profile 以有界面模式打开，处理完再启动服务。备选方案：用“用户登录时运行”的计划任务代替 NSSM，以有界面模式运行（需要自动登录 Windows），作为风控收紧时的选项。

**优雅退出顺序**：收到 Ctrl+C 后暂停调度器，等待正在进行的动作（最长 60 秒；正在打字的直接清空、不提交），关闭侧车和浏览器，flush 数据库，删除心跳文件。

**崩溃恢复**：NSSM 负责重启进程，`runtime/app.py` 的启动恢复负责续跑或终止中断的线程（见 4.9 节），catchup 负责补跑漏掉的关键任务。

### 14.2 备份

`xar backup` 每天 04:00 运行：
- 用 `sqlite3.Connection.backup()` 在线一致性备份 `state.db` 和 `checkpoints.db`（不能直接复制 WAL 模式的文件），加上 `cookies.enc`、`config/`、`persona/`，打包成 `data/backups/xar-YYYYMMDD.tar.zst`。
- 本地保留 14 份；可选用 rclone 同步到对象存储（异地）。
- 每周对最新备份跑一次 `PRAGMA integrity_check` 恢复演练。

### 14.3 迁移到 Linux VPS

1. **代码层前提**（从第 1 周开始遵守）：路径全部用 `pathlib` 加 `data_dir` 配置；不用 Windows 专属 API；keyring 后端可替换；时区显式使用 Asia/Shanghai，不依赖系统时区；所有状态都在 `data/`。
2. **VPS 准备**：Ubuntu 24.04；安装 uv、Python 3.12、Node 20、Chrome 正式版、`fonts-noto-cjk`（否则中文显示成方块，也会成为指纹异常）；可选安装 Xvfb 用于有界面模式；创建专用用户 `xar`。
3. **网络出口**（关键风险）：机房 IP 风控等级高，账号从家宽跳到机房 IP 容易触发验证。方案是购买静态住宅或 ISP 代理（同城或同省），通过 `XAR_PROXY` 只给侧车 Chrome 和 twikit 使用；fxtwitter、GLM、飞书直连。
4. **停机导出**：Windows 上先创建 STOP 文件，`nssm stop xar-core xar-panel`，运行 `xar backup --final`，再用 `xar export-cookies` 导出最新 cookie（加密）。
5. **浏览器 profile 不能直接搬**（A20）：Chrome 的 cookie 加密和操作系统绑定（Windows 用 DPAPI，Linux 用 keyring 或固定密钥），整个 profile 复制过去也无法解密。两个办法：①在新 profile 里通过 CDP `Network.setCookies` 导入 cookie；②通过 noVNC 或 SSH 转发有界面模式，人工重新登录一次（换 IP 后通常还会要求验证，推荐这个办法）。
6. **导入**：`xar restore xar-final.tar.zst` 后运行 `xar migrate`（schema 版本校验）、`PRAGMA integrity_check`、`xar secrets import`（从加密导出包导入到 Linux 的 keyring）。
7. **systemd**（`deploy/linux/xar-core.service`）：

```ini
[Unit]
Description=xar core
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
User=xar
WorkingDirectory=/opt/x-auto-runner
Environment=PYTHONUTF8=1 XAR_ENV=prod XAR_SIDECAR_HEADLESS=new
ExecStart=/opt/x-auto-runner/.venv/bin/python -m xar run
Restart=always
RestartSec=10
KillMode=control-group
TimeoutStopSec=30
WatchdogSec=180              # 应用内 sd_notify("WATCHDOG=1") 每 60s 一次，替代 Windows 看门狗
NotifyAccess=main
Type=notify

[Install]
WantedBy=multi-user.target
```

`xar-panel.service` 同理（`Type=simple`）；`xar-backup.timer` 每天 04:00 运行。最后 `systemctl enable --now xar-core xar-panel xar-backup.timer`。
8. **灰度恢复**：先在 PAUSE_WRITES 模式下跑 24 小时（只浏览和回收），跑一次影子封禁自检；GREEN 才删 PAUSE_WRITES；迁移后一周内预算乘 0.7（当作小型 warm-up）。
9. **回退预案**：Windows 端保留 7 天不删，VPS 出问题就反向执行上面的步骤。

---

## 15. 开发里程碑（8 周）

**账号节奏和开发并行**：第 2 周末开始 warm-up（全人工审核），第 4 周末结束；第 5 周进入 ramp 阶段开始发帖；第 8 周进入 normal。

| 周 | 主题 | 交付物 | 验收标准 |
|---|---|---|---|
| W1 | 骨架与读通道 | 仓库初始化（uv、ruff、pre-commit、DECISIONS.md）；settings 和 config 加载；4 个 migration 全部建表；侧车 `start.mjs` 跑通 XActions MCP stdio；`list_tools` 回填 `tool-map.json`；读操作（timeline、notifications、get_tweet、self_stats）；fxtwitter 客户端；`xar login`；XClient 契约测试框架；**核验 A1–A5、A21** | Python 侧拉取 For You 40 条和通知 20 条，字段完整；fxtwitter 对已知推文返回 views 和 likes；migration 重复执行幂等；假设核验报告写入 DECISIONS.md |
| W2 | 安全层、拟人、写通道 | GuardedXClient、BudgetGuard、RateLimiter、KillSwitch、PhaseMachine；humanize 全部分布；侧车 `human_type`、`human_scroll`、`browse_session`；post、reply、like 加幂等和回读校验；XActor 优先级与抢占；LangGraph 骨架加 checkpointer 加 browse 子图；STOP 和 PAUSE_WRITES | 1000 次并发模拟预留，预算不超限；STOP 后 ≤3 秒停止，打字中的文本不发出；一次真实浏览会话 5–8 分钟，日志里停留和滚动分布符合配置；测试号成功发帖、回复、点赞各 1 次且回读校验通过；**warm-up D1 启动** |
| W3 | 记忆、回复、审核 | 三层记忆仓储和检索；GLM 网关（档位、降级、prompt 注册表、json_guard）；回复分档决策树；去AI味门禁（规则加判官）；reply_scan 和 reply_one；approval 加 interrupt 加恢复轮询；飞书卡片；面板 MVP（总览、审批、STOP、开关） | 通知到达后 ≤2 分钟收到飞书卡片，手机审批后回复成功发出；审批等待中重启 core，恢复后正常；门禁在 50 条 AI 味样本上召回 ≥90%、在 50 条人写样本上误杀 ≤15% |
| W4 | 内容管线与调度 | sources（RSS、浏览热点、手动投喂）；选题评分；草稿、风格化、分句（twitter-text）、预评分、去重；content_prepare 和 publish 子图；DayPlanner 加 APScheduler 全表加 catchup；warm-up 阶段自动推进 | 连续 3 天空跑（只生成不发）：每个槽位前 120 分钟进入审核队列；加权长度 100% ≤280；发帖间隔 100% ≥2.5h；静默时段 0 动作；人设卡占位可替换 |
| W5 | 数据回收、日复盘、自检 | collect（6 个快照点）、粉丝快照与归因；指标计算；daily_review（案例、洞察、风格分、日报卡片）；影子封禁四维（twitterapi.io 观察者）加定级处置；**ramp_1 开始发帖** | 每帖 6 个快照完整率 ≥95%；00:40 收到日报；人为注入 fail 后 YELLOW 和 RED 的处置正确（预算减半、停发）；四维检测各输出 pass、fail 或 inconclusive 及证据 |
| W6 | 优化引擎 | 参数注册表、版本、回滚；A/B 分臂（槽位轮转）与贝叶斯判定；weekly_tune 子图（含审批）；人设漂移检测；面板的策略页和数据页 | 用 4 周合成数据仿真：每周改动 ≤3 项；连续两周期变差时自动回滚，回滚后参数快照逐字段一致；safety_locked 参数提案为 0；A/B 在注入 +15% 真实效应时 2 周内判出胜者 |
| W7 | 降级链与部署 | twikit 适配器（cookie 同源）；twitterapi.io 只读适配器；官方 API 适配器（post、reply，默认关）；路由熔断；混沌测试（杀侧车、过期 cookie、fxtwitter 宕机）；NSSM 服务、看门狗、备份恢复脚本；RUNBOOK | 杀掉 Node 后 ≤60 秒自动恢复，期间读操作自动切到 twikit；cookie 失效时写操作暂停并告警、不降级乱写；72 小时连续运行无人工干预、无崩溃泄漏；备份在另一台机器恢复后 integrity_check 通过 |
| W8 | 渐进自动化与稳定化 | reply 进入 shadow（ramp_2）；面板补全（互动者、记忆、运行）；首次真实周调参；prompt 回归集；Linux 迁移演练（可选，在测试 VPS 上用测试号） | 7×24 运行 7 天：预算违规 0、重复发帖 0、STOP 演练通过；reply 影子一致率可在面板查看；首次周调参产出 ≤3 项提案并完成审批 |

**W8 之后**：reply 依次进入 partial 和 auto（预计第 10–12 周），content 进入 shadow 和 partial（第 12 周起），VPS 正式迁移（在本机稳定 4 周后），视频和媒体管线，Bold 自动化评估。

---

## 16. 风险清单

| # | 风险 | 可能性/影响 | 应对 |
|---|---|---|---|
| R1 | **封号或锁号**（自动化被识别） | 中/致命 | 两周 warm-up 加 ramp；硬预算写死在代码里；拟人行为模型；固定家宽 IP 和持久指纹；真实 Chrome 加 stealth；默认不自动关注；验证码或锁号自动 STOP；两步验证和备用邮箱人工兜底；永远不批量做相似动作 |
| R2 | **影子封禁或降权** | 中/高 | 四维每日自检加三级处置；Bold 和外链受控；负向代理指标监控；版本生效后出问题自动回滚；回复间文本相似度约束 |
| R3 | **XActions 失效**（X 改版、单一维护者停更、610 星社区小） | 高/高 | 固定 commit 并 fork 到自己的仓库；每天跑契约测试和探针；关键写操作走自研扩展工具，降低依赖面；twikit 备选；tool-map 配置化，可快速修补；官方 API 作为付费逃生通道 |
| R4 | **twikit 失效**（GraphQL queryId 轮换、反爬升级） | 中/中 | 只作备选和读通道；版本固定；twitterapi.io 只读兜底 |
| R5 | **登录态失效或验证挑战** | 中/中 | 每 6 小时检查健康；写操作不降级；飞书告警和 RUNBOOK 指导人工 `xar login`；cookie 加密备份 |
| R6 | **fxtwitter 不可用或字段变化** | 中/中 | 每日字段契约校验；降级到 XClient 的 `get_tweet`；指标缺失时复盘降级为部分样本，不做调参 |
| R7 | **人设漂移**（LLM 同质化、优化器把风格调偏） | 中/高 | 人设卡只能人工修改；优化器只调权重、不改内容；locked 风格条目；每周用嵌入质心距离加 LLM 一致性评分做漂移检测，超阈值告警并冻结 content 类调参；门禁含人设一致性检查 |
| R8 | **AI 味或低质被识别**（用户反感、算法降权） | 中/高 | 规则加判官双层门禁；突发度统计；风格条目轮换；初期全人工审核；失败案例反哺 |
| R9 | **内容合规与舆情**（敏感话题、虚假信息、对线） | 低中/高 | taboo 话题硬过滤；事实断言要求来源；Bold 门槛与配额；敌意不回、同线程 ≤2 次；严重事件自动降级开关 |
| R10 | **GLM 套餐限额、模型下线或拦截** | 中/中 | 档位别名配置化；fallback 链；额度耗尽时暂停内容生成，只保留 Safe 回复；本地嵌入备选；prompt 回归集保证换模型时有数据可比 |
| R11 | **外部成本超支** | 低/低 | twitterapi.io 和官方 API 各设 $5/月硬上限，超出自动关闭；日报列出成本；官方 API 默认关 |
| R12 | **优化噪声**（样本少导致误调） | 高/中 | 每周 ≤3 参数、每项 ≤1 step；贝叶斯判定加 2 周上限；连续两周期才回滚；槽位平衡分臂；优先在 reply 上做实验 |
| R13 | **SQLite 锁冲突或损坏** | 低/高 | core 为唯一主写者，面板写入量很小；WAL 加 busy_timeout；业务和检查点分库；每日在线备份加每周恢复演练 |
| R14 | **主机睡眠、更新重启、断网** | 中/中 | 电源设置；NSSM 延迟自启；catchup 补跑；看门狗；断网时 XActor 暂停，恢复后不追补行为类任务 |
| R15 | **VPS 迁移触发风控** | 中/高 | 静态住宅或 ISP 代理同地域；重新人工登录；PAUSE_WRITES 灰度 24 小时；迁移后一周预算乘 0.7 |
| R16 | **密钥泄露** | 低/高 | keyring 加 Fernet；gitleaks；日志脱敏；面板只监听本地，经 Tailscale 访问；飞书链接用一次性 HMAC 令牌 |
| R17 | **平台政策和服务条款风险** | 客观存在/高 | 这是自有账号的自动化，保持真实人设、不冒充、不刷量；初期全人工审核；所有动作留痕可审计；需要时可整体切到官方 API 合规通道（成本可控：每月约 120 帖约 $1.8） |
| R18 | **审批延迟导致回复时效丢失** | 中/低 | 审批 TTL 加过期作废；卡片聚合；reply 优先放开到 partial |

---

## 17. 假设清单与待补充接口

**假设**（第 1 周集中核验，结果写入 `docs/DECISIONS.md`）：

- **A1** XActions MCP 的工具名和参数以 `list_tools()` 实测为准，`tool-map.json` 只给了占位映射。
- **A2** XActions 能以库的形式加载，并和 xar 扩展工具共享同一个 Puppeteer Browser 实例。不成立时，侧车自己管理浏览器，XActions 只作为动作和选择器函数库使用。
- **A3** XActions MCP Server 支持 stdio transport。只支持 HTTP/SSE 时，改为 `sse_client` 连接 127.0.0.1 随机端口，其余不变。
- **A4** fxtwitter 的 status 和 user 端点字段如 9.1 节所列，views 和 bookmarks 可能为 null；免费接口没有正式 SLA。
- **A5** GLM 模型名（glm-4.7、glm-4.5-airx、glm-4.7-flash、embedding-3）、套餐专属 base_url、JSON mode 支持情况、内容拦截和额度耗尽的错误码，均以智谱当前文档和用户套餐为准。
- **A6** APScheduler 3.x 的 jitter 语义（单向还是双向）以实测为准；行为类任务改由 DayPlanner 物化，不受影响。
- **A7** 账号不是 Premium，按 280 加权单位（CJK 每字计 2）计算；如果开通 Premium，放宽 `length_target` 和 thread 策略。
- **A8** 外链降权是业界的普遍观察，不一定是 x-algorithm 的显式信号；默认把外链放到自回复，“是否外置链接”可以作为 A/B 参数验证。
- **A9** X 未登录时不能搜索，影子封禁检测必须依赖外部观察者（twitterapi.io 或观察号）。
- **A10** 飞书自定义机器人卡片按钮只支持 URL 跳转；需要回调时升级为自建应用。
- **A11** 面板通过 Tailscale 或 Cloudflare Tunnel 供手机访问，用户需要选定其一。
- **A12** 运营账号是新号或低活跃号；x-algorithm 新账号加权的窗口长度未知，用 ramp 期前置高质量内容的方式应对。
- **A13** 冻结预算 4、15、30 理解为绝对上限，±30% 随机在“上限 ÷ 1.3”的基准上进行，并截断到上限以内。
- **A14** warm-up 的“只浏览”包含少量点赞，点赞被视为浏览行为的自然组成部分；如果用户要求零点赞，把 warmup_a 和 warmup_b 的点赞设为 0 即可。
- **A15** 我方回复和外链自回复不触发“同作者连续发帖衰减”。
- **A16** 加权互动的初始权重借用 2023 年开源 heavy ranker 的配置；x-algorithm 新版的多动作加权以蒸馏结果为准，后续再替换。
- **A17** LangGraph 1.x 的 `interrupt`、`Command(resume=)`、`RetryPolicy`、`AsyncSqliteSaver` API 稳定；开工时锁定版本。
- **A18** X 账号分析页对非 Premium 受限，不依赖主页访问数等后台指标。
- **A19** Windows 服务（Session 0）下 Chrome 以 headless=new 运行，指纹风险可以接受；服务以用户身份运行，才能访问 DPAPI 和 keyring。
- **A20** Chrome cookie 加密和操作系统绑定，profile 不能跨 OS 复用，迁移时需要导入 cookie 或重新登录。
- **A21** twitterapi.io 提供高级搜索、推文回复、用户搜索端点，按量计费，自检用量每月在 $5 以内。

**待用户补充的部分和对应接入点**：
- **人设语料**：放到 `persona/persona_card.yaml`、`persona/corpus/`、`persona/style_seeds.yaml`，然后运行 `xar persona import`。导入时会生成人设卡 v1、风格库种子、语料嵌入质心，并校准门禁的人设一致性阈值。
- **现有记忆系统**：实现 `memory/external.py` 里的 `MemoryProvider` 协议（`retrieve(query, scope, k) -> list[MemorySnippet]`、`write(event) -> None`），在 `settings.yaml` 设置 `memory.external.enabled: true` 和 token 配额；支持只读增强和双写两种模式。
- **内容源列表**：填入 `config/sources.yaml`。每条包括 `type`（rss、web、x_list、github_trending、manual）、`url`、`pillar`、`trust`（0–1）、`lang`、`poll_cron`；新的源类型通过实现 `content/sources/base.py` 的 `SourceProvider.fetch() -> list[TopicCandidate]` 扩展。
- **主动回复目标 KOL 池**：填入 `persona/kol_targets.yaml`（handle、领域标签、优先级）。

---

以上方案全文完。

**答卷元信息（来自 Cursor Agent，claude-opus-5-5-high）**

- 执行引擎：cursor-agent v2026.08.31，`--mode plan`（只读），模型显式指定 `claude-opus-5-5-high`（Claude Opus 5.5 1M High，用户点名 opus 5.5 的 high 力度档），运行约 17 分钟。
- 项目零改动：`E:\x-auto-runner` 保持空目录（方案全文另存于项目外 `%TEMP%\xrunner_plan_opus55.txt` 供查证）。
- 我方补充验证（3 次检索往返，均在 2026-10-22 完成，与方案主体一致）：XActions 仓库、npm 包与免费 MCP Server 形态属实（[github.com/nirholas/XActions](https://github.com/nirholas/XActions)）；fxtwitter 免登录 JSON 端点 `api.fxtwitter.com/status/<id>` 属实（[相关讨论](https://github.com/obsidianmd/obsidian-clipper/issues/331)）；智谱走 OpenAI 兼容接口属实，且 2026 年已上线 GLM-4.7 系列并存在更新的 GLM-5.3-Flash/FlashX 档位（[Z.AI GLM-4.7 文档](https://docs.z.ai/guides/llm/glm-4.7)、[智谱模型总览](https://docs.bigmodel.cn/cn/guide/start/model-overview)）——方案 13.1 节的“档位别名 + 只改 pro.model 即可换新旗舰”设计正好承接这一点，A5 假设核验时可直接把 pro 档切到 GLM-5.x。
agentId: agent_80e92265-8235-4585-9e83-e8f4e87f30ea (use SendMessage with to: 'agent_80e92265-8235-4585-9e83-e8f4e87f30ea' to continue this agent)
<usage>subagent_tokens: 580508
tool_uses: 21
duration_ms: 1567022</usage>