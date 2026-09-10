# 三层架构契约

本仓库按照可部署、可测试的 Python 三层结构组织代码：

- **L1 基础层**：`idor_workbench/foundation/`。只放跨领域的基础设施和工具。
- **L2 领域层**：`idor_workbench/domains/<domain>/`。一个业务领域只能依赖 L1 和本领域；不同领域不得互相导入。
- **L3 视图层**：`idor_workbench/views/`。只处理 HTTP/CLI/页面适配，将请求交给 L2；不得承载业务规则。

依赖方向固定为 `L3 -> L2 -> L1`。根目录不再保留旧模块门面；启动入口统一为 `idor_workbench.views.api:app`，禁止新增旁路入口或把业务逻辑写回根目录。

执行 `python scripts/check_architecture.py` 可得到“文件、行号、规则、修复方式”四项明确反馈。
