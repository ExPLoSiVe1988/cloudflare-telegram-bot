<div align="center">
  <strong><a href="README.md">English</a></strong> | <strong><a href="README-FA.md">فارسی</a></strong> | <strong><a href="README-CH.md">中文</a></strong>
</div>
<br>

# Cloudflare 管理机器人 🐳
一个功能强大的 Telegram 机器人，用于完整的 DNS 记录管理和智能监控。配备 **独立监控**、**自动故障转移** 和 **智能负载均衡**，此机器人是您确保最大正常运行时间和最佳性能的一体化解决方案。

---
<div align="center">
  <a href="https://www.youtube.com/watch?v=OOQ9rtHqeFQ" target="_blank">
    <img src="https://img.youtube.com/vi/OOQ9rtHqeFQ/hqdefault.jpg" alt="观看视频教程" width="320">
  </a>
  <p><strong>点击上方图片观看完整的 YouTube 视频教程</strong></p>
</div>

## ✨ 功能

### 🚀 通用与高级监控
*   👁️ **独立监控**: 独立于您的 Cloudflare DNS 监控任何 IP 或域名（如数据库服务器），并即时接收上线/下线警报。
*   🎯 **定向通知系统**:
    *   为**每个故障转移/负载均衡规则或独立监控器**定义特定的警报接收者（个人用户或整个 Telegram 群组）。
    *   警报只发送给负责的团队，防止警报疲劳和信息泛滥。
*   📍 **集中式监控组**: 创建可重用的监控位置组（例如“欧洲”、“亚洲”），并定义故障阈值，轻松分配给任何监控器或策略。
*   🛡️ **自动故障转移 (高可用性)**: 如果主服务器宕机，机器人会自动将 DNS 记录切换到预定义列表中的健康备用 IP。
*   🚦 **高级加权负载均衡**:
    *   **加权 IP 池**: 根据服务器容量按比例分配流量（例如 `1.1.1.1:2`）。
    *   **两种智能算法**: 选择**加权随机**或**加权轮询**（默认）。
*   📊 **高级报告与分析**:
    *   **基于时间的分析**: 负载均衡器报告现在显示每个 IP 处于活动状态的**总时间（小时）和百分比**。
    *   生成自定义时间范围的按需报告，并管理日志保留策略。

### ⚙️ 高级 DNS 与用户管理
*   **🏷️ 域名和记录别名**: 为您的域名和单个记录分配友好的显示名称，以便更轻松地识别和管理。
*   **👥 高级机器人内用户管理**:
    *   **超级管理员** (在 `.env` 中定义) 可以在机器人内部直接管理**普通管理员**。
*   **📤 跨区域移动和复制记录**: 轻松地将 DNS 记录在不同区域之间迁移，甚至可以跨不同的 Cloudflare 账户。
*   **🔄 转换记录类型**: 即时更改记录类型（例如，从 `A` 到 `CNAME`）。
*   **👥 批量操作**: 一次性删除或更改多个记录的 IP。
*   **💾 备份与恢复**: 为您的任何区域创建和恢复 `.json` 备份。

### 🤖 通用机器人与用户体验
*   **📄 域名列表分页**: 如果您管理许多域名，不再需要无休止地滚动。域名列表 (`/list`) 现在已进行分页，导航更轻松快捷。
*   **🚀 快速设置向导**: 全新的分步向导可引导新用户轻松创建他们的第一个监控规则。
*   **📊 `/status` 命令**: 通过一个简单的命令，即时获取所有已配置策略和监控器的健康状况概览。
*   **🧠 自动数据迁移**: 机器人能智能检测旧的 `config.json` 文件（包括旧的通知结构），并在不删除用户数据的情况下自动将其更新到新格式。
*   **🐳 智能安全的安装脚本**: 管理脚本现在会在重新安装时警告您，并提供备份 `config.json` 的选项。
*   **🎨 全新用户界面 (HTML)**, **多账户支持**, 和 **多语言**。

---

<div align="center">
  <h3>💖 支持我们</h3>
  <p>如果此项目对您有帮助，请在 GitHub 上给它一个 Star 来表示您的支持！</p>
  <a href="https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot/stargazers">
    <img src="https://img.shields.io/github/stars/ExPLoSiVe1988/cloudflare-telegram-bot?style=for-the-badge&logo=github&color=FFDD00&logoColor=black" alt="在 GitHub 上 Star 项目">
  </a>
</div>

## 🚀 安装

该机器人设计为在 Docker 中运行。提供的脚本将自动完成整个安装过程。

### 自动安装

您可以安装最新的稳定版本或开发版本。对于大多数用户，建议**安装最新的稳定版本**。

要安装 `main` 分支的最新开发版本，请运行：
```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/main/install.sh)
```
要安装特定的稳定版本，请将 `<VERSION>` 替换为 [Releases](https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot/releases) 页面上的最新版本号（例如 `v4.1.2`）。
```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/<VERSION>/install.sh)
```
脚本提供一个完整的管理菜单：
*   **安装或重新安装机器人 (Install or Reinstall Bot):** 克隆代码仓库，允许您选择所需的版本（最新版或稳定版），提示您进行初始配置（`.env`），并使用 Docker Compose 安装并运行机器人。
*   **从 GitHub 更新机器人 (Update Bot from GitHub):** 从 GitHub 获取最新的代码。要应用更新，您应随后再次运行“安装或重新安装机器人”选项。
*   **编辑核心配置 (.env) (Edit Core Configuration):** 打开一个文本编辑器，让您可以随时修改机器人的基本设置（令牌和管理员）。
*   **查看实时日志 (View Live Logs):** 显示机器人的实时输出，用于监控和调试。
*   **停止/启动机器人 (Stop Bot / Start Bot):** 允许您停止或启动机器人的容器，而不会删除任何数据。
*   **完全移除机器人 (Remove Bot Completely):** 停止并完全删除所有相关的数据、配置文件、容器和镜像。

---

## ⚙️ 配置

安装脚本将为您创建一个 `.env` 文件，其结构如下。您可以稍后使用脚本中的“编辑核心配置”选项进行管理。

*   `TELEGRAM_ADMIN_IDS`: 超级管理员用户 ID 的逗号分隔列表。这些用户拥有完全控制权，包括在机器人内部管理其他管理员的能力。
*   `CF_ACCOUNTS`: 用逗-号分隔的 Cloudflare 账户列表，格式为 `Nickname1:Token1,Nickname2:Token2`。昵称是您为每个账户选择的友好名称。
*   `TELEGRAM_BOT_TOKEN`: 来自 @BotFather 的 Telegram 机器人 API Token。

---

## 🤖 机器人管理

如果您希望直接使用命令，可进入项目目录 (`cloudflare-telegram-bot`) 并使用以下 `docker-compose` 命令：

| 操作                      | 命令                                   |
| :-------------------------- | :---------------------------------------- |
| **查看实时日志**          | `docker-compose logs -f`                  |
| **更新到最新版本**| `docker-compose pull && docker-compose up -d` |
| **停止并移除容器** | `docker-compose down`                     |

---

### Cloudflare API Token 权限
每个 Cloudflare 账户的 API Token 需要以下权限：

| 类型 | 资源 | 访问 |
| :--- | :---     | :---   |
| **Zone** | **DNS**    | `Edit` |
| **Zone** | **Zone**   | `Read` |

访问 [API Tokens](https://dash.cloudflare.com/profile/api-tokens) 创建自定义 Token，应用于“所有域”。

---

### 👨‍💻 开发者与支持
*   GitHub: [@ExPLoSiVe1988](https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot)
*   Telegram: [@H_ExPLoSiVe](https://t.me/H_ExPLoSiVe)
*   频道: [@Botgineer](https://t.me/Botgineer)
---
### 💖 支持 / 捐赠
如果您觉得本项目有用，请考虑支持其开发：

| 加密货币            | 地址                                      |
|:--------------------------|:---------------------------------------------|
| 🟣 **Ethereum (ETH - ERC20)** | `0x157F3Eb423A241ccefb2Ddc120eF152ce4a736eF` |
| 🔵 **Tron (TRX - TRC20)**     | `TEdu5VsNNvwjCRJpJJ7zhjXni8Y6W5qAqk`         |
| 🟢 **Tether (USDT - TRC20)**  | `TN3cg5RM5JLEbnTgK5CU95uLQaukybPhtR`         |

🙏 感谢您的支持！🚀
