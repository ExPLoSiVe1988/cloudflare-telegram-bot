<div align="center">
  <strong><a href="README.md">English</a></strong> | <strong><a href="README-FA.md">فارسی</a></strong> | <strong><a href="README-CH.md">中文</a></strong>
</div>
<br>

# Cloudflare Management Bot 🐳
A powerful Telegram bot for complete DNS record management and intelligent monitoring. Equipped with **Standalone Monitoring**, **Automatic Failover**, and **Smart Load Balancing**, this bot is your all-in-one solution for ensuring maximum uptime and performance.

---
<div align="center">
  <a href="https://www.youtube.com/watch?v=OOQ9rtHqeFQ" target="_blank">
    <img src="https://img.youtube.com/vi/OOQ9rtHqeFQ/hqdefault.jpg" alt="Watch the video tutorial" width="320">
  </a>
  <p><strong>Click the image above to watch the full video tutorial on YouTube</strong></p>
</div>

## ✨ Features

### 🚀 Universal & Advanced Monitoring
*   👁️ **Standalone Monitoring**: Monitor any IP or domain (like a database server or a third-party API) independently of your Cloudflare DNS, with instant up/down alerts.
*   📍 **Centralized Monitoring Groups**: Create reusable groups of monitoring locations (e.g., "Europe," "Iran") with a defined failure threshold. Assign these groups to any monitor or policy for fast, consistent, and easily manageable setups.
*   🛡️ **Automatic Failover (High Availability)**: If a primary server goes down, the bot automatically switches DNS records to a healthy backup IP from your predefined list.
*   🚦 **Advanced Weighted Load Balancing**:
    *   **Weighted IP Pools**: Distribute traffic proportionally based on server capacity (e.g., `1.1.1.1:2, 2.2.2.2:1`).
    *   **Two Intelligent Algorithms**: Choose between **Weighted Random** and **Weighted Round-Robin** (default).
*   📊 **Advanced Reporting & Analytics**:
    *   **Time-Based Analytics**: Load Balancer reports now show the **total time (hours) and percentage** each IP was active, providing a true reflection of traffic distribution.
    *   Generate on-demand reports for custom timeframes and manage log retention policies.

### ⚙️ Advanced DNS & User Management
*   **🏷️ Zone & Record Aliases**: Assign friendly display names to both your zones (domains) and individual records for much easier identification and management.
*   **👥 Advanced In-Bot User Management**:
    *   **Super Admins** (from `.env`) can manage **Regular Admins** directly within the bot.
    *   Manage a separate list of **Notification Recipients** who only receive alerts without having admin privileges.
*   **📤 Move & Copy Records**: Easily migrate DNS records between different zones, even across different Cloudflare accounts.
*   **🔄 Convert Record Types**: Change a record's type (e.g., from `A` to `CNAME`) on the fly.
*   **👥 Bulk Actions**: Delete or change the IP for multiple records at once.
*   **💾 Backup & Restore**: Create and restore `.json` backups for any of your zones.

### 🤖 General Bot & UX
*   **🚀 Quick Setup Wizard**: A new step-by-step wizard guides new users through creating their first monitoring rule effortlessly.
*   **📊 `/status` Command**: Get an instant, real-time overview of the health of all your configured policies and monitors.
*   **🐳 Smart & Safe Installation Script**: The management script now warns you before overwriting settings on reinstall and offers to back up both `config.json` and `monitoring_log.json`.
*   **🧠 Automatic Data Migration**: The bot intelligently detects old `config.json` files and automatically updates them to the new structure without deleting user data.
*   **🎨 Revamped UI (HTML)**, **Multi-Account Support**, and **Multi-Language**.

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

*   `TELEGRAM_ADMIN_IDS`:  A comma-separated list of Super Admin User IDs. These users have full control, including the ability to manage other admins from within the bot.
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
