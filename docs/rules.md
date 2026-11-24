# CCU监狱（CCUJailer）开发规范与架构红线 (rules.md)

本文件定义了 **CCU监狱（CCUJailer，代码简称 `cj`）** 项目的代码规范、架构边界与质量门禁。所有新增与修改的代码必须严格遵守本规范，不得引入超出轻量单体目标的重型中间件（如 Redis、Celery 等）。

---

## 1. 运行环境与架构红线

* **Python 版本**：统一使用 **Python 3.14+**（`backend/.python-version` 锁定为 `3.14`），不为了兼容旧版 Python 而限制依赖包版本。
* **后端主包**：统一位于 `backend/cj/`，采用**轻量模块化单体**架构，严格执行四层单向依赖，禁止循环导入：

```text
cj/api/ (接口层) -> cj/services/ (业务编排层) -> cj/repositories/ (数据访问层) -> cj/db/ (模型与引擎)
                                              -> cj/core/ (基础设施：配置、事件总线、代理网络、调度器)
```

* **`cj/api/`（接口层）**：仅负责路由分发、Pydantic 参数校验与响应序列化。**严禁直接导入 ORM 模型、操作 SQLAlchemy Session 或编写业务逻辑**。
* **`cj/services/`（业务层）**：负责核心业务编排（如最近可升级自船只计算、Webhook 过滤匹配、中文化同步）以及显式事务边界控制。
* **`cj/repositories/`（数据层）**：封装全部 SQLAlchemy 查询与写入操作，屏蔽 SQLite 与 PostgreSQL 方言差异。
* **`cj/scraper/`（抓取层）**：仅负责抓取 RSI 商店/CCU 数据并交由 Service 落库，**严禁直接调用 Webhook 或其他通知代码**。
* **`cj/core/`（基础层）**：承载静态配置、进程内事件总线、统一代理网络客户端与定时调度器。

### 生命周期与事件解耦
1. **进程内事件总线（`cj/core/events.py`）**：爬虫落库后仅发布领域事件（如 `ccu.listed`、`ccu.price_changed`、`store.listed`、`store.price_changed`、`i18n.updated`），Webhook 引擎作为订阅者异步消费并按 `ccu.warbond_upgrade` / `store.warbond_ship` / `store.special_sale` / `ccu.new_listing` / `ccu.price_changed` 事件类型路由过滤。
2. **提交后再发事件**：所有外部副作用（Webhook 推送等）**必须在数据库事务成功提交（commit）之后触发**，严禁在未提交的事务中发射事件。
3. **优雅启停（FastAPI `lifespan`）**：
   * 启动顺序：`校验静态配置 -> 初始化 DB 连接池 -> 初始化全局代理 httpx 客户端 -> 注册事件订阅 -> 启动定时调度器`。
   * 停机顺序：`停止接收新调度任务 -> 等待进行中的抓取/推送完成（设超时上限）-> 关闭 httpx 客户端 -> 释放 DB 连接池`。

---

## 2. 数据库与平滑迁移规范

* **双引擎兼容**：统一使用 SQLAlchemy 2.0 Async ORM，同时兼容 SQLite（`aiosqlite`，默认 `./data/ccujailer.db`，自动开启 WAL 与外键约束）和 PostgreSQL（`asyncpg`）。禁止使用特定数据库的私有语法；JSON 字段统一用 `JSON` 类型，时间字段统一用带时区的 UTC `DateTime(timezone=True)`。
* **显式事务所有权**：
  * `cj/repositories/` 中的方法必须接收调用方传入的 `AsyncSession`，只执行查询、`add`、`delete` 或 `flush`，**严禁在 Repository 内部创建 Session 或自行 `commit`/`rollback`**。
  * 事务生命周期由 `cj/services/` 层通过 `async with session.begin():` 显式管理。
* **Alembic 迁移强制要求**：
  * 任何 SQLAlchemy 模型变更（增删改表/列/索引）**必须同步生成对应的 Alembic 迁移脚本**（位于 `backend/alembic/versions/`），并在提交前复核。
  * `alembic/env.py` 必须开启 `render_as_batch=True` 以保证 SQLite 的 `ALTER TABLE` 兼容。
* **防降级保护与自动备份**：
  * 启动执行 `alembic upgrade head` 前，先比对数据库当前的 `alembic_version` 是否存在于当前代码的版本树中；若数据库版本高于代码版本（例如镜像回退），直接报错中止启动，**严禁自动执行 downgrade**。
  * SQLite 模式下，检测到待执行迁移时自动备份数据库文件至 `./data/backups/`（默认保留最近 5 份）。
* **历史数据自动清理**：`webhook_logs` 和 `changelog` 表需支持按天自动清理（默认保留 30 天，设为 `0` 禁用），防止 SQLite 文件无限膨胀。
* **防 SQL 注入**：严禁字符串拼接 SQL；如确需原生 SQL，必须使用 SQLAlchemy `text()` 配合参数绑定。

---

## 3. 网络、配置与安全红线

* **统一出口代理**：所有出站 HTTP 请求（含 RSI、Wiki、UEX、GitHub、OIDC Discovery/Token/JWKS、AI 翻译、Webhook）必须通过 `cj/core/net.py` 提供的共享网络管理器发起，严禁在业务代码中散落裸 `requests` 或 `httpx` 调用。Webhook 推送需根据 `WEBHOOK_USE_PROXY` 环境变量决定是否走代理。
* **静/动态配置分离**：
  * **静态配置**（`cj/core/config.py`，基于 `pydantic-settings`）：`DATABASE_URL`、`HTTP_PROXY`、`PORT`、`AUTH_REQUIRE_LOGIN`（全站强制登录开关，Web UI 不可修改）、`AUTH_SECRET_KEY`、`AUTH_REGISTRATION_MODE`、`INIT_ADMIN_*`、`UPGRADE_PLANNER_ENABLED` 等基础设施与访问边界参数（项目 `.env` 优先于宿主机环境变量）。
  * **动态配置**（数据库 `system_settings` 表）：抓取频率、目录同步间隔、汉化词典源 URL（前端共置于「舰船词典」）、默认显示语言、日志保留天数、升级路线规划开关（默认关闭）、本地账号注册模式（`SystemConfigKey.REGISTRATION_MODE`：关闭/邀请制/开放）、OIDC SSO 参数与自动翻译参数等，支持前端热更新。
* **配置键枚举化**：所有动态配置键必须统一定义在 `SystemConfigKey` 枚举中，**严禁在代码中使用魔法字符串**（如 `get("scrape_interval")`）。
* **全站访问控制与多角色 RBAC 鉴权**：
  * 全站强制登录仅由环境变量 `AUTH_REQUIRE_LOGIN`（默认 `true`）控制且不支持通过 Web UI 修改；浏览类端点挂载 `require_site_access`，个人安全端点挂载 `require_authenticated_user`，管理写接口与用户/配置/Webhook 管理挂载 `require_admin_user`。
  * 会话同时支持 `Authorization: Bearer <jwt>` 与 `HttpOnly` `cj_session` Cookie，并实时校验数据库 `is_active` 与 `token_version`（改密/禁用/改角色后立即失效旧令牌）；`ADMIN_TOKEN` 仅保留作向后兼容脚本令牌。
  * OIDC SSO 采用 Authorization Code + PKCE（`S256` 无 `client_secret`）公共客户端流，必须在独立 `oidc_states` 表核验一次性 `state` 与 `nonce` 并通过 JWKS 验签 `id_token`。
* **敏感信息脱敏与密码学存储**：统一通过 `cj/core/security.py` 处理敏感信息：用户密码一律使用 Argon2id 单向哈希、TOTP 密钥一律经 Fernet 对称加密存储，严禁在日志或 API 响应中泄露密码、JWT、OTP Base32 密钥、`code_verifier`、`code`、`id_token`、`access_token`、`DATABASE_URL` 密码及敏感 Header。前端编辑规则回传 `******` 掩码占位符时，服务端必须保留库中原密钥。
* **路径安全**：所有本地文件与目录操作必须使用 `pathlib.Path`，禁止使用原始字符串拼接路径。

---

## 4. API、错误处理与代码风格

### REST API 与错误处理
* **强类型 Schema**：所有 REST API 的请求体与响应体必须定义 Pydantic `BaseModel`，**严禁端点直接返回裸 `dict`**。
* **分层异常边界**：
  * `cj/scraper/` 与 `cj/services/` 层**严禁抛出 `HTTPException`**；发生外部请求或业务异常时记录日志并返回领域结果或 `None`。
  * `cj/api/` 端点层负责将领域失败结果统一转换为标准响应 Schema 或 `HTTPException`。
* **禁止吞掉异常**：严禁使用裸 `except:`，必须捕获具体异常类型或至少捕获 `Exception` 并记录有效上下文日志。

### 日志规范与分级策略
所有日志必须通过 `cj/core/logging.py` 提供的统一 Logger 输出，**严禁在生产代码中使用 `print()`**。

* **日志分级使用标准**：
  * `DEBUG`：排查细节。记录外部请求参数/状态、CCU 逐条 Diff 过程、Webhook 规则逐条命中/跳过原因等（默认关闭，排查问题时通过 `LOG_LEVEL=DEBUG` 开启）。
  * `INFO`：关键业务与生命周期节点。记录服务启停、数据库迁移结果、定时抓取开始与汇总（耗时、新增/变动数量）、检测到新 CCU、Webhook 推送成功、词典同步完成等。
  * `WARNING`：可恢复的异常。记录单次请求超时/触发重试、Webhook 单次发送失败进入退避重试、船名缺失中文译名走兜底逻辑等。
  * `ERROR`：影响功能的失败。记录重试耗尽后仍失败的抓取或 Webhook 推送、未捕获的业务异常、数据库迁移失败等；捕获意外异常时优先使用 `logger.exception(...)` 保留完整堆栈。
* **格式与输出策略**：
  * 统一格式：`[YYYY-MM-DD HH:MM:SS] [LEVEL] [module_name] 消息内容`（定时任务或 Webhook 推送建议附带任务/规则标识，方便串联排查）。
  * **Docker 友好**：对于 docker 项目，默认输出至 `stdout/stderr` 以便直接通过 `docker logs` 查看；同时支持通过环境变量 `LOG_TO_FILE=true` 开启本地轮转文件日志（存放在 `./data/logs/ccujailer.log`，单文件上限 10MB，保留最近 5 份）。
* **日志内容要求**：日志文案统一使用英文；记录错误时必须包含关键上下文（如船名、规则名、HTTP 状态码、耗时），但**严禁输出任何明文密钥、密码或敏感 Header**。


### 前端 UI、设计令牌与无障碍规范
* **三层令牌与对比度**：`frontend/src/style.css` 统一维护 Primitive -> Semantic -> Component 三层 CSS 变量，禁止在组件中硬编码覆盖深浅主题色值或大面积内联 `style`；浅色与夜间模式文本均须满足 WCAG AA >= 4.5:1 对比度，并保持 `theme-color` 元标签与 `color-scheme` 同步。
* **布局与交互一致性**：顶部导航使用品牌图标（`public/favicon.svg`），紧邻站名展示服务版本号（如 `v0.1.0`）与 GitHub 项目主页入口，页脚同步标注版本及 GitHub 主页外链；主标签使用「在售舰船」；桌面端主容器自适应视口宽度（`max-width: min(1760px, 100%)`），页脚说明全宽平铺，版本价格卡片统一把 `WARBOND 升级` 置于最左侧，紧接 `WARBOND 整船`、`Best in Show`、弱化底色常驻 `10 Year` 及绿色 `IN GAME` 卡片并保持等高对齐；列表行 `.nearest-row` 仅显示最低加价单档位的候选升级底船；舰船详情通过悬浮模态框呈现，悬浮详情弹窗内各个详情卡片与 Webhook 规则卡片支持折叠状态与拖曳/键盘调序（持久化于 `localStorage`），Webhook 编辑弹窗将推送目标与自定义请求头置于同一区块，远程汉化词典源 URL 与词典管理共置于 `ShipsView`，顶部账号菜单统一承载账号切换、添加与安全入口。
* **可访问性与交互细节**：
  * 必须保留 `.skip-link`、全局 `:focus-visible`（Compound 控件使用 `:focus-within`）以及模态框 `role="dialog"`、`aria-modal="true"`、`Escape` 键关闭；纯图标按钮必须声明 `aria-label`，装饰性 SVG/图标必须声明 `aria-hidden="true"`。
  * 严格**禁止使用 `transition: all`**，所有过渡与动画必须显式列出属性并包裹于 `@media (prefers-reduced-motion: no-preference)`。
  * 移动端（`<= 720px`）交互控件热区与滚动区域须满足零横向溢出（`documentElement.scrollWidth <= window.innerWidth`）、支持 `touch-action: manipulation`、`overscroll-behavior: contain`（抽屉/弹窗）及安全区底部内边距。

### 命名与代码风格
* **文件与目录**：使用 `snake_case`；当单一能力需要多个文件时，建同名包并使用单词子文件（如 `services/webhook.py`），避免平铺冗长前缀。
* **标识符**：类/枚举/Pydantic 模型用 `PascalCase`；函数/方法/变量用 `snake_case`；私有成员前缀 `_`；模块级常量与环境变量用 `UPPER_SNAKE_CASE`。
* **类型注解与字符串**：所有公共函数与方法必须带完整类型注解；字符串插值统一使用 `f-string`。
* **导入顺序**：按 `标准库 -> 第三方库 -> 本地模块（cj.*）` 分组并留空行，组内按字母排序，禁止使用 `from xxx import *`。

### 中文注释与 Epytext Docstring 规范
* **全中文编写**：所有注释、Docstring 及 Pydantic 字段说明均须使用中文；专有名词、代码标识符、协议名、URL 等可保留原语言（如 `WBCCU`、`SQLite`、`Webhook`、`AsyncSession`）。
* **统一 Epytext 风格**：所有 Docstring 使用多行三双引号 `"""`（起止 `"""` 独占一行），并严格使用 **Epytext** 标签（`@param name:`、`@return:`、`@raise ExceptionType:`、`@ivar name:`、`@cvar name:`），禁止混用 reST（`:param:`）或 Google 风格。
* **文件头注释（必填）**：新建任何源码文件时，必须在文件开头（`import` 之前）添加模块级 Docstring，简要说明该文件的核心职责与边界。
* **类与函数注释（必填）**：所有类与函数在定义时必须添加 Docstring，清晰说明职责、各参数/关键变量的业务含义、返回值及可能抛出的异常。
* **Pydantic 字段说明**：所有对外 Schema 字段除类注释外，仍须提供中文 `Field(description="...")` 以自动生成清晰的 OpenAPI 文档。
* **过长代码分块注释**：对于逻辑较长或步骤较多的函数/代码块，必须按逻辑阶段划分并添加单行 `#` 分块注释，明确说明每一块代码做了什么。
* **拒绝废话与死代码**：禁止遗留无上下文的 `TODO`/`FIXME` 或大段注释掉的死代码；代码逻辑变更时必须同步更新对应注释。

**Epytext 注释示例**：
```python
"""
CCU 差价计算与最近可升级起始船筛选服务。

本模块仅负责领域计算与业务编排，不直接发起外部 HTTP 请求。
"""


def find_nearest_from_ships(target_price: float, limit: int = 5) -> list[dict]:
    """
    计算距离目标 CCU 价格最近的可用起始船列表。

    @param target_price: 目标 CCU 的实际购买价（WBCCU 取优惠价，普通 CCU 取原价）
    @param limit: 最多返回的起始船数量，默认 5 艘
    @return: 按价格降序排列的起始船列表，每项包含船名与需补差价
    @raise ValueError: 当 target_price 小于等于 0 时抛出
    """
    # 1. 校验输入价格合法性
    if target_price <= 0:
        raise ValueError("目标价格必须大于 0")

    # 2. 筛选严格低于目标价的船只并按价值降序排列
    ...

    # 3. 截断数量并计算每艘船的需补差价
    ...
```


---

## 5. 依赖管理、测试与提交规范

* **依赖锁文件**：
  * 后端使用 `uv` 管理依赖：运行依赖写入 `[project].dependencies`，开发/测试工具写入 `[dependency-groups].dev`，修改 `pyproject.toml` 后必须同步更新并提交 `uv.lock`。
  * 前端严格锁定 `package-lock.json`（或 `pnpm-lock.yaml`）。
* **质量门禁**（提交前及 CI 发布镜像前必须通过）：
  ```bash
  # 后端检查（Python 3.14）
  uv lock --check
  uv run --locked --no-sync ruff check .
  uv run --locked --no-sync pytest

  # 依赖安全扫描（运行时依赖变更或发版前）
  uv export --quiet --locked --no-dev --no-emit-project -o /tmp/audit-req.txt
  uvx --from pip-audit pip-audit --require-hashes --disable-pip --strict -r /tmp/audit-req.txt

  # 前端类型检查、构建与 Playwright 桌面/移动端端到端验收
  cd frontend && npx tsc --noEmit && npx vue-tsc --noEmit && npm run build
  ```
* **测试覆盖**：修复 Bug 前优先补充复现测试；核心算法（如 `nearest_from_ships` 差价计算、Webhook 多条件过滤匹配与多平台渲染、四级汉化词典解析与双语索引、防降级迁移校验）必须具备 `pytest` 单元测试；UI 或交互调整后需基于 `playwright-skill` 在桌面端（`1440×900`）与移动端（`390×844` / `375×812`）验证零 Console/Runtime 报错、零横向溢出及视觉截图巡检。
* **提交信息（Conventional Commits + AI Trailer）**：格式为 `<type>(<scope>): <description>`，标题不超过 72 字符，支持类型：`feat`、`fix`、`docs`、`chore`、`refactor`、`test`、`ci`。执行 `git commit` 时必须在提交信息末尾附加标准 AI 与 Harness Trailer（如 `Assisted-by: Gemini` 与 `Assisted-by: Antigravity`，不写具体版本号，无官方邮箱时严禁伪造邮箱）。
* **开源协议与文档规范**：
  * 保留仓库根目录的 AGPL-3.0 `LICENSE` 文件及上游 `DieLaughing/pledgewatch` 衍生版权声明。
  * 更新 `README.md` 时必须在顶部包含统一 `style=for-the-badge` 风格的 Shields.io Badges（仓库统计、Docker/GHCR 镜像、支持平台及 `LINUX DO` 徽章），并包含「AI 使用声明 / Acknowledgements」小节。
* **诚实汇报原则**：严禁宣称未执行的测试或检查项已通过；交付改动时必须如实列出实际运行过的验证命令与结果。
---

## 6. AI 代理协作与联动更新规范

### 工作守则
* **最小正确改动**：严格围绕当前任务做最小且正确的变更，禁止顺手进行无关的重构、重命名或大面积格式化清理。
* **批量验证**：在执行耗时操作（如 Docker 镜像构建）前先批量完成相关代码修改，不要每改几行就重复触发构建。
* **测试严格隔离**：`pytest` 必须使用临时目录/临时数据库，**严禁触碰真实的 `./data/ccujailer.db`、真实外部网络（RSI 商店）或真实 Webhook 地址**；外部 HTTP 边界必须 Mock 或使用 fixture，新测试统一采用 pytest 函数与 fixture 风格。
* **沟通与决策**：默认使用中文输出变更摘要、验证结果与风险提示。遇到文档与代码不一致时，以当前代码事实为准；仅在阻塞验收或涉及数据/密钥安全时停下来确认，其余不受影响的工作继续推进并在总结中说明差异。

### 联动更新清单（Coupled Updates）
修改左列内容时，必须在同一次变更中同步更新右列配套文件：

| 变更项 | 必须同步更新 |
| :--- | :--- |
| 数据库 ORM 模型（`cj/db/`） | 新增 Alembic 迁移脚本（`backend/alembic/versions/`） |
| 静态环境变量（`cj/core/config.py`） | `.env.example`、`docker-compose.yml` 与 `docs/architecture-overview.md` |
| 后端依赖（`pyproject.toml`） | `uv.lock`（并通过 `uv lock --check` 与 `pip-audit`） |
| REST API 结构（`cj/api/`） | 前端对应 TypeScript 类型定义、调用代码与单测 |
| 动态配置键（`SystemConfigKey`） | `/api/v1/settings` 接口与前端设置页表单 |
| 架构或规范约定修正 | `docs/architecture-overview.md` 与 `docs/rules.md` |

