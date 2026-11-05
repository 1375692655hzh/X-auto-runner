# SP0 · x-hand 操作层底座

## 对应优势
全部优势的地基——没有它，认知层没有任何真实世界的手。

## 目标
把 x-auto-run 的窗口操作范式（比特浏览器固定指纹窗口 + DOM 探测 + state/ 记账）重构为 **XClient 端口 + 闸门**形状，供认知层（或任何上层）调用。

## 处理方式
1. **原语全量移植**：openWindow / verifyLogin（导航链接→twid 反查双通道）/ gotoFeed（expectSid 落地软校验）/ findArticleByStatus（不取 arts[0]）/ humanClick / humanScroll（15% 回看）/ submitReply（气泡判定）/ readToast / probeReplyComposer / collectPosts → `hand/primitives.js`
2. **包成 loopback HTTP 服务**：127.0.0.1:8741 + bearer，只绑本机；单飞队列优先级 post > reply > like > browse（浏览可被抢占）
3. **每动作临时页隔离**：独立页用完即关，与 resident 轮询页互不拆工位；intent 先落盘 `queue/intent-<id>.json` 再执行（崩溃可辨"可能已发出"）
4. **--dry 模式**：过全部闸门、不点击、不写状态
5. **cookie 安全模型**：cookie 不出比特浏览器 profile，hand 只经 CDP 操作页面，不落盘、不进日志、不进 LLM 上下文

## 关键部件
`server.js` / `primitives.js` / `probe.js` / `humanize.js`（接口预留，SP4 填充）/ `quality.js`（接口预留，SP3 填充）/ `resident.js` / `resident-guard.js` / `state/` 全套文件族 / `deploy/*.bat`（拉起/看门狗/登录维护）

## 验收指标（kimi M0/M1 原文）
- 杀进程三次（intent 落盘后 / 气泡判定前 / 对账前）→ X 上只有一条
- STOP 信号 ≤3s 停止，未提交文本不落盘
- 单飞队列 1000 次并发注入，预算不超限
- 连续 7 天只读运行零事故
- cookie 不出现在任何日志与上下文（全量日志审计）
- 看门狗能识别僵尸窗口（心跳+页面双健康）并拉起

## 依赖
无。x-auto-run 现成代码复用率 ≥70%。

## 单独价值
即使永远不建认知层，端口化 + --dry + 闸门化本身就是 x-auto-run 的一次工程升级（可测试、可演练、可被其他上层复用）。

## 方案出处
kimi-k3 §一/§六 · cursor-opus-5.5 §2（BitWindowXClient 端口） · grok-4.7 接口节
