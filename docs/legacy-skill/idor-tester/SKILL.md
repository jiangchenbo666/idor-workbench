---
name: "idor-tester"
description: "IDOR/越权漏洞自动化测试工具。支持从 Swagger 文档、HAR 抓包文件或公司内部 API 工具导入接口，AI 自动生成越权测试配置并执行多角色权限校验。Invoke when user wants to test privilege escalation, IDOR vulnerabilities, or role-based access control."
---

# IDOR 越权测试工具

基于角色权限模型的越权漏洞自动化检测工具。核心思路：用多个不同权限角色的账号，逐一访问所有接口，断言越权角色是否被正确拦截。

---

## 目录结构

```
IDOR_test_skill/
├── .trae/skills/idor-tester/
│   ├── SKILL.md                # 本文件 - Skill 指令
│   └── PRD.md                  # 三权分立业务说明（帮助AI理解业务上下文）
├── config.py                   # 配置文件（账号、接口详情、断言规则）
├── privilege_test.py           # 主测试脚本
├── idor_workbench/domains/idor/reporting.py  # Word 报告生成
├── requirements.txt            # Python 依赖
├── README.md                   # 用户操作指南
├── fetchapihar/                # 放置 HAR 抓包文件
│   └── <IP>-<角色名>.har
├── swagger/                    # 放置 Swagger JSON/YAML 文件
│   └── swagger.json
└── api_tool/                   # 放置公司内部 API 工具导出的文件（待后续补充格式说明）
```

---

## 用户操作流程

### Step 1: 导入接口（三种方式任选）

用户通过以下任意方式提供接口数据：

| 方式 | 数据来源 | 用户操作 | 能提取的信息 |
|------|---------|---------|------------|
| **A: Swagger** | 接口文档 | 把 `swagger.json` / `swagger.yaml` 放到 `swagger/` 目录 | path、method、params、body schema、description |
| **B: HAR 抓包** | 浏览器开发者工具 | 把 `.har` 文件放到 `fetchapihar/` 目录 | path、method、params、body、**请求头**、**响应状态码**、**响应体** |
| **C: 内部工具** | 公司内部 API 扫描工具 | 把工具导出的文件放到 `api_tool/` 目录（待内置后补充格式） | path、method、params、body、请求头、响应体 |

用户告诉 AI 即可触发，例如：
- "解析 Swagger 文档，生成越权测试配置"
- "从 HAR 文件中解析接口"
- "从内部工具导入接口"

---

### Step 2: AI 解析并展示接口列表，用户增删改查

AI 解析完接口后，先**不直接写入 config.py**，而是展示一个接口清单让用户审核管理：

```
┌──────────────────────────────────────────────────────────────────┐
│  共解析到 35 个接口，按模块分组如下。请审核并进行增删改查：       │
│                                                                  │
│  【平台管理中心】(12 个)                                          │
│   1. 任务管理-列表                POST /api/v1/task/backendList   │
│      └─ 角色: 系统管理员  |  请求体: {page, size}                │
│   2. 数字员工-多智能体查询         GET /api/v1/digitalStaff/...   │
│      └─ 角色: 系统管理员  |  无请求体                             │
│   ...                                                            │
│  【安全管理中心】(8 个)                                           │
│   9. 成员管理-列表                GET /api/v1/users/pageList      │
│      └─ 角色: 安全管理员  |  无请求体                             │
│   ...                                                            │
└──────────────────────────────────────────────────────────────────┘
```

**用户可以执行的操作**：

| 操作 | 用户怎么说 | AI 做什么 |
|------|-----------|----------|
| **查看详情** | "看第 1 个接口的详情" | 展示该接口的完整信息：请求头、请求体、响应体示例、URL 参数 |
| **修改** | "第 3 个接口 allowed_roles 加上普通用户" | 更新该接口的角色权限 |
| **删除** | "删掉第 5、8 个，不需要测" | 从列表中移除 |
| **新增** | "加一个 /api/v1/xxx 接口，角色是安全管理员" | 补充到列表 |
| **调整角色** | "安全审计员不需要测平台管理中心的接口" | 批量去掉该角色 |
| **确认** | "没问题，写入 config.py" | 将最终列表写入 config.py |

### 每个接口的详细展示格式

当用户要求查看某个接口详情时，AI 应展示完整信息：

```
────────────────────────────────────────────────────────────
接口名称: 成员管理-列表
功能说明: 分页查询所有成员信息
请求方法: GET
请求路径: /api/v1/users/pageList
所属模块: 安全管理中心
有权角色: 安全管理员

── 请求头 ──────────────────────────────────────────────────
Authorization: Bearer {{token}}
Content-Type: application/json

── URL 参数 ────────────────────────────────────────────────
page: 1
size: 10

── 请求体 ──────────────────────────────────────────────────
(无，GET 请求)

── 响应体示例 ──────────────────────────────────────────────
HTTP 200
{
  "code": 200,
  "success": true,
  "data": {
    "records": [{ "userId": 1, "userName": "admin", ... }],
    "total": 42
  }
}

── 越权断言 ────────────────────────────────────────────────
有权限角色 → 期望 200, body 含正常数据
越权角色   → 期望被拦截 (403 或 code=-1)
────────────────────────────────────────────────────────────
```

**字段来源说明**：
- `name`、`description` → 优先取 Swagger summary/description，否则从 URL 推断
- `path`、`method` → 所有来源都能提供
- `params`、`body` → HAR / 内部工具直接提取；Swagger 提取 schema
- `headers` → HAR / 内部工具直接提取；Swagger 不提供，填默认头
- `sample_response` → HAR / 内部工具直接提取（最准确）；Swagger 通常只有 schema 无示例
- `module`、`allowed_roles` → AI 根据 PRD.md 的角色权限矩阵推断

---

### Step 3: 写入 config.py

用户确认接口列表无误后，AI 将最终结果写入 `config.py` 的 `TARGET_ENDPOINTS`。

**写入规则**：
1. **保留原有结构**：只修改 `TARGET_ENDPOINTS` 列表，不动 `BASE_URL`、`ACCOUNTS` 等配置
2. **按模块分组**：接口按 module 分组，用注释分隔，保持可读性
3. **每个接口写入完整字段**：name、description、path、method、params、headers、body、sample_response、module、allowed_roles
4. **缺少的字段留空**：如果某个来源没提供（如 Swagger 无响应体），写入空值而非省略字段

---

### Step 4: 运行越权测试

```powershell
python privilege_test.py                  # 测试所有角色 × 所有接口
python privilege_test.py --role 安全管理员  # 只测试指定角色
```

AI 执行脚本并分析输出，区分：
- **PASS**：鉴权正常，该拦截的拦了，该放行的放了
- **FAIL（越权漏洞）**：越权角色成功获取数据 → 真正的安全问题
- **FAIL（配置问题）**：账号密码错误、接口路径变更 → 提示修正
- **ERROR**：网络不通 → 检查 VPN/环境

---

### Step 5: 生成报告

```powershell
python -m idor_workbench.domains.idor.reporting
```

生成 Word 报告 `三权分立越权测试报告.docx`。

---

## AI 行为规范

1. **先读 PRD.md**：理解三权分立业务模型后再操作，确保角色推断准确
2. **展示 → 审核 → 写入**：不要跳过用户审核环节直接写 config.py
3. **展示详情要完整**：用户说"看详情"时，必须展示请求头、请求体、响应体示例
4. **解释推断依据**：每次分配 allowed_roles 时说明原因，方便纠正
5. **敏感信息脱敏**：不将密码、token 明文输出到对话中
6. **增量修改**：修改 config.py 时只增删改目标接口，不覆盖无关配置

---

## 接口角色推断速查表

详见 `PRD.md`，快速参考：

| URL 关键词 | 默认归属角色 |
|-----------|-------------|
| `admin`, `backend`, `system/getSystemSetting`, `system/saveSystemSetting`, `monitor`, `licence`, `mcp/authList` | 系统管理员 |
| `users`, `tool/blacklist`, `tool/whitelist`, `system/getExpiry`, `system/saveExpiry` | 安全管理员 |
| `audit-logs`, `login-logs` | 安全审计员 |
| `projects`, `task/history`, `task/case`, `task/create`, `task/sendMessage`, `task/events`, `model/`, `mcp/my`, `apiKey`, `user/expiryReminder`, `digitalStaff/getUserAllStaff` | 普通用户 |
| `licence/getLicenceInfo` | 所有角色（公共） |
| `mcp/list` | 安全管理员 + 普通用户 |
