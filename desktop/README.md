# 验链 Windows 个人桌面应用（开发版）

GitHub Actions → **Build Windows desktop app** → Run workflow → main。成功后下载`windows-desktop-<commit>`，保留3天；包含安装版`CodeFix-Setup-*.exe`、免安装版`CodeFix-Portable-*.exe`、SHA256SUMS、准确构建身份和首次启动截图。二选一使用，不需要另装Python或Docker。尚未签名，构建成功不等于老师电脑的真实验收通过。

首次打开填写自己的兼容Chat Completions接口的API Base URL、API Key、模型ID、输出上限与参数名，以及云端执行地址/个人访问码。没有预置团队账号。密钥使用Electron safeStorage（Windows当前用户加密）保存，不回显、不写入报告；安装包不带个人配置。更新保留用户数据，换电脑请重新配置。顶部菜单“应用 → 模型与云端连接设置”可修改；正在运行的任务结束前拒绝重启后端。

模型地址使用HTTPS，填写服务商文档中的兼容接口根路径，例如`https://你的服务商/v1`；应用追加`/chat/completions`。自填模型的temperature/top_p尚未核实能力，保持禁用；角色任务指令和输出长度在工作台校验保存后可用。保存、刷新和历史回放不调用模型；只有启动修复任务才可能收费。

金额预算可选，启用时填写美元单价和每日美元额度（不是平台余额）。关闭时费用显示未知，仍保留任务轮次、调用次数、输出长度和总时限；用量未知或请求失败仍阻止自动继续，不能靠切换配置绕过。任务数据及账本保存在当前用户的应用数据目录，不写入安装目录。

## 没有域名时：本机SSH隧道联调

在使用应用的Windows电脑上打开PowerShell，执行并保持窗口连接：

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:17880:127.0.0.1:8780 root@8.148.85.237
```

使用服务器的SSH认证登录，不在应用中保存SSH密码。应用勾选SSH隧道，执行地址填`http://127.0.0.1:17880`，访问码填管理员发放的新码；它不是模型API Key。服务端8780保持仅监听127.0.0.1，不需要向公网开放。SSH账号暂用于当前A机联调；向老师分发前另配HTTPS或受限SSH账号，不分发root凭据。

## 本地开发

Python3.14下运行`python scripts/prepare_desktop.py --python-source <完整Windows Python安装目录>`，使用复制后的`desktop/build/python/python.exe -I -m pip install -r requirements-lock.txt`安装依赖。`cd desktop`后`npm ci`、`npm start`；`npm run dist`生成两个exe。构建目录、node_modules、个人凭据与任务日志不入Git。Windows Actions额外检查实际捆绑Python的后端启动和配置保存、已打包Electron首次启动；这些检查均不调用模型或远程Docker。
