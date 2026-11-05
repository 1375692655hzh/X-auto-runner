# 来源：gemini 岗 · Antigravity CLI（gemini-3.8-flash-high，status: SUCCESS）· MoA 第二轮 · 2026-10-22

> 原始 JSON 捕获于 %TEMP%\agyx\plan_extracted.md（项目目录未写入任何文件）。
> ZCode 侧复核补充：XActions 属实（npx 参数形式建议 Phase 0 实测确认）；fxtwitter 属实（/{user}/status/{id} 与 /status/{id} 均可）；勘误：settings.yaml 中 glm-4-flash/glm-4-plus 模型名落地时替换为套餐内现行型号；SqliteSaver 导入路径按安装版本统一。

## 1. 项目总览

### 1.1 核心目标
打造 7x24 小时低成本、长期稳定自主运行的 X 中文 AI/科技垂类人设账号。通过"行业信源追踪 → 洞察提炼 → 拟人去 AI 味发帖 → 实时分级互动 → 算法正反馈回收 → 策略闭环调优"，从零冷启动沉淀为具备独立观点、真实可信、高粘性的科技行业 KOL。

### 1.2 7x24 运行形态
- 守护进程常驻（Windows NSSM / Linux systemd），心跳保活与崩溃自愈。
- 拟人生理节律：拒绝机械化整点定时，拟合真实从业者作息（早晨浏览盘点、午间浅度互动、晚间黄金深度输出、深夜休眠），所有任务叠加泊松随机扰动。
- 人机协同到渐进放行：飞书 Webhook 交互卡片 + FastAPI 面板人工秒级核准；指标满足护栏后分流平滑过渡至全自动。

### 1.3 核心指标矩阵

| 维度 | 指标 | 计算/监控逻辑 | 阶段目标 (30/60天) |
|---|---|---|---|
| 北极星 | 有效互动率 QIR | (深度回复数+优质转推数+收藏数)/总曝光量 | ≥3.5%（行业基准约1.2%） |
| 北极星 | 粉丝转化效率 FCR | 周期内净增真实关注数/(总曝光量/10000) | ≥8.0 关注/万次曝光 |
| 护栏 | 影子封禁状态 | 每日四维检测 | 0 异常（100% 绿灯） |
| 护栏 | 用户负向反馈率 | (被拉黑+被举报+被静音)/总互动人数 | ≤0.05%（触发即停机） |
| 护栏 | API与会话异常率 | 失败及重试请求数/总请求数 | <1.0% |
| 护栏 | 预算击穿率 | 实际操作量/每日随机预算上限 | 绝对为 0（硬性门禁阻断） |

## 2. 总体架构

8 个核心功能层级：触发层（APScheduler AsyncIOScheduler，泊松抖动与作息节律控制器）→ 编排层（LangGraph StateGraph，SqliteSaver 断点持久化，Dynamic interrupt() 审批中断）→ 三引擎并列（内容生产引擎：选题聚合+多级分层 Prompt+Humanizer-zh 门禁+分句拟人节奏；回复互动引擎：巡检+画像检索+三档分流+负向博弈过滤；数据优化引擎：fxtwitter 回访+48h 衰减归档+每日复盘+每周 A/B 调参回滚）→ 真人感层（WPM 泊松打字、字符数加权视线停留、贝塞尔鼠标、跨操作抖动冷却池）→ 接入层（XClientBase 统一抽象：XActions MCP 主通道 / Twikit 备选 / TwitterAPI.io 逃生1 / 官方按量逃生2）→ 安全合规层（每日随机预算中心、Warm-up 爬坡表、毫秒级 STOP 断路器、四维影子封禁自检）→ 记忆层（SQLite WAL 三层账本）+ 审核层（飞书卡片+FastAPI 面板+渐进切阀）→ 基础设施层（NSSM→systemd，纯文件 SQLite 跨环境迁移）。

核心端到端数据流（两条 Mermaid 时序）：
- 数据流 A 原创推文：APScheduler 触发 → LangGraph 检索记忆（人设卡+近3日主题+高权重案例）→ GLM 草稿 → Humanizer 校验（未过≤3次带违规原因反思重写）→ interrupt() 挂起 → 飞书/面板审批 → Command(resume) → 拟人执行（阅读延迟→打字分段→提交）→ 写账本 → 48h 观测窗口（1h/6h/24h/48h）→ fxtwitter 定时回收 → 沉淀复盘库。
- 数据流 B 互动回复：扫描 Mentions/关键词/目标博主 → 循环每条候选：预算预检+黑名单过滤 → 画像查询（VIP/新访客/黑名单）→ 三档路由 → 分句草稿 → 门禁 → reply 开关自动/人工 → 拟人发布 → 画像矩阵更新。

## 3. 技术栈清单

Python 3.11.9+；langgraph ≥0.2.20；langgraph-checkpoint-sqlite ≥2.0.0；openai ≥1.40.0（智谱兼容）；apscheduler 3.10.4（AsyncIOScheduler）；fastapi/uvicorn ≥0.112.0；pydantic ≥2.8.0；aiosqlite/sqlite3（WAL）；httpx ≥0.27.0；XActions（Node 侧车）；twikit ≥2.2.0（冷备）；pyyaml/jinja2（分层 Prompt 装配）；NSSM 2.24。

## 4. 目录结构

```
E:\x-auto-runner\
├── .env.example / .gitignore / README.md / requirements.txt / ecosystem.config.js
├── config/{settings.yaml, persona.yaml}
├── data/{ledger.db, checkpoints.db, STOP}
├── logs/{runner.log, xactions_stderr.log, audit.log}
├── src/
│   ├── main.py
│   ├── clients/{__init__,base,xactions_mcp,twikit_impl,twitterapi_io,official_api,fallback_manager}.py
│   ├── graph/{state,nodes_publish,nodes_reply,nodes_optimize,workflows}.py
│   ├── memory/{db,ddl,short_term,mid_term,long_term}.py
│   ├── humanizer/{engine,rhythm,rules_zh}.py
│   ├── optimizer/{fxtwitter_collector,retrospective,param_tuner}.py
│   ├── security/{budget_center,warmup_manager,kill_switch,shadowban_detector}.py
│   ├── review/{feishu_card,server,gate_controller}.py
│   └── scheduler/{jobs,biorhythm}.py
├── scripts/{setup_nssm.ps1, backup_sqlite.py, import_data.py}
└── tests/{test_humanizer,test_xclient,test_memory}.py
```

## 5. 核心模块设计（含代码级细节）

### 5.1 XClient 统一抽象
- Pydantic 模型：TweetResult / TweetMetrics / MentionItem。
- XClientBase 抽象方法：initialize / post_tweet / post_reply / like_tweet / fetch_mentions / search_tweets / health_check / close。
- XClientFallbackManager（四级降级状态机）：current_tier 1=XActions→2=Twikit→3=TwitterApi.io→4=Official；连续 3 次失败降级并告警，成功重置计数；`_ensure_sidecar_running()` 用 httpx 探测 `http://127.0.0.1:{port}/health`，离线则 `subprocess.Popen(["npx","-y","xactions","mcp","--http","--port",port])` 拉起。

### 5.2 LangGraph 编排
- AgentState TypedDict 全字段：task_id/thread_id/flow_type、内容线（selected_topic/topic_sources/raw_draft/gate_passed/gate_violations/gate_retry_count/final_text/split_sentences）、互动线（target_tweet_id/target_author_*/interaction_tier/author_profile）、审核（approval_status/human_feedback）、发布（published_tweet_id/error_message）、优化（metrics_snapshot/tuning_proposal）。
- human_approval_node：approval_status 已 auto_approved 则跳过；否则 `interrupt({...})` 挂起（载荷含内容预览与可用动作），恢复值 approve/modify/reject。
- build_publish_graph：START→topic→draft→gate→(条件: 过审→human_approval / 未过<3次→draft / 超次→END)→(approved→post_to_x / else→END)→END；SqliteSaver WAL。

### 5.3 三层记忆 DDL
- short_term_context(trace_id UNIQUE, action_type, target_id, content_snippet, status pending|success|failed)
- user_profiles(user_id PK, username, bio, follower_count, domain_tags JSON, attitude_bias friendly|neutral|critical|troll, interaction_count, is_vip, is_blacklisted)
- interaction_history(user_id FK, tweet_id, direction inbound|outbound, tier, content, feedback_sentiment -1.0~1.0)
- persona_definition(version UNIQUE, dimension: background|voice|standpoints|taboos, content_payload, is_active)
- style_exemplars(category: breakthrough_news|paper_review|sharp_comment, exemplar_text, performance_score)
- failure_cases(trigger_type: shadowban|reported|high_drop_rate|ai_hallucination, bad_content, root_cause, correction_rule)
- 读写时机矩阵：short_term 每次抓取后检索近 24h 防重复；user_profiles 策略路由时读、回复后累加；interaction_history 生成 Prompt 提取近 3 次；persona 每日载入内存缓存；style_exemplars 起草时 Top3 few-shot、每周复盘提拔；failure_cases 起草与门禁时作负向提示约束。

### 5.4 内容生产线
- persona.yaml 示例（虚构"林川"人设：前硅谷 AI Infra 架构师；语气克制极客；禁用感叹号/分号/连续问号；核心立场：看好端侧 SLM、质疑盲目 scaling、崇尚开源、一线开发者视角算账）。
- 选题矩阵：ArXiv cs.CL/cs.AI/cs.DC 过滤知名实验室；GitHub Trending & Release 监控（vLLM/SGLang/Ollama 等）；复盘引擎反馈主题。
- Humanizer-zh 门禁 rules_zh.py：AI_CLICHE_WORDS 禁词强过滤、标点规范（感叹号 0 配额）、三段论排比检测、长度与换行呼吸感检测（>260 字超标、单段>100 字无换行）。
- 反思重写 Prompt："上一版草稿未通过真人感质检…抹去公文说明腔，改用一线程序员在茶水间分享技术的松弛口吻重新输出"。

### 5.5 回复互动线
三档判据/策略/配额矩阵：Safe 50%（普通提问/新人打招呼，友善肯定+客观增量，绝不抬杠）；Interesting 35%（迷因/开发者吐槽，幽默自嘲+黑话+轻调侃）；Bold 15%（KOL 片面观点/工程缺陷炒作，犀利但基于工程常识的反事实质疑）。
不回复情形清单（硬性风控，命中静默丢弃严禁调 LLM）：政治敏感/意识形态；虚假信息与加密货币营销（airdrop/pump/free mint/合约地址/财富密码）；恶意攻击（Sentiment<-0.6）；僵尸号（注册<1月或0粉丝无头像）；套壳 Bot 互吹；24h 内同 Thread 已交互>2 轮（防死循环）。

### 5.6 真人感层
- calculate_reading_delay：基底 2s + 字数×U(0.12,0.18)s + |Gauss(1.5,0.5)|，夹在 [3s,30s]。
- calculate_typing_delay：每字 U(0.2,0.4)s + 10% 概率停顿 U(0.8,2.0)s。
- get_action_interval_jitter：高斯抖动 σ=0.3×base。
- BIORHYTHM_HOURLY_WEIGHTS 24 小时活跃系数：0-1 点 0.15/0.02，2-5 点 0.00（深度休眠），21 点 1.00 峰值；周末总预算下调 30-40%。

### 5.7 优化引擎
- FxTwitterCollector.fetch_tweet_metrics：GET /{username}/status/{id} 解析 tweet.views/likes/retweets/replies。
- 综合互动得分 S_c = (10×Replies + 5×Retweets + 2×Likes) / max(Views,100) × 1000（权重依据：回复与深度转推的长尾会话推荐收益最高）。
- strategy_parameters 表（version_tag UNIQUE, parameters_json, parent_version, ab_test_group, status candidate|active|rolled_back, avg_composite_score）。
- 调参硬规则：每周 ≤2-3 参数；A/B 分流 MD5(tweet_task_id)%2；连续 2 周期综合得分低于基线 10%+ → 全自动回滚 parent_version + 飞书红色告警。

### 5.8 算法知识库落地表
| 信号 | 机理 | 落地 | 硬编码默认 |
|---|---|---|---|
| 停留时长与视频完播 | 重排层正向特征，>2s 正分 | 换行/代码引用/悬念设问/思维留白 | 单推≥2处换行，段落≤3行 |
| 关注转化 | Heavy Ranker 极高加权 | 工程可落地 Insight + Bio/置顶定位 | 每 3 篇原创穿插 1 篇体系性长贴 |
| 作者疲劳衰减 | 短期连续曝光指数衰减 | 冷却窗口 | 发帖间隔 ≥2.5h |
| Agatha 负向 | 一次举报抵数十次点赞 | 杠精仅一次幽默化解或不理 | 单次负面回复后直接跳过 |
| 新号冷启动 | 前两周小流量试探 | Warm-up 只读+极少量优质互动 | Day1-7 发帖=0，点赞≤10 |
| 48h 窗口 | 超期进冷存储 | 回收采样锁死 1h/6h/24h/48h | 48h 后不纳入实时调参 |

### 5.9 安全风控
- daily_budget_ledger DDL + generate_daily_budget（BASE 4/15/30 × U(0.7,1.3)）。
- Warm-up 五阶段爬坡表：D1-3 纯潜伏（发0回0赞5-8，浏览+关注3-5人）→ D4-7 微弱互动（回2-4赞8-15，完善Profile）→ D8-10 渐进试水（发1回5-8赞15-20，人工全审批）→ D11-14 步入正轨（发2回8-12赞20-25，启动回收与自检）→ D15+ 正式生产。
- STOP：<100ms 生效，os.path.exists 每次动作调度前检查。
- ShadowbanDetector 四维（未登录匿名独立会话），任一命中：预算降 0 + 48h 只读 + 飞书严重告警。

### 5.10 审核协作层
- 飞书 interactive 卡片 JSON（header 蓝色 + 内容预览 lark_md + 三按钮：批准/驳回/进面板修改，url 指向 127.0.0.1:8000 API）。
- FastAPI 路由：/api/approval/pending、/api/approval/action、/api/control/toggles、/api/control/kill-switch、/api/dashboard/stats、/review（在线改稿单页）。
- 放行门槛：Content 全自动 = 2 周 warm-up 过 + 连续 30 篇批准率 ≥95% + 自检全绿；Reply 全自动 = 连续 100 条通过率 ≥98% + 门禁首轮通过率 ≥90%。

### 5.11 部署
- setup_nssm.ps1 完整脚本（install/AppDirectory/AppParameters/AppStdout+AppRotate 10MB/24h/RestartDelay 10s/start）。
- systemd unit 完整配置；SQLite 迁移用 VACUUM INTO 在线快照 + PRAGMA integrity_check 校验。

## 6. APScheduler 任务总表

job_daily_budget_reset 00:01 / job_shadowban_check 04:30 / job_scan_mentions 每15-25min泊松 / job_publish_slot_morning 08:45+0-30min / job_publish_slot_evening 20:30+0-45min / job_fxtwitter_metrics 每30min / job_daily_retrospective 23:45 / job_weekly_tuning 周日23:55 / job_sidecar_heartbeat 每60s。各带幂等键规则。

## 7. settings.yaml 完整规范

app（env/timezone/db/checkpoints/STOP）→ llm（provider/base_url/default_model/advanced_model/temperature 0.65）→ x_client（primary_driver/auto_fallback/failure_threshold 3/xactions port 8787+cookie+headless/twikit cookies_path/twitterapi_io/official_api）→ budget_and_limits（4/15/30/min_post_interval_hours 2.5/warmup_mode/account_created_at）→ automation_gates（is_content_auto/is_reply_auto=false/approval_timeout_hours 12）→ humanizer（typing_simulation/reading_speed_cpm 400/max_exclamation_marks 0）→ feishu_webhook → server（127.0.0.1:8000）。

## 8. 路线图（6-8 周，开发期与 warm-up 重叠）

甘特图：账号风控生命周期（注册→W1潜伏→W2微互动→正式运营）× 工程演进（Phase 0 基础设施/XClient/DDL → Phase 1 门禁/生产/飞书 HITL → Phase 2 回复线/三档/NSSM → Phase 3 优化引擎/fxtwitter/A/B → Phase 4 全自动切阀/VPS 平移）。
各 Phase 目标/交付物/验收标准齐备（如 Phase 1 验收：100% 拦截感叹号与 AI 陈词滥调；飞书批准 3 秒内恢复工作流）。

## 9. 风险与对策矩阵（8 项）

账号封禁（warm-up+间隔+随机预算+STOP）；浏览器指纹检测（Puppeteer-Extra Stealth+贝塞尔鼠标+固定住宅IP）；Cookie 过期（心跳探测+降级只读+飞书告警）；fxtwitter 限流（阶梯采样+退避）；GLM 限流（tenacity 指数退避+flash 降级）；Windows 休眠（卓越性能+misfire_grace_time 300+coalesce）；SQLite 死锁（WAL+busy_timeout 5000+读写分离）。

## 10. 成本估算

LLM 0 元（套餐内）；X 接口 0 元；前期宿主 0 元；后期 VPS 20-35 元/月；逃生 0-5 元。总计 ≈0-35 元/月。

## 11. 待补充数据接入接口

- 人设语料 data_import/corpus_template.json（category/text/tags/performance_score）
- 互动者画像 data_import/profiles_template.json（username/attitude_bias/is_vip/domain_tags/interaction_notes）
- 内容源 data_import/sources_template.yaml（rss/trending_github）
- scripts/import_data.py 统一 CLI（--type corpus|profiles|sources --file --db）
