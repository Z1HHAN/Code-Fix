# 目标机Docker复跑交接

A已选择已有Docker目标机。代码commit：40cd0a25f6ed3db0f37a86ea33f15f902313011b。本机完整离线报告见同目录C-acceptance-20261109-010631-94db719f/report.md，真实Docker仍未运行。

本地交付包为工作区runs/simplification-review.bundle（尚未上传GitHub），SHA256：24e0435c62bb103f82f81c84080ea888f86cc76610ec6872c0f1f82ce0ded953。目标仓库必须先具备main基线9d5b5bef23004a69b112e72e1f9417cf0b77b32a；本包是增量Git包，不含依赖或虚拟环境。

在目标机先确认工作区干净，再运行（将包路径替换为实际转交路径）：

```powershell
git bundle verify 'D:/received/simplification-review.bundle'
git fetch 'D:/received/simplification-review.bundle' refs/heads/codex/repair-evidence-boundaries:refs/remotes/repair-bundle/code
git switch --detach 40cd0a25f6ed3db0f37a86ea33f15f902313011b
git status --short
$env:CODE_FIX_EXECUTION_APPROVED='1'
$env:CODE_FIX_DOCKER_IMAGE='sha256:<目标机实际已构建镜像完整ID>'
.\.venv\Scripts\python.exe -B -X utf8 scripts/check_repair_docker.py --output-root runs/docker-followup
```

镜像必须已核验且为内容寻址ID，不填历史示例或latest。Python环境在目标机按requirements-lock.txt重建，不拷贝本机venv。脚本只执行容器内合成夹具，无模型调用。

需回传：实际被测commit、干净Git状态、镜像inspect/版本、原样命令/退出码、新生成的完整docker-repair-*目录及SHA256。正常对照必须通过；其余四项必须实际执行、不得通过。任何前置阻断或未运行不算恶意防护成功，不能代填C签收。当前独立验收仍待回传。
