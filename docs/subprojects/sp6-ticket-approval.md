# SP6 · 票据审批与新场景处置

## 对应优势
无人工处理率 ↑ + 处理质量 ↑；你的介入时间随 agent 信用增长**单调递减**。

## 目标
所有写动作走票据制异步审批（agent 开票后继续干活，绝不阻塞等待）；新颖场景先判断、拿不准开票问你。

## 处理方式
1. **开票**：需人审的写意图 → ticket（intent 全文 + rationale + lint 报告 + 预算快照 + expires_at：**原创 6h / 回复 2h**）+ 预算**预留占位**（ledger.meta.reserved）→ 立即返回 `{status:'pending_review', ticketId}`，agent 循环照常继续
2. **人审面**：飞书卡片三按钮——通过 / 改后发 / 驳回；改后重过 lint；复用 x-auto-run 的 feishu-report.js 通道，零新增基础设施
3. **执行**：获批 → 票据落 `state/tickets/` 副本 → hand 在下一次单飞空档执行，**执行前重过全部门闸**（STOP/熔断/预算确认/间隔/正文哈希）；幂等键 = ticketId，同票最多发一次；目标帖已删则跳过不计失败
4. **流转**：驳回/过期 → 释放预留 + 事件进下次唤醒简报；旧票不可复活，agent 自行决定重新提案；agent 有 `cancel_ticket`（热点凉了可撤回）
5. **结构化拒绝**：`{code, retry_after, hint}` ——agent 据此调整策略而非原样重试；同因 3 拒自动下架该动作（防死循环）
6. **防重发三保险**：24h 相同正文哈希拒、简报含最近动作与待审票据、票据幂等键

## 关键部件
`core/approval/`（票据服务/飞书卡片/面板回调）· `tickets/` 表（SQLite 主档）+ `state/tickets/`（hand 侧只读副本）· DenyReason 结构（SP0 定义）

## 验收指标
- 审批等待中杀进程 → 不重发（回归用例）
- 票据过期 → 预算预留全额释放（对账验证）
- 端到端：开票 → 人审 → 执行 ≤ 15 分钟（人工响应时间除外）
- HYBRID 档抽审率下降曲线（30% → 10%）期间质量抽审不降

## 依赖
SP0。

## 单独价值
可以独立给任何自动发布系统加人审闸门——包括现 x-auto-run 的 llm.js 生成文案（先审后发）。

## 方案出处
cursor-opus-5.5 票据制 · kimi-k3 §四.4 · grok-4.7 PlanVerdict（allow/deny/needs_approval + code）
