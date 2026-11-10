# QuixBugs全量覆盖与对照入口开发核对

本目录分开保存历史覆盖静态清单和新功能开发验证。助手本机执行，不是B真实模型/Docker重跑或C本人签收；没有付费模型请求。

- coverage.json / coverage.csv：逐题读取历史quixbugs-batch1.md与当前批准RPC名单，40题、历史摘要29通过/11阻塞。历史含重跑及配置修正，600秒上限；不是新版本首尝试成功率，也不是40题固定/动态配对。历史逐run原件在当前包中不齐。
- full-coverage-tests.xml：对照与赛事组件专项39过，含40题HTTP保存/读取、80行CSV、128KiB请求及分块限制、任务8KiB边界、未知费用、旧快照不变和不调度任务。
- api-regression.xml：既有API回归7过。
- browser/：隐藏Edge验证新页面默认40题，保存、导出、刷新及原配置编辑/只读链路。只创建一条未启动任务，POST清单无/start；PNG是全部not_run的计划，不能当成实验成功率。
- development-verification.json：基线、工作树源码hash、命令与验证范围。前端60过来自命令实际输出，未宣称完整C入口或真实候选验收。

产品改动：正式对照允许批准的40道QuixBugs-Python题，保留1～40项显式子集清单；页面默认全40题；对照POST上限128KiB、任务POST仍8KiB；只整理证据，不执行任务。11题RPC准入及新优化尚未实施，不能因清单允许登记就认为执行资格通过。

原始B六run包和三题对照均未改写。新功能源码身份独立记录，不沿用旧B结果给新版本签收。正式PPTX/真实参数覆盖/UI启动/C验收/视频和彩排继续单列。

复核命令见development-verification.json；无需模型凭据。浏览器脚本需本机Edge，结果目录中的profile为本地缓存，已排除归档。
