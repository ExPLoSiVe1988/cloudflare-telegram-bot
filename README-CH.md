<!-- English README link -->
<div align="left">
  <a href="README.md">🇬🇧 English Version</a>
</div>

# Cloudflare 管理机器人 🐳

一个强大的多用户、多账号 Telegram 机器人，用于管理您的 Cloudflare DNS 记录。完全容器化，使用 Docker 部署，快速简便。

---
<div align="center">
  <a href="https://www.youtube.com/watch?v=OOQ9rtHqeFQ" target="_blank">
    <img src="https://img.youtube.com/vi/OOQ9rtHqeFQ/hqdefault.jpg" alt="观看视频教程" width="320">
  </a>
  <p><strong>点击上方图片观看完整的 YouTube 视频教程</strong></p>
</div>

## ✨ 功能
*   **👥 多管理员支持：** 允许多个 Telegram 用户管理机器人。
*   **🏢 多账号支持：** 管理多个 Cloudflare 账户，每个账户拥有独立的 API Token。
*   **🐳 简单的 Docker 部署：** 使用自动化安装脚本，几分钟即可完成部署。
*   **🌐 多域支持：** 自动检测所选 Cloudflare 账户下的所有域。
*   **👥 批量操作：** 可一次性删除或修改多个记录的 IP 地址。
*   **💾 备份与恢复：** 为任何域创建 `.json` 备份，并可恢复。
*   **🌍 多语言支持：** 完全支持英语和中文。

---
<div align="center">
  <h3>💖 支持我们</h3>
  <p>如果此项目对您有帮助，请在 GitHub 上给它一个 Star 来表示您的支持！</p>
  <a href="https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot/stargazers">
    <img src="https://img.shields.io/github/stars/ExPLoSiVe1988/cloudflare-telegram-bot?style=for-the-badge&logo=github&color=FFDD00&logoColor=black" alt="在 GitHub 上 Star 项目">
  </a>
</div>

---

## 🚀 安装

该机器人设计为在 Docker 中运行。提供的脚本将自动完成整个安装过程。

### 先决条件
*   Linux 服务器（推荐 Ubuntu/Debian）。
*   已安装 `git` 和 `curl`（通常默认安装）。

### 自动安装
在服务器终端运行以下命令，脚本会处理一切，包括安装 Docker（如果未安装）。

```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/main/install.sh)
```

脚本提供以下菜单：
*   **安装机器人：** 克隆仓库，添加多个管理员和 Cloudflare 账户，并通过 Docker Compose 启动机器人。
*   **更新机器人：** 从 GitHub 拉取最新代码，重建 Docker 镜像，并提示是否更新配置。
*   **编辑配置：** 随时添加或删除管理员和 Cloudflare 账户。
*   **查看实时日志：** 监控机器人的实时输出。
*   **完全移除机器人：** 停止容器并删除所有关联数据、容器和镜像。

---

## ⚙️ 配置

安装脚本将为您创建一个 `.env` 文件，可通过“编辑配置”菜单进行管理。

*   `TELEGRAM_ADMIN_IDS`: 用逗号分隔的 Telegram 用户 ID 列表，这些用户被授权使用机器人。
*   `CF_ACCOUNTS`: 用逗号分隔的 Cloudflare 账户列表，格式为 `昵称1:Token1,昵称2:Token2`。
*   `TELEGRAM_BOT_TOKEN`: 来自 @BotFather 的 Telegram 机器人 API Token。

---

## 🤖 机器人管理

如果您希望直接使用命令，可进入项目目录 (`cloudflare-telegram-bot`) 并使用以下 `docker-compose` 命令：

| 操作 | 命令 |
| :--- | :--- |
| **查看实时日志** | `docker-compose logs -f` |
| **更新到最新版本** | `docker-compose pull && docker-compose up -d` |
| **停止并移除容器** | `docker-compose down` |

---

### Cloudflare API Token 权限
每个 Cloudflare 账户的 API Token 需要以下权限：

| 类型 | 资源 | 访问 |
| :--- | :--- | :--- |
| **Zone** | **DNS** | `Edit` |
| **Zone** | **Zone** | `Read` |

访问 [API Tokens](https://dash.cloudflare.com/profile/api-tokens) 创建自定义 Token，应用于“所有域”。

---

## 🔄 旧用户迁移指南（从 PM2 版本）

此版本引入 **Docker** 和全新的多账户配置，旧设置不兼容。升级需要全新安装。

**步骤 1：完全删除旧版本**
```bash
pm2 delete cfbot && pm2 save && cd ~ && rm -rf cloudflare-telegram-bot
```

**步骤 2：安装新版本**
运行新的通用安装命令：
```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/main/install.sh)
```
脚本将引导您完成新的多管理员、多账户设置。

---
### 👨‍💻 开发者与支持
*   GitHub: [@ExPLoSiVe1988](https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot)
*   Telegram: [@H_ExPLoSiVe](https://t.me/H_ExPLoSiVe)
*   频道: [@Botgineer](https://t.me/Botgineer)
---
### 💖 支持 / 捐赠
如果您觉得本项目有用，请考虑支持其开发：

| 加密货币 | 地址 |
|:---|:---|
| 🟣 **Ethereum (ETH - ERC20)** | `0x157F3Eb423A241ccefb2Ddc120eF152ce4a736eF` |
| 🔵 **Tron (TRX - TRC20)** | `TEdu5VsNNvwjCRJpJJ7zhjXni8Y6W5qAqk` |
| 🟢 **Tether (USDT - TRC20)** | `TN3cg5RM5JLEbnTgK5CU95uLQaukybPhtR` |

🙏 感谢您的支持！🚀

