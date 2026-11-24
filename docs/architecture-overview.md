

## 1. 项目概述与改造目标

**CCUJailer（简称 `cj`）** 基于开源项目 `DieLaughing/pledgewatch`（AGPL-3.0 协议）进行二次开发。在完整继承并复刻上游桌面与移动端阅读流体验（含全版本商店卡片、游戏内买租 aUEC 价格、实装版本、售卖规律、金牌标准、武器火力/DPS、历史价格、俯视货舱 SVG 网格以及 CCU 升级路线规划器）的基础上，重构为现代前后端分离架构，并新增**多用户认证体系（账密 + OTP + ZITADEL PKCE SSO）、双数据库引擎与平滑迁移、出口代理支持、中文化双语检索、可编程 Webhook 通知引擎**及**自动化 CI/CD 镜像发布流程**。

### 核心需求清单
1. **Docker 极简部署**：生产环境保持单容器（或极简 Compose）一键部署，数据持久化至挂载卷。
2. **出口代理支持**：通过环境变量接管容器内所有出站请求（RSI 抓取、Wiki/UEX/GitHub 富化、OIDC Discovery/Token 交互、翻译源同步、Webhook 推送），支持 `NO_PROXY` CIDR 网段匹配。
3. **Webhook 推送与过滤**：新 CCU 上架或降价时触发 HTTP 推送，支持按船名、原价、是否 WBCCU、优惠价等条件精准过滤与多平台卡片格式渲染。
4. **船名中文化与双语支持**：自动同步/翻译船只中文名，支持人工在系统设置词典页逐船覆盖；前端全面支持「中英双语」「仅中文」「仅英文」切换。
5. **全量商店体验与升级路线规划**：完整复刻 `pledgewatch` 桌面端与移动端界面，支持价格/版本/游戏内售价/性价比/主驾火力排序、完整 `Filters` 弹窗与底部滑出抽屉（含「我拥有的底船」实时向 RSI 核实补差价）与内置 CCU 省钱连跳规划器。
6. **企业级身份认证与访问控制**：全站强制登录完全由服务端环境变量 `AUTH_REQUIRE_LOGIN` 控制（默认开启且 Web UI 不可修改），支持首次引导创建管理员或环境变量预设、Argon2id 本地账密、RFC 6238 TOTP 二步验证、ZITADEL OIDC PKCE 无 `client_secret` 公共客户端单点登录及基于角色（`admin` / `user`）的权限管控与用户管理。
7. **前后端分离与工程化**：前端独立 Vue 3 + Vite + TypeScript 工程，支持浅色 / 夜间 / 跟随系统三态切换，统一使用品牌图标（`favicon.svg`），并在界面展示 `CCUJailer` 服务版本号与 GitHub 项目主页入口。
8. **双数据库与平滑迁移**：默认支持 SQLite 零配置运行，同时支持通过 `DATABASE_URL` 切换 PostgreSQL，配合防降级保护、并发锁与迁移前自动备份。

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
* **前端**：Vite + Vue 3 + TypeScript（延续原 `pledgewatch` 极简阅读流视觉体系，支持手动切换浅色 / 夜间模式或跟随系统，UI 站名统一展示为 `CCUJailer`）。
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
4. **静/动态配置分离与多方式认证鉴权**：
   * **静态配置**（`pydantic-settings`）：`DATABASE_URL`、`HTTP_PROXY`、`PORT`、`AUTH_REQUIRE_LOGIN`（全站强制登录开关，Web UI 不可修改）、`AUTH_SECRET_KEY`、`INIT_ADMIN_*`、`UPGRADE_PLANNER_ENABLED` 等基础设施与访问控制策略参数，启动时加载（项目 `.env` 优先级高于宿主机残留环境变量）。
   * **动态配置**（数据库 `system_settings` 表，键名统一枚举化为 `SystemConfigKey`）：抓取间隔、目录同步间隔、汉化词典源 URL（前端在「舰船词典」内管理）、默认显示语言、日志保留天数、升级路线规划功能开关（默认 `false`）、ZITADEL SSO 参数与可选自动翻译参数等，支持管理员修改后热生效。
   * **认证与 RBAC**：支持 `admin` 与 `user` 双角色，基于 Argon2id 密码 + TOTP 2FA + OIDC PKCE 无密钥 SSO，通过 HttpOnly `cj_session` Cookie 与 `Bearer JWT` 双通道校验（保留 `ADMIN_TOKEN` 仅作脚本向后兼容）。

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

* **数据源优先级（四级流水线）**：
  1. **本地人工覆盖表**（`data/ship_i18n_override.json`）：最高优先级，允许用户手动修正译名。
  2. **社区汉化词典自动同步**：定时或手动拉取社区维护的星际公民船名中英对照源（同时兼容 JSON 映射表与星际公民汉化组 `global.ini` `vehicle_Name*` 格式解析），缓存在 `data/ship_i18n_cache.json`。
  3. **内置离线词典与家族前缀推导**（`cj/resources/ship_i18n_builtin.json`）：随镜像内置 200+ 星际公民常见舰船/载具中英对照与家族前缀自动推导规则，零外部依赖保底可用。
  4. **自动翻译兜底（可选）**：当官方推出全新概念船且本地词典尚未收录时，若开启 `TRANSLATE_ENABLED` 并配置了 `TRANSLATE_API_KEY`（OpenAI 兼容接口），自动生成初始译名并标记为 `auto_translated: true`。
* **双语存储与检索**：
  * 数据库 `ships` 表新增字段：`name_en`、`name_zh`、`search_index`（拼接英文名、中文名、常见简称/拼音首字母）。
  * 前端支持三种显示模式切换：`中英双语（默认）`、`仅中文`、`仅英文`。
  * 搜索框与 Webhook 过滤规则均同时匹配 `name_en` 和 `name_zh`（大小写不敏感、支持模糊匹配）。

### 3.3 CCU 监控、WBCCU 认定、最近升级自计算与异常价格过滤（需求 5）
* **在售 CCU 精准过滤与严格 WBCCU 认定**：RSI `initShipUpgrade` 会返回历史全部已存在过的升级 SKU（含已停售版本），因此 `rsi_client` 在同一访客会话中额外查询 `filterShips(fromId: null)`，仅保留当前实际在售 `to.ships.skus` 集合中的有效 SKU，同时保留全量舰船估值（MSRP）用于计算底船差价。仅有 SKU 标题明确包含 `Warbond` 关键字的升级包（`Warbond Edition` / `Best in Show ... Warbond Edition`）才会标记为 `is_warbond=true`（WBCCU）；`10 Year` 与普通非 Warbond 版 `Best in Show` 不计入 WBCCU。
* **整船与升级 Warbond 分离展示（升级卡片置最左）**：RSI 单船商店（`category 72`）与升级商店（Ship Upgrades）各自独立上架。前端舰船行右侧购买区（`.buy`）固定将 `WARBOND 升级` 卡片（`.ed-wbccu`）置于所有价格卡片最左侧，其后依次排列 Warbond 整船（`.ed-warbond`，文案 `WARBOND 整船`）、`Best in Show`、常驻弱化配色的 `10 Year` 卡片与 `IN GAME` 卡片（保持等高），即使暂无 Warbond 整船 SKU 也会独立展示 `WARBOND 升级` 价格卡片。
* **异常价格变动拦截（防税率/汇率污染）**：针对非 `$5` 整数倍价格（`price % 500 != 0`，如含税价）以及单轮同步中出现的系统性同比例偏移批次（`>= 8` 条且占比达阈值，例如 `1.1x` 税率校正导致的批量回落），`ccu_service` 在对比单船商店与 CCU 价格时跳过写入 `changelog` 与事件推送，防止污染历史记录与通知。
* 在爬虫检测到新 CCU / 单船上架（且非首次冷启动基线）或有效降价并在事务提交后发布事件，由 `ccu_service` 执行数据增强逻辑：

1. **最近可升级自可选船列表（Nearest "Upgrade From" Ships）**：
   * **计算规则**：设目标 CCU 的购买价为 `target_price`（若为 WBCCU 则取优惠价，否则取原价）。从当前全量船只表中筛选所有 `ship_value` 严格小于 `target_price` 的船只，按 `ship_value` **降序排列**（服务端最多返回 12 艘供详情弹窗完整查看；主列表行 `.nearest-row` 仅截取并展示差价最低的第一档 `+cost` 全部候选舰船）。
   * **细分维度**：优先标注**常驻可买（Always Available）**的船只（方便玩家随时直接买底船），同时列出全部船只中最接近的选项，并计算每艘船的**需补差价**（`target_price - ship_value`）。
2. **到期时间（Expiration Time）**：
   * 优先解析 RSI Ship Upgrades GraphQL / Store API 返回的 `valid_until` / `end_date` 字段并转为 ISO-8601 格式。
   * *注意*：RSI 官方接口对部分日常 WBCCU 不会下发精确下架时间；若接口未返回，字段置为 `null`，前端/通知文案展示为 `"未公布"`。

### 3.4 Webhook 规则过滤与推送引擎（需求 3 & 5）
支持在前端页面或接口中管理多条 Webhook 规则，每条规则可将推送目标地址与自定义 HTTP 请求头集中配置在同一区块，并可独立勾选订阅事件类型（`ccu.warbond_upgrade` Warbond 升级、`store.warbond_ship` Warbond 整船、`store.special_sale` Best in Show / 限量特殊销售，以及包含标准版的兼容事件 `ccu.new_listing` / `ccu.price_changed`），独立配置过滤条件（Web UI 已移除与订阅事件类型重复的 `仅推送 Warbond` 复选框），并选择消息模板格式（`provider_type`：`generic` 标准通用 JSON / `feishu` 飞书卡片 / `wecom` 企业微信 Markdown / `discord` Discord Embed / `custom` 自定义 JSON 模板，支持 `{{sale_type}}` 与 `{{edition_label}}` 占位符）。

#### 过滤条件模型（Rule Schema）
```json
{
  "id": "rule_wb_high_value",
  "name": "高价值 WBCCU 推送",
  "enabled": true,
  "url": "https://example.com/webhook",
  "method": "POST",
  "provider_type": "generic",
  "custom_template": null,
  "headers": {
    "Authorization": "Bearer your-token"
  },
  "events": ["ccu.warbond_upgrade", "store.warbond_ship"],
  "filters": {
    "only_warbond": false,
    "ship_keywords": ["Zeus", "宙斯", "Polaris", "北极星"],
    "min_original_price": 100,
    "max_original_price": 1000,
    "min_wb_price": null,
    "max_wb_price": 800,
    "min_savings": 10
  }
}
```
* 过滤逻辑：`filters` 中为 `null` 或空数组的字段表示不限制；所有非空条件按 **AND** 关系求交集；`ship_keywords` 内部按 **OR** 关系匹配中英文船名（`only_warbond` 仅在底层 API 向后兼容保留，前端已统一交由事件类型复选控制）。

#### Webhook 推送 Payload 标准格式（`provider_type: "generic"`）
```json
{
  "event": "ccu.new_listing",
  "timestamp": "2026-11-23T10:15:00Z",
  "data": {
    "ship_id": "123",
    "sku_id": 4001,
    "ship_name_en": "Zeus Mk II ES",
    "ship_name_zh": "宙斯 Mk II ES",
    "edition_label": "Warbond",
    "is_warbond": true,
    "original_price": 175.0,
    "discounted_price": 160.0,
    "savings": 15.0,
    "currency": "USD",
    "expires_at": "2026-11-24T16:00:00Z",
    "store_url": "https://robertsspaceindustries.com/pledge",
    "image_url": "https://robertsspaceindustries.com/media/zeus-mk-ii-es/store_small.jpg",
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
        "name_zh": "黑弯刀 红",
        "value": 135.0,
        "upgrade_cost": 25.0,
        "always_available": true
      }
    ]
  }
}
```
* **可靠性机制**：内置 3 次指数退避重试；每次推送结果（HTTP 状态码、耗时、脱敏后的错误信息）写入数据库 `webhook_logs` 表，前端可查看推送历史并支持“发送测试通知”按钮；事件负载 `data["image_url"]` 自动附带舰船官方头图，其中 Discord Embed 自动挂载 `image` 预览图，自定义 JSON 模板支持 `{{image_url}}` 占位符。

### 3.5 数据库双引擎与平滑迁移设计（需求 8）
彻底剥离原项目散落的原生 SQLite SQL 语句，统一收口至 **SQLAlchemy 2.0 ORM + Alembic**（已发布 `0001_initial_schema`、`0002_store_enrichment_and_auth`、`0003_invite_codes` 与 `0004_cleanup_abnormal_changelogs` 四阶段迁移，其中 `0004` 负责安全清理历史非 `$5` 倍数及批量税率纠偏产生的异常 `changelog` 记录）。
* 新增业务数据表：`store_skus`（单船各版本与 Add-Ons 模块）、`upgrade_paths`（升级路线规划可达图邻接边）、`users`（多用户账号、Argon2id 密码、Fernet 密文 TOTP 密钥、OIDC 身份绑定与 `token_version`）、`oidc_states`（PKCE 临时防伪与防重放状态）、`invite_codes`（限时/限次邀请注册凭证表，支持绑定初始角色与启停控制）。

### 3.6 认证、用户管理、OTP 与 ZITADEL PKCE SSO 模块（需求 6）
* **全站强制登录（仅环境变量 `AUTH_REQUIRE_LOGIN=true` 控制）**：未登录用户仅显示登录或首次初始化页；所有商店与规划 API 受 `require_site_access` 守卫保护；该项仅可通过 `.env` 或容器环境变量配置并重启生效，Web UI 仅作只读展示、禁止在线关闭。
* **三种本地账号注册模式（`registration_mode`）**：
  * **关闭注册（`closed`，默认）**：仅管理员可在后台手动新建账号，登录页不展示注册入口；
  * **仅限邀请制注册（`invite_only`）**：管理员在后台「用户管理」生成限时/限次邀请码（支持绑定 `user`/`admin` 角色及复制 `/?invite=CODE` 直达链接），新用户须凭有效邀请码通过 `POST /api/v1/auth/register` 注册并自动消费额度；
  * **开放注册（`open`）**：允许访客在登录页自助注册普通 `user` 账号；
  * 注：三种模式仅约束本地自助注册，ZITADEL OIDC SSO 自动建号继续由 `sso_auto_register` 独立控制。
* **初始管理员引导**：
  * 若配置 `INIT_ADMIN_USERNAME` 与 `INIT_ADMIN_PASSWORD` 且账号表为空，服务启动即自动建号；
  * 未配置时网页首次打开自动展示初始化管理员创建表单（`POST /api/v1/auth/setup`，表非空后永久返回 409）。
* **账号密码 + RFC 6238 TOTP 二步验证**：
  * 密码使用 Argon2id 哈希存储；不存在用户名时执行恒定耗时校验防枚举。
  * 在「个人安全」支持本地由 `segno` 渲染内联 SVG 二维码绑定验证器；启用后密码登录进入 5 分钟有效期 `mfa_challenge` 二次验证步骤。
* **ZITADEL OIDC PKCE 无 Secret 模式**：
  * 采用 Public Client 流程（无需且不发送 `client_secret`）：后端生成随机 `code_verifier`、`S256` `code_challenge`、`state` 与 `nonce` 落库 `oidc_states`（10 分钟单次消费）；
  * 回调时仅以 `grant_type=authorization_code` + `client_id` + `code` + `redirect_uri` + `code_verifier` 向 `token_endpoint` 换取令牌，从 `jwks_uri` 拉取公钥验签 `id_token` 并校验 `iss` / `aud` / `nonce`，必要时请求 `userinfo_endpoint` 补全用户名与邮箱并支持按策略自动注册。
* **防自锁用户管理**：管理员可创建、编辑、禁用、删除账号或强制清除丢失设备的 OTP；系统严禁管理员禁用/删除自己，也不允许删除或降级最后一名活跃管理员。

### 3.7 全量商店富化数据管线与升级路线规划器（需求 5）
* `cj/scraper/rsi_client.py` 与 `enrichment_client.py` 协同定期采集（外部 HTTP 下载不占用高频抓取互斥锁）：
  * RSI 单船商店版本（Standard / Warbond / 10 Year / Best in Show，优先读取 `nativePrice` 税前 USD 美分）与 Add-Ons 模块；
  * RSI 升级商店（`initShipUpgrade` 原始 `price` 税前 USD 美分优先于 `filterShips`）；
  * `/api/v1/store` 为每条在售升级 SKU 随路计算并附带最近可用起始底船推荐（`nearest_from_ships`）；
  * RSI Support 官方借用船清单（Loaners）；
  * `starcitizen.tools` 实装版本号（`addedinversion`）、售卖规律（`pledgeavailability`）与金牌标准进度；
  * Star Citizen Wiki API + UEX API 游戏内购买/租赁 aUEC 价格、保险理赔耗时、原厂武器 DPS/炮塔明细与历史价格轨迹；
  * `sc-cargo.space` 俯视货舱 SVG 网格数据。
* `UpgradePlannerView` 与 `/api/v1/plan` 支持通过 `UPGRADE_PLANNER_ENABLED` / `SystemConfigKey.UPGRADE_PLANNER_ENABLED` 控制（默认 `false` 关闭，开启后在后台映射全图可达边并展示路线规划入口）。

### 3.8 前端三层设计令牌、响应式与无障碍体验（需求 7）
* **三层 CSS 设计令牌架构**（`frontend/src/style.css`）：
  * **Primitive 层**：中性灰阶、琥珀（Warbond 整船与升级高亮 `--wb*`/`--wbccu*`）、弱化中性冷灰（常驻 `10 Year` 低权重底色 `--ten*`）、靛青（`Best in Show` 特殊涂装销售 `--bis*`）、翡翠绿（游戏内 aUEC）、深红（售罄/告警）与间距/圆角/阴影/缓动原始变量；
  * **Semantic 层**：浅色（默认）与夜间模式（`[data-theme="dark"]` 及 `@media (prefers-color-scheme: dark)` 跟随系统）双套语义令牌，全量文本对比度均达到 **WCAG AA >= 4.5:1**（同步更新 `<meta name="theme-color">` 与 `color-scheme`）；
  * **Component 层**：桌面与移动端差异化控件高度（`--control-h: 38px` / `--control-h-mobile: 44px`）、圆角与卡片边界令牌。
* **桌面与移动端体验强化**：
  * 桌面端（`> 720px`）：容器采用宽屏自适应流式布局（`max-width: min(1760px, 100%)`）、底部 `footer p` 全宽平铺、右侧价格/游戏内 `.ed` 卡片统一高度与宽幅；舰船详情改为悬浮弹出模态框，悬浮详情弹窗内的各个信息卡片与 Webhook 规则卡片支持折叠与拖曳/键盘调序（持久化至 `localStorage`），顶部账号下拉菜单支持切换账号与添加账号；
  * 移动端（`<= 720px` / `<= 560px`）：精简顶部导航（隐藏重复 `.desktop-only` 跳转按钮，依赖横向可滚 `.tabs`）、控件触控高度提升至 `44px`（含 `.why::after` 扩展热区）、版本购买区采用等高 **两列网格**，筛选 `<dialog class="sheet">` 与详情/编辑弹窗统一采用底部滑出抽屉并适配 `env(safe-area-inset-bottom)`。
* **Web Interface Guidelines 无障碍规范**：提供 `.skip-link`、全域 `:focus-visible` 与 `:focus-within` 键盘焦距环、模态框 `role="dialog"` + `Escape` 关闭、`aria-live="polite"` 轻提示与实时差价状态、数字 `tabular-nums` 对齐、`prefers-reduced-motion` 尊重以及显式动画属性声明（禁用 `transition: all`）。

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
| 方法 | 路径 | 权限 | 说明 |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/auth/status` | 公开 | 查询系统初始化状态、强制登录开关、注册模式、SSO 状态及当前登录用户 |
| `POST` | `/api/v1/auth/setup` | 仅首次无用户 | 创建系统首个管理员账号并签发会话 Cookie/JWT |
| `POST` | `/api/v1/auth/register` | 公开（受注册策略管控） | 根据当前 `registration_mode` 执行开放注册或校验有效邀请码后创建账号并登录 |
| `POST` | `/api/v1/auth/login` | 公开 | 用户名密码登录（若启用 OTP 则返回 `mfa_required` + 挑战令牌） |
| `POST` | `/api/v1/auth/login/otp` | 公开 | 提交 6 位 TOTP 口令完成二步验证 |
| `POST` | `/api/v1/auth/logout` | 公开 | 清除会话 Cookie 退出登录 |
| `GET` | `/api/v1/auth/sso/authorize` | 公开 | 生成 ZITADEL OIDC PKCE (S256 无 Secret) 授权跳转地址 |
| `POST` | `/api/v1/auth/sso/callback` | 公开 | 无密钥提交 `code` + `code_verifier` 换票、验签 ID Token 并完成登录 |
| `GET` | `/api/v1/auth/me` | 已登录 | 获取当前账号详情 |
| `PUT` | `/api/v1/auth/password` | 已登录 | 修改个人密码并递增 `token_version` 吊销旧会话 |
| `POST` | `/api/v1/auth/otp/setup` | 已登录 | 生成待绑定 TOTP 密钥与本地内联 SVG 二维码 |
| `POST` | `/api/v1/auth/otp/enable` | 已登录 | 验证 6 位动态口令启用个人 TOTP 2FA |
| `POST` | `/api/v1/auth/otp/disable` | 已登录 | 验证 OTP 或密码停用个人 TOTP 2FA |
| `GET` | `/api/v1/status` | 站点访问策略 | 获取在售 CCU 数、WBCCU 数、汉化统计与最近刷新时间 |
| `GET` | `/api/v1/store` | 站点访问策略 | 获取全量商店版本、模块、CCU、游戏内买租/武器/保险/历史与货舱网格 |
| `GET` | `/api/v1/upgrades/{ref}/costs` | 站点访问策略 | 实时向 RSI 查询指定起始船可购升级 SKU 及税前补差价（10 分钟缓存） |
| `GET` | `/api/v1/plan` | 站点访问策略（需开关启用） | 当 `upgrade_planner_enabled=true` 时返回 `/plan` 节点与可达边；关闭时返回 `403` |
| `GET` | `/api/v1/ccus` | 站点访问策略 | 获取在售 CCU 列表与最近起始底船推荐 |
| `POST` | `/api/v1/ccus/refresh` | 管理员 | 手动触发一次商店与 CCU 同步（支持 `enrich=true` 触发全量外部富化） |
| `GET` | `/api/v1/ships` | 站点访问策略 | 获取全量船只目录及中英文映射 |
| `PUT` | `/api/v1/ships/{ship_id}/i18n` | 管理员 | 手动设置或清空单艘舰船的自定义中文译名覆盖 |
| `GET` | `/api/v1/changelog` | 站点访问策略 | 分页查询商店与 CCU 历史上下架及调价日志 |
| `GET/POST/PUT/DELETE` | `/api/v1/webhooks*` | 管理员 | Webhook 规则增删改查、推送日志查询与一键测试 |
| `POST` | `/api/v1/i18n/sync` | 管理员 | 手动触发远程汉化词典拉取与全库检索索引重建 |
| `GET/PUT` | `/api/v1/settings` | 管理员 | 读取或热更新系统动态参数与认证/SSO 配置 |
| `GET/POST/PUT/DELETE` | `/api/v1/users/invites*` | 管理员 | 邀请码列表、生成、启停切换与删除 |
| `GET/POST/PUT/DELETE` | `/api/v1/users*` | 管理员 | 用户列表、新建、编辑、重置 OTP（`POST /users/{id}/reset-otp`）与删除 |

### 目录结构规划
```text
ccujailer/
├── backend/                  # Python 3.14+ 后端
│   ├── alembic/versions/     # 0001 初始库表 + 0002 全量商店富化与认证 + 0003 邀请码表迁移
│   ├── cj/
│   │   ├── api/              # FastAPI 路由、RBAC/会话依赖、强类型 Schema
│   │   ├── services/         # auth_service, ccu_service, i18n_service, webhook_service, setting_service
│   │   ├── repositories/     # ship_repo, ccu_repo, store_repo, changelog_repo, webhook_repo, setting_repo, user_repo
│   │   ├── scraper/          # rsi_client（商店/升级）、enrichment_client（Loaners/Wiki/UEX/货舱）
│   │   ├── resources/        # 内置离线船名中英词典（ship_i18n_builtin.json）
│   │   ├── db/               # ORM 模型、异步引擎、防降级校验与 SQLite 自动备份
│   │   └── core/             # config.py, security.py, logging.py, events.py, net.py, scheduler.py
│   └── main.py               # Lifespan 容器装配与 SPA 托管入口
├── frontend/                 # Vue 3 + Vite + TypeScript 前端工程
│   └── src/components/       # StoreView, ShipRow, ShipDetails, CargoGridSvg, UpgradePlannerView, AuthView, SettingsView 等
├── docs/
│   ├── architecture-overview.md
│   └── rules.md
└── ...
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
export HTTP_PROXY="http://127.0.0.1:7890"
export HTTPS_PROXY="http://127.0.0.1:7890"

# 可选：切换本地 PostgreSQL（不设置则默认使用 ./data/ccujailer.db）
# export DATABASE_URL="postgresql+asyncpg://ccujailer:changeme@127.0.0.1:5432/ccujailer"

# 执行数据库迁移（含防降级保护与 SQLite 自动备份）
python -m cj.db.migration

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
    image: ghcr.io/sinterdial/ccujailer:latest
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
      - ADMIN_TOKEN=
      - AUTH_REGISTRATION_MODE=closed
      - UPGRADE_PLANNER_ENABLED=false
      - HTTP_PROXY=http://host.docker.internal:7890
      - HTTPS_PROXY=http://host.docker.internal:7890
      - ALL_PROXY=
      - NO_PROXY=localhost,127.0.0.1,postgres
      - WEBHOOK_USE_PROXY=false
      - TRANSLATE_API_KEY=
      - LOG_LEVEL=INFO
      - LOG_TO_FILE=false

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
