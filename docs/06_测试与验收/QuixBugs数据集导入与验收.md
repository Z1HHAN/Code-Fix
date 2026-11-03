# QuixBugs数据集导入与验收

2026年11月2日，队长明确授权查找并导入公开QuixBugs数据集。本轮在`codex/development-v3`继续开发，从先前三道题扩展到固定上游版本的全部40道Python缺陷题目；不修改main，不安装Docker，不调用模型，不在宿主执行公开代码。

## 实际导入结果

- 上游：[QuixBugs固定版本](https://github.com/jkoppel/QuixBugs/tree/4257f44b0ff1181dedaedee6a447e133219fcebf)，commit=`4257f44b0ff1181dedaedee6a447e133219fcebf`，保留其[LICENSE](https://github.com/jkoppel/QuixBugs/blob/4257f44b0ff1181dedaedee6a447e133219fcebf/LICENSE)。上游说明包含40道Python/Java程序；本平台仅导入Python部分。
- 通过GitHub连接器只读获取固定提交的树与116个白名单文件；逐个核对`SHA1("blob "+长度+NUL+原始字节)`与上游Git对象ID，同时登记SHA256。没有下载correct_python_programs、Java参考实现、评测工作区或Git历史。
- 完整原始字节数据包在`datasets/quixbugs/source-bundle.json`，独立上游对象锁在`datasets/quixbugs/upstream-lock.json`。此前curl的DNS失败没有被隐藏；本轮改用可用的GitHub连接器完成独立上游获取，不再仅依赖C压缩包。
- 40题按题目分别导入`task_assets/quixbugs/`，共228个运行资产文件，逐题保留许可。原先三题的资产字节相同，未覆盖改写；当前目录来源元数据升级为本轮已核验的上游数据包，旧版本与历史证据仍保留。
- 连同本地闰年共41个登记任务。算法涵盖查找、排序、图、链表、字符串、数值和动态规划；这仍是Python缺陷修复场景，不增加其他执行场景。
- 静态预登记278个原生测试ID。它们来自原生测试语法树、JSON数据与固定pytest8.3.5的参数ID格式器；**没有导入目标模块、没有执行pytest收集，也没有运行这些公开测试**。已有三题的ID与先前资格登记一致。
- 38题可选择，仍待Docker实际收集、基线与候选执行资格验证。knapsack和levenshtein两题因原生慢测跳过策略暂禁用，API直接启动也会在探针和模型请求前blocked；不删测试、不忽略跳过、不将跳过算作全通过。

Node接口材料按明确白名单加入相关图/链表任务输入，仅含原生Node辅助类；辅助类只读，不把整套数据包、其他题目、测试全文、参考实现或历史记录送给产品Agent。

## 新增实现

- `app/quixbugs_import.py`：固定版本与对象锁校验、默认预览、逐题资产准备、拒绝覆盖不同字节、原子更新任务目录及幂等重入。只处理数据，不执行上游代码，不发模型请求，不启动Docker。
- CLI新增`import-quixbugs`。默认只读预览；显式`--write`才写入。所有现存资产在开始写入前核对，目录最后更新；目录写入失败时不登记半成品，保留完整新资产供安全重试。
- `app/tasks_v3.py`与页面：展示40题、预登记测试数与资格状态；慢测策略受阻的两题显示禁用标记。
- `app/public_baseline.py`和`app/workflow_v3.py`：在任何探针、测试和模型请求前检查任务准入阻碍。其他公开题目继续要求真实Docker探针，禁止宿主回退。
- `app/agents_v3.py`：仅从当前任务批准的接口辅助文件读取上下文，允许修改的文件仍只有当前缺陷源码。
- 16项新导入测试及1项图任务编排测试，均不执行公开Python。覆盖损坏/缺失/重复/参考文件拒绝、固定版本、预览零写入、禁止覆盖、幂等、写入失败重试、静态分析不执行顶层代码、慢测题在模型/隔离前阻断。

## 可复制命令与实际结果

在仓库根目录，使用本机已有开发环境：

```powershell
# 默认只预览；不会写资产，也不会执行公开代码。
& runs/v3-venv/Scripts/python.exe -B -X utf8 -m app.cli import-quixbugs
# 已核验数据包导入；重复执行不会覆盖不同字节的已有资产。
& runs/v3-venv/Scripts/python.exe -B -X utf8 -m app.cli import-quixbugs --write
& runs/v3-venv/Scripts/python.exe -B -X utf8 -m app.cli list-projects
```

首次实际写入新增37个资产目录、更新任务登记；再次实际执行新增0目录、catalog_changed=false。两份原始输出保存在`evidence/quixbugs-import/import-result.json`与`reimport-result.json`。

完整平台回归命令已实际执行：

```powershell
& runs/v3-venv/Scripts/python.exe -B -X utf8 -m pytest -q -p no:cacheprovider --basetemp=runs/qb-import-acceptedtmp --junitxml=runs/qb-import-accepted.xml
```

结果：**167通过、1项宿主软链接权限跳过、1条第三方TestClient弃用警告**。新增导入专项16项通过。JS语法检查通过。167项是平台测试，不是167个修复案例；278是预登记原生ID，不是本轮实际执行数。复跑使用新临时目录和新日志名，保留旧证据。

真实Edge156浏览器已验证：共41个选项，2项慢测禁用；选择新增快速排序、创建、启动后因隔离条件不足blocked；tool_calls=0、model_calls=0、final_result=not_run，刷新保持相同run且没有重复创建。公开任务run=`run-db3b322d653b427a8aa16fdf8eef37bc`。兼容闰年基线run=`run-47db96c53d014f738f8e1cdaf416c666`仍实际运行可信冻结pytest，3过1败。

浏览器截图、运行记录、JUnit、平台回归文本、完整任务ZIP与SHA256索引在`evidence/quixbugs-import/`。默认8000服务已重新启动，刷新页面即可看到题目；旧任务历史保留。

## 验收边界

本阶段“公开数据集导入及安全登记”通过限定范围验收，开发标签`dev-v3-quixbugs-import`。真实模型仍待真实模型验证，全部公开题目仍待Docker环境验证；未产生公开题目的实际基线、修复成绩、独立复验或成功率。两个原生慢测策略需后续明确测试范围与资源条件，不自动扩大预算或宣称可完整通过。

这次没有完成批量模型实验、固定/动态同条件对照、绝对请求时限补强或正式比赛交付；[全文完成情况](../99_历史归档/2026-11/2026-11-02_V3全文完成情况与公开案例接入.md)中的其他缺失继续有效。未经授权的系统安装、付费与不可信代码执行仍不进行。

## Windows导入权限复核

首次提交发现私有临时目录移动后的资产无法被普通Git进程读取。已改为继承工作区权限的暂存目录；原37目录与登记文件保留于runs备份，按原字节重建，未执行ACL命令或修改系统设置。额外跨进程检查还发现Windows偶发拒绝移动刚写入的目录，现对PermissionError最多尝试6次、合计等待1.55秒，持续失败仍中止且不更新登记。新增两项测试验证短暂拒绝恢复与持续拒绝的有界失败、完整重试。

独立目录的40题导入经中断重试后完成；普通用户进程可读取新登记文件与quicksort源码哈希。最终回归167过1跳过，原165项日志和旧索引继续保留，最终日志为`regression-final.junit.xml`、`regression-final.txt`，跨进程输出为`host-read-probe.json`。该检查只读取字节，不执行公开代码。
