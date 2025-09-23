<div align="center">
  <strong><a href="README.md">English</a></strong> | <strong><a href="README-FA.md">فارسی</a></strong> | <strong><a href="README-CH.md">中文</a></strong>
</div>
<br>

# Cloudflare Management Bot 🐳
A powerful Telegram bot for complete DNS record management, equipped with two fully independent systems: **intelligent Monitoring & Automatic Failover** and **Smart Load Balancing**. This bot monitors your servers 24/7, redirecting traffic to backup servers in case of an outage or distributing it among healthy servers to ensure maximum uptime and performance.

---
<div align="center">
  <a href="https://www.youtube.com/watch?v=OOQ9rtHqeFQ" target="_blank">
    <img src="https://img.youtube.com/vi/OOQ9rtHqeFQ/hqdefault.jpg" alt="Watch the video tutorial" width="320">
  </a>
  <p><strong>Click the image above to watch the full video tutorial on YouTube</strong></p>
</div>

## ✨ Features

### 🚀 Advanced Monitoring & High Availability
*   ⚡ **High-Speed Concurrent Monitoring**: The monitoring engine checks all unique IPs across all policies **simultaneously**, reducing detection time from minutes to seconds.
*   🛡️ **Automatic Failover (High Availability)**: Continuously monitors your primary servers from multiple locations worldwide. If downtime is detected, it automatically switches DNS records to your backup IPs.
*   🚦 **Advanced Weighted Load Balancing**:
    *   **Weighted IP Pools**: Assign numerical weights to each IP in a pool (e.g., `1.1.1.1:2, 2.2.2.2:1`) to distribute traffic proportionally based on server capacity or cost.
    *   **Two Intelligent Rotation Algorithms**: Choose your preferred strategy—**Weighted Random** for organic distribution or **Weighted Round-Robin** for precise, sequential balancing.
    *   **In-Bot Algorithm Switching**: Easily switch between algorithms on-the-fly from within the bot's settings menu.
*   🔄 **Automatic Failback**: Once a primary server is stable again, the bot automatically switches traffic back.
*   ⚙️ **Complete In-Bot Management**: All Failover and Load Balancing policies are fully manageable (add, edit, delete) through the settings menu.
*   🧠 **Smart Settings Copy**: Easily copy monitoring settings (cities and threshold) from a primary IP to backup IPs.

### ⚙️ General DNS Management & UX
*   **🎨 Revamped User Interface (HTML)**: A complete migration to HTML formatting for all bot messages, resulting in a cleaner, more professional interface with no distracting characters and significantly improved readability.
*   **👥 Multi-Admin & Multi-Account Support**: Authorize multiple users and manage DNS across several Cloudflare accounts.
*   **🐳 Easy Docker Deployment**: Get the bot running in minutes with a fully automated installation script.
*   **🌐 Multi-Zone Support**: Automatically detects all zones within a selected Cloudflare account.
*   **👥 Bulk Actions**: Select multiple records to delete or change their IP address all at once.
*   **💾 Backup & Restore**: Create and restore `.json` backups for any of your zones.
*   **🌍 Multi-Language**: Full support for English and Persian (فارسی).

---

<div align="center">
  <h3>💖 Show Your Support</h3>
  <p>If this project has been helpful, please give it a star on GitHub to show your appreciation!</p>
  <a href="https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot/stargazers">
    <img src="https://img.shields.io/github/stars/ExPLoSiVe1988/cloudflare-telegram-bot?style=for-the-badge&logo=github&color=FFDD00&logoColor=black" alt="Star the project on GitHub">
  </a>
</div>

## 🚀 Installation

This bot is designed to run with Docker. The provided script automates the entire setup process.

### Automated Installation

You can install either the latest stable version or the development version. For most users, **installing the latest stable version** is recommended.

To install the latest development version from the `main` branch, run:
```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/main/install.sh)
```
To install a specific stable version, please replace `<VERSION>` with the latest version number from the [Releases](https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot/releases) page (e.g., `v4.1.2`).
```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/<VERSION>/install.sh)
```
The script provides a full management menu:
*   **Install or Reinstall Bot:** Clones the repository, allows you to choose the desired version (latest or stable), prompts for initial configuration (`.env`), and installs/runs the bot using Docker Compose.
*   **Update Bot from GitHub:** Fetches the latest code from GitHub. To apply the update, you should then run the "Install or Reinstall Bot" option again.
*   **Edit Core Configuration (.env):** Opens a text editor to let you modify the bot's essential settings (tokens and admins) at any time.
*   **View Live Logs:** Shows the real-time output of the bot for monitoring and debugging.
*   **Stop Bot / Start Bot:** Allows you to stop or start the bot's container without removing any data.
*   **Remove Bot Completely:** Stops the container and completely removes all associated data, config files, containers, and images.

---

## ⚙️ Configuration

The installation script will create an `.env` file for you with the following structure. You can manage it later using the "Edit Core Configuration" option in the script.

*   `TELEGRAM_ADMIN_IDS`: A comma-separated list of numerical Telegram user IDs who are authorized to use the bot.
*   `CF_ACCOUNTS`: A comma-separated list of your Cloudflare accounts in the format `Nickname1:Token1,Nickname2:Token2`. The nickname is a friendly name you choose for each account.
*   `TELEGRAM_BOT_TOKEN`: The API token for your Telegram bot from @BotFather.

---

## 🤖 Bot Management

If you prefer to use commands directly, `cd` into the project directory (`cloudflare-telegram-bot`) and use these `docker-compose` commands:

| Action                      | Command                                   |
| :-------------------------- | :---------------------------------------- |
| **View Live Logs**          | `docker-compose logs -f`                  |
| **Update to Latest Version**| `docker-compose pull && docker-compose up -d` |
| **Stop and Remove Container** | `docker-compose down`                     |

---

### Cloudflare API Token Permissions
For each Cloudflare account, your API token needs the following permissions:

| Type | Resource | Access |
| :--- | :---     | :---   |
| **Zone** | **DNS**    | `Edit` |
| **Zone** | **Zone**   | `Read` |

Go to [API Tokens](https://dash.cloudflare.com/profile/api-tokens) and create a custom token with these two permissions applied to `All zones`.

---

### 👨‍💻 Developer & Support
*   GitHub: [@ExPLoSiVe1988](https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot)
*   Telegram: [@H_ExPLoSiVe](https://t.me/H_ExPLoSiVe)
*   Channel: [@Botgineer](https://t.me/Botgineer)
---
### 💖 Support / Donate
If you find this project useful, please consider supporting its development:

| Cryptocurrency            | Address                                      |
|:--------------------------|:---------------------------------------------|
| 🟣 **Ethereum (ETH - ERC20)** | `0x157F3Eb423A241ccefb2Ddc120eF152ce4a736eF` |
| 🔵 **Tron (TRX - TRC20)**     | `TEdu5VsNNvwjCRJpJJ7zhjXni8Y6W5qAqk`         |
| 🟢 **Tether (USDT - TRC20)**  | `TN3cg5RM5JLEbnTgK5CU95uLQaukybPhtR`         |

🙏 Thank you for your support! 🚀
