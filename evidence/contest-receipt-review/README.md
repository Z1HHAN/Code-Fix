# B交付接收核对（069）

接收B提交43efb37a16e00a61be8aed9b81a2f5d0bd3614b0，声明功能冻结ebc7450973081fe95d88a777f49f26633aa5d851。核对脚本只读取原件与可信仓库代码，不执行候选、模型或Docker，不代签C。

`verification.json`记录1828项静态一致性核对无错误：738文件SHA256及清单自身校验、六run及调用/测试/候选/final、配对身份、容器配置、逐调用计价、预算快照算术、CSV/JSON一致性和ZIP CRC。PNG另经人工查看。检查数量不是测试通过数，也不覆盖下面未完成事项。

`first-review.json`保留首次核对：当时直接比较报告和增量事件，六处raw_text字段差异被记为错误。复查现有API设计后确认增量事件省略raw_text、完整报告/调用保留；最终脚本仅排除这个已说明字段后精确比较顺序和其余内容。没有改动B原件，也没有通过删除业务字段掩盖差异。

## 未完成与发现

- A确认六次由API/CLI启动，不能签为要求的UI点击创建/启动实跑；PNG通过真实页面导出只证明该导出路径。
- 两份保存配置省略role_overrides，六run均为空覆盖；实际调用证明默认max_tokens=8192，不证明编辑后的角色指令/高级参数真实生效。
- 六次均首轮通过，没有失败重试、上限停止、待人工移交的真实控制分支；原离线专项仍单列。
- 六run源码总hash相同，但既不等于本地检出字节总hash，也不等于冻结Git blob总hash；见verification.json的source_identity_review。换行可能影响结果，但常见统一LF/CRLF重建亦未命中，原因未确认，需B提供冻结点文件级源码SHA256或源码归档。不能据此认定篡改，也不能直接认定仅换行差异。
- 费用按调用usage和已登记费率独立重算，合计0.148028 CNY，与运行/预算快照一致；包内只有budget-after快照，缺原账本或脱敏逐call账本行，不能宣称已直接核验数据库。金额是估算，不是供应商实账。
- C本人准确版本复核、正式视频和整场彩排原件未在本包中。另两题资格清单仍pending，是否据本次原生采集登记须A/C核定，未回写冻结配置。

复跑接收核对：`python -B -X utf8 evidence/contest-receipt-review/review.py`，输出到runs/b-contest-receipt-review.json。原件目录evidence/contest-strategies-config-comparison保持不变。真实运行成功/配对数据已收到，与整体验收通过分别登记。
