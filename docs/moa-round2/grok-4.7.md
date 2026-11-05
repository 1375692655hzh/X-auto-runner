# 来源：grok 岗 · Grok Build CLI（-m grok-4.7，modelUsage 验证 grok-4.7-build，成本 $0.097）· MoA 第二轮 · 2026-10-22

> 执行备注：首次裸调（默认模型）实测走的是 grok-4.6-build，按用户点名改用显式 -m grok-4.7 重跑。
> 对用户历史信息的三处事实修正（Grok 联网核验）：
> 1. XActions 的"152+ 工具"不是 MCP 工具清单——仓库 MCP 示例露出约 12 个工具，152+ 是脚本/CLI/浏览器片段总盘子；侧车内另有托管只读端 xactions.app/mcp。
> 2. twikit 上游密码登录流已坏（LoginFlow code 366、transaction 签名破损报告），备选实现只走 cookie 且钉版本。
> 3. fxtwitter 的 views 字段时有时无，只能当参考列，不能当主 KPI；稳定字段是 likes/reposts/quotes/replies。

# X 中文 AI/科技人设号 7×24 自动运营技术方案

项目：E:\x-auto-runner（空目录起步）；远端 github.com/1375692655hzh/X-auto-runner.git（空仓库）。

## 事实核对（2026-10-22，只约束实现，不改决策）

- XActions 仍在维护。技术栈 Puppeteer + stealth，登录用浏览器 auth_token（再加 ct0）。独立侧车入口 `npx xactions-mcp`，环境变量 XACTIONS_SESSION_COOKIE。仓库文档里 MCP 示例露出约 12 个工具；「152+」是脚本、CLI、浏览器片段总盘子。另有托管只读端 https://xactions.app/mcp（x_post/x_thread/x_video 免费），只用于公开读。方案把业务绑在自有窄接口上，侧车内部再调用 XActions。
- x-algorithm 仍是 For You 的开源实现，agatha 目录仍在。社区对 home-mixer/params/param.rs 的摘录（2026-08 公开默认值）：停留分二值 dwell 与连续 dwell time；关注作者是独立打分项；同作者在同一次排序里按 multiplier=(1-floor)×decay^k+floor 衰减（摘录 decay 0.5、floor 0.25）；回复、引用高于点赞；视频质量观看有最短时长门闩。决策⑦的「间隔≥2–3h」「48h 窗口」「新账号加权」是运营翻译，源码里没有这些字面常量。系数以落地时重读 param.rs 为准，程序里只消费方向。
- fxtwitter：GET https://api.fxtwitter.com/2/status/{id} 返回公开 JSON，无 key。稳定字段 likes/reposts/quotes/replies。旧路径 /{screen_name}/status/{id} 仍可用。views 时有时无，不作主 KPI。需处理 401（私密）、404、500。
- twikit 2026 年上游密码登录流返回 LoginFlow code 366，transaction 签名破损。备选实现只走 cookie（auth_token+ct0），版本钉在能跑通的发行版，不做密码登录。
- 影子封禁四维与 shadowbanradar 公开方法论一致。自建检查复用四步，不把该网站当运行时依赖。

## A. 总体架构

一个编排进程，一个浏览器侧车，一块 SQLite。所有对 X 的写操作经过同一条动作队列，同一时刻只有一个浏览器动作。

- xrunner-orch（Python，NSSM 服务）：APScheduler（到点只负责入队）/ Worker（单线程，一次只跑一个图）/ LangGraph（SQLite checkpointer）/ FastAPI :8787（只听 127.0.0.1）/ 三层记忆、策略库、审计 / XClient（选择实现、降级、对账）。
- xrunner-sidecar（Node，NSSM 服务）：长驻 Chrome（独立 user-data-dir）/ XActions·Puppeteer 动作实现 / 拟人时序（打字、阅读、滚动）只放在这里 / 单飞锁：侧车内部再拒并发。
- data/runner.sqlite 唯一状态源，可整库搬走；data/STOP kill switch。

进程职责与故障行为：
| 进程 | 谁启动 | 持有 | 挂了会怎样 |
|---|---|---|---|
| xrunner-orch | NSSM | 调度、图、SQLite、审核 API、飞书 | 侧车继续空转；服务拉起后续跑未完成的 checkpoint |
| xrunner-sidecar | NSSM，编排依赖它 | Chrome 配置、cookie、拟人动作 | 编排把写操作标为延期，不换通道立刻重发 |
| Chrome | 侧车拉起 | 登录会话 | 侧车重启一次；仍失败则熔断并飞书告警 |

两条硬规则：① 编排进程不碰 cookie，不启动浏览器。换 XActions/twikit/官方 API 只换 XClient 实现。② 指标回收永远走 fxtwitter，不占用浏览器配额，避免「用发帖通道给自己帖子刷曝光」。

## B. 模块拆分与职责（做什么/明确不做什么）

scheduler：按人设作息入队；warm-up 日历；每日预算摇号。不直接发帖。
graph：内容、回复、浏览、回收、复盘、调参、封禁自检七张图。不持有浏览器。
xclient：统一动作、幂等、降级、发帖后对账。不含文案生成。
sidecar：会话、拟人时序、DOM 动作。不含策略。
memory：当日上下文、互动者、人设卡、风格库、案例库。不调 letta/mem0。
content：选题、生成、去 AI 味、失败重试。审核通过前不发。
reply：目标筛选、分档、防骚扰。不主动私信。
metrics：2h/8h/24h/48h 拉取、KPI、每日复盘、每周调参、A/B、回滚。48h 未满的帖子不参与调参。
safety：预算、STOP、熔断、四维自检、审计。不尝试绕过验证码。
review：人工审核节点、飞书卡片、两个自动开关。不做内容生成。
llm：智谱 GLM 的 OpenAI 兼容调用，JSON 输出。不把密钥写入仓库。
api：本机面板。不对公网监听。

## C. 项目目录结构

config/{settings.example.toml, persona.example.toml, algo_signals.yaml（方向表，系数留空待复核）, humanizer_zh.yaml, schedules.yaml}；src/xrunner/{main,settings,db,killswitch,xclient/*(base,models,factory,xactions_http,twikit_client,official_api,read_escape,fxtwitter,fake),graph/*,scheduler/*,memory/*,content/*,reply/*,metrics/*,safety/*,review/*,llm/glm.py,api/*}；sidecar/{package.json,src/}；tests/；deploy/{nssm,systemd}；data/{runner.sqlite,secrets/(DPAPI),chrome-profile/,logs/,STOP}。Python 钉 3.11+，Node 按 XActions engines 钉。

## D. XClient 抽象接口

health()（ok/degraded/login_required/checkpoint）、whoami()、browse_home(minutes,seed)、search_recent(query,limit)、typeahead_users(prefix)（影子封禁用）、user_tweets、tweet、thread(tweet_id, mode="ranked"|"chronological")（幽灵回复/降权用）、post(text, idempotency_key, reply_to)→ActionResult(posted|already_posted|failed|uncertain)、like、follow（默认关闭）、metrics_public（固定走 fxtwitter）。

ActionResult.uncertain 的对账顺序：幂等键查本地账本 → 拉自己最近 20 条对比正文哈希 → 找到补写 tweet_id 返回 already_posted → 找不到才允许再试一次。

双实现切换（settings.toml：x.primary=xactions|twikit；x.write_escape=off|official；x.read_escape=off|twitterapi_io）：

| 实现 | 传输 | 登录 | 适合 |
|---|---|---|---|
| xactions_http | 127.0.0.1:3921 + X-Sidecar-Token | 侧车持有 cookie，常驻 Chrome | 默认，拟人时序完整 |
| twikit_client | 进程内 asyncio | set_cookies(auth_token,ct0)，日志打码 | 侧车连续两个健康检查失败后 |
| official_api | HTTPS | 官方密钥，默认不加载 | 仅 post，且 write_escape=official |
| fxtwitter | HTTPS | 无 | 永远只服务 metrics_public |
| fake | 内存 | 无 | 测试与无账号演练 |

故障降级链（写）：XActions 侧车（网络抖动同幂等键再试1次，3-8min 间隔）→ login_required/验证码：不降级，熔断飞书要人（验证码不自动打）→ 侧车进程挂：NSSM 拉起一次，仍失败切 twikit（仅 cookie 健康检查通过）→ twikit 也失败：动作留队 deferred，不自动改走官方 API → 官方 API 仅在已武装+发原创+当日逃生预算未用尽时由面板一次人工确认放行；回复不走付费逃生 → twitterapi.io 只补搜索/读帖，不获得写权限。
读：侧车→twikit→（已武装）twitterapi.io。自己帖子计数始终先 fxtwitter；连续失败 3 次记 metrics_gap 下一周期补拉。每次切换写 audit_log（from_impl/to_impl/reason）。

## E. LangGraph 图设计

七张图共用一个 SqliteSaver（库就是 runner.sqlite）。thread_id：content:{date}:{slot} / reply:{date}:{hour_bucket} / browse:{date}:{session} / metrics:{tweet_id}:{checkpoint} / daily:{date} / tune:{iso_week} / shadow:{date}。Worker 全局一把 run_lock，调度器只插 jobs 行不并发跑图。节点入口第一行检查 STOP、熔断器、当日预算。

- 内容图：load_context→pick_topic→generate→humanize_gate（未过<3次回 generate 带门禁意见）→decide_review（manual: interrupt(APPROVE)；auto且分值过线: publish；auto灰区: interrupt）→publish→reconcile→write_memory→END。interrupt 载荷：草稿全文、选题理由、门禁分数、命中规则、今日已发数、建议发布时间。恢复值 approve/approve_with_text/reject。发布节点是唯一副作用节点，idempotency_key=thread_id；崩溃续跑先对账再决定是否真发。
- 回复图：load_budget→scan_candidates→rank_and_filter→draft_one→humanize_gate→tier_policy（Safe/Interesting 且 auto: send；Bold 永远 interrupt 直到 bold_mode=auto+warm-up 过+自检全绿）→send→reconcile→write_interaction→（预算仍有余且队列有目标→回 draft_one）→END。一时段最多处理本时段配额。
- 浏览图（warm-up 与发帖间隙，无 interrupt）、回收图（失败 15/40/90min 三次重试后标 gap）、日复盘图（只写 daily_reviews 和次日选题提示，不改线上参数）、周调参图（读满 48h 样本→提议≤2-3参数→interrupt(TUNE)（开关关闭时）→自动模式下直接写新版本保留回滚点）、自检图（四维→写 shadow_checks；任一维确认命中→browse_only + interrupt 通知人；自动恢复不做）。

## F. APScheduler 触发计划（Asia/Shanghai，全部加抖动，misfire 宽限 10 分钟，错过作废不补发）

| 时刻 | 作业 | 说明 |
|---|---|---|
| 00:05±2min | roll_budget | 当日上限=基数×U(0.7,1.3) 向下取整，当天不再重摇 |
| 07:40±20min | shadow_check | 四维自检，命中则后续写作业全跳过 |
| 08:10±25min | browse 25-40min | 早间只浏览 |
| 08:50 起间隔≥150min | content 槽 | 全天最多 4 槽，实际发几条由摇号决定；01:00-07:30 的槽丢弃 |
| 12:20/18:40/21:10 各±30min | reply | 三次拆当日回复配额，单次≤6 条 |
| 浏览后穿插 | like | 只给本轮真正读过的帖 |
| 每 30min | metrics_due | 到期 2/8/24/48h 检查点入队 |
| 23:20±15min | daily_review | 写复盘不动参数 |
| 周一 10:30±20min | weekly_tune | 只评估 age≥48h 的帖 |

活跃窗 08:00-09:30 / 12:00-13:30 / 18:30-23:00，窗外不发原创不回复。

Warm-up 十四天表（基数在摇号前被日历卡死，摇号只在日历上限内波动）：
| 日 | 浏览 | 原创 | 回复 | 赞 | 档位 |
|---|---|---|---|---|---|
| 1-3 | 2场20-40min | 0 | 0 | 0 | 只建立会话与作息 |
| 4-5 | 2场 | 0 | 3 | 5 | 只允许 Safe |
| 6-7 | 2场 | 0 | 5 | 8 | Safe |
| 8-9 | 2场 | 1 | 6 | 10 | Safe，帖子人工审 |
| 10-11 | 2场 | 1 | 8 | 12 | Safe+少量 Interesting |
| 12-13 | 2场 | 2 | 10 | 15 | Interesting 开启，Bold 关闭 |
| 14 | 2场 | 2 | 12 | 20 | 全量前最后观察日 |
| 15+ | 2场 | ≤4 | ≤15 | ≤30 | 再乘每日±30% |

毕业条件（全部满足才进第 15 天预算）：自检连续 3 天四维通过；无验证码无登录墙；人工审核拒绝率<40%；没有 uncertain 未对账的写操作。任一不满足按第 12-13 天额度延续，每次 3 天，飞书说明原因。

## G. SQLite 表结构（ULID 主键，UTC ISO8601，WAL+busy_timeout 5000+foreign_keys ON）

- daily_budgets(local_date PK, phase, post/reply/like cap+used, multiplier, rolled_at)
- jobs(id, kind, run_at, status queued|running|done|skipped|deferred|failed, thread_id, skip_reason)
- actions(id, idempotency_key UNIQUE, kind, impl, request_hash, status pending|posted|already_posted|failed|uncertain, tweet_id, target_*, error_class/detail)
- audit_log(id, ts, actor system|feishu:{open_id}|panel, event publish|approve|reject|degrade|stop|tune|rollback, ref_table, ref_id, payload_json)
- day_context(local_date PK, topics_used_json, reply_user_ids_json, tone_notes, summary)
- actors(user_id PK, screen_name, followers, bio, topic_tags, warmth 0陌生|1有过互动|2对方回复过我们, do_not_engage, engage_reason, first/last_seen)
- interactions(id, actor_id, direction out_reply|out_like|in_reply|in_like, tweet_id, our_action_id, tier, text, created_at)
- persona_cards(version PK, body_md, active, note)——换版不覆盖旧行
- style_examples(id, kind good_post|good_reply|bad|banned_phrase, text, why, weight, active)
- cases(id, kind success|fail, ref_post_id, text, reason, kpi_json)
- posts(id, thread_id UNIQUE, tweet_id, text, text_hash, topic, source_ref, gate_score, gate_report_json, review_status, status drafted|posted|dropped|uncertain, published_at)
- post_metrics(id, post_id, checkpoint 2h|8h|24h|48h, likes, reposts, quotes, replies, views可空, raw_json, UNIQUE(post_id,checkpoint))
- follow_daily(local_date PK, followers, following, delta_followers——关注转化单独成列)
- reply_drafts(id, thread_id, target_*, tier, text, gate_score, review_status, status, sent_tweet_id)
- strategy_versions(version PK, parent_version, params_json 只允许白名单键, reason, status active|superseded|rolled_back)
- strategy_changes / ab_assignments(week, param_key, variant, post_id) / weekly_scores(week PK, version, posts_n, median_eng 主KPI, follower_delta, shadow_hits, decision keep|rollback|freeze)
- shadow_checks(local_date, dimension suggestion|search|ghost|deboost, verdict pass|hit|unknown, confidence, evidence_json, PK(local_date,dimension))
- breakers(id, kind login|checkpoint|error_burst|shadow|stop, opened_at, until_ts 空=必须人关)

参数白名单（每周最多改 2-3 个）：post_cap_base / reply_cap_base / like_cap_base / min_gap_minutes / tier_mix / length_target / hook_style / cta_rate（默认0，关注引导很少开）/ humanize_threshold / bold_cap。

## H. 内容流水线

选题（内容源给出前只来自三处）：人设卡常设栏目（空着由用户补）/ day_context 与近 7 天 posts.topic 排重 / 复盘写入的次日可延续话题。有内容源后源条目先进 source_items 表，选题只引用已入库条目，生成节点不得自己临时联网发挥。串帖按条数占预算；warm-up 期间不做串帖。

生成：GLM OpenAI 兼容，一次调用要求 JSON：{texts:[...], topic, claim（这一帖只主张的一件事）, facts:[...]}。提示词四段：人设卡、3-5 条 good 风格例、今日已发主题、选题简报。温度 0.7。**缺少人设卡时生成节点直接失败并告警，不用通用「科技博主」口吻顶上。** 长度默认 40-140 汉字，两到四句。链接默认不放（源写复盘备注不写进帖子，减少点开就走）。视频不自动发；有成片素材前只发文字。

去 AI 味门禁（humanizer-zh 自建规则，每条草稿 0-100 分，默认阈值 75，阈值在白名单里）：禁开场套话（在当今/值得注意/不可否认/综上所述/赋能/抓手/闭环/深入探讨/让我们/作为一名）；禁 AI 自述；感叹号全文最多 1 个；省略号最多 1 处；不用 Markdown 标题编号列表星号；表情默认 0（人设卡允许则最多 1 个且不在首尾）；句子数 2-4；最长句不超最短句 4 倍；不能四句长度几乎相同；facts 为空且无数字专名例子扣分；与近 30 天自己的正文做字符 bigram 相似度>0.55 直接失败；claim 对不上或两个转折主题失败；人设卡 taboos 一票否决。未过线带命中规则原文回炉重写最多 3 次；3 次失败：人工模式进队列标「门禁未过」，全自动模式丢弃该槽不降阈值硬发。

审核：manual 模式飞书卡片+面板队列，按钮通过/驳回/改后通过（改后重跑门禁）。超时 6 小时作废不补发。auto 模式：门禁分≥阈值+8 直接发，灰区仍 interrupt。

发布失败处理：pending→post→posted（登记 4 个回收点、扣预算、写短期记忆）；uncertain（停在节点、飞书告警、人确认前禁止第二条同主题草稿；侧车「已输入未提交」对账窗口 10 分钟）；failed 网络类等待后再试 1 次。LLM 超时重试 2 次（2s、8s）。

## I. 回复互动引擎

候选条件（全部满足）：90 分钟内发布；中文或人设卡允许的英文技术帖；作者不在 do_not_engage、非广告/抽奖/引流帖；话题在人设允许范围（政治突发事件、灾难、私生活默认排除）；不是自己的帖、不是已回复过的帖。排序分：话题匹配、作者 warmth、原帖少量讨论未爆骂战、文本含可接住的具体点。

分档：Safe（默认；warm-up 前 7 天唯一档；接住对方一个具体点补一个事实或短问题，不评价对方本人；大部分）；Interesting（第 10 天起且原帖在讨论可分歧技术点；给不同看法带可核对理由，不嘲讽；每日最多回复预算一半）；Bold（第 15 天且自检连续全绿后；立场更硬的一句判断，不人身不骂战不拉踩指名；每日最多 2 条，bold_mode=auto 前每条等人）。档位由规则先定不由模型自选，模型只在给定档位里写句子。

防骚扰预算：同一作者 24h 最多 1 次出向（回复或赞合计）、7 天最多 3 次；同一帖只回一次，对方回复我们后才允许在那条底下再回一次并记 warmth=2；不追别人的新帖喊话；同一时段不对同一作者既赞又回复；不自动关注、不私信、不转发带评论；原帖作者粉丝超「大号阈值」（默认 50 万）且 warmth=0 只允许 Safe 且每日此类最多 1 条；连续 2 条回复被人工驳回后本时段剩余取消。

拟人时序全在侧车：打开帖子按字数停留（约每 30 汉字 1 秒 + 1-4 秒随机）再输入；每字 40-120ms 中途停顿；提交前再停 0.4-1.2 秒；编排层保证相邻动作至少相隔 40-90 秒。

## J. 数据回收与优化闭环

拉取：GET /2/status/{id}，解析 likes/reposts/quotes/replies，views 有则存无则 NULL。429/5xx 三次退避；404 标 missing 通知人不重试；请求间隔至少 2 秒。回收点 +2h/+8h/+24h/+48h 绝对时间。周调参只读满 48h 的帖。关注转化每天自检后调一次 whoami 写 follow_daily.delta_followers，不摊进单帖。

指标：单帖 48h 加权 eng = likes + 2×reposts + 3×quotes + 3×replies；有 views 时记 eng/views 只作参考。主 KPI=一周内满 48h 帖的 eng 中位数（防单条爆款推向极端）。辅助列：2h/48h 起量比、回复获赞比例、周净增关注、自检命中次数（一票约束：命中则本周禁止任何放量参数上调）。

每周调参（周一，样本<6 条原创不调）：计算本周中位数 vs 当前版本基线（该版本第一周中位数）→ 自检本周有 hit 则冻结 → 否则白名单挑最多 3 个参数，选择规则写死：中位数连续一周低于基线 15% 只允许收紧（间隔+30min、Bold 配额-1、长度目标更短一档）；不低于基线且涨粉非负允许一个探索性改动 → 步长是预设格子（min_gap_minutes 只取 150/180/210/240；length_target 只取 short/medium）→ 写新版本行，旧版 superseded → tune_mode=manual 时 interrupt 人批才 activated。自动回滚：某已激活版本连续两个评估周主 KPI 都低于父版本 15% 且两周样本各≥6 → 父版本参数原样写成新版本（来源标记 rollback），调参冻结一周。A/B：同周只跑一个参数两档，hash(post_id)%2 发布前定好；两臂各≥4 条满 48h 才比较中位数，差距<10% 视为持平回 A/B 前值。**GLM 可以给复盘写人话摘要，不能直接输出新参数。参数变更只能来自格子规则。**

## K. 影子封禁四维自检（每天 07:40，公开可见结果；证据不足记 unknown 不当通过也不当命中；连续两天 unknown 升告警）

| 维 | 做法 | 命中 | 误判处理 |
|---|---|---|---|
| 搜索建议屏蔽 | typeahead_users 用自己 screen_name 前缀，看返回有无自己 user_id | 列表正常、自己不在其中、已知大号对照能返回 | 对照大号也失败则整维 unknown |
| 搜索封禁 | search_recent 查 from:{handle} since:昨天，对照库里昨天确有公开帖 | 对照帖存在而搜索为空 | 昨天没发帖则跳过沿用前一日结论 |
| 幽灵回复 | 取自己过去 24h 回复最多 5 条，thread(id, chronological) 读到覆盖位置 | 对话读完仍没有自己的 tweet_id 且单帖接口能打开 | 单帖也 404 记已删不算幽灵 |
| 回复降权 | 同批回复再读 thread(id, ranked) 默认视图 | 时间序可见、默认视图没有、且默认视图已展示更晚的其他回复 | 对话很短默认视图本来就只显示几条时记 unknown |

动作：任一维 hit（对照成功且不可见，confidence=high）→ 当天档位 browse_only，取消未执行的发和回，飞书卡片写明维度与证据 id。恢复只能面板人点「恢复」，恢复后第一天按 warm-up 第 8 天额度跑。不访问 shadowbanradar 接口。

## L. 安全风控

预算随机化：roll_budget 00:05 写死三个整数；基数来自当前策略版本→被 warm-up 日历取 min→乘 U(0.7,1.3)。下限：毕业后摇到 0 提升到 1 避免突然失声。重启不重摇。作业执行前再查 used<cap。赞只发生在本轮浏览真正展示过的帖上。

Kill switch：STOP 存在时调度器可写「已跳过」记录但不启动新图；正在打字的侧车动作允许提交完当前一下或未输入时中止，不在句子打到一半强杀；新图每个节点返回前再查一次；**删除 STOP 不自动开跑，需面板/飞书 resume 事件写 audit_log（防编辑器误删导致恢复）**；RUNNER_PAUSED=1 等价（给 NSSM 用）。

Cookie 与账号：人在本机 Chrome 导出 auth_token+ct0 写入 data/secrets/x-cookies.bin（Windows DPAPI 加密）；仓库/日志/飞书卡片/审计不出现 cookie，日志中间件打码；侧车用专用 data/chrome-profile 与个人浏览器分开；不保存密码不调登录表单，cookie 失效熔断等人重新导出；官方 API 密钥与飞书签名密钥只在 data/secrets；一个 X 账号一个侧车一个配置目录，不支持多账号轮换。

异常熔断表：15 分钟内 3 次写失败→停写 90 分钟浏览也停；登录墙/验证码/异常活动提示→停机等人不刷新重试；自检 hit→browse_only 人恢复；对账 uncertain→全局停写直到对完；fxtwitter 整天全失败→只告警不影响发布，调参因缺数自动冻结；GLM 连续失败→内容与回复停浏览可继续；当日预算用尽→跳过记 budget。

## M. 部署

Windows 两个 NSSM 服务（XRunnerSidecar node sidecar/dist/server.js；XRunnerOrch python -m xrunner，依赖侧车），重启 5 秒起最多每小时 10 次；.env 不入库；日志按日切割保留 14 天；FastAPI 只绑 127.0.0.1:8787 + X-Panel-Token；面板四块：待审队列、今日预算与已用、自检结果、STOP/恢复/两个模式开关。健康：侧车 GET /health、编排 GET /healthz（数据库可写、上次成功动作时间、熔断状态）；超 6 小时无成功动作（活跃窗内）飞书「心跳停止」。飞书事件只六种：待审/熔断/自检命中/对账 uncertain/心跳停止/周调参结果；一天同事件最多 3 条。

迁 VPS：搬家单位是文件不是导出导入——停编排确认无 pending（有则先对账）→停侧车→拷 runner.sqlite（含 checkpoint 表）与 chrome-profile→cookie 是 DPAPI 密文 Linux 读不了，搬家当天在 VPS 重新放 cookie（600 权限+口令加密）→依赖按锁文件重装→Chrome 改 Puppeteer 自带 Chromium 或发行版 google-chrome；无桌面加 --headless=new，触发登录墙则 xvfb 跑有头（预案不预做两套）→systemd Restart=on-failure→时区写配置不依赖系统→面板继续 localhost 走 SSH 隧道→验收：fake 图跑通后开 browse_only 一天自检通过才恢复搬家前策略版本。**VPS 与本机不同时运行（同 cookie 互踢）。**

## N. 里程碑

M0 骨架（仓库、SQLite 迁移、XClient fake、七张图空跑、STOP；验收：无账号把一条假帖从选题跑到待审并断点续跑）→ M1 侧车（登录态、health、只读浏览、typeahead、搜索、读对话；验收：连续浏览 30 分钟不崩，日志无 cookie）→ M2 安全底座（预算摇号、warm-up 日历、四维自检、熔断；验收：拨日期到第 2 天原创作业被跳过；模拟搜索为空进 browse_only）→ M3 内容+人审（验收：人工通过后 X 上出现同一条；杀进程再启动不会发第二条）→ M4 回复（验收：同一作者当天第二条被拒；Bold 开关关闭时全挂起）→ M5 回收（验收：48h 行齐全；views 缺失其他字段仍在）→ M6 调参演练（验收：人为喂两周变差数据，回滚规则产出父版本）→ M7 长期跑（验收：连续 14 天 warm-up 按表执行，人每天只处理审核卡片）→ M8 放开自动（每开关独立；灰区草稿仍 interrupt；可一天内关回）→ M9 迁 VPS（拷库后续跑同一 thread；本机服务已停）。M7 完成前不开任何自动发布开关；M6 回滚先夹具数据再真实数据两周才允许 tune_mode=auto。

## O. 主要风险与对策

X 页面改版（失败进熔断不换通道连打；契约测试把假页面与真账号冒烟分开，冒烟每周人工看一次）；twikit 再失效（只是备选，钉版本升级单独冒烟）；cookie 过期（熔断等人导出）；验证码/账号检查点（立即停机告警，无打码无重试风暴）；自动化限制（预算/warm-up/间隔/拟人/自检降级是维持低量，不能保证账号安全）；发帖重复（幂等键+正文哈希对账+uncertain 全局停写）；fxtwitter 改字段（gap+冻结调参，不改用自己浏览器打开自己帖刷指标）；权重数字过时（algo_signals.yaml 只写方向，每季度对照 param.rs 改并记版本）；新号加权窗口内容质量差（前 7 天不发原创）；GLM 漂移（门禁阈值与风格例是硬门，人设卡缺失拒绝生成）；审核人不在（6 小时过期不补发）；SQLite 损坏（WAL+每日备份保留 14 份）；本机睡眠（安装说明要求关闭睡眠）；两台机器同时跑（编排启动写 owner_host，发现另一台心跳未过期拒绝启动）；第三方读接口条款变化（twitterapi.io 默认关闭，fxtwitter 只拉自己公开帖每天几十次）。

平台条款风险：浏览器自动化操作自己账号可能违反 X ToS，最坏限流或封号。本方案安全层是降量和停机，不是规避检测。

## P. 需要确认的开放问题（12 项，含临时默认值）

人设语料（未提供前内容图拒绝生成）；现有记忆系统格式（没有则从空账本开始）；内容源列表（未提供前只做人设栏目+昨日复盘延续）；账号现状（handle/注册多久/粉丝量/是否全新号，影响 warm-up 起算点；默认按全新号第 1 天）；审核入口（飞书回调能否到达这台 Windows；默认面板批准飞书只发通知）；大号阈值与禁答话题（默认不回粉丝>50 万且无互动史账号；政治/灾难/私生活不碰）；是否发视频图片（默认纯文字）；官方 API 与 twitterapi.io（默认都不武装）；运行机器（能否取消睡眠/常开 Chrome）；GLM 具体模型名（配置留空不猜）；周调参何时允许自动生效（默认一直 manual 直到夹具回滚通过+真实数据两周）；单帖语言（默认简体中文，整句英文不发除非人设卡允许）。
