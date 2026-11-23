

## 1. 项目概述与改造目标

**CCU监狱（CCUJailer，简称 `cj`）** 基于开源项目 `DieLaughing/pledgewatch`（AGPL-3.0 协议）进行二次开发。在保留其核心的 RSI 商店与 CCU 抓取、状态 Diff 能力的基础上，进行前后端分离重构，并新增**双数据库引擎与平滑迁移、出口代理支持、中文化双语检索、可编程 Webhook 通知引擎**及**自动化 CI/CD 镜像发布流程**。

### 核心需求清单
1. **Docker 极简部署**：生产环境保持单容器（或极简 Compose）一键部署，数据持久化至挂载卷。
2. **出口代理支持**：通过标准环境变量接管容器内所有出站请求（RSI 抓取、翻译源同步、Webhook 推送）。
3. **Webhook 推送与过滤**：新 CCU 上架时触发 HTTP POST，支持按船名、原价、是否 WBCCU、优惠价等条件精准过滤。
4. **船名中文化与双语支持**：自动同步/翻译船只中文名，前后端及 Webhook 全面支持中英双语展示、搜索与筛选。
5. **通知内容完备**：包含船名（中英）、原价、优惠价、最近可升级起始船列表、到期时间。
6. **工程化与自动化**：GitHub Actions 自动构建并发布 Docker 镜像；后端支持本地 Python 虚拟环境直接调试。
7. **前后端分离**：前端独立工程，支持本地热更新调试，生产环境无缝打包集成。
8. **双数据库与平滑迁移**：默认支持 SQLite 零配置运行，同时支持通过环境变量切换至 PostgreSQL；版本升级时保证数据库结构变更安全平滑。

---

## 2. 系统架构设计

为了同时满足**前后端分离（需求 7）**与**Docker 部署便利性（需求 1）**，采用**“开发态分离、生产态多阶段合并”**的架构模式：

```text
+-------------------------------------------------------------------+
|                   Production Docker Container                     |
|                                                                   |
|  +-----------------------------+   +---------------------------+  |
|  |   Static Frontend (dist/)   |   |    Backend (FastAPI)      |  |
|  |   Vue3/React + Tailwind     |<->|  - REST API (/api/v1/*)   |  |
|  +-----------------------------+   |  - Static File Serving    |  |
|                                    +-------------+-------------+  |
|                                                  |                |
|  +-----------------------------+   +-------------v-------------+  |
|  |   Background Scheduler      |   |  SQLAlchemy 2.0 + Alembic |  |
|  |  - RSI Catalog/CCU Scraper  |<->|  - Auto Migration on Boot |  |
|  |  - In-Process Event Bus     |   +-------------+-------------+  |
|  |  - Webhook / i18n Services  |                 |                |
|  +--------------+--------------+                 v                |
+-----------------|-----------------+  [ SQLite (Default) / PG ]    |
                  |  Outbound Traffic (HTTP_PROXY / HTTPS_PROXY)
                  v
   [ RSI Store / GraphQL ]   [ 汉化词典源 ]   [ User Webhook Endpoints ]
```

### 技术栈选型
* **后端**：**Python 3.14+**、FastAPI（替代原上游 `pw/site.py` 的内联 HTML 渲染，提供标准 RESTful API 及 OpenAPI 文档）、`httpx`（原生支持异步与代理）、`pydantic-settings`。
* **数据层**：SQLAlchemy 2.0（Async ORM）+ Alembic（数据库迁移工具），驱动层同时内置 `aiosqlite`（SQLite）与 `asyncpg`（PostgreSQL）。
* **前端**：Vite + Vue 3（或 React 18）+ TypeScript + Tailwind CSS。
* **部署**：多阶段构建 `Dockerfile`（Stage 1: Node 构建前端静态产物；Stage 2: Python 3.14 运行后端并由 FastAPI 托管前端静态文件）。

### 轻量模块化单体约束（开发规范）
为防止代码随迭代腐化，后端采用**轻量模块化单体**设计，严格遵守以下四项约定（不引入 Redis/Celery 等重型中间件；详细编码红线见 `rules.md`）：

1. **四层单向依赖与显式事务**：
   * `api/`（接口层）：仅负责 Pydantic 参数校验与响应组装，**严禁直接操作 ORM Session**。
   * `services/`（业务层）：负责核心业务编排（如最近升级自计算、Webhook 过滤匹配、中文化同步）及显式事务边界（`async with session.begin():`）。
   * `repositories/`（数据层）：封装全部 SQLAlchemy 查询，隔离 SQLite 与 PostgreSQL 细节。
   * `core/`（基础层）：承载配置、进程内事件总线、代理客户端与定时调度器。
2. **进程内异步事件总线（Event Bus）**：
   * 爬虫抓取并入库后，仅在事务提交成功后发布领域事件（如 `ccu.listed`、`ccu.price_changed`、`i18n.updated`），不直接调用通知代码。
   * Webhook 引擎作为事件订阅者异步消费，便于后续低成本扩展其他通知渠道。
3. **生命周期与优雅停机（Lifespan）**：
   * 统一通过 FastAPI `lifespan` 管理启停：**启动顺序**为 `校验配置 -> 初始化 DB 连接池 -> 初始化全局代理 httpx 客户端 -> 注册事件订阅 -> 启动定时调度器`；**停机顺序**为 `停止新任务 -> 等待进行中的抓取/推送完成（设超时）-> 关闭 httpx 与 DB 连接池`。
4. **静/动态配置分离**：
   * **静态配置**（`pydantic-settings`）：`DATABASE_URL`、`HTTP_PROXY`、`PORT` 等基础设施参数，启动时强校验（Fail Fast）。
   * **动态配置**（数据库 `system_settings` 表，键名统一枚举化为 `SystemConfigKey`）：抓取间隔、汉化词典源 URL、默认显示语言、日志保留天数等，支持前端修改后热生效，无需重启容器。

---

## 3. 核心功能模块详细设计

### 3.1 容器出口代理配置（需求 2）
所有外部 HTTP 请求必须统一封装在 `cj/core/net.py` 的网络客户端工厂中，禁止散落裸调用。

* **环境变量支持**：
  * `HTTP_PROXY` / `http_proxy`
  * `HTTPS_PROXY` / `https_proxy`
  * `ALL_PROXY` / `all_proxy`（支持 SOCKS5，需安装 `httpx[socks]`）
  * `NO_PROXY` / `no_proxy`（默认排除 `localhost,127.0.0.1`）
  * `WEBHOOK_USE_PROXY`（布尔值，默认 `false`）：允许用户控制发往内网/国内（如飞书、企微）的 Webhook 是否走代理。
* **实现要点**：使用 `httpx.AsyncClient(trust_env=True)` 自动继承环境变量；针对 Webhook 推送客户端，根据 `WEBHOOK_USE_PROXY` 决定是否绕过代理。

### 3.2 船名中文化与双语检索（需求 4）
在 `cj/services/i18n_service.py` 中实现中文化同步与映射逻辑，将 RSI 英文船名映射为中文标准译名。

* **数据源优先级（三级兜底）**：
  1. **本地人工覆盖表**（`data/ship_i18n_override.json`）：最高优先级，允许用户手动修正译名。
  2. **社区汉化词典自动同步**：定时拉取社区维护的星际公民船名中英对照源（参考 CitizenHub、星际公民盒子 SCBox 或 GitHub 开源汉化库如 `StarCitizen-Localization` 的船只映射表），缓存在 `data/ship_i18n_cache.json`。
  3. **自动翻译兜底（可选）**：当官方推出全新概念船且社区词典尚未收录时，若配置了翻译 API Key（如 DeepL / OpenAI 兼容接口），自动生成初始译名并标记为 `auto_translated: true`。
* **双语存储与检索**：
  * 数据库 `ships` 表新增字段：`name_en`、`name_zh`、`search_index`（拼接英文名、中文名、常见简称/拼音首字母）。
  * 前端支持三种显示模式切换：`中英双语（默认）`、`仅中文`、`仅英文`。
  * 搜索框与 Webhook 过滤规则均同时匹配 `name_en` 和 `name_zh`（大小写不敏感、支持模糊匹配）。

### 3.3 CCU 监控、最近升级自计算与到期时间（需求 5）
在爬虫检测到新 CCU 上架（`status: available` 且上次快照不存在或价格变动）并在事务提交后发布事件，由 `ccu_service` 执行数据增强逻辑：

1. **最近可升级自可选船列表（Nearest "Upgrade From" Ships）**：
   * **计算规则**：设目标 CCU 的购买价为 `target_price`（若为 WBCCU 则取优惠价，否则取原价）。从当前全量船只表中筛选所有 `ship_value` 严格小于 `target_price` 的船只，按 `ship_value` **降序排列**，取前 5 艘。
   * **细分维度**：优先标注**常驻可买（Always Available）**的船只（方便玩家随时直接买底船），同时列出全部船只中最接近的选项，并计算每艘船的**需补差价**（`target_price - ship_value`）。
2. **到期时间（Expiration Time）**：
   * 优先解析 RSI Ship Upgrades GraphQL / Store API 返回的 `valid_until` / `end_date` 字段并转为 ISO-8601 格式。
   * *注意*：RSI 官方接口对部分日常 WBCCU 不会下发精确下架时间；若接口未返回，字段置为 `null`，前端/通知文案展示为 `"未公布"`。

### 3.4 Webhook 规则过滤与推送引擎（需求 3 & 5）
支持在前端页面或配置文件中管理多条 Webhook 规则，每条规则独立过滤与推送。

#### 过滤条件模型（Rule Schema）
```json
{
  "id": "rule_wb_high_value",
  "name": "高价值 WBCCU 推送",
  "enabled": true,
  "url": "[https://example.com/webhook](https://example.com/webhook)",
  "method": "POST",
  "headers": {
    "Authorization": "Bearer your-token"
  },
  "filters": {
    "only_warbond": true,
    "ship_keywords": ["Zeus", "宙斯", "Polaris", "北极星"],
    "min_original_price": 100,
    "max_original_price": 1000,
    "min_wb_price": null,
    "max_wb_price": 800,
    "min_savings": 10
  }
}
```
* 过滤逻辑：`filters` 中为 `null` 或空数组的字段表示不限制；所有非空条件按 **AND** 关系求交集；`ship_keywords` 内部按 **OR** 关系匹配中英文船名。

#### Webhook 推送 Payload 标准格式
```json
{
  "event": "ccu.new_listing",
  "timestamp": "2026-11-23T10:15:00Z",
  "data": {
    "ship_id": "123",
    "ship_name_en": "Zeus Mk II ES",
    "ship_name_zh": "宙斯 Mk II ES",
    "is_warbond": true,
    "original_price": 175.0,
    "discounted_price": 160.0,
    "savings": 15.0,
    "currency": "USD",
    "expires_at": "2026-11-24T16:00:00Z",
    "store_url": "[https://robertsspaceindustries.com/pledge](https://robertsspaceindustries.com/pledge)",
    "nearest_from_ships": [
      {
        "name_en": "Prospector",
        "name_zh": "勘探者",
        "value": 155.0,
        "upgrade_cost": 5.0,
        "always_available": true
      },
      {
        "name_en": "Cutlass Red",
        "name_zh": "黑盗红",
        "value": 135.0,
        "upgrade_cost": 25.0,
        "always_available": true
      }
    ]
  }
}
```
* **可靠性机制**：内置 3 次指数退避重试；每次推送结果（HTTP 状态码、耗时、脱敏后的错误信息）写入数据库 `webhook_logs` 表，前端可查看推送历史并支持“发送测试通知”按钮。

### 3.5 数据库双引擎与平滑迁移设计（需求 8）
彻底剥离原项目散落的原生 SQLite SQL 语句，统一收口至 **SQLAlchemy 2.0 ORM + Alembic**。

* **双引擎切换配置**：
  * 通过单一环境变量 `DATABASE_URL` 控制：
    * **默认（SQLite）**：`sqlite+aiosqlite:///./data/ccujailer.db`（自动开启 `WAL` 模式与外键约束，提升并发读写能力）。
    * **PostgreSQL**：`postgresql+asyncpg://ccujailer:changeme@postgres:5432/ccujailer`（自动配置连接池 `pool_pre_ping=True`）。
  * **跨方言兼容规范**：
    * 严禁使用特定数据库的私有语法；JSON 字段统一使用 SQLAlchemy `JSON` 类型（SQLite 映射为 TEXT，PG 映射为 JSON/JSONB）。
    * 时间字段统一使用带时区的 UTC `DateTime(timezone=True)`。
* **安全平滑的升级迁移机制**：
  * **启动自动迁移**：容器入口脚本（`entrypoint.sh`）在启动 FastAPI 服务前，默认执行 `alembic upgrade head`（可通过环境变量 `AUTO_MIGRATE=false` 关闭以供高级用户手动控制）。
  * **防降级保护**：迁移前先比对数据库当前的 `alembic_version` 是否存在于当前代码的版本树中；若数据库版本高于代码版本（例如镜像回退），直接报错中止启动，**绝不自动执行 downgrade**。
  * **SQLite 迁移前自动快照**：检测到当前为 SQLite 且存在待执行的 Alembic 迁移版本时，自动将 `ccujailer.db` 备份至 `./data/backups/ccujailer_<timestamp>.db`（默认保留最近 5 份），迁移失败时可一键回滚。
  * **SQLite `ALTER TABLE` 兼容**：在 `alembic/env.py` 中强制开启 `render_as_batch=True`，确保 SQLite 在修改列类型、删除列或加约束时自动采用安全的 Batch Copy-and-Move 策略。
  * **并发防冲突与日志清理**：PostgreSQL 利用 `pg_advisory_lock`、SQLite 利用文件锁确保同一时刻只有一个进程执行迁移；`webhook_logs` 和 `changelog` 表内置按天自动清理策略（默认保留 30 天，设为 `0` 禁用）。

---

## 4. 前后端分离与 API 接口设计（需求 7）

### 后端 RESTful API 规范（前缀 `/api/v1`）
| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `GET` | `/api/v1/ccus` | 获取当前在售 CCU 列表（支持 `q` 双语搜索、`warbond_only`、`min_price`、`max_price` 筛选） |
| `GET` | `/api/v1/ships` | 获取全量船只列表及中英文映射 |
| `GET` | `/api/v1/changelog` | 获取历史上下架与调价日志 |
| `GET/POST` | `/api/v1/webhooks` | 获取或新增 Webhook 规则（敏感 Header 自动脱敏） |
| `PUT/DELETE` | `/api/v1/webhooks/{id}` | 修改或删除指定 Webhook 规则 |
| `POST` | `/api/v1/webhooks/{id}/test` | 触发一条模拟 CCU 数据测试 Webhook 连通性 |
| `GET` | `/api/v1/webhooks/logs` | 查询近期 Webhook 推送日志 |
| `POST` | `/api/v1/i18n/sync` | 手动触发一次中文船名词典同步 |
| `GET/PUT` | `/api/v1/settings` | 读取或热更新动态系统配置（抓取频率、翻译源、默认语言、日志保留天数等） |

### 目录结构规划
```text
ccujailer/
├── backend/                  # Python 3.14+ 后端（原上游 pw/ 核心迁移重构至此）
│   ├── alembic/              # Alembic 迁移脚本目录
│   │   ├── versions/
│   │   └── env.py            # 开启 render_as_batch=True
│   ├── alembic.ini
│   ├── cj/                   # 主业务包（CCUJailer 简称）
│   │   ├── api/              # FastAPI 路由与 Pydantic Schema（不直接操作 DB）
│   │   ├── services/         # 业务编排（ccu_service, i18n_service, webhook_service）
│   │   ├── repositories/     # SQLAlchemy 数据访问层
│   │   ├── scraper/          # 原 catalog/ccu 抓取逻辑（产出数据并发布事件）
│   │   ├── db/               # ORM 模型、Session 工厂、迁移前自动备份与防降级校验
│   │   └── core/             # config.py, events.py(事件总线), net.py(代理客户端), scheduler.py
│   ├── tests/                # pytest 单元测试目录
│   ├── .python-version       # 锁定 Python 版本为 3.14
│   ├── pyproject.toml        # 依赖声明与 ruff/pytest 配置
│   ├── uv.lock               # 后端依赖锁文件
│   ├── entrypoint.sh         # 容器启动脚本（执行迁移 -> 启动服务）
│   └── main.py               # 启动入口（含 FastAPI lifespan 管理）
├── frontend/                 # 独立前端工程
│   ├── src/
│   ├── .nvmrc                # 锁定 Node.js 版本（如 22）
│   ├── package.json
│   ├── package-lock.json     # 前端依赖锁文件（或 pnpm-lock.yaml）
│   └── vite.config.ts        # 配置 /api 反向代理至本地后端
├── .github/workflows/
│   └── docker-publish.yml    # 自动构建发布流水线（含质量门禁）
├── .gitignore                # 忽略 .venv/、node_modules/、data/、pgdata/、.env 等
├── .dockerignore             # 排除本地缓存与 .git，减小镜像构建上下文
├── .env.example              # 环境变量配置模板（代理、DB URL、翻译 Key 等）
├── Dockerfile                # 多阶段构建文件（构建时严格使用锁文件安装依赖）
├── docker-compose.yml        # 一键部署编排文件（支持 SQLite / Postgres Profile）
├── DESIGN.md                 # 本技术设计文档
├── rules.md                  # 开发规范与架构红线
├── LICENSE                   # 保留上游 DieLaughing/pledgewatch 的 AGPL-3.0 协议
└── README.md                 # 项目说明（含上游 AGPL-3.0 衍生声明）
```

---

## 5. 本地开发环境部署与调试（需求 6 & 7）

### 5.1 后端本地调试（Python 3.14 虚拟环境）
推荐使用 `uv` 一键同步锁文件，也可直接使用标准 `venv`：

```bash
cd backend

# 方式一（推荐）：使用 uv 按锁文件创建 Python 3.14 虚拟环境
uv sync --locked
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 方式二：标准 Python 3.14 虚拟环境
# python3.14 -m venv .venv
# source .venv/bin/activate
# pip install -e ".[dev]"

# 可选：配置本地代理
export HTTP_PROXY="[http://127.0.0.1:7890](http://127.0.0.1:7890)"
export HTTPS_PROXY="[http://127.0.0.1:7890](http://127.0.0.1:7890)"

# 可选：切换本地 PostgreSQL（不设置则默认使用 ./data/ccujailer.db）
# export DATABASE_URL="postgresql+asyncpg://ccujailer:changeme@127.0.0.1:5432/ccujailer"

# 执行数据库迁移
alembic upgrade head

# 启动热重载开发服务器（默认监听 8794 端口）
uvicorn main:app --reload --host 0.0.0.0 --port 8794
```

### 5.2 前端本地调试
在 `frontend/vite.config.ts` 中配置开发代理，将 `/api` 请求转发至本地后端 `http://127.0.0.1:8794`，彻底解决本地开发跨域问题：
```bash
cd frontend
npm install
npm run dev
# 访问 http://localhost:5173 即可进行前后端联调
```

---

## 6. Docker 部署与 CI/CD 自动化流程（需求 1 & 6）

### 6.1 生产环境 `docker-compose.yml` 示例
支持开箱即用的 SQLite 模式，以及通过 `--profile postgres` 一键启用配套 PostgreSQL：

```yaml
services:
  ccujailer:
    image: ghcr.io/<your-username>/ccujailer:latest
    container_name: ccujailer
    restart: unless-stopped
    ports:
      - "8794:8794"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
      - AUTO_MIGRATE=true
      
      # 默认 SQLite；若启用下方 postgres 服务，请取消下行注释：
      # - DATABASE_URL=postgresql+asyncpg://ccujailer:changeme@postgres:5432/ccujailer
      - HTTP_PROXY=[http://host.docker.internal:7890](http://host.docker.internal:7890)
      - HTTPS_PROXY=[http://host.docker.internal:7890](http://host.docker.internal:7890)
      - WEBHOOK_USE_PROXY=false

  postgres:
    image: postgres:16-alpine
    container_name: ccujailer-db
    profiles: ["postgres"]
    restart: unless-stopped
    environment:
      - POSTGRES_USER=ccujailer
      - POSTGRES_PASSWORD=changeme
      - POSTGRES_DB=ccujailer
    volumes:
      - ./pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ccujailer"]
      interval: 5s
      timeout: 5s
      retries: 5
```

* **SQLite 启动命令**：`docker compose up -d`
* **PostgreSQL 启动命令**：取消 `DATABASE_URL` 注释后执行 `docker compose --profile postgres up -d`

### 6.2 GitHub Actions 自动发布流水线
文件：`.github/workflows/docker-publish.yml`
* **触发条件**：推送 `v*` 标签（如 `v1.0.0`）或合并至 `main` 分支。
* **前置轻量质量门禁**（构建镜像前自动执行，失败则阻断发布）：
  1. 后端在 Python 3.14 环境下运行 `uv lock --check`、`ruff check .`、`pytest` 及 `pip-audit` 依赖安全扫描。
  2. 前端运行 `npx tsc --noEmit`（TypeScript 类型检查），确保前后端数据结构对齐。
* **构建流程**：
  1. 使用 `docker/setup-qemu-action` 与 `docker/setup-buildx-action` 开启多架构支持（`linux/amd64`, `linux/arm64`，兼容常见服务器与 ARM NAS）。
  2. 登录 GitHub Container Registry (`ghcr.io`)。
  3. 执行多阶段 `Dockerfile` 构建并推送，自动打上 `latest`、`sha-xxxxxxx` 及语义化版本标签。

---

## 7. 建议实施步骤（交付开发参考）

1. **阶段一：工程骨架、数据层与基础层**
   * 拆分 `backend/cj/` 与 `frontend/` 目录，锁定 Python 3.14，确立四层单向依赖架构，配置 `lifespan` 启停与 `cj/core/events.py` 事件总线。
   * 搭建 SQLAlchemy 2.0 + Alembic 数据层，实现 SQLite / PostgreSQL 双引擎兼容、动态配置表及启动前自动备份与防降级校验。
   * 封装 `cj/core/net.py`，全局接入环境变量代理。
2. **阶段二：中文化与数据增强**
   * 实现 `i18n_service` 词典同步与本地覆盖机制，扩展数据库双语字段。
   * 实现 `nearest_from_ships` 算法与 `expires_at` 解析逻辑，并编写核心 `pytest` 单元测试。
3. **阶段三：Webhook 引擎与配置接口**
   * 完成 Webhook 规则 CRUD、事件订阅消费、过滤匹配器、异步推送与日志记录（含自动按天清理）。
   * 完成 `/api/v1/settings` 动态系统配置热更新接口。
4. **阶段四：前端开发**
   * 搭建 Vite 前端，完成双语 CCU 看板、搜索筛选、Webhook 规则配置/测试及系统设置页面。
5. **阶段五：容器化与 CI/CD**
   * 编写多阶段 `Dockerfile`、`docker-compose.yml`（含 Postgres Profile）及带质量门禁的 GitHub Actions 工作流，验证镜像构建与双数据库环境的升级迁移流程。
