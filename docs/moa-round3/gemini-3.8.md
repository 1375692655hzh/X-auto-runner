# 来源：gemini 岗 · Antigravity CLI（gemini-3.8-flash-high，--effort high，status SUCCESS，408 秒）· MoA 第三轮（智能体形态）· 2026-10-22

> ZCode 侧复核注记：①方案第六章负向惩罚写为 Report -369 / Block -234 / Mute -107（2023 年 the-algorithm 原始值）；上轮核验的 x-algorithm 现行口径为 -31~-234 区间，方向性结论一致，落地以重读 param.rs 为准。②其余已验证事实均正确采用。③项目零写入；原始 JSON 暂存 %TEMP%\agyx\round3_gemini.md（编码损坏，本文档为完整重录）。

# X（Twitter）中文 AI/科技人设号：长效自治运营智能体架构方案

## 一、总体架构图景：智能体主体与最小确定性外壳

### 1.1 设计哲学：从"确定性管线"跃迁至"自治智能体"

上一轮的根本局限是流水线思维：外部定时器死板敲钟，硬编码驱动"抓取→仿写→推送→发布"，LLM 仅充当无状态文字加工节点。本方案核心范式转移为以自治智能体为主体：
- 智能体自身拥有状态与生命周期：目标（Goal）、精力（Energy）、情绪基底（Mood）、注意力焦点、记忆、用户画像均为内部状态。
- 由智能体自主决策：何时发声、发什么、去谁的推文下回复、何时保持静默避免算法疲劳、何时启动复盘调优。
- 确定性代码退居"最小安全与物理外壳"，仅承担三大物理职能：时钟心跳与外部感知输入、底层工具与浏览器驱动执行、不可逾越的安全风控硬熔断。

权责边界矩阵：发帖时机/内容/互动选择/收敛静默=智能体决策，外壳只执行约束与物理发布；风控阻断=外壳绝对一票否决（4D 预警、STOP、滑动窗口超限时秒级强行阻断所有写操作）；外壳不强制"每日必须发满 N 帖"，尊重静默指令。

### 架构总图
最小确定性外壳三件套（时钟心跳与感知输入：混合唤醒 Tick+Mentions 监听+飞书事件；不可逾越安全防御：滑动窗口预算器+4D 影子封禁守护+STOP；物理工具执行网关：XClient(XActions/Node)+fxtwitter 抓取+拟人动力学执行器 CDP）→ 智能体认知主体（LangGraph 自治认知环：Perceive→Plan→Act→审批门禁 interrupt?→Execute→Reflect→Consolidate 循环）→ 持久化运行时状态机（AsyncSqliteSaver 检查点恢复+强类型状态+ULID 幂等日志）+ 分层记忆与画像系统（短期/中期/长期 + BaseMemoryAdapter 用户系统适配层）。

## 二、框架选型对比与架构裁决

八框架横评矩阵（LangGraph/Letta/AgentScope2.0/AutoGen-AG2/OpenAI Agents SDK/CrewAI/PydanticAI/smolagents，按 7x24 长时自治、HITL 中断恢复、检查点容灾、认知主体契合度四维打分）：
- **主选：LangGraph (v0.2+) + Pydantic**。理由：①AsyncSqliteSaver 断点续跑不可替代；②原生生产级 interrupt() 原子冻结释放资源、Command(resume) 一行唤醒；③图状态机天然支持有向循环边，完美表达 Perceive→Plan→Act→Reflect→Sleep→Perceive 闭环。
- Letta：设计理念借鉴（Core-Recall-Archival 分层哲学），不引入重量级 Server。
- AutoGen/CrewAI/OpenAI Agents SDK/smolagents：管线特征过重或无检查点恢复，不推荐。
- PydanticAI：核心辅助库（类型安全协议验证与工具沙箱）。

## 三、智能体内核设计

### 3.1 认知决策循环（五大阶段）
1. PERCEIVE 感知：聚合外部环境切片（时间线高相关讨论/Mentions/内容源 RSS/ArXiv/GitHub Trending）+ 算法业务反馈（fxtwitter 指标变动、4D 健康状态）+ 内部心智切片（精力值 Energy∈[0,1]、情绪偏向理性/批判/兴奋、距上帖冷却时间、今日剩余预算）。
2. PLAN 规划：主力模型裁决输出唯一意图枚举——ACTION_POST_ORIGINAL / ACTION_ENGAGE_REPLY / ACTION_OPTIMIZE_STRATEGY / ACTION_REST_SILENCE（输出期望休眠时长建议）。
3. ACT 行动与门禁：人设卡+Humanizer-zh 31 条规则生成候选→自主门禁评估→人工模式下 interrupt() 挂起等待。
4. EXECUTE 执行：严格幂等（Idempotency Key 校验）→XClient/XActions 真机 Chrome 物理交互。
5. REFLECT 反思与记忆固化：预期互动效果存入基线账本；更新互动者画像与短期上下文。

### 3.2 混合唤醒机制
- 心跳驱动：T_tick 基准 15-25min ±20% 截断正态 Jitter；心跳≠发帖命令，多数心跳决策是"无事发生继续观察"。
- 事件驱动：被动互动事件（互关 KOL @、重要回复）立即唤醒；审核恢复事件触发 Command(resume)。
- 智能体自适应休眠：Plan 阶段返回 next_wake_delay=180min，调度器按智能体期望动态抑制非紧急心跳，实现真人作息起伏。

### 3.3 检查点恢复与 interrupt 幂等纪律
已验证：interrupt() 被 resume 唤醒时所在节点从第一行完全重新执行！方案严格三节点物理隔离：节点 1 纯逻辑生成（可重入，生成 ULID 幂等键）→节点 2 纯中断等待门禁（无外部写副作用）→节点 3 严格幂等物理执行（执行前查 action_log 幂等账本，SUCCESS 即短路返回 ALREADY_EXECUTED）。检查点 AsyncSqliteSaver；thread_id 规则 post:{date}:{slot} / reply:{mention_id}；启动时扫描所有 interrupted 状态 thread 重连等待回调。

## 四、记忆与画像系统

### 4.1 三层记忆
- 短期工作记忆：精力值、情绪、近 6h 已发议题防撞车表、预算消耗、热点上下文。
- 中期互动者画像：Handle、互关状态、粉丝量级、社交偏好（技术派/吃瓜派/分析师）、亲密度好感度（Affinity Score，高质量互动递增，杠精降级 IGNORED/RESTRICTED）。
- 长期人设与知识：核心人设卡（专业中立、第一性原理、冷幽默、拒绝公关腔）、Humanizer 底座、高表现 Few-Shot 案例库、复盘沉淀的黄金策略法则。

### 4.2 用户系统适配层（BaseMemoryAdapter 六方法）
get_agent_context / update_agent_context / get_interactor_profile / update_interactor_profile / search_relevant_wisdom / commit_daily_reflection。
双实现策略：LocalSqliteMemoryAdapter（开箱即用）+ ExternalAgentMemoryAdapter（用户系统 REST/RPC/IPC 桥接，配置切换 memory_backend: "external"，LangGraph 认知内核零修改）。

## 五、X 接入层

XClientRouter 熔断路由：主 XActions（Puppeteer CDP 真机 Chrome，共享 UserDataDir，95% 场景）→备 twifork（只读/应急）→逃生（twitterapi.io 只读 / 官方 API $0.015/帖，仅人工授权）。
**XActions 152+ 工具实测裁决 SOP（M0 硬性执行）**：`npx xactions-mcp --list-groups` dump 分组 → audit_xactions_tools.py 调 list_tools() 捕获全量 schema → 白名单精简（Write: post_tweet/reply_to_tweet/quote_tweet；Read: get_user_timeline/get_home_timeline/get_mentions/get_tweet_details；Status: check_login_status；**剔除 spaces/dm/bulk_follow/crypto_x402** 高危组）。全量 152 工具 schema 达 60KB+，会撑爆上下文引发 tool-calling 幻觉。

## 六、内容与互动自主策略（x-algorithm 信号对齐）

信号→策略映射：
1. 冷启动试水位（<200 曝光、2h、<5 万粉→第 15-16 位）→"前两句定生死"：首句拒绝废话客套，抛高密度矛盾或断言提升 Dwell；发出后 2h 伴随守护优先回应评论。
2. 互关 reply ×4（5+15=20）→ Tier-1 互关作者动态雷达，30 分钟内深度增量探讨（冷启动期最高杠杆）。
3. ShareViaCopyLink 20 >> Like → 文末"高价值信息工具箱/图表备查"引导复制链接。
4. 负向断崖惩罚（-31~-234）+ 同作者抑制 → 杠精自动收敛绝不纠缠；间隔 ≥3h；尊重 48h 半衰期。

自主发帖决策 evaluate_posting_readiness()：硬约束检查→选题池高价值评估（相关度>0.85 且独特视角）→撰写（冷启动前两句法则）→Humanizer 31 条自检→审批/发布。无高价值议题则"继续酝酿，不发平庸内容"。

互动分档：Tier1 互关/KOL 30 分钟黄金窗口深度回复；Tier2 长尾提问者真诚解答激发二次回复（拉长回复链）；Tier3 灌水概率点赞 P≈0.15 不回复；Tier4 杠精坚决不回不辩解，画像打标 TOXIC 从感知流过滤。
自主收敛：连续 2 帖 2h 冷启动 likes+replies 未破阈值→自动"冷却模式"，下次发帖间隔延至 6-8h，转入深度阅读学习。

## 七、审核工作流

飞书交互卡片（标题栏动作类型/正文高亮/Humanizer 评分快报/x-algorithm 预期标注/三按钮：通过、快捷行内修改携带 edited_text、打回丢弃附原因注入反思记忆库）+ FastAPI 面板（草稿箱/STOP/策略微调）。

渐进放权三阶段：Phase1 全人工（1-2 周，沉淀人设语气修正范例）→Phase2 互动低风险放权（3-4 周：Tier1 互关/礼貌互动 Humanizer>85 自动秒发免审率约 60%；争议长辩论仍人审）→Phase3 全自动巡航（原创引入"超时免审队列"：评分>92 推卡片 30 分钟无异议自动执行；互动 95% 自治仅留攻击性熔断）。**动态倒退**：4D 异常降权或单日负反馈≥1 次→瞬间无条件退回 Phase 1。

## 八、数据回收与优化闭环

fxtwitter 生命周期回收：T+2h 冷启动试水检查 / T+24h 核心扩散复盘 / T+48h 终局归档（48h AgeFilter 对齐）。views 缺失时用代理活跃分数（Likes+3×Replies+5×Reposts）。
每日 00:30 微复盘：表现最优/最差各 1 篇→自省归因（首句 Hook？题材撞车？时刻错位？）→《次日发帖微调备忘录》写入短期上下文作先验。
每周日 23:00 宏观调参（Git-like）：聚合周互动图谱/粉丝净增/去 AI 味合格率→自主提案 ≤3 项、单项幅度 ≤15%→版本化提交 config_versions（Git Hash+变更理由+预期收益）→**自动回滚看门狗：新版本生效 48h 整体互动率下降超 20% 无条件回滚**。

## 九、风控反封禁与真人动力学

### 9.1 真人动力学（CDT 物理注入，禁 DOM 注入）
- 打字：拼音分块上屏（jieba 分词 1-4 字块，块间 TLN(median=650ms, σ=0.45, 250ms, 2200ms)；英文逐字 110ms）。
- 手误纠错：2.5% 概率打错字退格（先输错误块，停 400-800ms，连按 Backspace，再键入正确块）。
- 发前复核：全文输完光标停顿 TLN(2.5s, 0.5, 1s, 8s)；15% 概率前移检查标点再移回。
- 鼠标：三次贝塞尔曲线 15-35 控制点+末端 5-15px 微抖与过冲修正。
- 滚动：甩动 Flicks（单次 2-5 滚动事件 150-450px；7% 概率微幅反向回滚"看漏了回头重看"）。
- 作息：Pulse/erii 能量系数模型（每日清晨随机生成今日能量 0.7-1.0；夜间 01:30-07:00 低功耗潜水期）。

### 9.2 四维影子封禁自检（每天 07:30±抖动，未登录匿名会话+第三方代理节点）
搜索联想禁/搜索展示禁 from:username/幽灵线程禁（匿名抓取回复节点验证公众可见性）/回复降权禁（Show more 折叠检测）。分级：GREEN 全功能放行 / YELLOW 预警+间隔拉长 50%+暂停主动 @ 陌生人 / RED（命中 Search Ban 或 Ghost Ban）自动激活 PAUSE_WRITES 冻结一切发帖回复、纯浏览养号、飞书紧急呼叫人工。

### 9.3 预算与 STOP
滑动窗口硬预算：初期发帖 ≤3/日、成熟期 ≤5；回复 ≤5/小时、≤25/日。幻觉或 Bug 触顶即 BudgetExceededException 强行终止。STOP 文件毫秒级检查（每次 CDP 动作前+每个打字词块间隔），检测到即 3 秒内物理终止键入与网络请求、未提交文本立即丢弃、飞书紧急报告；删除 STOP 后须面板"二次确认恢复"。

## 十、部署

### 10.1 Windows NSSM
power_setup.ps1 彻底关闭休眠（standby/hibernate/monitor-timeout 全 0）；install_services.ps1：xar-core（`nssm install xar-core $py "-m src.main run"`，ObjectName 当前用户保证 DPAPI/Chrome Profile 可访问，DELAYED_AUTO_START，10MB 轮转，AppThrottle 30s，AppKillProcessTree 1 连同 Node 侧车 Chrome 进程树回收）+ xar-panel + xar-watchdog 计划任务（每 5 分钟检查心跳文件，超 300 秒 nssm restart+飞书）。
登录维护：日常 headless=new（指纹与有头无异）；遇验证码运行 `python -m src.cli login`（暂停服务→前台用户会话弹原生 Chrome 窗口人工验证→Session 存盘→重启服务）。

### 10.2 迁 Linux VPS
`python -m src.cli backup`（SQLite backup() 在线一致性快照+Chrome userDataDir+config+checkpoints → xar-backup-*.tar.zst）→ SCP → bootstrap.sh（Python 3.12 uv+Node 20+Chrome Stable+Xvfb 虚拟显示+systemd 双服务+网络自测+4D 快检）。

## 十一、目录结构

config/{base,safety,humanize,persona}.yaml + deploy/{windows/{install_services,power_setup,watchdog}.ps1, linux/{xar-core.service,xar-panel.service,bootstrap.sh}} + sidecar/src/{mcp_server.ts（XActions 桥接白名单剪裁）,browser_pool.ts} + src/{main,cli,agent/{state,graph,nodes/{perceive,plan,act,execute,reflect,consolidate},skills/humanizer_zh/{SKILL.md,evaluator.py}},memory/{adapter,local_sqlite,external_bridge},xclient/{base,router,xactions_client,twifork_client},approval/{feishu,progressive},optimizer/{fxtwitter,metrics,tuner},safety/{budget,shadowban,kinematics,killswitch},web/{app,routes}} + data/{xar.db,xar_checkpoints.db,chrome_profile,logs}。

技术栈：Python 3.12+(uv)、Node 20 LTS、LangGraph 0.2.x+、Pydantic v2.8+、SQLite 3.45+ WAL、XActions/Puppeteer、twifork、FastAPI 0.115+、飞书 SDK、NSSM 2.24+、Systemd。

## 十二、里程碑（M0-M4，6-8 周）

M0 接入裁决与骨架（第 1 周）：侧车+list_tools() 实测白名单剪裁+LangGraph 极简认知环原型+飞书连通。交付《XActions 真实工具面裁决表》。
M1 智能体内核与受控闭环（2-3 周）：完整认知循环+心跳唤醒+Humanizer 技能+BaseMemoryAdapter+幂等 interrupt 审批+fxtwitter 回收。交付首个完整 Agent 实例。
M2 真人动力学与风控（第 4 周）：拼音分块/贝塞尔/甩动注入+4D 自检+预算与 STOP（3s 刹车验收）+14 天 warm-up 启动。
M3 持续进化与渐进放权（5-6 周）：微复盘+周调参引擎+Phase2/3 放权+用户记忆系统实际对接。
M4 稳态运行与迁移（7-8 周）：NSSM 7x24 压测（连续 14 天无人工崩溃干预）+备份脚本+Linux 迁移演练+全套生产代码与 SOP。

## 十三、风险清单

XActions 维护断代（BaseXClient 解耦+twifork 备选+twitterapi.io 兜底）；影子封禁（warm-up+4D 自检秒切 PAUSE_WRITES+全量拟人）；interrupt 重复发推（零副作用三节点隔离+ULID 幂等账本）；幻觉人设崩塌（Humanizer 前置硬滤+全人工兜底+争议对象直接阻断）；Windows 断网蓝屏休眠（power_setup 锁死+NSSM 延迟自启+看门狗+WAL 断点恢复）。

## 待补充接口规范

1. 人设语料→config/persona.yaml（name/tone/core_beliefs/taboos/golden_samples 标准格式，热加载）。
2. 记忆系统→src/memory/external_bridge.py（UserCustomMemoryAdapter 骨架已留，用户补 HTTP/gRPC/Redis 调用即通）。
3. 内容源→config/sources.yaml（rss/github_trending/watchlist_x_accounts 三类标准格式）。

——本份与上轮 gemini 管线形态的本质区别：决策权从 APScheduler 时刻表移交智能体认知环（Plan 节点四类意图枚举+自主协商 next_wake_delay），心跳只是"脉搏"而非"发帖命令"；框架维持 LangGraph 主选但补齐 8 框架横评与 Letta 分层哲学吸收；用户记忆系统 BaseMemoryAdapter 六方法零侵入接入。
