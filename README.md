# 验链 Code Fix

## V3主线：可运行的CLI、HTTP与真实基线页面

V3在`codex/development-v3`按队长提供的[745行要求](docs/开发要求V3.0.md)开发，经队长本轮授权审核、干净检出复核后合入main，保留原提交和团队分支。[主线合并审核](docs/V3主线合并审核与复核-20261102.md)、[启动与模型配置](docs/V3运行与接口.md)、[阶段验收及版本记录](docs/V3开发与验收.md)。

已真实验证：持久化任务创建/查询、防重复启动、原始闰年pytest、FastAPI、页面启动任务/显示真实失败日志/刷新恢复、报告与完整证据ZIP。真实模型传输、三角色协议、动态编排及候选隔离路径已编写，只有离线测试；本机没有模型密钥和Docker，真实模型候选修复及独立复验**尚未验收**。禁止以本轮基线演示宣称模型修复成功。

已从固定上游版本导入QuixBugs全部40道Python题目，连同闰年共41题；其中2道原生慢测题暂禁用，其余仍待Docker实际验证。公开代码仅导入和核验，必须隔离执行；当前环境会blocked。最新167项平台回归通过，不是167个修复案例；[数据集导入验收](docs/QuixBugs数据集导入与验收.md)、[V3全文完成与缺失对照](docs/V3全文完成情况与公开案例接入.md)说明实际范围。

付费启用前仍需补强跨日未结费用阻断和绝对请求时限；本轮只登记，不批准付费/隔离，不发布完整平台稳定版。批量实验和固定/动态正式对照尚未完成。

Python3.14环境，在仓库根目录：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m app.cli doctor
.\.venv\Scripts\python.exe -m app.cli serve --port 8000
```

浏览器打开[本机任务页面](http://127.0.0.1:8000/)，选择“真实基线检查”→创建→启动。预期3通过1失败、独立验收未运行。密钥只配置到本机环境，不填进网页。

以下为保留的v0.2接入与历史边界；新V3能力按上方验收记录区分，旧模拟入口及静态原型不改称真实模型平台。

证据驱动的多 Agent 编排与验证平台，以 Python 软件缺陷修复为首个场景。

## v0.2历史交付状态（保留原阶段结论）

11月2日经A授权，已接入 **C结构化执行器及B模型适配层，完成模拟联调**。固定坏代码提供真实证据，模拟定位/修复产出JSON候选，候选不应用、不执行。启动和检查见[本次交接](docs/integration/BC组件接入与模拟联调-20261102.md)；状态见[功能状态与优化待办](docs/功能状态与优化待办.md)。
接口仍待B/C本人签收。UI是静态原型；真实模型请求、隔离、候选执行、独立验收、Self-Loop及HTTP尚未启用。当前不发布正式v0.2功能标签。后续优化先说明范围与验收条件并取得A确认。

A已确认启动会、B主执行机、模型API和B/C分支访问已解决。
C的历史闰年基线与手册参考版本已放入C_Day1_20261030；见[已有成果与交接](docs/已有成果与交接.md)。它不等于本轮模型自动修复或候选的干净副本独立复验。

保留Code-Fix-C-20261101.zip（abaf2fd）；[内容复核已完成](docs/integration/C压缩包复核-20261102.md)。选择性接入执行器、冻结夹具和控制检查，原包及历史证据未改写，参考资格检查不计模型成绩。

## 从这里开始

| 使用者 | 阅读与行动 |
| --- | --- |
| A队长 | [11月2日台账](docs/daily/2026-11-02.md)、[五天计划](docs/五天落地与参赛分工-20261101至1105.md)、[11月1日台账](docs/daily/2026-11-01.md) |
| B开发 | [接口确认](docs/接口确认清单.md)、[角色配置](configs/agents.json)、[失败规则](configs/decision_rules.json)、[UI交付提示词](docs/UI交付B完整提示词.md) |
| C测试 | [测试计划](configs/test_plan.json)、[已有成果](docs/已有成果与交接.md)、[成果登记](docs/成果登记与阶段结论.md) |
| 三人共同 | [材料与范围对齐](docs/11月1日需补齐材料与对齐记录.md)、[验收](docs/acceptance.md)、[实验协议](docs/experiment-protocol.md) |

项目范围以根目录[scope.md](scope.md)为准；[差距对照](docs/执行方案与当前版本差距.md)区分历史基线、当前材料和待实现能力。

## 文件结构

```text
Code-Fix/
├── scope.md                  项目范围
├── .gitignore                密钥、缓存与运行产物忽略规则
├── app/                      配置、规则、权限、知识与执行工具组件
├── c_runner/                 固定夹具执行器，拒绝模型候选执行
├── c_fixtures/               按字节冻结的工具/参考/故障注入样例
├── c_tests/                  JUnit、结构化证据和执行边界回归
├── tests/                    组件回归和原型交互检查
├── ui/prototype/             明确标模拟的静态原型
├── ui/app/                   V3真实本地任务页面
├── docker/                   候选执行路径，待真实Docker资格验证
├── task_assets/quixbugs/     40题白名单运行资产，仅隔离后可执行
├── datasets/quixbugs/        固定上游数据包和对象锁
├── evidence/                分阶段原始运行证据与哈希索引
├── C_Day1_20261030/           C历史证据及交付校验器
├── day1-kit/                 保留的v0.1任务与提示词
├── configs/                  v0.2草案：角色、决策、测试计划、联调样例
├── prompts/                  v0.2岗位提示词
├── knowledge/demo_project/   闰年项目知识与检索检查问题
├── evaluation/               空白运行记录模板（完整ToolResult待联调）
├── presentation/             PPT/答辩内容骨架，尚无成品
└── docs/                     交接、UI、验收与复核记录
    └── ui/                   原型参考图（非运行证据）
```

使用Python3.14和pytest8.3.5（执行器会检查Python版本），在仓库根目录：

```powershell
python -m pip install -r requirements-dev.txt
python -X utf8 -m pytest -q
node --test tests/ui_prototype.test.cjs
python -m app.cli validate-config
python -m app.cli decision-fixtures
python -m app.cli run-baseline
python -X utf8 -m app.cli model-dry-run
python -X utf8 scripts/check_c_controls.py --output-root runs/controls
```

run-baseline仅检查原始基线，3过1败是预期结果。model-dry-run仅验证真实基线证据与模拟角色接口，源码不变，final_result=not_run。旧UI启动见[原型说明](ui/prototype/README.md)。V3真实任务页面使用上方新入口；完整真实模型修复闭环仍待验收。

## 验证首日任务基线

在已有Python与pytest的开发环境、仓库根目录运行：

```powershell
python -m pytest day1-kit/tests/ -v
```

原始坏代码预期为 **3通过、1失败**，失败项为1900年；出现导入或依赖错误不算复现成功。
这只是任务基线测试，不会调用模型或自动修复。检查组件与真实平台的完成边界见[集成说明](docs/集成版本说明与验收.md)。

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

## v0.2历史版本与演示材料

配置iteration-0.2-20261101-draft，接口0.2-20261101-draft，状态pending_BC_confirmation。测试角色为deterministic；固定夹具组件已接入，自动工作流配置执行绑定仍待B/C确认。新final_check_failed及旧heldout_failed兼容迁移仍待B/C签收。预算建议仅在手册，null不表示无限。

[报告六章骨架](docs/研究报告六章骨架.md)、[demo脚本](docs/demo-script.md)、[PPT内容](presentation/README.md)、[11月1日材料发布复核](docs/11月1日材料发布复核记录.md)。旧[10月31日安排](docs/今天迭代与测试安排.md)和[旧阶段模板](docs/day2_decision.md)保留历史，当前行动见[11月2日交接](docs/integration/BC组件接入与模拟联调-20261102.md)。
