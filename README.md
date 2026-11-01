# 验链 Code Fix

证据驱动的多 Agent 编排与验证平台，以 Python 软件缺陷修复为首个场景。

## 当前交付状态

本轮提交提供 **v0.2 候选配置与交接材料**：角色、失败规则、接口样例、知识输入、测试计划、UI规格与成果模板。
这些材料仍为草案，等待B/C确认、接入程序并实际验收。本仓库本轮没有提交自动修复引擎或可运行UI，不发布正式v0.2功能标签。

A已确认启动会、B主执行机、模型API和B/C分支访问已解决。
C在其他分支已有闰年基线与参考版本记录；见[已有成果与交接](docs/已有成果与交接.md)。它不等于模型自动修复或独立保留测试验收。

## 从这里开始

| 使用者 | 阅读与行动 |
| --- | --- |
| A队长 | [迭代与测试安排](docs/今天迭代与测试安排.md)、[队长台账](day1-kit/队长今日执行与台账.md) |
| B开发 | [接口确认](docs/接口确认清单.md)、[角色配置](configs/agents.json)、[失败规则](configs/decision_rules.json)、[UI交付提示词](docs/UI交付B完整提示词.md) |
| C测试 | [测试计划](configs/test_plan.json)、[已有成果](docs/已有成果与交接.md)、[成果登记](docs/成果登记与阶段结论.md) |
| 三人共同 | [阶段结论模板](docs/day2_decision.md)、[本轮复核记录](docs/v0.2候选材料复核记录.md) |

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
├── evaluation/               空白运行记录模板
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
- 模型输入按白名单构造，不提供完整资料目录、Git历史、参考答案、保留测试或密钥。

## 协作与提交

- 每人使用自己的分支，通过PR审查合并；B负责集成，A确认范围，C复核测试。
- 提交说明采用`feat:`、`fix:`、`docs:`、`test:`、`chore:`等前缀。
- 不提交真实密钥、虚拟环境和原始大型运行目录；关键证据检查后放`evidence/<run_id>/`或登记共享链接。
- `configs/interface_examples.json`是模拟联调数据，`evaluation/run_record.template.json`是空模板，均不计实际成绩。
- UI实现以[完整交付规格](docs/UI交付B完整提示词.md)为准；[早期生图模板](docs/UI草图生图提示词与操作步骤.md)只用于讨论。

## B v0.2 本地检查

当前仓库已加入最小 v0.2 接口检查组件，仍需 A/C 确认隔离环境、工具调用上限和当天费用上限后，才允许接真实模型自动运行。

在仓库根目录执行：

```powershell
python -m pip install pytest
python -m app.cli validate-config
python -m app.cli decision-fixtures
python -m pytest tests/ -q
python -m pytest day1-kit/tests/ -v
```

`validate-config` 只检查草案配置能否解析；配置仍为 `pending_BC_confirmation`，不代表自动执行已经启用。修复员输出必须符合 v0.2 JSON 格式，不能把裸 Python 代码直接写入文件。可用以下命令检查一份输出：

```powershell
python -m app.cli parse-output repairer path/to/repair-response.json
```

组件位置：

- `app/config.py`：TaskSpec 与草案配置检查；
- `app/repair_output.py`：定位员/修复员 JSON 协议解析；
- `app/security.py`：允许路径、禁止路径、路径穿越和软链接检查；
- `app/decision.py`：按 `decision_rules.json` 顺序匹配 Decision；
- `app/cli.py`：本地接口与规则夹具检查。

尚未接通真实模型、隔离执行器、C 的独立验收报告和 UI；这些缺口必须保留在阶段结论中，不能用规则夹具当作真实运行证据。
