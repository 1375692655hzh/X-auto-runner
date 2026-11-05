# core/ — 认知层与指令面（Node）

P3 起填充（对应 docs/build-plan.md P3/P4/P4b）：

- P3：QQ(NapCat/OneBot) 接入、票据服务（开票/预算预留/IM 三按钮/过期释放）、闸门链
- P4：心跳唤醒(TLN)、人设卡+四层 prompt+场景矩阵、媒体检测闸、humanize、画像+三层记忆
- P4b：互关业务层（互关贴流水线/条件关注/串门/互关健康度）
- P5：fxtwitter 采样工人、日报周报、关系网评分、运营台 v1/v2

约束：core 组件不写 state/（红线 §一.2）；需要安全真相只读 getLedgerStatus。
