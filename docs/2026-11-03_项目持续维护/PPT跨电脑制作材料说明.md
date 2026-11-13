# Code Fix：跨电脑PPT制作材料

交付分支：`codex/ppt-materials-110`。本说明中的路径均相对于仓库根目录，另一台电脑无需具有原作者的C盘目录。主任务书为同目录的[PPT完整制作提示词.md](PPT完整制作提示词.md)。

## 如何取得文件

只做PPT，优先下载[精简制作材料ZIP](https://github.com/Z1HHAN/Code-Fix/raw/refs/heads/codex/ppt-materials-110/evidence/presentation-handoff/110/codefix-ppt-materials.zip)，解压后将整个目录作为Codex工作区。包内含源码说明、主提示词、指导文字、截图、SVG及精选实验原件，不需要安装应用、填写密钥或启动付费实验。

需要完整Git工作树，在一个新的空目录执行：

```powershell
git clone --branch codex/ppt-materials-110 --single-branch https://github.com/Z1HHAN/Code-Fix.git Code-Fix-PPT
cd Code-Fix-PPT
git rev-parse HEAD
```

也可下载[交付分支的整个仓库ZIP](https://github.com/Z1HHAN/Code-Fix/archive/refs/heads/codex/ppt-materials-110.zip)。只有`.git`意味着尚未取得可用工作树，不能据此判断项目没有源码；新目录克隆可避免覆盖另一台电脑的已有文件。

## 文件导航

| 需要什么 | 仓库路径 | 使用说明 |
| --- | --- | --- |
| 唯一完整任务书 | `docs/2026-11-03_项目持续维护/PPT完整制作提示词.md` | 论证、技术、逐页主图、讲稿、数据、视觉、复核、备份页要求 |
| 指导文档与赛道 | `docs/2026-11-03_项目持续维护/PPT参考资料/` | 两份用户DOCX的原文字提取和赛道截图；指导意见不是正式评分权重 |
| 当前功能/状态 | 同目录`当前开发任务台账.md`、`当前开发计划.md`、`持续交付与变更记录.md` | 重点读108、109、110记录，区分发布与实跑 |
| 四Agent合同与编排 | `app/agent_team.py`、`app/workflow_v3.py`、`app/agents_v3.py`、`app/role_config.py` | 以实际文件为准；模型调用、合同、交接、动态路由与程序强制规则 |
| 测试执行与隔离 | `app/split_runner.py`、`app/docker_runner.py`、`app/execution_server.py`、`docker/`、`c_runner/` | 双容器、受限RPC、测试版本、真实工具报告 |
| 应用与界面 | `desktop/`、`ui/`、`app/api.py` | Electron桌面壳、本地后端、Web工作台；不附账号/密钥 |
| 冻结100完整分母 | `evidence/repair-chain-review/all-100-combined/manifest.json` | 40题：34通过、4停止、2阻断；63请求；估算人民币0.797610元 |
| 101补测 | `evidence/repair-chain-review/live-101/`、`live-101-recovery/` | 两份清单合计6题，4成功、2未通过；精选成功原件另附 |
| 真实主案例 | `evidence/repair-chain-review/live-104/` | wrap自然失败→证据反馈/裁决→第二轮→干净final；按历史实际角色调用作图 |
| 真实辅助案例 | `evidence/repair-chain-review/live-105/` | shortest_path_lengths，区分模型候选与RPC执行兼容问题 |
| 累计40题索引 | `evidence/presentation-handoff/110/累计40题成功索引.json` | 40个唯一项目成功run及批次；是跨迭代覆盖，不是同版本一次性100% |
| 历史固定/动态配对 | `evidence/contest-strategies-config-comparison/comparison/` | 三题各3/3、均首轮、各9请求，未触发失败分支，不证明动态更优 |
| 109新协作合同验证 | `evidence/multi-agent-collaboration/109/` | 开发回归与离线替身交接，不能当真实模型/Docker记录 |
| 界面参考截图 | `evidence/contest-delivery/final-browser/` | roles、conditions、snapshot、comparisons等历史界面，截图年代不能冒充109真实运行 |
| 旧12页图形参考 | `evidence/contest-demo-materials/storyboard-svg/`和`storyboard-preview/` | 01～12的可编辑SVG、PNG预览；只按需参考，旧数据不直接复用 |
| 参考讲稿/页面 | `evidence/contest-demo-materials/technical-storyboard.html`、`speaker-notes.txt` | 历史草稿；与最新提示词冲突时按原件与110核对结果纠正 |
| 本包哈希/核验 | `evidence/presentation-handoff/110/材料清单.json`、`verification.json` | 原件逐字节校验、文件数、ZIP大小与范围 |

## 制作方必须区分的事实

1. 当前109实现：四个独立角色合同和角色调用，模型可以共用；不是声称四台机器或四种模型。有效候选测试后必须回检测，定位须同时收到当前检测输出和程序批准的调度指令，缺失先阻断。Self-Loop受轮次、调用、时间、费用限制；不保证任意代码都能修好。
2. 历史104/105真实案例按原事件展示，不补造109新增检测调用。109代码提交`1d7e5da3ce46ec33eeff7b1d8ead38b84c5d025d`，开发登记`155ce3b`；Python804通过/1权限跳过、Node76、门槛26、控制12分别列出，不相加作修复题数。
3. 40题最终各有成功记录成立；同版本完整批次仍是冻结100的34/40。不存在可支持“单次25/40→编排34/40、净增9题”的已核验消融原件，不作该归因。
4. 主提示词对七个对话的归纳是已整理的文字，未向另一台电脑提供七个完整私聊原文。不得声称重新读取了全部会话。实验、源码和截图比聊天陈述优先。
5. 精选实验记录不是全量运行工作区。缺失原始证据时写未记录，不运行参考答案或把答案放入模型上下文；不新增付费实验。
6. 安装包、node_modules、Python环境、Git历史目录、密钥、访问码、个人配置及私聊导出不在本包。当前分支含109，不能自动当成main发布或C本人签收。

## 可以直接转发给另一台Codex的指令

> 请以当前解压/克隆目录为Code Fix项目根，先读`docs/2026-11-03_项目持续维护/PPT跨电脑制作材料说明.md`，再完整执行同目录`PPT完整制作提示词.md`。按C审校、D论证及制作的职责推进：先核对源码与逐题原件，输出事实底稿，再制作8分钟12页主稿及答辩备份页的可编辑PPTX与讲稿。视觉采用深蓝封面、白底技术内页、主流程蓝/重试橙/通过绿，一页一个主图。旧12图只按需参考。突出四Agent合同、证据驱动路由、检测失败后的有界Self-Loop、模型建议与程序裁决、双容器隔离和干净复验。真实104/105案例与当前109实现分开展示，累计40题覆盖与冻结100批次分开展示，不捏造消融或提升。按主提示词评估GitHub制作/绘图技能，安装外部工具前说明用途。完成后渲染检查文字、图表、连线与证据链接。不要改产品、启动付费模型、制作虚假数据或要求原作者本机路径。
