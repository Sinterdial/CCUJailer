# CCUJailer

![GitHub Repo stars](https://img.shields.io/github/stars/Sinterdial/CCUJailer?style=for-the-badge)
![GitHub forks](https://img.shields.io/github/forks/Sinterdial/CCUJailer?style=for-the-badge)
![GitHub contributors](https://img.shields.io/github/contributors/Sinterdial/CCUJailer?style=for-the-badge)
![GitHub repo size](https://img.shields.io/github/repo-size/Sinterdial/CCUJailer?style=for-the-badge)
![GitHub issues](https://img.shields.io/github/issues/Sinterdial/CCUJailer?style=for-the-badge)
![Docker Image](https://img.shields.io/badge/GHCR-ghcr.io%2Fsinterdial%2Fccujailer-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue?style=for-the-badge)
[![LINUX DO](https://img.shields.io/badge/LINUX_DO-Community-1c1c1e?style=for-the-badge&logo=linux&logoColor=white)](https://linux.do)

单容器自托管的《星际公民》商店与 WBCCU 监控服务——免人工盯盘，实时追踪单船与升级折扣、自动计算补差价并推送至飞书/企微/Discord。

> 术语说明：**CCU** 指《星际公民》（Star Citizen）官网商店的舰船升级包（Cross-Chassis Upgrade），**WBCCU** 指限现金支付的 Warbond 折扣升级包。
>
> **项目状态与边界**：活跃维护中的个人开源项目（`v0.x` 早期迭代阶段）· **[AGPL-3.0-or-later](LICENSE)** 协议 · 非官方社区工具（与 CIG / RSI 无关），前后端分离重构自 [DieLaughing/pledgewatch](https://github.com/DieLaughing/pledgewatch)。仅轮询 RSI 公开商店与升级目录，**不登录 RSI 账号、不存储 RSI 凭据、不提供自动下单或抢购**；单容器模块化架构，**无需 Redis、Celery 或外部消息队列**。

---

## 为什么选择自托管 CCUJailer

| 维度 | 手动逛 RSI / 公共静态站（如上游 Pledge Watch） | 自托管 CCUJailer |
| :--- | :--- | :--- |
| **折扣监控与通知** | 需人工刷新页面，错过限时 WBCCU | 后台定时巡检 + 短时波动防抖与暴跌保护，自动推送飞书交互卡片 / 企业微信 Markdown / Discord Embed / 自定义 JSON |
| **中文与双语检索** | 仅英文原名，新船与厂商筛选门槛高 | 四级中文译源流水线（人工覆盖 → `StarCitizenToolBox` 简体 `global.ini` + 国内镜像 → 内置词典 → 可选 OpenAI 兜底），支持中英混搜 |
| **多账号与同步** | 无用户体系，筛选/已囤 CCU 仅存于单浏览器 | 默认全站强制登录（Argon2id + TOTP 含 8 组恢复码 + Passkey + OIDC PKCE），偏好/收藏/已囤 CCU 跨设备同步 |
| **部署与网络适配** | 依赖外部代理或无统一持久化 | 单容器支持 `linux/amd64` 与 `linux/arm64`，内置 SQLite（WAL + 在线防降级备份）或 PostgreSQL，支持 HTTP/HTTPS/SOCKS5 代理与 `NO_PROXY` CIDR |

---

## 1 分钟快速开始（Docker Compose）

### 前置要求

- 已安装 **Docker** 与 **Docker Compose v2**（默认镜像 `ghcr.io/sinterdial/ccujailer:latest` 支持 `linux/amd64` 与 `linux/arm64`；如需锁定版本，可改为仓库 Releases 中已发布的标签，如 `v0.1.0`）。
- 若宿主机无法直连 `robertsspaceindustries.com`（例如中国大陆网络环境），请先在 `docker-compose.yml` 中取消 `HTTP_PROXY` / `HTTPS_PROXY` 注释并指向可用代理。
- 若需在生产环境使用 **Passkey（WebAuthn）**，请确保通过 **HTTPS 域名** 或 `localhost` 访问。

### 启动服务（SQLite 默认模式）

> 注意：`docker-compose.yml` 直接通过 `environment:` 读取配置，**不会加载仓库根目录的 `.env` 文件**（`.env` 仅用于本地非 Docker 开发）；如需改端口、代理或认证参数，请直接编辑 `docker-compose.yml`。

```bash
git clone https://github.com/Sinterdial/CCUJailer.git
cd CCUJailer
mkdir -p data && sudo chown -R 1000:1000 data   # Linux 宿主机需匹配容器 UID 1000；Windows PowerShell 请改用 mkdir data
docker compose pull
docker compose up -d
```

### 验证启动与日常更新

```bash
curl.exe http://localhost:8794/healthz   # Linux/macOS 可直接用 curl
# 预期输出：{"status":"ok"}
```

确认返回 `{"status":"ok"}` 后，在浏览器打开 `http://localhost:8794`。若未预设 `INIT_ADMIN_*` 环境变量，页面将自动进入初始化引导创建首个管理员账号。后续升级版本时建议先备份 `./data` 目录，再执行 `docker compose pull && docker compose up -d`。

### 可选：使用 PostgreSQL 数据库

在 `docker-compose.yml` 中取消 `DATABASE_URL=postgresql+asyncpg://...` 的注释，并按需修改 `POSTGRES_PASSWORD`（**须同步更新 `DATABASE_URL` 连接串中的密码**，默认示例均为 `changeme`），随后启动：

```bash
docker compose --profile postgres up -d
```

---

## 核心能力

- **商店目录、版本卡片与详情分析**：
  - 完整呈现单船各在售版本卡片（`Standard`、`Warbond`、灰蓝低强调 `10 Year`、紫色 `Best in Show` 等及随附保险/涂装/模块）、游戏内货币 `aUEC` 买/租与肝度估算、Wiki 售卖规律标签与金牌标准进度（Gold Standard）。
  - 在售卡片与详情页同时展示**最近可升级自节点**（绿底区分 **WBCCU** 与灰底 **普通 CCU**）；提供独立 **「我需要补多少？」** 实时补差价弹窗，自动关联个人已囤 CCU 并实时向 RSI 核实差价，另可选启用**升级路线规划器**计算最优连跳方案。
  - 支持分页浏览、跨 Tab 滚动位置记忆与移动端拖拽排序；详情弹窗涵盖终端买/租清单、保险索赔与加急费用、原厂武器/炮塔/导弹 DPS 汇总、Erkul 配装器外链、历史价格与 `sc-cargo.space` 俯视货舱 SVG。
- **精细化变动追踪与可编程 Webhook**：
  - 精确区分历史全量 SKU 与实际在售 CCU SKU，细粒度记录 `WBCCU 下架`、`普通 CCU 下架`、`整船下架` 与价格波动；基于异步事件总线触发推送，敏感请求头全链路脱敏并支持自定义 JSON 模板。
- **账号鉴权与多端偏好同步**：
  - 默认开启全站强制登录（`AUTH_REQUIRE_LOGIN` 仅允许通过环境变量控制），区分 `admin` 与 `user` 角色并支持 `closed` / `invite_only` / `open` 三种注册模式。
  - 支持 Argon2id 密码登录、RFC 6238 TOTP 二步验证（本地 SVG 二维码 + 8 组一次性恢复码）、Passkey（WebAuthn / FIDO2）无密码通行密钥与 OIDC Authorization Code + PKCE (`S256`) 无密钥 SSO；支持浅色/夜间/跟随系统、中英双语/仅中文/仅英文、个人默认筛选与已囤 CCU 跨端同步。
- **四级中文译源流水线**：
  - 管理员人工覆盖（「系统设置 -> 中文译源」）→ `StarCitizenToolBox/LocalizationData` 简体中文 `global.ini`（支持一键切换国内镜像 `git.scbox.xkeyc.cn`）→ 内置全系词典与家族前缀推导 → 可选 OpenAI 兼容自动翻译兜底。

---

## 常用配置

下表列出最常调整的静态环境变量；完整变量清单见 [.env.example](.env.example)，系统架构与模块职责见 [docs/architecture-overview.md](docs/architecture-overview.md)。

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/ccujailer.db` | 异步数据库连接串，支持 `sqlite+aiosqlite://` 与 `postgresql+asyncpg://` |
| `AUTO_MIGRATE` | `true` | 启动前是否自动执行防降级校验与 Alembic 数据库迁移 |
| `HOST` / `PORT` | `0.0.0.0` / `8794` | HTTP 服务绑定地址与监听端口 |
| `AUTH_REQUIRE_LOGIN` | `true` | 是否开启全站强制登录（仅环境变量生效，Web UI 不可修改） |
| `AUTH_SECRET_KEY` | `""`（自动持久化） | 会话 JWT 签名与 TOTP 密钥加密主密钥，留空自动保存至 `data/.auth_secret` |
| `INIT_ADMIN_USERNAME` / `INIT_ADMIN_PASSWORD` | `""` | 可选：首次无用户启动时自动创建管理员账号 |
| `OIDC_ENABLED` / `OIDC_ISSUER_URL` / `OIDC_CLIENT_ID` | `false` / `""` / `""` | OIDC PKCE 公共客户端开关、Issuer 地址与 Client ID（可在界面热更新） |
| `PASSKEY_ENABLED` / `PASSKEY_RP_ID` / `PASSKEY_ORIGIN` | `true` / `""` / `""` | Passkey 开关、依赖方域名与允许 Origin（留空按请求自动推断） |
| `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `NO_PROXY` | `""` / `localhost,127.0.0.1` | 出站 HTTP/HTTPS/SOCKS5 代理与绕过列表（`NO_PROXY` 支持 CIDR 网段） |
| `WEBHOOK_USE_PROXY` | `false` | Webhook 推送请求是否复用容器出站代理 |
| `TRANSLATE_API_KEY` | `""` | 可选：OpenAI 兼容自动翻译兜底密钥 |
| `LOG_LEVEL` / `LOG_TO_FILE` | `INFO` / `false` | 日志级别与是否开启本地轮转日志文件（`data/logs/ccujailer.log`） |

> 提示：Webhook 推送地址与模板可在管理员顶栏 **「推送设置」** 配置；巡检/同步间隔、首页默认筛选与排序、升级路线开关、中文译源、OIDC 与 Passkey 域名均可在部署后于 Web UI 热更新（路径：`系统设置 -> 全局设置` / `中文译源` / `OIDC` 与 `账户设置 -> 安全与隐私` / `个人偏好`）。

---

## 本地开发与测试

### 后端（Python 3.14 + `uv`）

```bash
cp .env.example .env   # 可选：按需填写本地开发环境变量
cd backend
uv sync --locked
uv run --locked --no-sync python -m cj.db.migration
uv run --locked --no-sync uvicorn main:app --reload --host 0.0.0.0 --port 8794
```

代码检查与自动化测试：

```bash
cd backend
uv lock --check
uv run --locked --no-sync ruff check .
uv run --locked --no-sync pytest -v
```

### 前端（Node.js 22 + Vue 3 + TypeScript）

```bash
cd frontend
npm ci
npm run dev   # http://localhost:5173（/api 与 /healthz 自动代理至 127.0.0.1:8794）
```

类型检查与生产构建（构建产物输出至 `frontend/dist`，可直接由后端 `:8794` 托管）：

```bash
cd frontend
npm run typecheck
npm run build
```

---

## 开源协议与致谢（Acknowledgements）

- **开源协议**：本项目遵循 **[AGPL-3.0-or-later](LICENSE)**，请勿将修改后的托管服务闭源商用。
- **上游与数据源致谢**：界面布局、RSI 商店与 Upgrade GraphQL 逻辑衍生重构自 [DieLaughing/pledgewatch](https://github.com/DieLaughing/pledgewatch)（AGPL-3.0）；游戏内数据与货舱网格源自 Star Citizen Wiki、UEX、sc-cargo.space 与 StarCitizenToolBox，详细第三方许可声明见 [THIRD_PARTY.md](THIRD_PARTY.md)。
- **免责声明**：CCUJailer 为非官方社区开源工具，与 Cloud Imperium Games 或 Roberts Space Industries 无关；实际购买前请务必以 RSI 官网实时价格与规则为准。

### AI 使用声明

本项目在架构重构设计、认证鉴权实现、数据库平滑迁移、前后端功能开发、文档梳理与自动化测试编写过程中使用了 **Gemini** 模型及 **Antigravity** 辅助开发，所有代码、配置、迁移脚本与测试均经过人工及自动化验证把关。
