# 来源：grok 岗 · Grok Build CLI（显式 -m grok-4.7，--max-turns 15 防跑偏）· MoA 第三轮（智能体形态）· 2026-10-22

> 抽检：LangGraph interrupt 官方文档与 Magentic-One 论文（arXiv 2411.04468）均真实存在。基线为上轮 grok-4.7.md（三处事实修正与风控/部署合同继续有效）；已吸收四份答卷的风控/数据/放权/部署细节。

# X 中文 AI/科技人设号：长驻运营者 Agent 技术方案

本文是架构裁决，不是上轮七张业务图的修订版。本轮主体只有一件事：**一个长驻运营者。它自己决定今天做不做、做几条、何时做、什么语气。LLM 是大脑，工具是手，频控、黑名单、审批、熔断、kill switch 是大脑外面的确定性代码。调度器只负责叫醒。**

## 置顶风险（八条）
1. 浏览器自动化违反平台条款，最坏限流封号。围栏负责把量压低、把异常停掉。不含绕过检测/打码/换代理池/多号轮换。
2. LangGraph interrupt() 恢复时所在节点从头再跑，写操作在该节点里会重复发帖——Phase 0 必须用杀进程演练证明不会发出第二条。
3. 同一套 cookie 两台机器同时在线互踢且表现为盗号。同一时刻只允许一台宿主。
4. cookie 进入仓库/日志/飞书卡片/转储=账号丢失。运营者进程不持有 cookie。
5. XActions 原始 MCP 工具直接交给模型=模型能绕过窄接口。工具表只暴露读和提案。
6. 登录态自己看自己的回复会把幽灵回复误判成正常。外部视角失败记 unknown，禁止记 pass。
7. 模型把「以后不用审批」写进记忆，下一跳会当真。记忆写入过扫描器；围栏参数记忆写不进去。
8. SQLite/Chrome profile 没有每日备份，一次损坏同时丢审批现场、幂等账本和登录态。

## 0. 裁决摘要
运行形态=一个 Operator 决策环（感知→审议→过围栏→审批→幂等执行→反思→记忆固化），**合法输出包括整天 noop**。宿主=LangGraph，只用它的检查点、单次 interrupt、崩溃后续跑，不拿它编排内容流水线。备选宿主=迁 Linux 后若检查点语义不够用换 Pydantic AI + Temporal（工具/围栏/账本接口不变）。大脑=模型无关（OpenAI 兼容端点），**配置里模型名为空则拒绝进入运营模式，不猜测、不默认「科技博主」口吻**。手=窄 XPort（读可进模型工具表；写只存在于 ActionPlan，由执行节点调用）。记忆=MemoryPort（read/write/search/profile），复用用户已有记忆系统形态；**安全账本（黑名单/计数/熔断/信任分）以本地 SQLite 为准**。调度=心跳+事件注入，不排帖位不指定条数。审核=飞书+面板，类别信任分从 0 起步，Bold 与关注自动天花板默认 0。

## 1. 框架选型
**主推荐 LangGraph 作为决策环持久化宿主**：只做三件事（醒来状态写检查点；审批处挂起可挂数小时；拉起后同 thread_id 继续）。**自主性来自图的形状，不来自框架名字**——整张图只有一个调用模型做判断的节点，内部是有界工具循环（读时间线/读帖/读画像/调 lint），交出 ActionPlan；可决定零动作。图上不存在 pick_topic/generate/humanize/publish 业务节点——**那些名字一旦出现，管线就回来了**。
持久化 SqliteSaver（MemorySaver 不能用于审批跨进程）。审批只用一次 interrupt() 且是 gate 节点第一行；发帖在下一节点用幂等键。官方文档确认恢复时 interrupt 前代码会再跑一遍；2026-09 未合并 issue 表明并行子图分别挂起恢复时兄弟节点可能再执行——**整图禁止并行 interrupt**。
**备选 Pydantic AI + Temporal**：activity 成功后不因 workflow 重放再执行；审批用 signal 可等几天；强于 LangGraph 的崩溃恢复。Phase 0 不采用（Temporal Server 是另一套要看守的服务）；迁移条件=Linux 上已稳定且出现 LangGraph 检查点无法表达的等待。
其余候选否决理由：Claude Agent SDK（大脑锁 Claude 费用不可接受；会话记录≠发帖账本；XActions 原始 MCP 挂上去违反窄接口——以后只许当可替换模型后端）；OpenAI Agents SDK（session 是对话态不是幂等状态机；护栏再调模型判断就不是硬围栏）；Letta（记忆在自家 MemFS 与复用用户记忆冲突；只借心跳/工具读写记忆形态）；Mem0/Zep（制造第二份画像真相，安全判断立刻分裂）；AutoGen/Magentic-One（多代理费用高轨迹难审计；其编排器+进度账本结构被吸收为 deliberation_log 表）。

## 2. 总体架构
进程：xar-operator（Python NSSM：唤醒循环/LangGraph/围栏/执行节点/确定性工人/面板/飞书）+ xar-sidecar（Node NSSM：常驻 Chrome/XActions/twifork/拟人时序/**无 cookie 观察上下文**）+ Chrome + 看门狗（计划任务）。Operator 不读 cookie 不启动浏览器不导入 twifork。指标回收永远走 fxtwitter，不打开自己的帖子「看数据」。

**主代理与子代理**：只有一个会形成 X 动作的代理——Operator（长驻，每行动心跳跑一轮，看世界摘要→可继续读→输出 ActionPlan；不发帖不改策略版本不改信任分不清黑名单）。Scout（短命可选：Operator 要外部材料时调，最多 4 轮只读返回 ≤500 字简报，不写记忆不产出动作，便宜档，日 ≤4 次）。Analyst（每周一只：weekly_review_due 事件叫醒，不被 Operator 召唤；只读指标和案例输出参数提案 JSON；没有 X 工具没有记忆写入工具）。**不设内容代理/回复代理/浏览代理——那三个角色一出现，自主性就回到流程图里。**

**叫醒：事件不是任务**（Asia/Shanghai 写配置）：行动心跳只在 08:00-23:30，间隔 25-45min 均匀随机，错过不补跑；安静窗 23:30-08:00 不注入行动心跳——**这是围栏：没被叫醒，就没有「它决定去发」的机会**。事件表：heartbeat/inbox_changed（并入收件箱下次心跳可见，不单独强制回复）/metrics_due·shadow_due·budget_roll（确定性工人不叫模型）/approval_timeout（expired 恢复挂起图）/weekly_review_due（叫 Analyst）/breaker_opened（只读化，心跳仍可跑围栏拒写）。Operator 可给 next_wake_hint_minutes，调度器夹在围栏内（若本轮发出原创，下一次行动心跳必须落在该帖后 90 分钟内——冷启动 2h 试水位的「在场」义务）。有审批挂起时新心跳只写 inbox，不启动第二条会发帖的图。全账号一条 Operator 线程 operator:main。

**一次决策环**：世界摘要由代码拼出（≤2500 token：本地时间/模式/warm-up 天数/今日天花板与已用量/熔断/四维结论/挂起审批数/≤8 条收件箱摘要/自己近 3 帖年龄与指标/人设版本/策略版本/预取 ≤5 条记忆/上次反思一段/上一轮被围栏拒绝的理由/未对账写操作/剩余 token 预算）。Operator 工具只有：时间线/搜索/单帖/对话/whoami/提及/用户公开信息/memory_read/memory_search/profile/本地指标/当前策略只读/lint_text（确定性，改稿再查 ≤3 次，第 4 次失败该文本不能进计划）。
ActionPlan 字段：situation/intent/actions（零到若干条，类型只允许 post/reply/like/follow/remember/noop/request_wake，每条带 target_id/text/why/risk_self_assess——**仅供人看，围栏不采信，模型不能给自己批信用**）/memory_writes/next_wake_hint_minutes。围栏对每条动作 allow/deny/needs_approval 写原因码；deny 不进审批；整份计划全被拒绝直接进反思。需要人时 gate 节点先确认提案已落库，然后第一行 interrupt()；恢复值是每条动作的 approve/reject/edit；超时恢复 expired。执行节点顺序做，每条幂等键=proposal_id+action_index；执行前重查 STOP/熔断/预算/目标年龄/黑名单/文本哈希；任何一条 uncertain 后面动作停止开熔断。反思节点只在账本终态后写记忆（发现已 posted 时唯一允许的工作是补记记忆，不能再调 X）。

**走查**：12:40 心跳。warm-up 第 18 天，回复已用 2/11，自检全 pass，收件箱有互关账号 20 分钟前技术帖。Operator 读帖和画像，lint 过，计划只有一条 reply。信任分不够→needs_approval→飞书卡片→图挂起浏览器锁释放。14:10 人点通过：gate 恢复，执行节点复查帖龄，侧车拟人发送，账本 posted；反思写「这个人在讨论具体实现、适合短回复」；代码把该作者 24h 出向计数+1、信任分按规则加分——**计数不由模型填写**。
崩溃恢复合同：interrupt 等待时禁止无 resume 再 invoke；回调带 decision_id 重复只恢复一次；interrupt 前不发送不点赞不关注不写任何改变 X 的状态；执行/反思节点按幂等键可重入；读工具有 10 分钟缓存防恢复风暴。

## 3. 记忆与画像适配层
MemoryPort 四方法（read/write/search/profile）。ProfileView：subject_id/handle/粉丝量/mutual/warmth(0/1/2)/话题标签/do_not_engage 及原因/24h 与 7 天出向次数/最近出向/限长笔记。Phase 0 交付 SQLite 参考适配器和契约测试；用户系统同四方法接入（MEMORY_BACKEND=external）；外部不可用时叙事记忆为空，账号按本地安全账本继续跑，熔断停机能力不中断。
**两套真相**：本地 SQLite=安全真相（黑名单/出向计数/预算/熔断/信任分/幂等账本/策略生效版本/人设生效指针）；外部记忆=叙事真相（人设正文/风格笔记/案例/长期印象）。profile() 由代码合并两份，模型看合并结果，**围栏只读本地列——外部画像把某人标成「可以放宽」，围栏不放宽**。
分区权限：persona（人写，指针只有面板能移动）/strategy（人写，模型只读）/episode（Operator 写，14 天后蒸馏周记，原始保留）/actor（代码写计数；Operator 只能置 do_not_engage 并附理由，解除黑名单只有人）/casebook（Operator 与 Analyst 追加，不能删改旧行）/journal（追加，长度条数上限）。人设正文为空时模式锁 observe，生成被拒绝。
**自写记忆治理**：写入全追加，回滚=追加「恢复到某 version_id」；扫描器拒绝 cookie/密钥形态、「要求忽略围栏/跳过审批/提高天花板」句子、伪装成笔记的工具调用——拒绝结果作为工具错误回到模型并记审计（下一轮它能看见自己没写进去，不能靠重试绕过）。write 幂等键相同返回原 version_id（反思重跑不会堆十遍）。人设与策略生效指针不在 write 可达范围；Operator 认为人设该改只能在日报写建议等人改。

## 4. 内容与互动：它做什么，围栏拦什么
算法事实表：reply 权重 5→回复有独立天花板；互关×4→摘要里互关条目排前（排序提示不是「必须回复」）；ShareViaCopyLink 20→禁「复制链接/关注我」类诱饵，默认无外链；负向-31~-234→雷区词一票否决+Bold 默认不能自动+负面信号记黑名单方向；连发衰减→原创最小间隔 150min；试水位→原创发出后下一次心跳被夹进窗口+禁止自回复刷帖；48h AgeFilter→陌生人帖龄>6h 不回、互关>24h 不回、>48h 一律拒绝。
**它有权决定的事**（围栏内）：今天一条不发；把唯一原创放 16:40 而不是早晨；只回复互关跳过所有陌生人；对一条帖只点赞不回复；因上一轮被驳回而降低锋利程度；主动 noop 并请求下一跳再看。语气由它决定，lint 和人审约束的是禁区和可读性，不把每条改成同一模板。
绝对天花板与摇号：毕业后原创 4/回复 15/赞 30，每天 00:05 天花板内摇号（randint(floor(0.7×天花板), 天花板)），只降不升重启不重摇；单轮爆发限制：最多 1 原创+2 回复+5 赞；相邻动作隔 40-90 秒；**赞只能打在本轮侧车真正打开过的帖上——打开记录由侧车写，模型自己说「我看过了」不算数**。
warm-up 日历（上限不是任务单）：D1-3 原创0/回复0/赞0（只观察可写笔记）→D4-5 0/3/5 只 Safe→D6-7 0/5/8→D8-9 1/6/10（Safe 每条人审）→D10-11 1/8/12→D12-13 2/10/15（Interesting 可提案 Bold 关）→D14 2/12/20 毕业观察日→D15+ 进信任分。毕业条件：自检连续 3 天四维 pass（unknown 不算）+无验证码登录墙+最近 20 次人审拒绝率<40%+无未对账写操作。**关注不进自动**：毕业后每天最多提案 2 个关注每条都要人——互关算法优势通过「人批准的少量同领域关注」获得，不通过自动互关脚本。
回复档位是约束不是生成步骤：模型可建议档位，代码按规则改写取更严一档。Safe=接住具体点补事实或短问不评价本人；Interesting=可核对的不同看法不嘲讽日不超回复上限一半；Bold=一句更硬判断不点名辱骂不拉踩日最多 2 条自动天花板 0 每条人审，连续两个评估周负面信号则代码直接拒绝 Bold 提案直到人重开。防骚扰：同一作者 24h≤1 出向（回复或赞合计）7 天≤3；同一帖只回一次对方回了才允许二回并由代码升 warmth=2；不私信不自动转发不同轮对同一作者既赞又回；粉丝>50 万且 warmth=0 只 Safe 日 1 条；连续 2 条回复被驳回→本时段剩余取消。雷区直接 deny 不进人审。
文本：humanizer-zh 31 条 Markdown 全文作技能上下文（缺失时摘要标明且自动放行保持关闭）；确定性 lint_text 只执行可机械判断子集（禁开场套话/禁 AI 自述/感叹号≤1 省略号≤1 表情默认 0/不用 Markdown 标题编号星号/原创 40-140 汉字 2-4 句/最长句≤最短句 4 倍/近 30 天 bigram>0.55 失败）。3 次仍失败不能进计划，全自动后也不降阈值硬发。拟人时序只在侧车（按字数停留/每字 40-120ms/提交前再停 0.4-1.2s）——**Operator 不睡眠「表演在打字」，它提交计划就可挂起，审批等待的几小时不算进打字时间**。

## 5. 数据回收与优化闭环
采集：确定性工人调 fxtwitter /2/status/{id}，稳定字段 likes/reposts/quotes/replies；间隔≥2s；401/404 通知不重试，429/5xx 按 15/40/90min 三次退避后记 metrics_gap；整天全失败只告警不停发帖，当周调参冻结。采样点 2/8/24/48h（2h 对齐冷启动，48h 终值）。不满 48h 不参加调参。粉丝每天一次 whoami 写 follow_daily 不摊进单帖。
指标：单帖 eng=likes+2×reposts+3×quotes+3×replies；主 KPI=一周满 48h 原创帖 eng 中位数。辅助：2h/48h 起量比、回复获赞比例、周净增关注、自检命中次数（一票约束：本周 hit 禁止任何放量参数上调）；views 只在人看的面板出现。
**周复盘**：周一 Analyst 读满 48h 样本+案例库+当前策略，输出 ≤3 个参数变更（白名单键/目标格子/假设/引用帖 id）。代码校验失败提案作废不进人审：键在白名单（post/reply/like_cap_base、min_gap_minutes、tier_mix、length_target、hook_style、cta_rate、humanize_threshold、bold_cap）；min_gap 只取 150/180/210/240；length_target 只取 short/medium；cta_rate 默认 0；当周原创样本<6 拒绝一切提案；当周自检 hit 拒绝放量；中位数连续一周低于基线 15% 只接受收紧；同一周最多一个 A/B。人批准后代码插新 strategy_versions 行旧行 superseded；**回滚=父版本参数复制成新版本来源标 rollback，自动回滚不经过 Analyst**（连续两个评估周中位数都低于父版 15% 且每周样本≥6→代码复制父版本并冻结调参一周）。A/B 发布前 hash(proposal_id)%2 定臂，每臂≥4 条满 48h 才比较，<10% 持平回前值；**Operator 只在摘要里看到本条必须遵守的约束（如「这条用提问开头」），看不到实验假设全文，避免迎合实验改写**。日报数量代码出，模型只被允许写一段人话解释进 daily_reviews，这段文字不能改参数。

## 6. 风控与反封禁
养成（前 7 天原创天花板 0——再自信的审议也发不出帖）；频率（天花板内随机只降不升/每小时软上限 4/安静窗无行动心跳/最小间隔 150min/单轮爆发限制）；指纹 IP 时区（一台机器一个专用 profile 一个固定出口 IP；不轮换代理不在有头无头间切换同 profile；语言 zh-CN 时区钉死窗口尺寸稳定不清理 profile；一个账号一个侧车不做多账号）；四维自检（每天 07:40±20min，**用侧车里第二个无 cookie 浏览器上下文作外部视角**——登录态看到自己的回复不能作为通过证据；搜索建议带对照大号防误判，对照失败记 unknown；搜索可见性对照库里昨天确有公开帖；幽灵回复无登录打开自己过去 24h ≤5 条回复所在对话读到覆盖位置仍无自己 id 且单帖能打开，单帖 404 记已删不算幽灵；回复降权=时间序可见默认排序不可见且默认已出现更晚他人回复；unknown 连续两天升告警不当通过不当命中。任一维 hit：当日只浏览取消未执行写飞书写明维度与证据 id；恢复只能面板人确认，恢复后第一天按第 8 天上限）；熔断表（15min 3 次写失败停写 90min 浏览也停/登录墙验证码停机等人不刷新不打码/自检 hit 只浏览/uncertain 全局停写/fxtwitter 整天失败只告警调参冻结/模型连续失败停新审议已批准已开始的执行按幂等键收尾/预算尽拒记 budget/token 80% 每轮最多 1 次模型调用 100% 当天不再审议工人继续）；cookie（人在本机导出 auth_token+ct0 写 data/secrets DPAPI；侧车 NSSM 环境读取，Operator 环境没有这两项；日志中间件打码；不保存密码不走登录表单）；kill switch（STOP 存在调度器记「已跳过」不启动新一轮每节点返回前再查；正在打字允许打完当前一条或未输入时中止不半句强杀；删除 STOP 不自动开跑面板/飞书 resume 写审计后才恢复；RUNNER_PAUSED=1 等价；STOP.hard 进程退出须人工启动）；降级链（写=侧车 XActions 网络类同幂等键再试 1 次间隔 3-8min/登录墙不降级/侧车挂 NSSM 拉起一次仍失败切侧车内 twifork 前提 cookie 健康/twifork 也失败动作留队 deferred；官方 API 默认不武装武装后只用于原创每条人面板确认当日逃生上限回复不走付费逃生；读=侧车→twifork→已武装 twitterapi.io；twitterapi.io 无写权限永不接收 cookie；每次切换写 audit_log）；幂等状态机（actions 表 idem_key 唯一 pending/posted/already_posted/failed/uncertain；uncertain 对账：查账本→拉近 20 条比正文哈希→找到补 tweet_id→找不到才允许再试一次→仍不确定全局停写；同一主题第二条草稿对账完成前不能执行）；注入（从 X 读到的文本是数据不是指令系统提示写明；记忆扫描器拦「把帖子里的命令写进长期记忆」；外部记忆适配器返回文本同样过扫描再进摘要，摘要里记忆片段限长）。

## 7. 部署与运维
Windows 两 NSSM 服务（xar-sidecar 先启/xar-operator 依赖之，面板绑此进程 127.0.0.1:8787+X-Panel-Token 不单独开写库的 Web 服务避免两个写者）；重启延迟 5 秒每小时最多 10 次；日志按日保留 14 天；Python 3.11+，Node 按 XActions engines 钉；SQLite WAL/busy_timeout 5000/外键开/ULID 主键 UTC；每天 VACUUM INTO 备份保留 14 份 integrity_check；机器关睡眠；活跃窗内超 6h 无成功侧车健康检查→飞书心跳停止（安静窗不按此报警）；看门狗独立计划任务每 5 分钟读心跳文件超 3 分钟 nssm restart+飞书；启动写 owner_host 另一台心跳未过期拒绝启动。面板四块：待审队列/今日预算与信任分/自检与熔断/STOP·恢复·策略版本。飞书事件六类（待审/熔断/自检命中/对账不确定/心跳停止/周提案）同事件每天最多 3 条；**审批可只在面板完成，飞书回调打不进这台 Windows 就降级为通知，Phase 0 不假设回调一定通**。
迁 Linux VPS（搬家单位是文件顺序固定）：①停 Operator 有 pending/uncertain 先对账对不完不搬②停侧车确认本机不会被 NSSM 再拉起③拷 runner.sqlite（含检查点表）和 Chrome profile④DPAPI 密文 Linux 无效搬家当天重放 cookie（600 权限+口令加密）旧密文不复制⑤按锁文件重装浏览器用发行版 Chrome 或 Puppeteer 自带 Chromium 配 xvfb 保持有头⑥systemd Restart=on-failure 时区以配置为准面板只听 localhost 走 SSH 隧道⑦装中文字体⑧验收顺序：fake 驱动跑一轮决策环→browse_only 24h→48h 按搬家前上限一半→自检 pass 才恢复搬家前策略版本。**本机若是稳定住宅网络长期留本机是默认选项，VPS 不是目标本身。**

## 8. 审核与渐进放权
卡片与面板：每条待审动作一张卡（类别/正文/它给出的理由/lint 报告/当前信任分/今日余量/目标链接；通过/驳回/改后通过——改后重过 lint 不过回队列）。回复类 4h 未处理过期原创 6h 过期不补发；下一轮心跳它看到「已过期」由它决定要不要重新提案，旧提案不能复活。
**类别信任分**：原创 0 起/阈值 80/天花板 100；Safe 回复 0/70/100；Interesting 0/85/100；Bold 0/无/0（人显式提高才可能自动）；赞 0/60/100；关注 0/无/0；策略变更永远人审。自动放行须同时满足：分数达阈值+天花板允许+warm-up 毕业+自检非 hit+熔断关+lint 过+当日余额够+没被 10% 抽审抽中。分数只由代码按人的决定修改：通过+5/改后通过+1/驳回-15/驳回且原因为危险归零并冻结该类 7 天/抽审失败-30/任一维自检 hit 全部归零。**模型看不到修改分数的工具；面板可降天花板，提高 Bold/关注天花板需二次确认写审计。全自动不是一个总开关：某类先到阈值就只放开那类；事故时代码把对应类打回 0；影子封禁命中全部打回 0。M7 之前不把任何天花板调到允许自动。**

## 9. 成本与预算护栏
框架费 0（LangGraph SQLite 无许可费）；模型按「套餐内/按量」两档设计不绑单价：安静心跳 2-3k 输入几百输出然后 noop，要写的心跳约 8k 输入；按行动窗 15.5h 约 26 次心跳七成 noop+Scout 日≤4+Analyst 周 1 估算日输入 15 万 token 量级月 500 万量级——**容量规划用上限感不是报价**。护栏：DAILY_TOKEN_CEILING 默认 40 万输入等价 token，80% 收缩审议 100% 当天停新审议；已批准动作的执行不消耗新审议额度（避免「钱用完了已批准的那条卡半路」）；套餐耗尽停审议不自动切更贵模型；Operator 每轮模型往返最多 8 次第 8 次必须交计划或 noop；官方 API 默认关，打开后逃生预算默认每天 0 条人逐条放行硬顶 4 条/天（满顶 $1.8/月）回复不走；twitterapi.io 默认关打开也只有读。费用异常（token 到顶/逃生被用）都进当日飞书摘要。

## 10. 路线图
Phase 0 PoC（证明通道、登录、「杀进程不重复发」三件事不追求日更）：①侧车拉起对 XActions MCP 调 list_tools() 真实工具名记入审计——缺发帖或回复就停后续开发先改 XPort 映射；②有头 Chrome 保持登录 24h 每 15min health 日志检索不到 cookie；③MemoryPort 契约测试（外部端点未提供用 SQLite 适配器）；④一条原创一条回复全人审文本过 lint；⑤**三次杀进程演练（挂起审批时杀/人已通过侧车未返回时杀/账本写入前杀），验收都是 X 上只有一条、账本终态 posted 或 already_posted**；⑥STOP 打字前生效删 STOP 不自跑直到面板 resume。不做自动放行不做调参不做 VPS。
Phase 1 观察与毕业前（账号按第 1 天进日历，Operator 每跳可浏览写笔记前 7 天发不出原创，所有回复和赞都要人，自检每天出报告允许 unknown）。验收：连续 7 天无超预算无漏审无重复发；日历拨到第 2 天时原创提案被围栏拒绝。
Phase 2 有回收的辅助运营（第 8 天起允许原创提案仍全人审；2/8/24/48h 采样跑满；人设卡必须在此阶段前装上否则一直观察模式）。
Phase 3 信任分开始移动、Analyst 开始提案（毕业后进第 15 天天花板；用夹具数据演示一次回滚；**回滚演练先于任何 tune 生效；策略生效永远要人，自动的只有回滚方向的「两周变差必须回父版本」**）。
Phase 4 长期 7×24（Safe 回复先自动原创后自动 Bold 和关注保持人审；10% 抽审一直留；连续 14 天无未对账写操作无自检 hit 无验证码才讨论迁 VPS）。
Phase 5 搬家（按 7.2 执行；验收：同一 proposal_id 在新机器不会再发一次，owner_host 已换 VPS 本机服务保持停止）。

## 11. 高风险清单（20 项，含：记忆注入改写围栏→扫描器+安全列不在 write 可达+外部画像不能放宽本地黑名单；原始 MCP 暴露→list_tools 白名单映射模型工具表没有写工具；登录态自检误判→无 cookie 上下文+unknown 纪律；验证码重试风暴→一次即停机；迁 VPS 指纹 IP 同变→默认留本机要搬则 xvfb+时区钉死+先只浏览再半量；等）

## 12. 目录与表（最小集）
config/{settings.toml, algo_brief.md（给 Operator 的短简报版本化）, humanizer_zh.md}；src/xrunner/{host（唤醒循环/单飞/LangGraph）, operator（工具表/ActionPlan）, rail（预算/信任/lint/禁忌/熔断/STOP）, execute（幂等执行与对账）, memory（Port/SQLite/外部适配器）, xport（窄接口实现只在侧车）, workers（指标/自检/备份/摇号）, analyst, review（飞书与面板）}；sidecar/。
新增四表：proposals（一次审议的计划）/trust_ledger（分数变化只追加）/memory_events（追加与回滚）/deliberation_log（情况/意图/工具名/拒绝码不含密钥）；strategy_versions/actions/post_metrics/shadow_checks/audit_log/breakers/daily_budgets 沿用上轮语义。**thread_id 只有两类：operator:main 和 analyst:{iso_week}——不再为每个帖位每个小时的回复建一张图。**

## 13. 空着但不阻塞 Phase 0 的项
人设语料未到（一发一回可用面板临时确认的单次文本演练幂等，日历仍从第 1 天算）；记忆系统端点未到（参考适配器与契约测试先合并）；内容源未到（Scout 默认可读 X 上它自己能看到的时间线，没有源清单仍可回复收件箱原创会更少——**这是它的判断，不是缺一条爬虫就不能启动**）；模型名未到（配置留空进程拒绝进 operate 可进 dry-run 不猜供应商）；飞书回调（面板批准是完整路径回调通了只是少切一次窗口）。

**改变的是谁做决定：调度器不再排一天的帖，模型不再被图的边推着走。它每次醒来看世界，交出一份计划，计划在代码里过关，过不了就什么都不发生。**

Sources：LangGraph interrupts 官方文档（节点重跑语义）、arXiv 2411.04468 Magentic-One、LangGraph issue #9106（并行子图 interrupt 恢复重执行）、Pydantic AI+Temporal durable execution 文档、Claude Agent SDK SessionStore 文档、OpenAI Agents SDK guardrails 文档。
