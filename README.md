# CCUJailer

![GitHub Repo stars](https://img.shields.io/github/stars/Sinterdial/CCUJailer?style=for-the-badge)
![GitHub forks](https://img.shields.io/github/forks/Sinterdial/CCUJailer?style=for-the-badge)
![GitHub contributors](https://img.shields.io/github/contributors/Sinterdial/CCUJailer?style=for-the-badge)
![GitHub repo size](https://img.shields.io/github/repo-size/Sinterdial/CCUJailer?style=for-the-badge)
![GitHub issues](https://img.shields.io/github/issues/Sinterdial/CCUJailer?style=for-the-badge)
![Docker Image](https://img.shields.io/badge/GHCR-ghcr.io%2Fsinterdial%2Fccujailer-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue?style=for-the-badge)
[![LINUX DO](https://img.shields.io/badge/LINUX_DO-Community-1c1c1e?style=for-the-badge&logo=linux&logoColor=white)](https://linux.do)

**CCUJailer** 是一款专为《星际公民》（Star Citizen）玩家打造的自托管 **RSI 商店监控、升级（CCU / Warbond CCU）差价分析、升级路线规划与可编程 Webhook 通知服务**。项目基于开源项目 `DieLaughing/pledgewatch`（AGPL-3.0）进行前后端分离架构重构，完整复刻原版桌面端与移动端商店体验，并新增全站认证鉴权、ZITADEL OIDC PKCE 无密钥 SSO、双语词典流水线与多渠道 Webhook 推送能力。

---

## 核心特性

- **完整复刻 Pledge Watch 桌面与移动体验**：
  - 支持单船在售全部版本卡片（`Standard` / `Warbond` / 紫色 `10 Year` / `Best in Show` 随附保险与涂装/模块）、游戏内 `aUEC` 购买与租赁价格绿色卡片（按自定义每小时 `aUEC` 计算肝度）、Wiki 售卖规律标签（`仅限大促` / `限量发售` / `即将退市`）与金牌标准进度。
  - 点击「详情」展开：游戏内终端购买/租赁清单、保险索赔与加急费用、原厂武器/炮塔/导弹 DPS 汇总表、Erkul 配装器外链、历史价格表以及 `sc-cargo.space` 俯视货舱 SVG 网格模型。
  - 原版 `Filters` 弹出面板/手机底部抽屉筛选器，支持「我拥有的底船」实时向 RSI 升级接口核实补差价，以及按价格、实装版本、游戏内价格、性价比 `aUEC/$`、主驾火力等多维度排序；内置 **升级路线规划器** 自动寻找最优连跳链条。
  - 支持 **浅色 / 夜间 / 跟随系统** 三态外观切换，以及 **中英双语 / 仅中文 / 仅英文** 一键切换。
- **用户认证、OTP 与 ZITADEL PKCE SSO**：
  - 默认开启全站强制登录（仅通过环境变量 `AUTH_REQUIRE_LOGIN` 控制，Web UI 不可修改），区分 `admin` 管理员与 `user` 普通用户角色。
  - 支持首个管理员网页引导初始化或环境变量预设、Argon2id 密码登录、RFC 6238 TOTP 二步验证（内置本地 SVG 二维码绑定）及完整用户管理（含防最后管理员自锁保护）。
  - 原生支持 **ZITADEL / OIDC Authorization Code + PKCE (`S256`) 无 `client_secret` 公共客户端** 单点登录。
- **实时 CCU 监控与可编程 Webhook 引擎**：
  - 精确区分历史全量 SKU 与实际在售 CCU SKU（`filterShips` 过滤），新上架与降价通过异步事件总线触发推送，具备连续缺失防抖与暴跌保护。
  - 支持 **标准通用 JSON**、**飞书交互卡片**、**企业微信 Markdown**、**Discord Embed** 与 **自定义 JSON 模板**，敏感请求头全链路脱敏并保留修改占位符。
- **四级船名中文化流水线**：管理员人工覆盖（可在「系统设置 -> 舰船词典」直接编辑） -> 远程社区 JSON / `global.ini` 词典同步 -> 内置 200+ 全系词典与家族前缀推导 -> 可选 OpenAI 兼容自动翻译兜底。
- **双数据库与出站代理**：默认 SQLite（WAL + 在线备份防降级）或一键切换 PostgreSQL；所有外部 HTTP 请求统一经由代理路由管理器发出，原生支持 `NO_PROXY` CIDR 网段匹配。

---

## 快速开始（Docker Compose）

### 1. SQLite 默认模式（开箱即用）

默认拉取多架构镜像 `ghcr.io/sinterdial/ccujailer:latest`（或固定版本标签 `ghcr.io/sinterdial/ccujailer:0.1.0`，支持 `linux/amd64` 与 `linux/arm64`）。容器以非 root 用户（UID `1000`）运行，Linux 宿主机挂载 `./data` 前请确保目录归属权一致：

```bash
cp .env.example .env
mkdir -p data && chown -R 1000:1000 data
docker compose pull
docker compose up -d
```

服务启动后访问：`http://localhost:8794`（首次无用户且未设置 `INIT_ADMIN_*` 时，网页将自动引导您创建首个管理员账号；健康检查端点：`http://localhost:8794/healthz`）。

### 2. PostgreSQL 模式

编辑 `docker-compose.yml` 取消 `DATABASE_URL=postgresql+asyncpg://...` 注释（并建议修改默认数据库密码），随后执行：

```bash
docker compose --profile postgres up -d
```

---

## 环境变量说明

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/ccujailer.db` | 异步数据库连接字符串，支持 `sqlite+aiosqlite://` 与 `postgresql+asyncpg://` |
| `AUTO_MIGRATE` | `true` | 启动前是否自动执行防降级校验与 Alembic 迁移 |
| `PORT` / `HOST` | `8794` / `0.0.0.0` | HTTP 服务监听端口与绑定地址 |
| `AUTH_REQUIRE_LOGIN` | `true` | 是否开启全站强制登录（仅支持环境变量配置并重启生效，Web UI 不可修改） |
| `AUTH_SECRET_KEY` | `""`（自动持久化） | 会话 JWT 签名及 TOTP 密钥加密主密钥，留空自动持久化至 `data/.auth_secret` |
| `INIT_ADMIN_USERNAME` / `INIT_ADMIN_PASSWORD` | `""` | 可选：首次无用户启动时自动创建的初始管理员凭据 |
| `OIDC_ENABLED` / `OIDC_PROVIDER_NAME` | `false` / `ZITADEL` | SSO 默认开关与按钮显示名称（支持界面热更新） |
| `OIDC_ISSUER_URL` / `OIDC_CLIENT_ID` | `""` | ZITADEL / OIDC Issuer 与 PKCE Public Client ID（无需 Secret） |
| `ADMIN_TOKEN` | `""` | 可选向后兼容静态管理令牌（建议留空，优先使用账号登录） |
| `HTTP_PROXY` / `HTTPS_PROXY` | `""` | 出站 HTTP/HTTPS 代理（容器内访问宿主机代理可用 `http://host.docker.internal:7890`） |
| `ALL_PROXY` / `NO_PROXY` | `""` / `localhost,127.0.0.1` | 通用代理（支持 `socks5://`）与代理绕过列表（支持 CIDR 网段） |
| `WEBHOOK_USE_PROXY` | `false` | Webhook 推送请求是否复用容器代理 |
| `TRANSLATE_API_KEY` | `""` | 可选的 OpenAI 兼容接口密钥，用于在设置中启用新船自动翻译兜底 |
| `LOG_LEVEL` / `LOG_TO_FILE` | `INFO` / `false` | 日志输出级别与是否写入 `./data/logs/ccujailer.log` 轮转文件 |

---

## 本地开发与调试

### 后端（Python 3.14 + uv）

```bash
cd backend
uv sync --locked

# 执行数据库安全迁移
uv run --locked --no-sync python -m cj.db.migration

# 启动热重载 API 服务（默认监听 8794）
uv run --locked --no-sync uvicorn main:app --reload --host 0.0.0.0 --port 8794
```

运行后端质量检查与测试：

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
npm run dev
# 访问 http://localhost:5173（/api 自动代理至 http://127.0.0.1:8794）
```

类型检查与生产构建：

```bash
cd frontend
npx tsc --noEmit
npx vue-tsc --noEmit
npm run build
```

---

## 开源协议与致谢（Acknowledgements）

- 本项目遵循 **AGPL-3.0-or-later** 开源协议，详见 [LICENSE](LICENSE)。
- **上游与数据源致谢**：界面布局、RSI 商店及 Upgrade GraphQL 逻辑衍生重构自开源项目 [DieLaughing/pledgewatch](https://github.com/DieLaughing/pledgewatch)（AGPL-3.0）；游戏内数据与货舱网格源自 Star Citizen Wiki、UEX 与 sc-cargo.space，详细许可说明见 [THIRD_PARTY.md](THIRD_PARTY.md)。
- **免责声明**：CCUJailer 为非官方开源工具，与 Cloud Imperium Games 或 Roberts Space Industries 无关；购买前请务必在 RSI 官网核对实时价格与规则。

### AI 使用声明

本项目在架构重构规划、认证与单点登录设计、前后端模块化实现、数据库平滑迁移与自动化测试编写过程中使用了 **Gemini** 模型及 **Antigravity** 辅助开发，所有代码、迁移脚本与测试均经过验证把关。
