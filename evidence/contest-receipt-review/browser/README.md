# B真实历史的A侧只读浏览器核对（070）

在本机临时数据库复制B的6条终态run、210条原事件、两份保存配置及已冻结对照；生产UI资源不改，原始B目录不写。通过现有API提供只读查看，审查包装器拒绝全部非GET/HEAD，并将TaskService执行入口置为断言失败。费用页读取明确的B历史预算快照，未创建新费用账本或读取密钥。仅审查页面添加“B机真实历史回放 · API/CLI原件 · 只读核对，不启动任务”提示，不作为产品新增功能交付。

最终result.json为passed=true，server-verification.json记录浏览器退出0、mutations=0、6条状态均success、事件数保持210。逐条打开六run的final日志，完整报告与B原件事件相同、ZIP可读取；保存配置只读可见，暂停回放、返回当前步骤及刷新通过。原对照JSON与页面一致、CSV导出与原件一致、PNG可导出，宽屏和720px截图保留。JS异常0、API/资源错误0，非GET/HEAD请求0。

first-attempt保留首次退出1的输出及截图：最后的“所有HTTP错误均失败”断言包含了/favicon.ico 404，六run与回放/对照此前已检查完。最终脚本仅将favicon404单列warnings，API/资源错误仍失败；没有修改产品代码去规避问题。favicon警告原样留在result.json，未声称所有网络请求均200。

这不是B在UI创建/启动任务，也不是新模型或Docker执行，不是C本人签收、正式视频或整场彩排。069的非空角色覆盖、源码身份补核、账本行及控制出口缺口不因本核对解除。截图是接收原件的浏览器检查，不能冒充本轮正式录屏。

原样复跑：将同目录check_b_replay.py和browser_b_replay.cjs复制到仓库runs/，用项目Python执行runs/check_b_replay.py；要求本机Node、Edge及空闲端口8784/CDP9333。会创建独立临时只读回放库及runs/b-real-replay-browser输出，不启动任务。脚本保留原测试时字节，Node脚本按runs位置定位仓库，不能直接从归档目录运行。

review-source.json记录实际核对代码身份及UI文件事后hash；功能源码与ebc7450差异为空。SHA256SUMS.txt覆盖本目录除清单自身外的全部归档字节；不包含浏览器profile、密钥或缓存。
