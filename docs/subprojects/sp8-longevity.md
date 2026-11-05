# SP8 · 寿命工程（熔断 / 对账 / 不变量）

## 对应优势
账号活得住——**寿命本身就是复利的前提**，一次封号清零所有积累。

## 目标
把 x-auto-run 已验证的安全实践升格为**可断言、可演练**的寿命保障体系。

## 处理方式
1. **state/ 单写者纪律**：只有 hand 和确定性工人可写；core 任何组件直接写 state/ 视为 P0 缺陷（code review 检查项）
2. **unknown 纪律全套**：
   - intent 残留（崩溃）→ 一律转 unknown、补记额度、**禁重试**
   - unknown → 30min 后 verify-unknown 走 FxTwitter 匿名核验（正文规范化哈希比对，≤2 次）
   - 仍不明 → 保持 unknown、占额度、**全局停写等人** + 飞书告警；同主题第二条草稿在对账完成前不得执行
   - reply 类 → verify-reply 双端点确认是否"误发独立帖"，双端点确认才进人工待办
3. **19 条不变量**（cursor I1-I19）：assert-invariants.js 每次唤醒前后断言，违例即 STOP
4. **熔断梯度**：风控 toast 即当日熔断；同类连败三次当日熔断；GREEN/YELLOW/RED——YELLOW 预算×0.5 关 Bold、RED 48h 只读、恢复需连续 3 天 GREEN + 人工确认
5. **观察窗自检**：比特浏览器第二固定 profile（无登录外部视角），搜建议/搜封禁/幽灵回复/回复降权四维；任一通道失败 = unknown 不计 pass；**禁用登录态自己看自己当证据**
6. **PAUSE 旗标**：`state/PAUSE` 存在即全停；删除不自动恢复，须面板二次确认（与 x-auto-run 同款语义）

## 关键部件
`verify-unknown.js` / `verify-reply.js` / `health.js` / `selfcheck.js` / `assert-invariants.js` / `state/pending-verify-main.json` / `state/health-snapshots/`

## 验收指标
- 熔断演练：注入假 toast → 验证全停 + 告警 + 恢复需人工
- unknown 演练：发帖瞬间断网 → 验证不重发、占额度、停写
- 僵尸演练：手动杀窗口进程 → 看门狗双健康判定拉起
- 连续 4 周 zero 安全事故

## 依赖
SP0（可与 SP0 同批并行，熔断逻辑随 primitives 一起移植）。

## 单独价值
x-auto-run 直接受益——它已有雏形（toast 熔断/verify-unknown），这里是体系化：演练化、断言化、梯度化。

## 方案出处
grok-4.7 两套真相/反退化条款 · gemini-3.8 §2.2 uncertain 对账协议 · cursor-opus-5.5 §2.6-2.7 · kimi-k3 §二.3/§四.7
