<!-- Persian README link -->
<div align="right">
  <a href="README-FA.md">🇮🇷 نسخه فارسی</a>
</div>

# Cloudflare Telegram Bot

A powerful and professional Telegram bot that acts as a complete control panel for managing your Cloudflare DNS records. Now fully containerized with Docker for easy, fast, and isolated deployment.

---

## ✨ Features
*   **🐳 Easy Docker Deployment:** Get the bot running in minutes with a fully automated installation script.
*   **🌐 Multi-Zone Support:** Automatically detects all zones in your account, allowing you to manage multiple domains.
*   **🚀 Fully Asynchronous:** Built with `httpx` for a fast and non-blocking interface.
*   **👥 Bulk Actions:** Select multiple records to delete or change their IP address all at once.
*   **🔎 Instant Search & Pagination:** Quickly find any record and navigate through long lists with ease.
*   **☁️ Proxy Toggle:** Change the Cloudflare proxy status (orange/grey cloud) with a single tap.
*   **🔄 Refresh Button:** Instantly reload the record list from Cloudflare to see external changes.
*   **💾 Backup & Restore:** Create and restore `.json` backups for any of your zones.
*   **🌍 Multi-Language:** Full support for English and Persian (فارسی).

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
*   **Install Bot:** Clones the repository, asks for your API tokens, and starts the bot using Docker Compose.
*   **Update Bot:** Pulls the latest code from GitHub and rebuilds the Docker image.
*   **View Live Logs:** Shows the real-time output of the bot for monitoring.
*   **Remove Bot Completely:** Stops the container and removes all associated data, containers, and images.

---
### Bot Commands
| Command | Description |
|:---|:---|
| `/start` | Starts the bot and shows the language selection menu |
| `/language` | Select your preferred language (English/Persian) |
| `/list` | Enters the main menu and displays the paginated list of records |
| `/search`| Searches for a record by its name |
| `/bulk` | Enters Bulk Actions mode for multiple selections |
| `/add` | Starts the process of adding a new subdomain |
| `/backup` | Backs up all DNS records |
| `/restore`| Restores records from a backup file |
##  migrating from the previous version?

➡️ **Please see the migration guide at the bottom of this file.**

---

## 🤖 Bot Management

If you prefer to use commands directly, `cd` into the project directory (`cloudflare-telegram-bot`) and use these `docker-compose` commands:

| Action | Command |
| :--- | :--- |
| **View Live Logs** | `docker-compose logs -f` |
| **Update** | `docker-compose up -d` |
| **Stop and Remove Container** | `docker-compose down` |

---

### Cloudflare API Token Permissions
For the bot to function correctly, your API token needs the following permissions:

| Type | Resource | Access |
| :--- | :--- | :--- |
| **Zone** | **DNS** | `Edit` |
| **Zone** | **Zone** | `Read` |

Go to [API Tokens](https://dash.cloudflare.com/profile/api-tokens) and create a custom token with these two permissions applied to `All zones`.

---

## 🔄 Migration Guide for Existing Users (from PM2 version)

This version introduces a complete shift from a PM2-based setup to a much more stable **Docker-based deployment**. The old "Update Bot" option is not compatible.

To upgrade, you must perform a clean installation. Please follow these simple steps:

**Step 1: Completely Remove the Old Version**

First, run your old installation script to completely remove the PM2 version.
```bash
# Navigate to your old bot's directory
cd cloudflare-telegram-bot 
# Run the old script
bash install.sh
# Choose the "Delete Bot" option from the menu.
```
If you don't have the old script, you can manually remove it with these commands:
```bash
pm2 delete cfbot && pm2 save && cd ~ && rm -rf cloudflare-telegram-bot
```

**Step 2: Install the New Docker Version**

Now, simply run the new universal installation command from your home directory (`~`):
```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/main/install.sh)
```
The new script will guide you through the fresh installation process. Thank you for upgrading!

---
### 👨‍💻 Developer & Support
*   GitHub: [@ExPLoSiVe1988](https://github.com/ExPLoSiVe1988)
*   Telegram: [t.me/H_ExPLoSiVe](https://t.me/H_ExPLoSiVe)
---
### 💖 Support / Donate
If you find this project useful, please consider supporting its development:

| Cryptocurrency | Address |
|:---|:---|
| 🟣 **Ethereum (ETH - ERC20)** | `0x157F3Eb423A241ccefb2Ddc120eF152ce4a736eF` |
| 🔵 **Tron (TRX - TRC20)** | `TEdu5VsNNvwjCRJpJJ7zhjXni8Y6W5qAqk` |
| 🟢 **Tether (USDT - TRC20)** | `TN3cg5RM5JLEbnTgK5CU95uLQaukybPhtR` |

🙏 Thank you for your support! 🚀
