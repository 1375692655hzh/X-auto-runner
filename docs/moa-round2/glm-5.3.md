# 来源：glm 岗（GLM-5.3 原生）· MoA 第二轮 · 2026-10-22

> 执行声明：本轮先做了四项联网核验（XActions 现状、twikit 2026 维护状态、fxtwitter 接口字段、智谱 OpenAI 兼容接入），全部与锁定决策吻合，其中 twikit 上游 2026 年实际已停滞失修——这不动摇"XActions 为主"的决策，但方案中将 twikit 降为代码级参考、把 twitterapi.io 提为运维级逃生通道。

## 一、执行摘要

一台长期运行的 Agent 服务（本机 Windows 起步，后迁 Linux VPS），以浏览器自动化（XActions，Node 侧车进程）为手脚、智谱 GLM（OpenAI 兼容接口）为大脑、LangGraph + SQLite checkpointer 为脊柱、三层自建记忆账本为经验、APScheduler 为生物钟，围绕一个中文 AI/科技人设持续完成"看—想—写—发—回—复盘—调参"闭环。安全底线：预算硬顶 + 两周 warm-up + STOP kill switch + 四维影子封禁每日自检 + 人工审核渐进放权。全部状态收敛于单文件 SQLite，可整体搬迁。

核验结论（本轮检索）：
- XActions 活跃维护，npm 包 `xactions`，MCP server + CLI + 浏览器脚本齐全，无 API 费——主选成立。
- twikit 2026 年上游停滞、X 内部接口变更未修复，社区已在讨论迁移与 fork——备选地位降级为"接口范式参考"，运行时逃生顺序改为 XActions → twitterapi.io（按量付费）→ 官方 API（$0.015/帖，仅保命用）。
- fxtwitter 公开 JSON 接口确认：`GET https://api.fxtwitter.com/:screen_name/status/:tweet_id` 返回 `tweet.likes / replies / retweets / quotes / views` 整数字段，免登录，限速 1000 req/min/IP——每天几十帖 × 4 个采样点，用量不足限速 1%。
- 智谱 OpenAI 兼容确认：`base_url=https://open.bigmodel.cn/api/paas/v4/`，OpenAI SDK 直接换 key 即用。

## 二、总体架构

```
┌────────────────────────── 触发层 APScheduler（生物钟） ──────────────────────────┐
│ 07:50 日规划 | 5-6个互动窗口 | 3-4个发帖slot | 23:00 复盘 | 周日22:00 调参 | 06:00 封禁自检 │
└───────────────────────────────────┬───────────────────────────────────┘
                                    ▼
┌──────────────────── 编排层 LangGraph（SqliteSaver 断点续跑 + interrupt） ─────────┐
│  日流程图 → 内容管线子图 / 互动子图 / 复盘子图                                    │
│  人工审批节点 interrupt ── 飞书卡片 ── FastAPI 回调 ── Command resume             │
└──┬──────────────┬──────────────┬──────────────┬──────────────┬────────────────┘
   ▼              ▼              ▼              ▼              ▼
┌────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐
│ 接入层 │  │ 大脑 GLM │  │ 记忆三层 │  │ 真人感层 │  │ 数据回收      │
│ XClient│  │ 智谱套餐 │  │ 短/中/长 │  │ 分档+节奏 │  │ fxtwitter    │
│ 统一抽象│  │ 成本≈0  │  │ SQLite  │  │ +拟人行为 │  │ t+1/6/24/48h │
└───┬────┘  └──────────┘  └──────────┘  └──────────┘  └──────┬───────┘
    ▼                                                  ┌──────▼───────┐
┌─────────────────────────────────────┐                │  优化引擎      │
│ XActions(Node侧车/Puppeteer)  主实现 │                │ 每日复盘      │
│ twitterapi.io  运维级逃生            │                │ 每周版本化调参 │
│ 官方API $0.015/帖  保命兜底          │                │ A/B + 自动回滚 │
│ twikit  仅接口范式参考(已失修)        │                └──────────────┘
└─────────────────────────────────────┘
        全局横切：预算器 | STOP kill switch | 四维影子封禁自检 | 风险词门禁
```

单进程守护：`xrunner.daemon` 一个 Python 进程承载 APScheduler + LangGraph + FastAPI 面板，XActions 作为被其拉起/看护的 Node 子进程（sidecar）。SQLite 开 WAL，写操作全部收敛到 daemon 主进程；面板只读 + 通过 `commands` 表投递单向命令，规避 SQLite 并发写。

## 三、目录结构

```
E:\x-auto-runner\
├─ xrunner\
│  ├─ daemon.py            # 单进程入口：APScheduler + FastAPI(uvicorn) + sidecar看护
│  ├─ config.py            # 预算/时段/开关/STOP检测
│  ├─ graph\
│  │  ├─ daily.py          # 主日流程图
│  │  ├─ content.py        # 内容管线子图
│  │  ├─ engagement.py     # 互动子图
│  │  └─ review.py         # 复盘+周调参子图
│  ├─ xclient\
│  │  ├─ base.py           # XClient 抽象接口（Protocol）
│  │  ├─ xactions_impl.py  # 主实现：驱动Node侧车
│  │  ├─ escape_http.py    # twitterapi.io / 官方API 逃生实现
│  │  ├─ fxtwitter.py      # 只读数据回收（独立，不参与写路径failover）
│  │  └─ health.py         # 滑动窗口成功率监测 + 自动failover
│  ├─ memory\
│  │  ├─ short_term.py     # 当日上下文（滚动摘要）
│  │  ├─ profiles.py       # 互动者画像+互动历史
│  │  └─ persona.py        # 人设卡/风格库/案例库装载
│  ├─ persona\             # 人设资产（版本化YAML + SQLite留痕）
│  ├─ humanize\            # Humanizer-zh 规则门禁 + AI味判分
│  ├─ safety\              # 预算器/影子封禁自检/STOP/风险词
│  ├─ metrics\             # fxtwitter回收 + KPI报表
│  ├─ review\              # 复盘Prompt资产 + 调参引擎 + A/B
│  ├─ panel\               # FastAPI审核面板 + 飞书卡片回调
│  └─ sidecar\             # XActions子进程管理（启动/健康/重启/日志）
├─ sidecar\                # npm i xactions 独立安装，含专用Chrome profile
├─ data\
│  ├─ xrunner.db           # 唯一状态库（WAL）
│  ├─ browser_profile\     # 专用浏览器指纹profile（随库一起迁移）
│  └─ backups\             # 每日SQLite备份，保留14天
├─ logs\
└─ STOP                    # kill switch：文件存在即全停（只浏览都不做，进程保活）
```

## 四、接入层：XClient 统一抽象

接口契约（所有实现必须满足，保证四条路线可切换）：

```python
class XClient(Protocol):
    async def post_tweet(text: str, media: list[Path] = []) -> TweetRef: ...
    async def reply(tweet_id: str, text: str) -> TweetRef: ...
    async def like(tweet_id: str) -> None: ...
    async def get_notifications(since: datetime) -> list[Notification]: ...
    async def get_home_timeline(limit: int) -> list[Tweet]: ...
    async def search(q: str, limit: int) -> list[Tweet]: ...
    async def get_tweet_stats(tweet_id: str) -> TweetStats: ...   # 自己帖表现
    async def check_shadowban() -> ShadowbanReport: ...           # 四维报告
    async def health_probe() -> bool: ...
```

实现路由与健康度：

| 优先级 | 实现 | 用途 | 说明 |
|---|---|---|---|
| 1 | `xactions_impl`（XActions 侧车） | 全部读写 | 唯一日常实现；专用 Chrome profile 常驻、固定住宅 IP、不 headless 切换，指纹稳定压倒一切 |
| 2 | `escape_http`（twitterapi.io） | 读写逃生 | XActions 连续 5 次失败自动切入；按量付费，仅保命 |
| 3 | `escape_http`（官方 API） | 只写兜底 | $0.015/帖；仅当 1、2 双挂时保发帖/回复，不承担读 |
| 参考 | `twikit_impl`（不部署） | 接口范式 | 2026 上游已停滞失修；保留 `base.py` 兼容其能力面即可，触发切换需人工确认 |
| 独立 | `fxtwitter.py` | 只读数据回收 | 不参与写路径 failover，与浏览器链路解耦，天然旁路 |

健康度规则：每个实现维护 1 小时滑动窗口成功率写 `health_log`；failover 事件即时飞书告警；恢复后自动切回主实现。**注意**：failover 只救会话，不救指纹——切到 HTTP 实现期间暂停一切拟人行为模拟，因为真实浏览器与 HTTP API 的指纹不一致本身就是风险信号。

## 五、编排层：LangGraph + APScheduler

主日流程图 `daily.py`：

```
trigger → context_load(三层记忆装配) → plan_day(今日预算抽取±30%、选题、排slot)
        ├─ content_pipeline × N
        ├─ engagement_loop × M
        → evening_review(23:00 复盘) → memory_consolidate(案例沉淀+短期滚动)
```

内容管线子图 `content.py`：

```
topic_scout(内容源池抓取) → persona_match(人设过滤+角度选择)
→ draft(GLM生成) → humanize_gate(硬规则+AI味判分, 不过重写≤2次)
→ risk_check(风险词/雷区话题硬门禁) → budget_check
→ interrupt[approval]  ←—— 飞书卡片: 通过/驳回/改后通过
→ persona_gate(拟人延迟: 非整点、slot内随机抖动±40min)
→ publish → register(登记post表+挂回收任务)
```

互动子图 `engagement.py`：

```
scan(通知/时间线/关注者动态) → filter(画像分数+48h相关性+预算)
→ classify(Safe/Interesting/Bold 分档) → draft_reply(GLM)
→ humanize_gate → risk_check → interrupt[reply_approval](独立开关)
→ act_sequence(阅读8-40s→思考15-120s→打字→回复/点赞, 全程拟人节奏)
→ record(interaction_history + 画像更新)
```

复盘子图 `review.py`（23:00 每日 / 22:00 周日）：

```
collect_metrics(48h窗口齐活的帖) → kpi_compute
→ daily_review(GLM诊断: 哪类内容好/差、回复转化、互动者情绪)
→ [周日] tune_propose(最多2-3参数, 每参数带hypothesis)
→ [周日] interrupt[param_approval](面板diff视图人工确认)
→ apply_version(写strategy_versions, parent链) → schedule_ab
```

机制要点：
- SqliteSaver checkpointer 挂在图上，任一节点崩溃/进程重启，从最近 checkpoint 续跑；interrupt 状态持久化，飞书卡片回调（FastAPI 收 `card.action`）转换为 LangGraph `Command(resume=...)`。
- APScheduler job 与 LangGraph 的分工：Scheduler 只负责"点火"（cron 触发图的入口 + 数据回收 interval 采样点），一切业务状态在图和 SQLite 里，Scheduler 重启零损失。
- 时钟全部固定 UTC+8 存储并带时区字段，防 VPS 迁移时区漂移。

## 六、记忆层：三层账本（自建，不引入 letta/mem0）

| 层 | 载体 | 内容 | 生命周期 |
|---|---|---|---|
| 短期 | `short_term_context` | 当日已发/已回/已赞摘要、今日选题池状态、会话内上下文滚动摘要 | 当日有效，23:00 蒸馏进中期后清空 |
| 中期 | `interactor_profiles` + `interaction_history` | 每个互动者：熟悉度、立场倾向、兴趣话题、上次互动、回复档位偏好、情绪状态（对齐 Pulse/erii 思路）；逐条互动留痕 | 长期累积，画像按滑动窗口衰减 |
| 长期 | `persona_cards` + `style_rules` + `cases` | 人设卡（版本化）、风格库规则（启用/禁用可调）、成功/失败案例库（带指标与教训，供生成时 few-shot 检索） | 永久，人设卡变更走版本链 |

装配策略：`context_load` 节点按当前任务类型选择性装记忆——写帖装人设卡+风格库+近期成功案例 top3+当日上下文；回复装目标互动者画像+与其的历史互动 3 条+人设卡精简版。控制每次 GLM 调用的上下文在 4k token 内。

## 七、安全与风控层

### 7.1 预算器（config 默认值）

| 参数 | 值 |
|---|---|
| daily_post_budget | 4（每日 ±30% 随机抽取） |
| daily_reply_budget | 15（±30%） |
| daily_like_budget | 30（±30%） |
| min_post_interval | 2.5h（对齐 x-algorithm 同作者连续发帖衰减） |
| action_gap | 相邻拟人动作 25-90min 随机 |
| active_hours | 作息窗口制：主力 08-13、15-22 点；凌晨 1:30-7:30 睡眠（10% 概率保留深夜一条行为，模拟夜猫） |
| 发帖 slot | 从黄金时段（8-9/12-13/18-20/21-23）选 3-4 个，slot 内 ±40min 抖动 |

### 7.2 Warm-up 日程

| 周 | 发帖 | 回复/日 | 点赞/日 | 行为 |
|---|---|---|---|---|
| W1 | 0 | ≤2 | ≤8 | 纯浏览：每日 2-3 个 15-45min 会话，滚动、停留、10% 概率点开媒体 |
| W2 | 0 | ≤3 | ≤15 | 同上 + 关注少量（≤3/日）同赛道真人号 |
| W3 | ≤1/日 | ≤6 | ≤20 | 内容管线上线，全人工审 |
| W4 | ≤2/日 | ≤10 | ≤25 | 开始数据回收与每日复盘 |
| W5+ | ≤4/日（±30%） | ≤15（±30%） | ≤30（±30%） | 满额运行，渐进切自动 |

### 7.3 STOP kill switch

- `E:\x-auto-runner\STOP` 文件存在 → 所有触发任务秒级短路，进行中的拟人序列安全走完当前动作后停止，浏览器保活（不杀 profile，防指纹突变），飞书推送确认。
- 面板提供一键创建/删除 STOP 的按钮（写 commands 表由 daemon 执行）。

### 7.4 四维影子封禁每日自检（06:00）

| 维度 | 检测方法 |
|---|---|
| 搜索建议屏蔽 | 登出态/干净会话输入 handle 前缀，看搜索建议是否出现本账号 |
| 搜索封禁 | 登出态搜 `from:handle`，是否有结果 |
| 幽灵回复 | fxtwitter 拉自己帖的回复线程，比对本地回复记录是否对外可见（fxtwitter 天然是登出态视角，零成本旁证） |
| 回复降权 | 回复他人帖后，登出态按该帖关键词搜索，看自己的回复是否被折叠/排序异常 |

响应梯度：单维异常 → 当日预算 ×0.5；连续 2 日任一异常 → 暂停发帖/回复、只浏览 + 飞书告警人工介入；恢复后从半预算重新爬坡。

### 7.5 风险词门禁

雷区硬清单：政治敏感、医疗断言、投资建议、色情、引战身份话题。命中即拒，不进人工审。中文语境另加：绝对化表述（"肯定/100%"）降权警告。

## 八、内容生产管线细节

### 8.1 内容源池（默认池先行）

- 中文：机器之心、量子位、新智元、AIbase 等账号时间线（XActions 抓取）+ 对应 RSS
- 英文：xAI/OpenAI/Anthropic 官宣、HN 首页 RSS
- 论文：arXiv cs.AI/cs.CL 按 GenAI 关键词过滤
- 角度模板四型：转述+自己判断 / 观点评论 / 实测体验 / 日常观察吐槽

### 8.2 Humanizer-zh 去AI味门禁（硬规则 + 判分双保险）

硬规则（命中即打回重写，≤2 次，仍不过则弃稿换选题）：
- 禁词表：首先/其次/总之/综上所述/值得注意的是/在当今时代/赋能/抓手/闭环/底层逻辑(滥用态)/不难发现
- 结构禁令：三段式排比、"1. 2. 3."列表体（社交语境）、每句都以"我觉得"开头
- 句长节奏：平均句长 ≤22 字，单句 ≤40 字；单帖 ≤140 字
- 数字具体化：禁"很多/大量"，必须具体数

AI 味判分（glm-flash 执行）：0-10 分，>6 打回。允许且鼓励：口头语（"说实话""就离谱""讲真"）、不完美表达、鲜明立场、自嘲、中英混用（人设卡定比例）。

### 8.3 人设卡骨架（待用户语料填充）

身份设定 / 专业领域与深度边界 / 立场偏见清单（有偏见才像人）/ 口头禅与语气词 / 雷区话题（绝不碰）/ 观点鲜明度 0-10 / 自嘲程度 0-10 / 中英混用度 / 每日主题配比。人设卡变更走版本链，与调参一样可回滚。

## 九、互动管线细节

回复分档：
- Safe：赞同 + 补充一条信息
- Interesting：抛新角度 / 轻度反问，制造对话钩子
- Bold：直接不同意 + 论据（仅对画像熟悉度高、本身喜欢观点交锋的互动者）

档位决策 = 互动者画像 × 原帖话题风险 × 当日 Bold 配额（Bold ≤ 全部回复的 20%）。被互动者拉黑/举报信号记入画像负分，自动降频。

行为拟人：回复前强制"阅读 8-40s → 思考 15-120s → 打字（字数 × 2-5s/字，封顶 90s）"的墙钟延迟；点赞绝不机器连击，间隔 20s-3min。

## 十、数据回收与优化引擎

回收：fxtwitter 对每条自发帖在 t+1h / t+6h / t+24h / t+48h 采点。fxtwitter 拉不到但 XActions 浏览器可见的帖 → 影子封禁旁证信号。

KPI 三级：
- 一级：净增关注/日
- 二级：每帖 engagement rate；回复带来的关注转化（单独建模，反哺画像权重与回复优先级）
- 三级：内容维度切片（话题/体裁/长度/带图视频/发布时段）

每日复盘（23:00，GLM 旗舰）；每周调参（周日 22:00）：最多调 2-3 个参数、每个必须附 hypothesis、`strategy_versions` 版本链留痕、连续两周期变差 → 自动回滚 parent_ver、内置 A/B（一次一个参数、窗口 ≥5 天、按曝光归一化）。

## 十一、x-algorithm 信号落地对照表

| 算法信号 | 落地点 |
|---|---|
| 停留时长与视频秒数是显式正向信号 | 内容偏好带图/短视频；文字帖控制钩子结构 |
| 关注转化单独建模 | KPI 二级"回复→关注"转化追踪 + 画像权重反哺 |
| 同作者连续发帖衰减 | `min_post_interval=2.5h` 硬约束 + 每日发帖分散 |
| 举报/拉黑/静音负向（agatha 系统） | 画像负分 + 掉粉检测 + Bold 配额上限 |
| 新账号流量加权 | warm-up 两周正好吃满新号扶持期 |
| 48h 相关性窗口 | 数据回收四个采样点截至 48h |

## 十二、审核：飞书卡片 + FastAPI 轻量面板

飞书卡片：通过 / 驳回 / 改后通过。审批超时 4 小时自动驳回。面板：待审队列、KPI 曲线、策略版本 diff + 一键回滚、影子封禁历史、开关面板（content_auto / reply_auto 独立）、STOP 按钮、预算临时覆写。

渐进切自动：某类内容连续 7 天人工驳回率 <10% 且风险事件 0 → 亮绿灯提示可切；切自动后前 2 周保持 10% 随机抽审。

## 十三、部署与迁移

Windows：NSSM 注册 daemon，sidecar 由 daemon 看护；每日 04:00 SQLite 备份（VACUUM INTO），保留 14 天。
迁 VPS：打包 data\（xrunner.db + browser_profile）+ 代码。IP 必须稳定；迁后执行 3 天半预算观察期再恢复满额。本机已是稳定住宅 IP 则长期不迁也是合理选项。

## 十四、LLM 调用点与成本

| 调用点 | 模型档 | 频次/日 |
|---|---|---|
| 帖草稿生成 | 旗舰（写作质量优先） | ≤4 + 重写 |
| 回复草稿 | 旗舰或 flash（短文本） | ≤15 |
| 复盘诊断/调参提案 | 旗舰（推理） | 1 |
| AI 味判分 | flash（规则化） | ≤40 |
| 短期记忆蒸馏 | flash | 1-2 |

所有调用带结构化输出（JSON schema），失败重试 ≤2 次、温度分层（草稿 0.9 / 判分 0.1 / 复盘 0.3）。

## 十五、数据模型（SQLite，WAL 模式）

posts / post_metrics / replies / interactor_profiles / interaction_history / persona_cards / style_rules / cases / strategy_versions / ab_experiments / daily_reports / health_log / shadowban_reports / approvals / commands（面板→daemon单向队列）/ short_term_context。

## 十六、风险清单与对策

| 风险 | 对策 |
|---|---|
| 浏览器自动化被检测/封号 | 专用 profile + 固定 IP + 永不 headless 切换 + warm-up + 行为拟人 + 预算硬顶 |
| XActions 上游变更/侧车崩 | watchdog 重启 + failover twitterapi.io；侧车版本锁定 |
| twikit 已失修 | 不部署不依赖，仅保留抽象兼容 |
| fxtwitter 接口变更 | 降级用 XActions 浏览器抓自己帖统计 |
| LLM 产出风险内容 | 雷区硬门禁 + 人工审期拉长 + 10% 抽审常驻 |
| 内容同质化/风格漂移 | 案例库负反馈 + 角度配比进参数库 + 人设卡版本锚定 |
| SQLite 写并发 | WAL + 单写者（daemon），面板走 commands 队列 |
| 迁 VPS 指纹/IP 突变 | 半预算 3 天观察期；评估"长期留本机" |
| Windows 更新重启 | NSSM 自启 + checkpoint 续跑 |

## 十七、里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| M0（W1） | 骨架 + XClient/XActions 跑通 + warm-up 循环 + 面板只读 + STOP | 浏览器会话稳定运行 3 天无人工干预 |
| M1（W2） | 内容管线 + 飞书审核链路（演练不发布）+ 影子封禁自检 | 全链路 dry-run 通过 |
| M2（W3-4） | 开始发帖（≤1→≤2/日）+ 数据回收 + 每日复盘 | 首帖过审发布；48h 指标回收完整 |
| M3（W5-6） | 满额预算 + 互动管线全开 + 周调参 + A/B | 第一个带 hypothesis 的参数版本落库 |
| M4（W7-8） | reply 渐进切自动 + 迁 VPS 评估 | reply_auto=true 持续 2 周风险事件 0 |

## 十八、待用户补充项（不阻塞 M0/M1）

1. 人设语料 → persona_cards v1
2. 现有记忆系统实现细节 → 三层账本合并/迁移映射
3. 内容源清单 → 覆盖默认池或补充

## Sources

- github.com/nirholas/XActions · npmjs.com/package/xactions
- twitterapi.io/blog/twikit（twikit 状态）· twitterapis.com（2026 迁离 twikit 指南）
- docs.fxembed.com/api/introduction · korben.info（FxTwitter 免登录 API）
- docs.bigmodel.cn（智谱 OpenAI 兼容）
