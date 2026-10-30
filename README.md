# 验链（Code Fix）

证据驱动的多 Agent 编排与验证平台，以 Python 软件缺陷修复为首个应用场景。

## 这是什么

用户配置角色、工具、知识和约束，平台执行「定位 → 修复 → 测试」，并根据失败证据选择下一步，最终交付补丁与可追溯报告。

## 文档

- [scope.md](scope.md) —— 项目范围说明（必读）
- [day1-kit/](day1-kit/) —— 第一天闰年任务交接包

## 目录结构

```
Code Fix/
├── scope.md            # 项目范围说明
├── .gitignore          # 忽略 Python 运行时产物、密钥等
└── day1-kit/           # 第一天任务包
    ├── code/           # 任务代码（闰年判断）
    ├── configs/        # 任务配置
    ├── prompts/        # 角色提示词
    ├── tests/          # 开发测试
    └── README.md       # 交接说明
```

## 快速开始

```bash
python -m pytest day1-kit/tests/ -v
```

## 协作约定

- 提交规范：约定式提交（`feat:` / `fix:` / `docs:` / `test:` 等）
- 不要提交 `runs/`、虚拟环境、日志、密钥（已在 `.gitignore` 中排除）
- 提交前先拉取最新代码，避免冲突
