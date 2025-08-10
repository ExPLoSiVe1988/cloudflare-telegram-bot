<!-- Persian README link -->
<div align="right">
  <a href="README-FA.md">🇮🇷 نسخه فارسی</a>
</div>

# Cloudflare Telegram Bot 🐳

A powerful, multi-user, multi-account Telegram bot for managing your Cloudflare DNS records. Fully containerized with Docker for an incredibly simple and fast deployment.

---
🎥 [Watch the full installation and setup video on YouTube.](https://www.youtube.com/watch?v=OOQ9rtHqeFQ)
---

## ✨ Features
*   **👥 Multi-Admin Support:** Authorize multiple Telegram users to manage the bot.
*   **🏢 Multi-Account Support:** Manage DNS records across multiple Cloudflare accounts, each with its own API token.
*   **🐳 Easy Docker Deployment:** Get the bot running in minutes with a fully automated installation script.
*   **🌐 Multi-Zone Support:** Automatically detects all zones within the selected Cloudflare account.
*   **👥 Bulk Actions:** Select multiple records to delete or change their IP address all at once.
*   **💾 Backup & Restore:** Create and restore `.json` backups for any of your zones.
*   **🌍 Multi-Language:** Full support for English and Persian (فارسی).

---

<div align="center">
  <h3>💖 Show Your Support</h3>
  <p>If this project has been helpful, please give it a star on GitHub to show your appreciation!</p>
  <a href="https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot/stargazers">
    <img src="https://img.shields.io/github/stars/ExPLoSiVe1988/cloudflare-telegram-bot?style=for-the-badge&logo=github&color=FFDD00&logoColor=black" alt="Star the project on GitHub">
  </a>
</div>

---

## 🚀 Installation

This bot is designed to run with Docker. The provided script automates the entire setup process.

### Prerequisites
*   A Linux server (Ubuntu/Debian recommended).
*   `git` and `curl` (usually pre-installed).

### Automated Installation
Run the following command in your server's terminal. The script will handle everything, including installing Docker if it's not present.

```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/main/install.sh)
```
The script provides a menu to:
*   **Install Bot:** Clones the repository, prompts you to add multiple admins and Cloudflare accounts, and starts the bot using Docker Compose.
*   **Update Bot:** Pulls the latest code from GitHub, rebuilds the Docker image, and asks if you want to update your configuration.
*   **Edit Configuration:** A dedicated menu to add or remove Admins and Cloudflare accounts at any time.
*   **View Live Logs:** Shows the real-time output of the bot for monitoring.
*   **Remove Bot Completely:** Stops the container and removes all associated data, containers, and images.

---

## ⚙️ Configuration

The installation script will create an `.env` file for you with the following structure. You can manage it later using the "Edit Configuration" option in the script.

*   `TELEGRAM_ADMIN_IDS`: A comma-separated list of numerical Telegram user IDs who are authorized to use the bot.
*   `CF_ACCOUNTS`: A comma-separated list of your Cloudflare accounts in the format `Nickname1:Token1,Nickname2:Token2`. The nickname is a friendly name you choose for each account.
*   `TELEGRAM_BOT_TOKEN`: The API token for your Telegram bot from @BotFather.

---

## 🤖 Bot Management

If you prefer to use commands directly, `cd` into the project directory (`cloudflare-telegram-bot`) and use these `docker-compose` commands:

| Action | Command |
| :--- | :--- |
| **View Live Logs** | `docker-compose logs -f` |
| **Update to the Latest Version** | `docker-compose pull && docker-compose up -d` |
| **Stop and Remove Container** | `docker-compose down` |

---

### Cloudflare API Token Permissions
For each Cloudflare account, your API token needs the following permissions:

| Type | Resource | Access |
| :--- | :--- | :--- |
| **Zone** | **DNS** | `Edit` |
| **Zone** | **Zone** | `Read` |

Go to [API Tokens](https://dash.cloudflare.com/profile/api-tokens) and create a custom token with these two permissions applied to `All zones`.

---

## 🔄 Migration Guide for Existing Users (from PM2 version)

This version introduces a complete shift to **Docker** and a new multi-account configuration. The old setup is not compatible. To upgrade, you must perform a clean installation.

**Step 1: Completely Remove the Old Version**
```bash
pm2 delete cfbot && pm2 save && cd ~ && rm -rf cloudflare-telegram-bot
```

**Step 2: Install the New Version**
Simply run the new universal installation command:
```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/main/install.sh)
```
The script will guide you through the new multi-admin and multi-account setup.

---
### 👨‍💻 Developer & Support
*   GitHub: [@ExPLoSiVe1988](https://www.google.com/url?sa=E&q=https%3A%2F%2Fgithub.com%2FExPLoSiVe1988%2Fcloudflare-telegram-bot)
*   Telegram: [@H_ExPLoSiVe](https://t.me/H_ExPLoSiVe)
---
### 💖 Support / Donate
If you find this project useful, please consider supporting its development:

| Cryptocurrency | Address |
|:---|:---|
| 🟣 **Ethereum (ETH - ERC20)** | `0x157F3Eb423A241ccefb2Ddc120eF152ce4a736eF` |
| 🔵 **Tron (TRX - TRC20)** | `TEdu5VsNNvwjCRJpJJ7zhjXni8Y6W5qAqk` |
| 🟢 **Tether (USDT - TRC20)** | `TN3cg5RM5JLEbnTgK5CU95uLQaukybPhtR` |

🙏 Thank you for your support! 🚀
