# 验链 Code Fix

证据驱动的多 Agent 编排与验证平台，以 Python 软件缺陷修复为首个场景。

## 当前交付状态

本轮发布 **11月1日计划、今日材料及v0.2配置草案对齐**：五天分工、执行与验收手册、台账、原生测试政策、确定性测试角色、接口迁移、报告及demo/PPT骨架。
配置和接口仍待B/C确认与接入。A确认UI仅静态、后端和HTTP接口尚未开始、隔离未确认、整改已通知但未复验。本次不合并B/C执行分支，不发布正式v0.2功能标签。

A已确认启动会、B主执行机、模型API和B/C分支访问已解决。
C在其他分支已有闰年基线与参考版本记录；见[已有成果与交接](docs/已有成果与交接.md)。它不等于本轮模型自动修复或候选的干净副本独立复验。

## 从这里开始

| 使用者 | 阅读与行动 |
| --- | --- |
| A队长 | [五天计划](docs/五天落地与参赛分工-20261101至1105.md)、[今日手册](docs/11月1日执行手册与验收表.md)、[今日台账](docs/daily/2026-11-01.md) |
| B开发 | [接口确认](docs/接口确认清单.md)、[角色配置](configs/agents.json)、[失败规则](configs/decision_rules.json)、[UI交付提示词](docs/UI交付B完整提示词.md) |
| C测试 | [测试计划](configs/test_plan.json)、[已有成果](docs/已有成果与交接.md)、[成果登记](docs/成果登记与阶段结论.md) |
| 三人共同 | [材料与范围对齐](docs/11月1日需补齐材料与对齐记录.md)、[验收](docs/acceptance.md)、[实验协议](docs/experiment-protocol.md) |

项目范围以根目录[scope.md](scope.md)为准；[差距对照](docs/执行方案与当前版本差距.md)区分历史基线、当前材料和待实现能力。

## 文件结构

```text
Code-Fix/
├── scope.md                  项目范围
├── .gitignore                密钥、缓存与运行产物忽略规则
├── day1-kit/                 保留的v0.1任务与提示词
├── configs/                  v0.2草案：角色、决策、测试计划、联调样例
├── prompts/                  v0.2岗位提示词
├── knowledge/demo_project/   闰年项目知识与检索检查问题
├── evaluation/               空白运行记录模板（执行器待C提交）
├── presentation/             PPT/答辩内容骨架，尚无成品
└── docs/                     交接、UI、验收与复核记录
    └── ui/                   原型参考图（非运行证据）
```

`app/`、平台依赖和启动入口由B/C实现后提交。不要只创建空目录冒充交付。

## 验证首日任务基线

在已有Python与pytest的开发环境、仓库根目录运行：

```powershell
python -m pytest day1-kit/tests/ -v
```

原始坏代码预期为 **3通过、1失败**，失败项为1900年；出现导入或依赖错误不算复现成功。
这只是任务基线测试，不会调用模型或自动修复。平台安装与启动命令待B实现并验证后补充。

## 版本与执行边界

- `day1-kit/`保留v0.1输入。根目录`configs/`、`prompts/`、`knowledge/`为v0.2草案。
- v0.1修复员返回裸代码，v0.2返回JSON修改与说明，解析器必须匹配；迁移前由A/B/C确认。
- 开发测试与独立验收分别记录。独立验收未运行或证据属旧版本时，不得宣布最终成功。
- 自动运行模型代码前，C确认容器或可丢弃独立虚拟机及执行限制。副本与超时不能替代隔离。
- 预算未确认时不启动新草案的自动运行；未知费用记unknown，不能记0。
- 模型输入按白名单构造，不提供完整资料目录、Git历史、参考答案、评测端工作区或密钥。QuixBugs固定原生测试，独立复验为干净副本重跑，不称隐藏覆盖。

## 协作与提交

- 每人使用自己的分支，通过PR审查合并；B负责集成，A确认范围，C复核测试。
- 提交说明采用`feat:`、`fix:`、`docs:`、`test:`、`chore:`等前缀。
- 不提交真实密钥、虚拟环境和原始大型运行目录；关键证据检查后放`evidence/<run_id>/`或登记共享链接。
- `configs/interface_examples.json`是模拟联调数据，`evaluation/run_record.template.json`是空模板，均不计实际成绩。
- UI实现以[完整交付规格](docs/UI交付B完整提示词.md)为准；[早期生图模板](docs/UI草图生图提示词与操作步骤.md)只用于讨论。

## 当前版本与演示材料

配置iteration-0.2-20261101-draft，接口0.2-20261101-draft，状态pending_BC_confirmation。测试角色为deterministic且执行绑定未实现；新final_check_failed及旧heldout_failed兼容迁移仍待B/C签收。预算建议仅在手册，null不表示无限。

[报告六章骨架](docs/研究报告六章骨架.md)、[demo脚本](docs/demo-script.md)、[PPT内容](presentation/README.md)、[11月1日材料发布复核](docs/11月1日材料发布复核记录.md)。旧[10月31日迭代安排](docs/今天迭代与测试安排.md)和[旧阶段模板](docs/day2_decision.md)保留历史背景，当前行动按11月1日入口。
