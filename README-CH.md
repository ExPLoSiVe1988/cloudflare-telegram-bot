<div align="center">
  <strong><a href="README.md">English</a></strong> | <strong><a href="README-FA.md">فارسی</a></strong> | <strong><a href="README-CH.md">中文</a></strong>
</div>
<br>

# Cloudflare 管理机器人 🐳
一个功能强大的 Telegram 机器人，用于完整的 DNS 记录管理，并配备了**智能监控和自动故障转移系统**。该机器人 7x24 小时监控您的服务器，并在发生故障时智能地将流量重定向到备用服务器，以确保最长的正常运行时间。完全容器化，使用 Docker 部署，快速简便。

---
<div align="center">
  <a href="https://www.youtube.com/watch?v=OOQ9rtHqeFQ" target="_blank">
    <img src="https://img.youtube.com/vi/OOQ9rtHqeFQ/hqdefault.jpg" alt="观看视频教程" width="320">
  </a>
  <p><strong>点击上方图片观看完整的 YouTube 视频教程</strong></p>
</div>

## ✨ 功能

### 🚀 智能监控与高可用性
*   🛡️ **自动故障转移 (Automatic Failover)**: 持续监控您的主服务器，并在检测到停机时自动将 DNS 记录切换到备用 IP。
*   🔄 **自动故障恢复 (Auto-Failback)**: 当主服务器恢复在线并稳定运行一段可配置的时间后，机器人会自动将流量切回。
*   ⚙️ **完整的机器人内管理**: 所有的故障转移策略（添加、编辑、删除、启用/禁用）都可以通过机器人内部的设置菜单进行全面管理。

### ⚙️ 常规 DNS 管理
*   **👥 多管理员支持**: 授权多个 Telegram 用户管理机器人。
*   **🏢 多账号支持**: 管理多个 Cloudflare 账户。
*   **🐳 简单的 Docker 部署**: 使用自动化安装脚本，几分钟即可完成部署。
*   **🌐 多域支持**: 自动检测所选 Cloudflare 账户下的所有域。
*   **👥 批量操作**: 可一次性删除或修改多个记录的 IP 地址。
*   **💾 备份与恢复**: 为任何域创建 `.json` 备份，并可恢复。
*   **🌍 多语言支持**: 完全支持英语和波斯语 (فارسی)。

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

*   `TELEGRAM_ADMIN_IDS`: 用逗号分隔的 Telegram 用户 ID 列表，这些用户被授权使用机器人。
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
