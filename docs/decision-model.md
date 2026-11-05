# 决策模型接入与权限（2026-11-04）

## 通道

- `hand/decision.py` 统一支持阿里原生 System One 与 OpenAI 兼容 chat 回退。
- 原生问题类型：choice / noul / score；每次最多16问，返回概率、置信度、等级和实际延迟。
- 原生 Token Plan `/systemone` 实测可用，DeepSeek 示例返回49.5ms；chat回退与Kimi default链保留。
- 每次调用带 decision_id，写入 `state/decision-log.jsonl`，记录场景、输入摘要、答案、模型、延迟、回退和后续实际处置。

## 权限边界

决策模型只做分类、评分、候选排序和分流。无工具、票据、浏览器、批准或发布权限。模型输出不能改变作者内容、编辑结论、票据状态或风控状态。

## 当前接入

1. **资讯选题/互动预判**：沿用现有 `llm.decision` 兼容通道，已验证名企/小公司/头部模型公司案例。
2. **原生结构化选题门**：`news_auto.structured_gate=shadow`，只记录 in_scope、major_entity、excluded_domain、increment，不影响现有选题。
   - 首次硬门回归不稳定：能排掉部分小公司，但也误伤 AMD/美联储，故未启用 enforce。
   - shadow 验证：必贝特与 DeepSeek 仍分别由现有链判18/N和87/A，原生门不拦截。
3. **QQ影子路由**：异步记录 intent/ticket_ref/risk 和置信度，随后记录实际主脑回复；不干预主脑、不调用工具。
   - “直接发吧”实测识别 publish，候选票指向正确，约48ms；仅记录，不执行。

## 后续准入

- 只有当 shadow 数据证明准确、歧义能澄清、无越权动作，才评估只读路由灰度。
- 不接入作者写/SKIP、编辑最终裁决、auto批准、浏览器执行、unknown终局、熔断恢复。
- enforce 模式只能由运营者在独立验收后显式开启。
