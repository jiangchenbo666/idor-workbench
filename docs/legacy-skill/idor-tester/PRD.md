# 三权分立越权测试 - 产品需求文档 (PRD)

## 1. 业务背景

### 1.1 什么是"三权分立"

"三权分立"是企业安全产品中常见的权限管理模型，将系统管理权限拆分为三个相互独立的角色：

| 角色 | 职责 | 核心功能 |
|------|------|---------|
| **系统管理员** | 平台运维 | 系统配置、资源监控、许可证管理、数字员工管理、任务调度 |
| **安全管理员** | 成员与策略 | 用户/成员管理、黑名单/白名单策略、安全规则配置 |
| **安全审计员** | 审计与追溯 | 查看操作日志、登录日志、所有审计记录 |

此外还包含第四个角色：
| **普通用户** | 业务使用 | 创建任务、管理自己的项目、使用数字员工、配置自己的模型 |

### 1.2 权限隔离原则

- 每个角色只能访问**自己职责范围内**的功能
- 系统管理员 **不能** 操作安全策略和成员管理
- 安全管理员 **不能** 操作系统配置和资源监控
- 安全审计员 **不能** 操作系统配置，也不能操作安全策略
- 普通用户 **只能** 操作前台业务功能，**不能** 访问任何管理后台
- 如果一个角色访问了不属于它的接口 → **越权漏洞**

---

## 2. 角色权限矩阵

### 2.1 系统管理员

**权限范围：平台管理中心**

| 接口路径前缀 | 功能说明 | 权限 |
|-------------|---------|------|
| `/api/v1/task/backendList` | 任务管理-后台列表 | ✅ |
| `/api/v1/digitalStaff/backendList` | 数字员工-后台列表 | ✅ |
| `/api/v1/digitalStaff/getMultiAgent` | 数字员工-多智能体查询 | ✅ |
| `/api/v1/digitalStaff/getMultiAgentStaff` | 数字员工-多智能体成员 | ✅ |
| `/api/v1/digitalStaff/setMultiAgent` | 数字员工-设置多智能体 | ✅ |
| `/api/v1/digitalStaff/usageScenarios/admin/*` | 技能-使用场景管理 | ✅ |
| `/api/v1/mcp/authList` | MCP审计列表 | ✅ |
| `/api/v1/system/getSystemSetting` | 系统设置-查询 | ✅ |
| `/api/v1/system/saveSystemSetting` | 系统设置-保存 | ✅ |
| `/api/v1/monitor/engine` | 资源监控-引擎状态 | ✅ |
| `/api/v1/organizations/data-overview` | 组织架构-数据概览 | ✅ |

**禁止访问**：
- ❌ 安全管理中心的所有接口（`/api/v1/users/*`, `/api/v1/tool/*`）
- ❌ 安全审计中心的所有接口（`/api/v1/audit-logs/*`, `/api/v1/login-logs/*`）
- ❌ 前台普通用户的业务接口

---

### 2.2 安全管理员

**权限范围：安全管理中心**

| 接口路径前缀 | 功能说明 | 权限 |
|-------------|---------|------|
| `/api/v1/users/pageList` | 成员管理-列表 | ✅ |
| `/api/v1/users/importTemplate/download` | 成员管理-导入模板下载 | ✅ |
| `/api/v1/users/addBatch` | 成员管理-批量添加 | ✅ |
| `/api/v1/tool/blacklist/page` | 安全策略-黑名单列表 | ✅ |
| `/api/v1/tool/whitelist/page` | 安全策略-白名单列表 | ✅ |
| `/api/v1/system/getExpiryReminderSetting` | 系统设置-到期提醒查询 | ✅ |
| `/api/v1/system/saveExpiryReminderSetting` | 系统设置-到期提醒保存 | ✅ |
| `/api/v1/mcp/list` | MCP管理-列表 | ✅ |
| `/api/v1/licence/getLicenceInfo` | 许可证信息（公共） | ✅ |

**禁止访问**：
- ❌ 平台管理中心（系统管理员专属）
- ❌ 安全审计中心
- ❌ 前台普通用户业务接口

---

### 2.3 安全审计员

**权限范围：安全审计中心**

| 接口路径前缀 | 功能说明 | 权限 |
|-------------|---------|------|
| `/api/v1/audit-logs/page` | 操作日志-列表 | ✅ |
| `/api/v1/login-logs/page` | 登录日志-列表 | ✅ |
| `/api/v1/licence/getLicenceInfo` | 许可证信息（公共） | ✅ |

**禁止访问**：
- ❌ 平台管理中心（系统管理员专属）
- ❌ 安全管理中心（安全管理员专属）
- ❌ 前台普通用户业务接口

---

### 2.4 普通用户

**权限范围：前台业务功能**

| 接口路径前缀 | 功能说明 | 权限 |
|-------------|---------|------|
| `/api/v1/users/userInfo` | 前台-用户信息 | ✅ |
| `/api/v1/projects/list` | 前台-项目列表 | ✅ |
| `/api/v1/digitalStaff/getUserAllStaff` | 前台-我的数字员工 | ✅ |
| `/api/v1/task/history/list` | 前台-任务历史列表 | ✅ |
| `/api/v1/task/case/list` | 前台-案例列表 | ✅ |
| `/api/v1/task/create` | 前台-创建任务 | ✅ |
| `/api/v1/task/sendMessage` | 前台-发送消息 | ✅ |
| `/api/v1/task/events` | 前台-任务事件 | ✅ |
| `/api/v1/model/provider/list` | 前台-模型供应商列表 | ✅ |
| `/api/v1/model/user/getSetting` | 前台-模型设置查询 | ✅ |
| `/api/v1/model/user/saveSetting` | 前台-模型设置保存 | ✅ |
| `/api/v1/mcp/mylist` | 前台-我的MCP列表 | ✅ |
| `/api/v1/mcp/save` | 前台-MCP保存 | ✅ |
| `/api/v1/apiKey/list` | 前台-ApiKey列表 | ✅ |
| `/api/v1/user/expiryReminder` | 到期提醒-查询（公共） | ✅ |
| `/api/v1/mcp/list` | MCP管理-列表（与安全管理员共享） | ✅ |

**禁止访问**：
- ❌ 所有管理后台接口（平台管理中心、安全管理中心、安全审计中心）

---

## 3. 越权测试的核心逻辑

### 3.1 什么是越权（IDOR）

在这个业务模型中，越权指：

> **一个角色成功访问了不属于它权限范围的接口，并获取了数据或执行了操作。**

具体表现为：
- **水平越权**：系统管理员访问了安全管理中心的接口（跨模块访问）
- **垂直越权**：普通用户访问了管理后台接口（低权限访问高权限功能）

### 3.2 断言规则

测试脚本的断言逻辑：

```
if 角色在接口的 allowed_roles 中:
    期望: HTTP 200, 正常返回数据            → 断言通过 = 鉴权正常
else:
    期望: HTTP 403（服务端拦截）或 HTTP 200 + code=-1（应用层拦截）
    如果 HTTP 200 + success=true + code=200  → 断言失败 = 越权漏洞！
```

### 3.3 特殊边界情况

**情况1：公共接口**
- 如 `/api/v1/licence/getLicenceInfo` - 所有角色（甚至未登录）都可访问
- 这类接口的 `allowed_roles` 应包含所有角色

**情况2：跨角色共享接口**
- 如 `/api/v1/mcp/list` - 安全管理员和普通用户都可访问
- `allowed_roles` 设为 `["安全管理员", "普通用户"]`

**情况3：接口业务报错 ≠ 越权**
- HTTP 200 + code=-1 且 message 提示"参数缺失" → 鉴权通过，但是业务参数问题
- 这不代表越权漏洞，应与真正的越权绕过区分

---

## 4. 测试环境信息

| 配置项 | 说明 |
|--------|------|
| 被测系统 | 安恒信息安全平台（三权分立版本） |
| 认证方式 | Bearer Token（通过 `/api/v1/auth/getToken` 获取） |
| Token 位置 | HTTP Header: `Authorization: Bearer <token>` |
| 登录类型 | `XloginType: WEB` |
| 响应格式 | JSON，包含 `code`、`success`、`message`、`data` 字段 |
| 越权拦截机制 | 服务端返回 403，或应用层返回 code=-1 |

---

## 5. 扩展性说明

虽然当前 PRD 基于"三权分立"模型编写,但工具本身是**通用的越权测试框架**。只要遵循以下模式，任何基于角色的权限系统都可以使用：

1. 在 `ACCOUNTS` 中定义不同角色的账号
2. 在 `TARGET_ENDPOINTS` 中定义接口及其 `allowed_roles`
3. 脚本自动用每个角色的 token 访问每个接口，断言越权角色是否被拦截

适配新的业务场景时，只需修改 `config.py`，无需改动测试脚本。

---

## 6. 接口配置详情结构说明

`config.py` 中 `TARGET_ENDPOINTS` 的每个接口包含以下完整字段：

### 6.1 字段一览

```
{
    # ── 基本信息 ──
    "name": "成员管理-列表",                   # 接口名称（报告展示用）
    "description": "分页查询所有成员信息",       # 功能说明
    "path": "/api/v1/users/pageList",           # 请求路径
    "method": "GET",                            # GET/POST/PUT/DELETE
    "module": "安全管理中心",                    # 所属模块
    "allowed_roles": ["安全管理员"],             # 有权角色列表

    # ── 请求详情（发送请求时使用）──
    "params": {"page": "1"},                    # URL 查询参数
    "body": {"page": 1, "size": 10},            # 请求体（POST/PUT）
    "headers": {                                # 请求头
        "Authorization": "Bearer {{token}}",
        "Content-Type": "application/json",
    },

    # ── 响应体示例（从 HAR/内部工具提取，仅供参考）──
    "sample_response": {
        "status_code": 200,
        "headers": {"Content-Type": "application/json"},
        "body": {"code": 200, "success": True, "data": {...}},
    },

    # ── 断言配置（可选，不填用全局默认）──
    "expected_on_blocked": {...},
    "expected_on_allowed": {...},
}
```

### 6.2 各字段的数据来源

| 字段 | Swagger | HAR 抓包 | 内部工具 | AI 推断 |
|------|:-------:|:--------:|:--------:|:-------:|
| `name` | summary | — | — | ✅ 从 URL 推断 |
| `description` | description | — | — | ✅ 从 URL 推断 |
| `path` | ✅ paths key | ✅ request.url | ✅ | — |
| `method` | ✅ verb | ✅ request.method | ✅ | — |
| `params` | ✅ parameters (query) | ✅ queryString | ✅ | — |
| `body` | ✅ requestBody schema | ✅ postData.text | ✅ | — |
| `headers` | — | ✅ request.headers | ✅ | 默认头 |
| `sample_response` | — | ✅ response.content | ✅ | — |
| `module` | tags | — | — | ✅ 路径推断 |
| `allowed_roles` | — | ✅ 角色来源文件 | — | ✅ 权限矩阵 |

### 6.3 三种导入方式的能力对比

- **Swagger**：接口覆盖面最全，但缺少实际请求头和响应体示例
- **HAR 抓包**：有真实的请求头、响应体，但只能抓到用户在浏览器里点过的接口
- **内部工具**：覆盖面全 + 有真实响应体，最佳方案

三种方式可组合使用，AI 会自动去重合并（同一 path+method 以信息最完整的为准）。

### 6.4 用户增删改查时的关注点

用户审核接口列表时，最需要关心和可能修改的字段：

1. **`allowed_roles`** — 最重要，直接影响越权测试的准确性
2. **`body`** — POST 接口的请求体参数是否符合预期（HAR 提取的可能含用户真实数据，需脱敏）
3. **`params`** — URL 参数中是否有动态值需要替换（如 ID、时间戳）
4. **`module`** — 模块归属是否正确
5. **`name`** — 接口名称是否直观易懂
