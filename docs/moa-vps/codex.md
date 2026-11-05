# 来源：Codex 岗（Codex CLI gpt-6-astra 研究档 read-only，经 codex 岗核验合并）· VPS 选型 MoA · 2026-10-23

> 执行备注：任务经 Codex CLI 独立作答，codex 岗再用腾讯云官方文档联网核验全部价格/套餐/镜像能力后合并。对原稿两处修正：一是将"首台 4核8G 打磨"降级为"临时调试可用、母机必须在量产套餐上制作"（镜像盘大小继承约束，Codex 未考虑）；二是流量估算按官方"仅计出流量"口径（补充住宅代理侧双向计费对照）。

## 结论先行——可直接下单的组合

腾讯云轻量应用服务器（不是 CVM）· 中国香港 · 操作系统镜像 Ubuntu Server 24.04 LTS · 入门型 2核4G / 70GB SSD / 30Mbps / 2048GB 流量包 · 刊例 ¥90/月。先买 1 个月打磨+验证四条链路，关机制作自定义镜像，之后开新号 = 同套餐新实例选自定义镜像 + 灌四个配置。住宅代理、GLM token 费另计，不计入本预算。

购买页动作就两步：把 Hermes Agent 那个 Tab 切到「操作系统镜像」，把 OpenCloudOS 9 改成 Ubuntu。

## ① 镜像路线：选纯操作系统镜像，不选自带 agent，也不选容器镜像

| 路线 | 裁决 | 理由 |
|---|---|---|
| 应用镜像（Hermes Agent / OpenClaw / Octop / DeepSeek Harness） | 不选 | 它们是「另一套 agent 产品」的预装发行版：通用个人 AI 助手/聊天机器人框架（接微信/QQ/DeepSeek 那类，龙虾专区一键部署的就是这个），和你的 X 人设运营 agent 同名不同物。你的 resident、core 认知循环、state/ 账本、SQLite 叙事记忆、看门狗一样都不会被它承接 |
| 纯操作系统（Ubuntu 24.04 LTS） | 首选 | 服务边界干净，systemd/cron 原生管，黄金镜像纯净 |
| 容器镜像（Docker on Ubuntu） | 不选 | 对本项目无收益：Camoufox/Chromium 原生跑更利于排查 /dev/shm、字体、指纹一致性问题；"自动建 5 个容器"是购买页赠品，与一云一号无关 |

自带 agent 镜像的冲突在哪 / 省事在哪：省的只是"部署它自己"这一件事；冲突在于它会预装 Docker、Web 面板、自己的 systemd 单元和开机自启，占用 1-2GB 内存和端口，可能重复连 bot，还会把黄金镜像污染成一堆你永远不调用的软件。停用之后，预装价值归零——所以直接从 Ubuntu 开始最干净。列表里那个「Node.js」应用镜像同理不必选（自带演示应用和面板），Ubuntu 上用 NodeSource 装 Node 20/22 更可控。

OpenCloudOS 9 也不选：不是不能跑，而是 Camoufox / Playwright / rebrowser 这条指纹浏览器栈的官方文档、依赖包（fonts/locales/libstdc++）、NodeSource 支持面全部默认 Ubuntu/Debian 路径，EL 系要额外踩适配坑。Camoufox 官方就有 Linux + Xvfb 的用法（camoufox.com/python/virtual-display/）。选 Ubuntu Server 24.04 LTS。

## ② 地域：中国香港（维持截图当前选择）

关键前提：X 看到的出口 IP 来自住宅代理，账号风险画像与 VPS 地域无关。所以地域只优化四条链路：

| 链路 | 香港 | 硅谷/新加坡/东京 |
|---|---|---|
| 大陆 SSH 管理 | 30-60ms，最优 | 新加坡 ~80-100ms，硅谷 150-200ms，晚高峰国际链路可能抖 |
| GLM API（open.bigmodel.cn，大陆托管） | RTT 最低（约 40-60ms） | 硅谷 ~180ms+，LLM 流式长调用下可容忍但不优 |
| fxtwitter / Telegram / 飞书出站 | 全部直连无障碍 | 同样无障碍 |
| 价格 | 入门 2C4G ¥90/月 | 同档 ¥42-54/月（硅谷最便宜，超额流量单价也最低 ¥0.5/GB vs 香港 ¥1/GB） |

裁决：香港。你天天 SSH 上去管理、LLM 调用打回大陆，这两项香港全面占优，多花的 ~¥40/月买的是管理手感。省钱敏感时备选硅谷/东京入门 2C4G（¥42-54/月），接受 SSH 延迟即可。

一个官方明示的注意点：香港入门型套餐"不保障大陆与香港之间跨境公网质量"（SLA 层面，不代表不可用）。首月实测：SSH 卡顿率、open.bigmodel.cn、api.fxtwitter.com、Telegram Bot API 的连通性与延迟；SSH 若不稳，备用方案 mosh 或 Tailscale 内网通道兜底。

## ③ 规格：2核4G 是本项目的正确档位，2核2G 不许买

内存账（决定性因素）：常驻 Camoufox 起步 ~0.6GB，长跑泄漏增长到 1.5-2GB + Node 全家桶 ~0.5GB + 系统 ~0.4GB → 4GB 是安全线。2GB 机器会被 OOM killer 杀浏览器，进程中途暴毙在 X 看来正像 bot 行为，与看门狗设计初衷相悖。CPU 方面单浏览器 + 定时循环大部分时间空闲，2 核足够。

系统盘：profile + state/ + SQLite + 日志合计 <10GB，70GB 套餐盘绰绰有余（记得配 logrotate）。带宽 30Mbps 足够，agent 不在乎首屏速度。

流量账——先纠正一个直觉：腾讯云轻量只计出方向流量（官方计费说明）。浏览器经住宅代理拉页面，页面数据是"代理→VPS"的入站，不计费；计费的出站只有请求头、发帖上传媒体、LLM 请求体、备份外传这些小头，实际月出流量大概率在几 GB 到几十 GB。所以 2048GB 流量包是数十倍余量，流量焦虑应该记在住宅代理头上——刷 feed 的页面流量全额记在代理商的账上（多数住宅代理双向计费），选代理套餐时要按"日均 1-5GB 浏览量 × 账号数"核算。

套餐对照（香港刊例）：

| 套餐 | 配置 | 月价 | 备注 |
|---|---|---|---|
| 入门型（推荐下单） | 2C4G / 70GB / 30M / 2048GB | ¥90 | 年付85折不适用于此档（限2核8G及以上）；新用户限时活动价可低至 ~¥199/年，以购买页为准 |
| 锐驰型（备选） | 2C4G / 60GB / 200M峰值 / 无限流量 | ¥95 | 200M 是峰值上限非承诺值、CPU 共享调度，但永无流量账单 |
| 入门型 | 2C2G / 50GB / 30M / 1024GB | ¥54 | 不推荐：OOM 风险 |
| 海外入门型 | 2C4G / 70GB / 30M / 2048GB | ¥54 | 硅谷/新加坡/东京，省钱备选 |

单号完整月成本骨架：VPS ¥90 + 住宅代理（另购，$5-30/月量级视流量）+ GLM token（按用量）≈ 全包 ¥150-350/月量级。首台建议只买 1 个月，验证通过再做镜像、决定量产地域后续费。

## ④ Windows Server：不要

项目已定 Camoufox / rebrowser-patches Chromium 的 Linux 路线，Windows 只会带来：同档套餐加价（香港入门 2C4G Windows ¥115 vs Linux ¥90，许可费约 +¥25/月起）、无 systemd（调度退化成任务计划程序）、SSH 体验差、RDP 断开后 GUI 会话存活要额外折腾、镜像体积大。比特浏览器是 Windows 桌面软件，但它是被本项目替换掉的旧方案——若确有 Windows 桌面软件要临时跑，本地留一台 Windows 机器测试即可，不要让云上舰队背这个包袱。

## ⑤ 部署骨架：自定义镜像为主、版本化脚本为辅

1. 直接在目标套餐（2C4G/70GB）上打磨母机——硬约束：用自定义镜像创建新实例时，套餐系统盘必须不小于镜像的盘大小。若在 4C8G/180GB 大机上做镜像，量产只能买大盘套餐，成本被锁死。想打磨期宽裕可以临时开大机调试，但最终母机回到量产套餐重装一遍再制镜像。
2. 母机内容：Node 20/22（NodeSource）+ Camoufox 浏览器二进制 + 中文字体/locales（指纹一致性）+ Xvfb + systemd 单元（resident/core/bot/看门狗）+ logrotate + 2GB swap 兜底 + unattended-upgrades。
3. 验收后（重启自恢复、profile 持久、SQLite 正常）关机 → 轻量控制台「制作自定义镜像」→ 新购时选该镜像（同地域直接用；换地域用跨地域复制，还可共享至 CVM）。为做黄金镜像升级 CVM 没有必要。
4. 镜像内容边界：只固化环境 + 代码 + 空数据模板；不进 cookies/profile、state/ 账本、代理密码、bot/API 密钥。开新号 = 新实例首启脚本（或 scp）灌四个配置 → enable --now → 首次登录建 profile。
5. 双保险：黄金镜像管"快"，另存一份可重复执行的版本化 bootstrap 脚本（git 仓库 + 一键安装）管"镜像漂移修复"。日常运维：state/ + profile 每日 cron 打包传 COS（SQLite 用 .backup 一致性方式或停写窗口），与黄金镜像分开管理。

引用来源：腾讯云轻量价格总览 cloud.tencent.com/document/product/1207/73452 · 套餐说明 44755 · 自定义镜像 53038 · 跨地域复制 80571 · 共享至 CVM 63264 · 计费 FAQ 44569 · Camoufox Xvfb 文档 camoufox.com/python/virtual-display/ · Hermes Agent/OpenClaw 腾讯云活动页
