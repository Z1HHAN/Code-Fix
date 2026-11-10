# 云端隔离执行服务（开发预览）

## Docker Hub 无法访问：离线镜像交付

若曾遇到`No usable temporary directory`：请在更新后的main上新点Run workflow；Re-run旧运行仍使用旧版本。新workflow已给只读冒烟容器提供可写临时目录。

仓库 **Actions → Build cloud offline images → Run workflow → 选择 main → Run workflow**。仅手动触发，无模型调用，不需要配置模型密钥。构建成功后在该次运行底部Artifacts下载`cloud-offline-<commit>`，需登录并有仓库访问权限，产物保留3天。Actions额度不足时查看运行页报错，不自动购买额度。

在电脑解压下载的Artifacts压缩包，将里面6个文件一起上传到服务器同一个独立目录。进入该目录先执行`sha256sum -c SHA256SUMS`，5项均为OK才执行`docker load -i codefix-images.tar.gz`。随附`images.json`、`BUILD.txt`和`IMPORT.txt`记录提交、构建链接及镜像ID/架构；构建使用发布ZIP中的源码，不打包整个仓库或账号配置。

已在`/opt/codefix`解压同版源码者，按`IMPORT.txt`用导入的Python镜像验证现有文件；尚未解压者只向空目录解压随附`codefix-execution.zip`。执行`mkdir -p /opt/codefix-data && chmod 700 /opt/codefix-data`后，从下方第3步创建访问码继续。**不要重跑第2步构建脚本**，否则又会请求Docker Hub。构建成功不等于目标机服务部署或隔离资格通过。

## 服务器可下载基础镜像时

仅部署到本项目专用 Linux 主机。客户端仍在本机调用使用者自己的模型；本服务不接收模型密钥。默认一个执行任务、其余排队，实际隔离资格需在目标机验证。

1. 将发布 ZIP 上传至 `/tmp/codefix-execution.zip`，解压至空目录 `/opt/codefix`。不要覆盖正在运行的部署；更新另行停止并核对版本。
2. 执行 `bash /opt/codefix/scripts/prepare_execution_cloud.sh`。这会校验包内文件并构建执行镜像与控制服务镜像，不运行模型。
3. 为每位使用者单独创建访问码：

```bash
docker run --rm --network none \
  --mount type=bind,src=/opt/codefix,dst=/opt/codefix,readonly \
  --mount type=bind,src=/opt/codefix-data,dst=/opt/codefix-data \
  --entrypoint python codefix-execution:local -B -m app.execution_server \
  create-access --id teacher --tokens-file /opt/codefix-data/access.json
```

访问码只显示一次。请私下交给对应使用者，不写进安装包，不随截图分享。将 `teacher` 替换为其他使用者 ID 即可分别创建；同一命令的 `revoke-access` 可撤销指定访问码。

4. 执行 `bash /opt/codefix/scripts/start_execution_cloud.sh`。服务仅绑定主机 `127.0.0.1:8780`；源代码与任务数据在主机和控制容器中的绝对路径必须一致。控制服务是可信的 Docker 管理程序，只有它挂载 Docker socket，候选容器不会挂载。
5. 执行真实原生坏版本资格检查（没有模型调用）：

```bash
docker exec codefix-execution python -B -m app.execution_server qualify --project qb-get_factors
```

`passed: true` 在此表示“隔离探针通过、原始缺陷按登记测试复现”，并非修复成功；原件保存在 `/opt/codefix-data/qualification-*`。

6. 公网使用前配置 HTTPS 反向代理及独立访问码。不要开放 8780 或 Docker 管理端口。尚未完成公网 HTTPS、真实客户端联调及 Windows 安装包交付。

服务重启不会重跑旧任务；断线任务受队列与执行总时限限制。队列总长32、单访问者8、证据存储达到1 GiB后拒绝新任务，由管理员归档；不自动删除历史。默认并发1，修改须先验证服务器容量。
