# 来源：kimi 岗 · kimi-code/k3 effort max · MoA 第四轮（方案 vs x-auto-run 场景评估）· 2026-10-22

> 已逐字读完五份方案（约 159KB）并联网核对 x-auto-run 仓库实况（118 commits、planner/executor、resident-guard/maintain-watch、.bat 启动器、《规范与约束.md》、--dry、35±5 分钟错峰均属实）。

## 总前提
五份方案共同底色：Python 主栈 + XActions MCP 侧车 + SQLite + NSSM + 单账号 + 单人设号冷启动 + 关注默认禁止/永远人审。真实项目：JavaScript/Node + 自研真实窗口浏览器自动化（probe-reply-dom.js）+ state/ 文件 + schtasks/.bat/resident/看门狗 + 四账号错峰 + 给 Owen 做权重与社群互关 + 自动关注是核心动作。**世界观层面（风控文化、幂等、渐进放权）高度可借鉴，物理形态层面几乎全部需要改造。**

## 逐方案判定
- **kimi-k3**：护栏最严但产品假设正面对撞——"默认不开放自动关注""不支持批量互关"否定 contacts/followers-scan/mutual-post；单账号单飞；LangGraph 全家桶=推倒重写。可吸收：账本事实不进 LLM 记忆、uncertain 对账协议、防骚扰硬约束表（24h≤1/7d≤3/同帖一回）、四维自检+GREEN/YELLOW/RED。让步：单账号改四账号串行、关注进配额通道、agent 只做窗口内决策者、Python 栈放弃（薄循环思想可用 Node 200 行实现）。
- **glm-5.3**：五份里对互关态度最正面（"互关好友的原创帖是最贵的不动产"、候选排序互关置顶）；提案-审批-执行三分离契合批处理（.bat 窗口间天然异步，不需要 LangGraph interrupt）；态势报告模式可用现有 dashboard/insights/radar/report 模块组装零新建设；夜整理可映射为"第 7 天复盘"常态化版。冲突：关注 L0 永远人批、单账号、Python/SQLite。让步：关注降级为信任分级（前期人批→稳定后配额自动）、时机自主让位固定运营窗（窗内自主窗外无权）。
- **cursor-opus-5.5**：**形态 B 是五份中唯一直接回答"如何嫁接到已有系统"**——toolbox MCP 化挂到自研 agent，40 个模块现成变工具、llm.js 升级大脑、记忆/心跳不用迁；**票据制异步审批与 schtasks 批窗完美兼容**（人批的动作排下一窗口执行，不占常驻进程）；ToolGateway 13 级中间件链是"约束代码强制"的最佳 agent 化模板；关注有从禁用到配额的开放通道。冲突：Python 主栈（可只借架构不借代码）、单会话全局锁、关注 3/天偏低。可吸收：中间件链清单、票据+not_before、统一拒绝格式 {code,retry_after,hint}、预算原子预留、改稿回流案例、心跳 schema 禁 task 字段的反退化纪律。
- **gemini-3.8**：真人动力学参数矩阵独一无二（打字分块 TLN/手误纠错 2.5%/发前复核/贝塞尔鼠标/甩动回滚）——executor 拟人层唯一直接可抄的成体系参数；Tier-1 互关雷达 30 分钟黄金窗口与 boost 方向一致；登录维护 SOP（停服务→前台人工过验证码→存盘→重启）可并入 resident-guard。冲突：**bulk_follow 被白名单剔除（对关注最决绝）**、interrupt 挂起与批窗最不兼容、LangGraph 常驻、单 Chrome 共享 UserDataDir。让步：interrupt 整体放弃换票据制、follow 重设计为配额核心动作。
- **grok-4.7**：Windows 部署保真度与风控文化第一（两服务+计划任务看门狗读心跳+关睡眠+"长期留本机是默认选项"≈resident 体系镜像）；**"两套真相"原则（本地安全账本与叙事记忆分离、围栏只读本地）——state/ 文件无需迁 SQLite 的关键设计，建议直接采纳为总体原则**；关注有通道且机制最完整（提案→人审→计数→warmth 升级）但量太低（2/天人审）；类别信任分是"关注从人审渐进到自动"的可计算轨道。冲突：明文"一个账号一个侧车不做多账号"（理由同 cookie 互踢对独立 cookie 不成立）、"不通过自动互关脚本"直接否定 contacts 自动化、14 天 warm-up 停掉现网。

## 排序（贴合度高到低）
1. **cursor**（形态B+票据制+ToolGateway，唯一自带嫁接答案）2. **grok**（两套真相+信任分+部署保真）3. **glm**（互关语义正面+三分离契合批处理）4. **kimi**（部件供应商：防漂移/防骚扰/影子封禁/原子预算）5. **gemini**（拟人参数供应商）。
**一句话：骨架学 cursor，安全学 grok，部件从 kimi/glm/gemini 拆。**

## 嫁接建议（八条）
1. planner/executor 升级为核心缝：每 .bat 窗口=一次唤醒，代码组装《态势简报》（时间窗/各账号计数余量/熔断/未读通知/Owen 近帖/待办票据）→ llm.js 审议输出 **ActionPlan JSON**（post/reply/like/follow，每条带 target/text/why/账号）→ executor 网关链校验执行。
2. 调度一切照旧（早/午/晚窗、resident 承载票据执行器+通知监控），agent 时机自主收缩为"窗内自主"。
3. 四账号窗口内串行（butcher→fullwin→wcixot→才女），35±5 错峰留在 .bat 层，每账号独立 state/profile，全局单飞锁由 executor 强制。
4. 关注/互关翻案为信任分渐进放权：follow 类目 0 分起步→前期飞书票据人审→通过率累计到阈值后配额自动→驳回扣分/熔断清零；上限去重由 state 强制；互关置顶写进 inbox-reply 排序。
5. 两套真相：state/ 继续安全真相 agent 只读；叙事记忆（日志/案例/画像）另存；禁止 agent 凭记忆替代 state 查询。
6. 浏览器路径不动（真实窗口+probe-reply-dom.js，不接 XActions）；gemini 打字参数灌进输入层；uncertain 对账补进熔断语义。
7. feishu-report 升级双向票据（提案卡片→结果落 state 票据区→下窗口执行前重过网关；改稿 diff 回流模板迭代）。
8. --dry 升为强制演练；上线节奏扩为分动作类目信任分轨道；凭据隔离现状不变，agent 上下文日志永不出现 cookie。

**按此路径 agent 化不改变物理形态（Node/schtasks/state//四账号/真实窗口），只改变"窗口内谁做决定"——从确定性模板换成《规范与约束.md》和 state/ 硬闸门之内自主审议的 LLM 运营者。**
