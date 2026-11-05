# 来源：kimi 岗 · Kimi Code CLI（-m kimi-code/k3，effort max）· MoA 第二轮 · 2026-10-22

> 会话可续接：kimi -r session_f1e76465-f1a8-44e6-bbcb-e8f9ad737b11。纯方案文本，未写入任何文件。

## 检索发现摘要（四路并行联网检索验证）

- **XActions**：确证活跃（611 stars，最近提交 2026-10-21），架构是「GraphQL 直连读 + Puppeteer 写」混合，MCP Server 约 152 个工具（stdio/HTTP 双模），自带 cookie 池、限流窗口、429 轮换——主通道成立。风险：项目体量小、README 营销色彩重、无独立安全验证，需实测采信。
- **twikit**：上游已实质停更（2026 年因 X 前端变更大面积失效，issue #408/#425 无人修），写操作封号报告多（错误码 226）。备选应改用社区 fork **twifork**（drop-in 替换），并预算"解析器随时再崩"的维护成本。
- **x-algorithm**：xai-org/x-algorithm 不是 2023 年 twitter/the-algorithm 的镜像，而是 **xAI 2026 年 1 月开源的现行 For You 排序代码**（Phoenix），几乎每日同步生产参数。检索修正两处理解：①"新账号流量加权"实为**冷启动试水位机制**（曝光<200、2 小时内、粉丝<5 万的帖被抬到第 15-16 位），不是账号级加权；②"作者回复加权 75 倍"已退役，现行是**互关作者的原创帖 reply 权重 ×4**（5+15）。确证信号：reply 权重 5（点赞的 7.6 倍）、follow_author 4、ShareViaCopyLink 20、report/mute/block 为 -31~-234、同作者发帖衰减、48 小时 AgeFilter。
- **fxtwitter**：实测可用，v2 端点 /2/status/{id}，likes/reposts/replies/quotes/bookmarks/views 全有，免鉴权，1000 req/min。局限：拿不到仅作者可见的分析数据。
- **影子封禁**：四维方法论成立，但 ghost ban / reply deboost 两维**必须用不关注本号的第二账号**做外部视角观察；fxtwitter 的 /2/typeahead、/2/search、/2/conversation 可直接搭前两维。
- **twitterapi.io**：读便宜可作逃生；但其**写操作要求提交账号 login_cookies + 代理**，安全语义差，不建议用于发帖。
- **LangGraph**：AsyncSqliteSaver + interrupt()/Command(resume=) 为新版标准范式；关键坑——resume 时**节点从头重跑**，副作用必须在 interrupt 之后且幂等。social-media-agent 是 TypeScript 实现，只能借鉴 HITL 范式不能复用代码。
- **humanizer-zh**：是归藏维护的 **Agent Skill（31 条 Markdown 规则）**，不是 pip 库——需蒸馏成"规则引擎 + LLM 改写门禁"两道工序。
- **APScheduler**：锁 3.11 稳定线，SQLAlchemyJobStore + sqlite 持久化、jitter、misfire_grace_time、coalesce 均支持。

# X-Auto-Runner 技术方案（完整版）

版本 v1.0 草案（2026-10-22）｜约束：决策 ①–⑪ 已锁定，只展开不重议。

## 0. 设计总则与待补占位

总则：单账号、长期无人值守、可审计、可回滚、可搬迁。一切状态落 SQLite；一切写操作过预算与风控闸门；一切外发内容过 Humanizer 门禁与（初期）人工审批；一切自动化行为有"人在回路"的一键熔断。

| 占位 | 接入位置 | 占位实现 |
|---|---|---|
| 人设语料 | data/persona/ + persona_cards 表 | 骨架人设卡（字段齐全、内容 TBD） |
| 现有记忆系统 | memory/mid/ 的 MidMemoryPort 抽象 | SQLite 默认实现 |
| 内容源列表 | config/sources.yaml | 空列表 + 抓取器插件接口 |

## 1. 总体架构与进程划分

L6 审核观测层（飞书卡片 + FastAPI 面板）→ L5 编排层（LangGraph 四图 + APScheduler SQLite jobstore）→ L4 能力层（内容/回复/回收/优化/自检）→ L3 记忆层（三层账本 + 策略参数库）→ L2 安全层（预算随机化/warm-up 阶段表/STOP/Humanizer 门禁）→ L1 接入层（XClient 抽象，降级链 XActions→twifork/twikit→twitterapi.io 只读）→ L0 基础设施（SQLite/配置/日志/LLM 网关）。

三进程一机器：xar-core（Python asyncio，NSSM）、xar-panel（FastAPI 内嵌 uvicorn 子任务，127.0.0.1）、xar-sidecar（Node，XActions MCP --http，NSSM）。**sidecar 选 HTTP 不选 stdio**：core 与 sidecar 可独立重启、跨语言清晰、迁 VPS 可拆机。

## 2. 目录结构（src 布局）

pyproject.toml + package.json(sidecar pin XActions) + config/{settings.yaml, sources.yaml[占位], strategies/, secrets.env(gitignore)} + data/{xar.db, checkpoints.db, jobs.sqlite, persona/[占位], cookies/, logs/} + sidecar/server.js（XActions MCP --http 启动包装+健康探针）+ src/xar/{main.py, config/loader.py, xclient/{base, xactions_client, twikit_client, tapio_client, router}, graphs/{state, content_graph, reply_graph, review_graph, tune_graph}, memory/{short, mid, long, ddl}, pipelines/{topic, draft, humanizer, publish, listen, reply, collect, optimize}, safety/{budget, warmup, killfile, shadowban}, review/{feishu, panel/}, llm/{gateway, prompts/}, audit/log.py} + tests/（seam 契约测试优先）。

## 3. XClient 抽象接口设计

数据模型（Pydantic）：Tweet{id,author,text,created_at,metrics}、User、ActionResult{ok,error,rate_limited}、Mention、HealthStatus{OK/DEGRADED/DOWN}。

接口：post_tweet/reply/like/follow/delete_tweet（写）；get_mentions/search/get_tweet/get_user_tweets/get_conversation(tweet_id, sort default|latest)/typeahead（读，typeahead 供影子封禁第一维）；health/refresh_session（会话）。全部返回 ActionResult 风格不抛业务异常。

降级链：写 XActions→twifork（仅紧急，标记高风险）→失败挂起等人工；读 XActions→twifork→twitterapi.io（只读付费兜底）；**影子封禁自检读走 fxtwitter 独立通道（不经 XClient，防同源污染）**。

- 健康检查：每 5 分钟轻量探针（get_user_tweets(self,1)）；写前后各一次；连续 3 次失败标记 DOWN 切下一级 + 飞书告警。
- 切换按**操作类别**而非全局——读挂不影响写。切回需连续 10 次探针 OK（防抖动）。
- 能力矩阵：twitterapi.io 只注册读能力（写需提交 cookie+代理，安全语义不符，禁用）；twifork 写默认禁用由配置显式开启。
- cookie：主号 cookie 只由 XActions 侧车持有；**第二检测小号 cookie 独立存放**；失效时飞书"需人工重新登录"卡片。

## 4. LangGraph 图设计

全局状态 XarState(TypedDict)：run_id/kind/thread_id；内容线 topic/draft/humanizer_report/approval/publish_result；回复线 mention/interactor_profile/tier/reply_text/delay_plan；公共 budget_snapshot/errors/audit_trail。

四图节点流：
- content_graph：pick_topic → retrieve_memory → draft → humanizer_gate（规则+LLM 双检，不过回 draft ≤2 次）→ **approval_gate(interrupt)** → budget_check → publish → schedule_collect（登记 2h/24h/48h）
- reply_graph：classify_mention（垃圾/无关过滤）→ load_profile → tier_decision → draft_reply → humanizer_gate → **approval_gate(初期)** → budget_check → human_delay（拟人延迟节点）→ send → update_profile
- review_graph：collect_metrics → compare_baseline → daily_report → update_casebook → 飞书推送（全自动无 interrupt）
- tune_graph：weekly_aggregate → propose_changes(≤3) → rollback_check → **approval_gate** → apply_version

Checkpointer：AsyncSqliteSaver.from_conn_string("data/checkpoints.db")；thread_id 规则 content:2026-10-22:1 / reply:{mention_id}——天然幂等键，崩溃按 thread_id 重放即断点续跑。

**interrupt 审批流纪律**（关键）：① interrupt 前**零副作用**——发送/写库动作全放 interrupt 之后或独立节点（resume 时节点从头重跑，副作用不幂等会重复发推）；② 每节点至多一次 interrupt；拒绝走条件边回 draft（带回人工批注），禁止节点内 while+interrupt；③ payload=草稿全文+Humanizer 报告+理由；resume 值 {"decision": approve|edit|reject, "edited_text", "note"}（Pydantic 校验）；④ 超时：内容 6h 丢弃（热点过期），回复 2h 降级 Safe 模板或丢弃（可配）。

## 5. APScheduler 触发层

锁 3.11 + AsyncIOScheduler + SQLAlchemyJobStore(sqlite) + job_defaults{coalesce:True, max_instances:1}，全部 job 显式 id + replace_existing + misfire_grace_time。

时刻表（Asia/Shanghai，jitter 防机械感+防雪崩）：mentions 轮询 */7min（jitter 120）；内容生产每日 2-4 次，时刻从策略参数库读，**发帖间隔强制 ≥3h**；数据回收 T+2h/T+24h/T+48h 动态 add_job；每日复盘 23:35（jitter 600）；每周调参 周日 21:50（jitter 900）；预算重置 04:00（jitter 1800）；影子封禁自检 08:17（jitter 1200）；健康检查 */5min（jitter 30）；STOP 文件检查 * * * * *（每分钟+每个写操作前内联）。

## 6. 三层记忆数据模型（data/xar.db DDL）

- daily_context(id, day, kind mention|post|event|note, ref_id, payload JSON)——04:00 归档压缩进 interaction_history 后删 7 天前数据
- interactor_profile(handle PK, display_name, bio, follower_count, **is_mutual**（互关标记，算法×4权重杠杆）, tier_hint, affinity_score, tags JSON, notes（LLM 一句话画像）)
- interaction_history(id, handle FK, direction in|out, kind reply|like|mention|quote, tweet_id, our_tweet_id, sentiment, summary)
- persona_cards(id, version, card JSON, active) / style_library(category 开头钩|转折|结尾提问|金句, content, source manual|learned, use_count, avg_engagement) / case_book(kind success|failure, channel content|reply, content, context JSON, metrics JSON(T+48h 终值), lesson, strategy_version)
- post_metrics(tweet_id, snapshot T2h|T24h|T48h, likes/reposts/replies/quotes/bookmarks/views, PRIMARY KEY(tweet_id,snapshot))
- strategy_versions(version PK, params JSON 全量快照, change_log 强制非空, status active|rolled_back|superseded) + strategy_outcomes(version, week, score, PRIMARY KEY(version,week))
- daily_budget(day PK, *_limit 已随机化, *_used) / audit_log(action, payload, result, client, strategy_version) / shadowban_checks(dimension suggestion|search|ghost|reply_deboost, status pass|fail|untestable, evidence)

中期记忆 MidMemoryPort 抽象（get_profile/record_interaction/summarize）预留给用户现有记忆系统替换默认 SQLite 实现。

## 7. 内容生产流水线

选题（pick_topic）：内容源抓取[占位] + 当日上下文 + 48h 内已发主题去重 + 算法知识库过滤 → LLM 产 3 候选+推荐理由。
草稿（draft）：注入人设卡 active 版（压缩为 8-12 条行为准则）+ 风格库 few-shot×3（按 avg_engagement 优先）+ 近期 success 案例×2 + [DO NOT]（禁忌+Humanizer 精简版）；约束 ≤280 字/分句短句节奏；产出正文+备选开头。jinja2 模板渲染，版本随 strategy_version 留痕。
门禁（humanizer_gate）：规则引擎（humanizer-zh 31 条蒸馏：连接词套路/排比/拔高/emoji 堆砌/客服腔）+ LLM 二检（按清单打分），任一不过带批注回 draft ≤2 轮。

**算法知识库硬性约束**（检索修正后落地）：单帖间隔 ≥3h；48h 窗口内表现差的帖允许删除重发（无算法惩罚）；前两句必须"抓停留"；优先产出"可争论观点/开放问题"（reply 权重 5 与 ShareViaCopyLink 20 是最强杠杆）；杜绝钓鱼引战（mute/block/report -31~-234 且 agatha 给账号级标签，进 visibility-filtering 是整体 DROP 非扣分）。

## 8. 回复互动流水线

监听（每 7min）→ classify_mention（垃圾/广告/无关丢弃）→ load_profile（画像+近 10 条互动史；互关标记优先级上调）→ tier_decision（Safe 陌生人/敏感话题/warm-up 期；Interesting 熟人/同领域；Bold 仅高 affinity 互关+策略放行+人工审批）→ draft_reply（≤140 字优先，短句节奏）→ humanizer_gate → approval_gate → budget_check → human_delay（阅读=对方帖字数×80~150ms；打字=字数×150~400ms+停顿；总延迟截断 [45s,6min]；期间 sidecar 执行滚动浏览）→ send → update_profile。

Bold 档额外约束：每日 ≤2 条、连续两周期无负面信号（被 mute/block 代理指标）才保持开启，由优化引擎管理。

## 9. 数据回收与优化引擎

fxtwitter 独立通道：每帖 T+2h/T+24h/T+48h 三次快照，/2/status/{id}，失败重试 3 次指数退避。T+48h 为终值（对齐 AgeFilter）。

指标口径（以 T+48h，views 为分母）：ER=(likes+reposts+replies+quotes+bookmarks)/views；关注转化代理=周期粉丝净增÷周期发帖数（fxtwitter 拿不到 profile visits，粉丝数用 XClient 读）；回复质量比 replies/likes（杠杆信号，对齐 reply=5 vs like=0.66）；传播系数 (reposts+quotes)/views。周期综合分 score = 0.4·ER_z + 0.3·回复质量比_z + 0.2·粉丝净增_z + 0.1·传播系数_z（z-score 标准化，权重入参数库可调）。

每日复盘（全自动）：动作清单 vs 预算；每帖三次快照对比与归因（LLM）；新案例入 case_book（阈值：ER 高于/低于近 14 天中位数 1.5 倍）；飞书图文简报。

每周调参（interrupt 审批）：weekly_aggregate 计算本周 score 对比前两周 → rollback_check（当前版本 score 连续两周期低于其基线→自动回滚到最近无此问题版本，跳过新提案）→ propose_changes（LLM 基于 case_book+指标提出 ≤3 个参数修改，候选集：发帖时刻/选题分布/三档配比/Humanizer 严格度/回复延迟区间/ER 权重，每项带预期假设）→ 人工审批 → apply_version（新行全量快照+change_log，旧版 superseded；回滚=把目标版本 params 复制为新行，永不原地改）。

A/B：同周期对"同选题簇"内容帖随机分桶两个策略变体（如开头钩 A=提问 vs B=断言），单变量，每周最多 1 组；样本 ≥4 帖/桶才下结论，结论入 case_book.lesson。回复侧按 tier 天然分桶观察，不主动 A/B。

## 10. 安全与风控

预算随机化（每日 04:00）：factor=U(0.7,1.3)，写操作前原子扣减（UPDATE...SET used=used+1 WHERE used<limit，影响行数=0 即拒绝）；每小时写操作数另设软上限（≤4）防"预算集中在凌晨"的机械模式。

warm-up 阶段表：W0 静默（1-3 天：只浏览滚动，零写操作）→ W1（4-10 天：+回复≤3/日仅 Safe+点赞≤10）→ W2（11-14 天：+首帖≤1/日，回复≤5）→ W3 转正（15+：渐进到全额，每周+30% 至 base）。

影子封禁四维自检（每日 08:17±20min）：①②用 fxtwitter 免账号通道（/2/typeahead?q=handle、/2/search?q=from:handle 只测原创——repost 天然不召回）；③④用**不关注本号的第二小号**经 XClient 只读执行（ghost ban：小号拉原帖 /2/conversation 双排序读到底，回复在两者均缺席→fail；对话读不完→untestable 不报假警；reply deboosting：latest 存在但 default 中被压到 Show more 之后→疑似，最弱证据连续 3 天命中才告警）。任一 fail→飞书红色告警+**48h 静默期**（停一切写操作只读观察）+复测 3 次确认。

STOP 文件：每分钟轮询+每个写操作前内联检查；文件存在→写任务暂停（读保留），进行中的图在下一节点边界挂起留 checkpoint；删除文件→自动恢复需面板二次确认（防误删恢复）；另支持 STOP.hard：进程直接退出须人工重启。

## 11. 审核面

飞书 interactive 卡片（正文+Humanizer 报告+选题理由+预算余量+三按钮 通过/改后发/拒绝）；回调 FastAPI /feishu/callback → graph.invoke(Command(resume=...))；"改后发"走面板编辑框（卡片带深链）。

FastAPI 面板（127.0.0.1:8377，basic auth）：/approvals 待审队列、/switches 双开关 AUTO_CONTENT/AUTO_REPLY、/dashboard（预算用量/近7日指标/策略版本与回滚历史/影子封禁/XClient 健康）、/stop（hard 模式二次确认）、/cases 案例库。

双开关渐进：M1-M2 双关全人工 → 先开 AUTO_REPLY 的 Safe 档 → 再开 AUTO_CONTENT（保留每日首帖人工）。开关状态落库，切换写审计+飞书通知。

## 12. 配置系统

分层：settings.yaml（非密：调度/预算/降级链/开关/指标权重）、sources.yaml（占位）、secrets.env（GLM_API_KEY/FEISHU_WEBHOOK/COOKIE_PATH/PANEL_AUTH；NSSM AppEnvironmentExtra 注入，迁 VPS 换 systemd EnvironmentFile）、strategy_versions 表（运行时可调参数）。
密钥纪律：secrets 只经环境变量进进程不落日志；cookie 目录权限收紧；webhook 视为机密。

## 13. 里程碑计划

| 里程碑 | 周期 | 交付物 | 验收标准 |
|---|---|---|---|
| M0 骨架 | 第 1-2 周 | 目录/配置/DDL/XClient+XActions 侧车/seam 契约测试/NSSM | sidecar 手动发测试帖+读 mentions 成功；崩溃重启调度器恢复；STOP 生效 |
| M1 半自动内容线 | 第 3-4 周 | content_graph 全链（门禁/飞书审批/预算/T+48h 回收）、每日复盘 | 连续 7 天审批→发布→回收→复盘全通；interrupt 后重启可 resume；warm-up W2 合规 |
| M2 半自动回复线 | 第 5-6 周 | reply_graph 全链（画像/分档/拟人延迟）、四维自检 | 连续 7 天零超预算零漏审批；四维自检每日出报告（含 untestable 处理） |
| M3 优化引擎 | 第 7-8 周 | review/tune 图、策略版本化、A/B、自动回滚 | 跑过 2 个完整周周期；1 次调参提案（≤3 参数）；演示 1 次回滚（可注入假数据） |
| M4 渐进全自动+可搬迁 | 第 9-12 周 | 双开关切自动、VPS 迁移手册与演练 | 全自动连续 14 天无人工干预；data/ 打包到 VPS 恢复原样续跑（thread_id 级验证） |

## 14. 风险清单与对策

浏览器自动化被封（高）：预算硬上限+随机化+warm-up+拟人延迟；自检早发现；STOP；账号定位可牺牲资产（内容资产在 SQLite 可重生）。
XActions 项目小、宣称未经验证（高）：M0 用真实账号做 1 周能力实测再定主通道；XClient 抽象保证 1 周内可切 twifork。
twikit/twifork 解析器随 X 前端变更再崩（中）：降级第二级默认只读；解析器崩只影响读，写挂起等人工。
fxtwitter 被封或限流（中）：重试+降级 XClient 自带读取；指标缺口标记不阻塞主流程。
twitterapi.io 写需提交 cookie（安全语义差）：**禁用其写能力**，只作读的付费兜底。
Humanizer-zh 无 pip 库（中）：门禁双检；人工审批期积累"被改稿"案例反哺规则。
interrupt 副作用重放导致重复发推（中）：节点纪律（interrupt 前零副作用）+ thread_id 幂等 + publish 前查 audit_log 去重。
SQLite 并发写（低）：WAL+单写进程（面板只读走只读连接）+写操作串行队列。
Windows 断电重启（中）：NSSM Restart + misfire 补跑 + checkpointer 断点续跑。
GLM 质量波动（低）：OpenAI 兼容网关封装换模型只改配置；prompt 随策略版本留痕。
Bold 引负面反应（中）：默认关、需高亲密度+人工审批；mute/block 代理指标进周期 score 触发自动降档。
平台条款：定性个人研究项目、单账号低频次；不规模化不多号；用户自担账号风险（README 明示）。

## 附：算法知识库修正备忘（决策⑦ 经源码核验后两点修正，落地以本方案为准）

1. "新账号流量加权"→ 冷启动试水位（曝光<200、2h 内、粉丝<5 万的帖抬至信息流 15-16 位）：每帖都有试水温机会，**前两句抓 dwell 最重要**。
2. "作者回复加权"→ 互关作者原创帖 reply 权重 ×4（5+15）：**早期与同领域中小号真诚互关互回，是代码层面确证的最大杠杆**。
