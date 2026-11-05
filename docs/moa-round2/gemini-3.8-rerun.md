# 来源：gemini 岗 · Antigravity CLI（gemini-3.8-flash-high）· MoA 第二轮重跑（用户点名 Gemini 4）· 2026-10-22

> 模型池实测报告：用户点名 Gemini 4 系。`agy models` 返回池为 gemini-3.8-flash-high/medium/low、claude-sonnet-4-6、claude-opus-4-6-thinking、gpt-oss-120b-medium——**池内无 Gemini 4 系**。按指令未静默顶替、未降级 OA 系，显式选用池内最高档 gemini-3.8-flash-high（--effort high）。
> 四项联网核验已并入：XActions（npm 包 xactions）；twifork = PawiX25/twifork（twikit 活跃维护 fork）；fxtwitter Status Fetch API（约 1000 次/分/IP）；智谱 OpenAI 兼容端点（主力 glm-4.6）。

## 1. 总体架构

八层：触发层（APScheduler 拟人作息+泊松抖动+预算控制器+STOP 哨兵）→ 编排层（LangGraph 三图：PostGraph/InteractionGraph/ReviewGraph，AsyncSqliteSaver + interrupt()）→ 执行层（XClient 抽象：XActions 主/twifork 备/twitterapi.io 读逃生/官方API 写逃生 + 三态熔断器）→ 记忆层三层账本 → 真人感层 → 优化引擎（fxtwitter 1h/6h/24h/48h 回收→复盘→周调参 A/B）→ 算法知识库 → 审核控制层。

三条端到端数据流：发帖流（触发→算法库+记忆库→GLM 候选→双层门禁→interrupt→人工/自动→XClient 幂等写→短期账本+追踪注册）；互动流（轮询 mentions/时间线→画像过滤→算法加权定优先级（互关作者最高）→分档→GLM 短句→拟人延迟→发送→中期账本）；数据闭环流（fxtwitter 四点回收→指标库→日复盘→周调参≤2-3 超参→A/B 验证）。

## 2. 技术栈与侧车协同

Python 3.11+（langgraph/aiosqlite WAL/apscheduler 3.10 AsyncIO/httpx/fastapi/pydantic）+ Node 20（xactions/puppeteer-extra/express）+ 智谱 GLM（glm-4.6 主力 + glm-4.5-air 轻量门禁，OpenAI 兼容）。
侧车协同：Node 起 REST 服务（127.0.0.1:3100，/api/v1/tweet|reply|like|timeline + /health）；Python 主控 subprocess 校验/拉起；Windows 下侧车与主控分别注册 NSSM 服务且主控依赖侧车；心跳探测连续 3 次失败（>15s）告警并切 twifork。

## 3. 目录结构

config/{settings.py, persona_config.yaml, algorithm_rules.yaml}；src/{main, client/{base, xactions_client, twifork_client, fallback_client, circuit_breaker}, graphs/{state, post_graph, interaction_graph, review_graph, nodes/{content_gen, humanizer_gate, review_gate, execution_node}}, memory/{manager, short_term, medium_term, long_term}, humanizer/{rule_engine, llm_judge, sentence_splitter, behavior_sim}, optimizer/{collector, daily_reviewer, tuner}, security/{budget_controller, shadowban_checker, kill_switch}, review/{feishu, state_machine}, web/{app, routes, templates}}；sidecar/{server.js, browser_manager.js, cookie_helper.js}；data/{x_runner.db, checkpoints.db, cookies.json, STOP}；scripts/{install_nssm_services.bat, deploy_linux.sh}。

## 4. XClient 抽象（Pydantic 模型 + ABC）

Tweet/ActionResponse/ClientHealthStatus 模型；IXClient 方法：login_and_restore_session / check_health / post_tweet(quote_tweet_id) / reply / like / get_home_timeline / get_mentions / search_tweets / get_tweet_detail。
XActions 实现：httpx 访问 127.0.0.1:3100；独立 UserDataDir + cookie_helper.js 持久化恢复；连接异常重试 2 次后触发子进程重启。
三态熔断器：CLOSED→（连续失败 3 次或延迟>15s）→OPEN（全量切 twifork）→（冷却 30min）→HALF-OPEN（放行 1 次）→成功回 CLOSED / 失败回 OPEN（冷却翻倍 60min）。

## 5. LangGraph 三图 + 幂等纪律

- PostGraph：fetch_algorithm_context → generate_post_draft → humanizer_gate（否→重写）→ interrupt_review_node → idempotent_publish_node → log_and_record_memory（注册 1h/6h/24h fxtwitter 追踪）。
- InteractionGraph：scan_and_rank（x-algorithm 信号加权排序）→ retrieve_user_profile → classify_reply_tier → generate_reply_draft → humanizer_gate → simulate_behavior_delay（阅读 10-45s）→ conditional_review_gate（按模式开关）→ idempotent_reply_node → 更新画像。
- ReviewGraph（每日 01:00）：collect 48h 指标 → 计算 KPI → glm_daily_reflection 归因 → update_long_term_memory → generate_parameter_advice（周日版本化调参+飞书周报）。

**Resume 幂等化规程**（原理级硬约束：interrupt 恢复时节点从头重跑，副作用与 interrupt 必须节点分离）：review_gate_node 仅含校验+状态变更+interrupt；idempotent_publish_node 先按 idempotent_token 查 action_log，SUCCESS 即短路返回 already_executed 坚决不二次调用外部接口；未执行才写，事务落地。

调度：AsyncSqliteSaver 独立 checkpoints.db；thread_id=post_{task_id}/interaction_{tweet_id}。作息表：发布窗口锚定 11:45/17:30/21:15 + jitter 1800；互动扫描 8:00-23:30 每 25min jitter 300；00:00-07:30 深度静默；预算重置 00:01。

## 6. 三层记忆 DDL + 机制

memory_short_term（session_id/event_type/target_id/content/metadata，48h 滑动窗口）；memory_user_profiles（**is_mutual 互关标记**/attitude_tier enthusiast|neutral|troll|critic/persona_summary/tags）；memory_interaction_history（action_type/sentiment_score）；memory_persona_cards（key/version/is_active）；memory_exemplar_library（exemplar_type success_post|viral_reply|failure_negative + metrics JSON + why_it_worked_or_failed + embedding_text）。
写入：动作成功即事务写短期+互动史；日复盘前 5% 入 success、负反馈/零互动入 failure。Prompt 五段注入：人设卡 + 2 正例 + 1 负例 + 互动对象画像 + 近 6h 已发主题（防复读）。压缩：每日 02:00 GLM 蒸馏《昨日活动摘要》≤200 字入长期后硬删 48h 前明细；90 天无互动非大 V 降级出缓存。

## 7. 真人感层

分档判定树：内容低敏感+互关大V+好感度高→Bold；技术争议+符合赛道→Interesting；其余→Safe。档位规格：Safe 赞同+补 1 事实/延伸提问 20-50 字；Interesting 幽默隐喻/反常识/极客梗 30-80 字；Bold 鲜明立场/指出短板/前瞻预测。
分句节奏：单句 15-25 字强制截断；剥离连接词；标点拟人（换行替代顿号、"..."停顿、禁连续感叹号）。
Humanizer 双层：Layer1 正则硬门禁 8 类模式（AI 总结套话/八股排比/机械递进/情绪堆叠/AI 自我认同/元文本/互联网黑话/编号列表）；Layer2 glm-4.5-air 判分（"10 年推特冲浪经验资深博主"人设的评审 Prompt，输出 JSON {score, passed, critique, suggestion}，<8.0 不过带修改范例）。
行为拟人参数矩阵：阅读 12.0±4.0s [4,35]（+字数×0.08s）；打字单键 85±25ms [35,190]（标点后+200ms）；滚动 350±120px 贝塞尔+2-3 次停顿 1.5s；点赞决策 3.5±1.2s；作息曲线 8-11 点 15% / 11:30-14 点 30% / 18-23:30 点 50% / 凌晨 5%。

## 8. 优化引擎

fxtwitter 四点快照表 tweet_metric_snapshots（UNIQUE(tweet_id, snapshot_stage)）；T+1h 冷启动试水观察 / T+6h 二次扩散判定 / T+24h 主互动沉淀 / T+48h 终点归档。
指标：ER=(Likes+Replies×2+Reposts×3+Quotes×3)/max(Views,100)；深度讨论比 DD=Replies/max(Likes,1)，DD>0.3 为优质话题；算法健康度加权监控。
日复盘（01:30）：中位数基线 → ER>1.5× 黑马与 <0.5× 滑铁卢 → GLM 归因（成功：选题契合/首句悬念/结尾问句？失败：自嗨说教/缺情绪/时段错位？）→ 入库作 Prompt 正负参考。
周调参：parameter_version_log（changes JSON 记新旧值对/baseline_er/achieved_er/status evaluating|adopted|rolled_back）；每周日 23:00 最多 2-3 参数；周期 1 低于基线 10% 黄色警告，连续两周期变差强制回滚+飞书告警；A/B 按 hash(task_id)%2 分组穿插发布，7 天真实指标配对 T 检验 p<0.05 才合并。

## 9. 算法知识库落地（采用核验修正后结论，8 条规则表）

ALG-01 ShareViaCopyLink(20)：干货帖文末"值得收藏"Hook，P0；ALG-02 reply(5)：禁封闭式结论、必附低门槛开放式提问，P0；ALG-03 互关作者 reply×4：互关好友 30 分钟内必回，P0；ALG-04 follow_author(4)：主页置顶高度垂直促转化，P1；ALG-05 同作者衰减：间隔≥120 分钟，P0；ALG-06 48h AgeFilter：旧帖不消耗高额互动预算，P2；ALG-07 冷启动试水位（曝光<200、2h 内、粉丝<5 万抬至 15-16 位）：黄金 2h 内评论区自引补充 1 次，P1；ALG-08 负向 -31~-234：极化言论拦截前置，P0。

## 10. 安全层

预算控制器：**代码基准取 3/12/20（非 4/15/30），保证 +30% 抖动后永不破顶**（int(3×1.3)=3、int(12×1.3)=15、int(20×1.3)=26 均≤天花板）；00:01 随机重置；can_perform() 在 LangGraph 动作节点与 XClient 发包前双重检查。
Warm-up 五阶段：潜伏 D1-3（浏览 20min/天+赞 2-3，建兴趣标签）→ 破冰 D4-7（Safe 回复 1-2/天）→ 试跑 D8-10（首篇亮相推 1/天，避外链营销词）→ 爬坡 D11-14（2 推/天+分档测试+画像记录）→ 正规军 D15+（3-4 推/天+闭环全开）。
STOP：主干循环/每节点启动前/XClient 发包前三处检查；检测到即 sys.exit(1)+critical 日志；触发手段含手动 touch 与代码自动创建（严重封禁/连续 403·429 超阈值）；恢复=删文件+面板确认双步骤。
四维自检（每日 06:00）：搜索建议（补全接口无本号→降频 50%）；搜索封禁（确有帖却搜不到→自动创建 STOP 停发）；幽灵回复（独立代理+未登录复查讨论串，回复隐形→停回 48h 转只读）；回复降权（被折叠/垫底→锁定 Safe 档+强化门禁）。

## 11. 审核与渐进自动化

飞书卡片 JSON（类型/拟人打分/话题 + 代码块正文 + 批准/驳回按钮 value 带 task_id+decision）；FastAPI 路由：/ 总览、/api/review/queue、/api/review/{task_id}/action、/api/feishu/callback（→resume）、/api/config/switches。
三态渐进状态机（content/reply 独立）：MANUAL →（连续 14 天批准率>95% 且无风控报警）→ HYBRID（Safe/置信度>9.0 自动，Bold/<9.0 人工）→（连续 30 天无事故+自检绿）→ FULL_AUTO。紧急逆向回退：影子封禁任一维异常 / 用户举报 / 连续 2 次 LLM 幻觉异常格式 → 强制瞬间回 MANUAL。

## 12. 部署

Windows 三 NSSM 服务：XAutoRunner-Sidecar（node sidecar/server.js，先启）/ XAutoRunner-Web（uvicorn:8000）/ XAutoRunner-Worker（python src/main.py，DependOnService Sidecar）；AppRestartDelay 5000/10000；loguru 按天 10MB 滚动保留 14 天 zip；全局 sys.excepthook 未捕获异常→飞书红色告警。
迁 VPS：wal_checkpoint(TRUNCATE) 刷库 → 搬 data/x_runner.db + checkpoints.db + cookies.json + .env → systemd 双服务 → apt 装 fonts-wqy-zenhei + chromium。

## 13. 30/60/90 天路线图

D1-14 基础构建+潜伏破冰（XClient+侧车通讯、账本、飞书卡片、严格 warm-up）→ D15-30 骨架贯通全人工（2-3 推/天 100% MANUAL、调优规则库、48h 追踪启动）→ D31-60 数据闭环+混合自动（正负案例各 50+、互动流 HYBRID、4 个调参迭代；验收 ER+30%、粉丝破 1000）→ D61-90 全自动飞轮+迁移（VPS 7x24、发帖流 FULL_AUTO、主题级 A/B；验收连续 15 天免人工）。

## 14. 风险清单（7 项）

账号冻结（致命：预防为本，封后停一切重试走申诉）；影子封禁（高：降频至 0 静默 3-7 天待自然解除，解除后回半人工）；Puppeteer 指纹（高：stealth 插件+住宅代理+twifork/人工打码兜底）；第三方库断裂（中：一键降级+按量官方 API 保活）；GLM 抖动（中：退避 3 次+降 air 档+本地缓存草稿）；SQLite 锁（低：WAL+单写连接+每日 .dump）；合规伦理（中：Bio 标注"AI 探索实验体"+免责声明，杜绝政治/灰产/侵权）。

## 15. 待补项兜底（缺省可启动）

人设语料缺省：内置默认人设卡（"专注分布式系统架构、Agent 落地与前沿开源大模型的中国独立开发者/全栈工程师，理性中立、Talk is cheap、偶有自嘲、反感 PPT 黑话"）；后补：data/persona_seed.md + python -m src.memory.manager --import-persona 由 GLM 提取风格覆写。
记忆系统缺省：三层账本从零；后补：同签名适配器 + MEMORY_BACKEND="custom" 切换。
内容源缺省：HN Firebase API + GitHub Trending 解析 + ArXiv RSS + X 高赞中文讨论串；后补：content_sources/ 抽象接口加 RSSHub/飞书知识库。
