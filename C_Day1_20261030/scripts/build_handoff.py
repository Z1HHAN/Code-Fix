"""Build readable local evidence pages from an existing, real C test run."""

from pathlib import Path
import argparse
import html
import json


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    args = parser.parse_args()
    run_dir = ROOT / "runs" / args.run_id
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest["result"] != "passed":
        raise SystemExit("The run has not passed all C acceptance checks.")
    if not manifest["python_version"].startswith("3.14."):
        raise SystemExit("Selected evidence must use Python 3.14.")
    provenance = manifest.get("runtime_provenance", {})
    source = provenance.get("source") or "未知（原始运行未记录来源元数据）"
    signature = provenance.get("signature_verification") or "未知（无签名验证证据）"
    handoff = ROOT / "handoff"
    handoff.mkdir(exist_ok=True)
    cases = (
        ("bad", "01_错误版测试", "错误版复现成功", "1900 年不是闰年。原始代码仅判断能否被 4 整除，测试准确捕获了这个缺陷。", "#b45309"),
        ("reference", "02_参考答案测试", "参考答案验证通过", "在独立副本中使用手册给定的参考答案，同样的四条测试全部通过。", "#047857"),
    )
    for kind, filename, title, explanation, accent in cases:
        result = manifest["runs"][kind]
        record = result["tool_run"]
        stdout = (run_dir / f"{kind}.stdout.txt").read_text(encoding="utf-8")
        stderr = (run_dir / f"{kind}.stderr.txt").read_text(encoding="utf-8")
        document = f'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · {args.run_id}</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#f2f5f7;color:#172536;font:17px/1.6 "Microsoft YaHei",sans-serif}}
main{{max-width:1420px;margin:0 auto;padding:38px 42px 32px}} .eyebrow{{font-size:15px;color:#53697d;letter-spacing:2px}}
h1{{margin:10px 0 10px;font-size:36px;line-height:1.3}} .intro{{margin:0;color:#405166}}
.meta{{margin:18px 0 24px;padding:14px 18px;background:white;border:1px solid #d6dee6;border-radius:8px;font-size:14px;color:#405166}}
.summary{{display:flex;align-items:baseline;gap:26px;margin:0 0 20px}} .result{{font-size:30px;font-weight:700;color:{accent}}} .detail{{font-size:15px;color:#526477}}
h2{{font-size:18px;margin:14px 0 10px}} pre{{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;padding:24px;background:#172331;color:#f1f5f9;border-radius:10px;font:15px/1.65 Consolas,"Microsoft YaHei",monospace}}
footer{{margin-top:20px;font-size:13px;color:#546577}} a{{color:#245e88}} .evidence{{margin:12px 0 0;font-size:14px}}
</style><main>
<div class="eyebrow">验链 · C 环境与复现 · 真实本地测试</div>
<h1>{title}</h1><p class="intro">{explanation}</p>
<div class="meta">运行编号：{html.escape(args.run_id)}<br>完成时间：{html.escape(result['finished_at'])}（北京时间）<br>环境：Python {html.escape(manifest['python_version'].split()[0])} · pytest {html.escape(manifest['pytest_version'])} · Windows</div>
<div class="summary"><span class="result">{record['counts']['passed']} passed · {record['counts']['failed']} failed</span><span class="detail">pytest 退出码 {record['exit_code']} · 进程耗时 {record['duration_ms']} ms</span></div>
<h2>pytest 原始标准输出</h2><pre>{html.escape(stdout)}</pre>
{('<h2>原始标准错误</h2><pre>' + html.escape(stderr) + '</pre>') if stderr else ''}
<p class="evidence"><a href="../runs/{args.run_id}/{kind}.stdout.txt">原始文本</a> · <a href="../runs/{args.run_id}/{kind}.toolrun.json">ToolRun JSON</a> · <a href="../runs/{args.run_id}/{kind}.junit.xml">JUnit XML</a></p>
<footer>本页直接读取真实 pytest 日志；PNG 是本地日志展示页截图，不是原始终端截图。<br>本次仅验证 C 的环境与参考答案，未调用模型 API，不计为 AI 自动修复。原始代码与两份测试的哈希校验均通过。</footer>
</main></html>'''
        (handoff / f"{filename}.html").write_text(document, encoding="utf-8")

    readme = f'''# C 的 Day 1 任务已完成

已完成环境准备、错误复现和参考答案验证。运行编号：`{args.run_id}`。

| 验证对象 | 通过 | 失败 | pytest 退出码 | 结论 |
| --- | ---: | ---: | ---: | --- |
| 原始错误版本 | 3 | 1 | 1 | 仅 1900 年用例失败，复现成功 |
| 手册参考答案 | 4 | 0 | 0 | 相同测试全部通过，环境正常 |

这是 C 的环境与参考答案检查。参考代码直接来自手册，未调用 DeepSeek，不计作模型自动修复或 B 的完整闭环。

## 你现在怎么交付

把整个交付 ZIP 发给 B。两张图片在 `handoff/`；可复制的说明在 `handoff/给B的消息.txt`。本任务没有替你向队友发送消息。

- [错误版本测试截图](handoff/01_错误版测试.png)
- [参考答案测试截图](handoff/02_参考答案测试.png)
- [错误版本真实 ToolRun](runs/{args.run_id}/bad.toolrun.json)
- [参考答案真实 ToolRun](runs/{args.run_id}/reference.toolrun.json)
- [模拟接口样例](examples/SIMULATED_toolrun_failed.json)（明确标注模拟，供 B 编写读取逻辑）
- [完整运行清单](runs/{args.run_id}/manifest.json)（含命令、时间、哈希和校验结果）

两张 PNG 是原始 pytest 日志在本地 HTML 页面中的截图；原始 stdout、stderr、合并日志、JUnit 与代码副本全部保存在同一个 runs 目录，可相互核对。

## 这台电脑再跑一次

双击 `run_C.cmd`。它会生成新的 `runs/C-时间戳-随机值/`，运行错误版和参考答案两次测试，并自动记录 ToolRun。

或者在项目目录的 PowerShell 中运行：

```powershell
.\\.venv\\Scripts\\python.exe scripts/run_c_checks.py
```

正确现象：先看到 `bad: 3 passed, 1 failed`，再看到 `reference: 4 passed, 0 failed`，最后 `C checks: passed`。第一份测试出现失败正是本次要求；脚本验收通过时整体退出码为 0。

每次运行的源文件都保留，测试仅在副本中执行。脚本设有 60 秒测试超时。截图对应本次固定 run_id，重新运行不会覆盖旧证据或自动更新截图。

## B 在另一台 Windows 电脑上复现

1. 解压 ZIP，保持目录结构。
2. 电脑需已有 Python 3.14；双击 `setup_C.cmd`，创建本机 `.venv` 并用 `wheelhouse/` 离线安装锁定版本。
3. 双击 `run_C.cmd`，核对两组结果。

ZIP 不包含本机的 `.venv` 和 Python 运行时，虚拟环境需要在 B 的电脑上重新建立。B 已有主项目时可复制 `code/`、`tests/`、`reference/` 与 `scripts/run_c_checks.py`，用自己的 Python 环境运行。

## 文件与接口约定

- `code/leap_year.py`：手册中的原始缺陷代码，故意保留缺陷，供 B 后续模型修复。
- `code/__init__.py`：新增的空包标记，使 `from code.leap_year import is_leap` 能正确定位项目模块。
- `tests/test_leap.py`：手册中的四条断言和测试函数保持一致。
- `reference/leap_year.py`：手册给定参考答案，只用于验证。
- `runs/<run_id>/work_bad/`、`work_reference/`：两次真实运行的独立副本。
- `*.toolrun.json`：严格使用 `call_id`、`tool`、`status`、`exit_code`、`counts`、`log_path`、`duration_ms` 七个字段。
- `log_path`：相对于交付包根目录；把目录整体解压后可以定位。
- `events.jsonl`：仅记录 C 检查事件，不冒充 B 的模型调用流水账。
- `examples/`：仅模拟接口样例；不要计入运行成绩。

当前环境是 Python {manifest['python_version'].split()[0]}、pytest {manifest['pytest_version']}，全部依赖见 `requirements-lock.txt`。为了避免同名标准库导入问题，使用 `python -m pytest`，并禁用外部 pytest 插件自动加载。

Python 运行时来源：{source}。签名验证：{signature}。版本以 manifest.json 为准。

本执行器仅用于固定示例的开发检查。普通子进程和 60 秒超时不提供模型代码安全隔离，也不保证清理完整进程树；不得用于自动执行不可信模型代码。

历史 manifest 中的 python_executable 和 command 保留原电脑路径用于审计，不是接收方启动路径；请使用 setup_C.cmd 与 run_C.cmd 建立本机环境。

交付校验：运行 `python scripts/verify_delivery.py`。重建材料后重新生成截图，再运行 `python scripts/verify_delivery.py --freeze` 生成索引与校验清单，最后再次校验。

任务依据：Day0 第 7 章 C 分工；Day1 第 4 章示例、第 5.2 节 C 任务卡、第 6.2 节 ToolRun、第 7.2 节副本与超时规则。今日交付未包含 Docker、模型 API 接入或整个团队的自动修复闭环。
'''
    (ROOT / "README.md").write_text(readme, encoding="utf-8")
    message = f'''C 的环境和复现已完成。
Python {manifest['python_version'].split()[0]} + pytest {manifest['pytest_version']}。
错误版：3 passed / 1 failed，失败项为 1900 年，退出码 1。
参考答案：4 passed / 0 failed，退出码 0。
run_id：{args.run_id}
交付包里有两张日志展示页截图、真实 ToolRun、原始日志、JUnit、代码及离线依赖；另附明确标注模拟的接口样例。
原始坏代码和四条测试已保留。此次是 C 的环境与参考答案验证，未调用模型，不计为 AI 自动修复。
B 可以开始接 DS API 和串联主流程。解压后见 README.md；已有 Python 3.14 时运行 setup_C.cmd，再运行 run_C.cmd 可复现。
'''
    (handoff / "给B的消息.txt").write_text(message, encoding="utf-8")
    print(f"Handoff pages created for {args.run_id}")


if __name__ == "__main__":
    main()
