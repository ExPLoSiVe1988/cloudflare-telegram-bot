<div align="center">
  <strong><a href="README.md">فارسی</a></strong> | <strong><a href="README-EN.md">English</a></strong> | <strong><a href="README-CH.md">中文</a></strong>
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
*   🎯 **Targeted Notification System**:
    *   Define specific alert recipients (individual users or entire Telegram groups) for **each individual Failover/LB Rule or Standalone Monitor**.
    *   Alerts are delivered only to the responsible teams, preventing alert fatigue and spam.
*   📍 **Centralized Monitoring Groups**: Create reusable groups of monitoring locations (e.g., "Europe," "Asia") with a defined failure threshold. Assign these groups to any monitor or policy for fast, consistent, and easily manageable setups.
*   🛡️ **Automatic Failover (High Availability)**: If a primary server goes down, the bot automatically switches DNS records to a healthy backup IP from your predefined list.
*   🚦 **Advanced Weighted Load Balancing**:
    *   **Weighted IP Pools**: Distribute traffic proportionally based on server capacity (e.g., `1.1.1.1:2, 2.2.2.2:1`).
    *   **Two Intelligent Algorithms**: Choose between **Weighted Random** and **Weighted Round-Robin** (default).
*   📊 **Advanced Reporting & Analytics**:
    *   **Time-Based Analytics**: Load Balancer reports now show the **total time (hours) and percentage** each IP was active, providing a true reflection of traffic distribution.
    *   Generate on-demand reports for custom timeframes and manage log retention policies.

### ⚙️ Advanced DNS & User Management
### 🌐 Cloudflare + ArvanCloud DNS Provider Support
*   **Multi-provider DNS accounts**: Add and manage both Cloudflare and ArvanCloud accounts from the installation script.
*   **ArvanCloud domain and record selection**: List ArvanCloud domains, browse DNS records, and select `A` records for monitoring policies.
*   **Provider-aware Failover & Load Balancing**: Existing Cloudflare policies continue to work, while new ArvanCloud policies can automatically update DNS records when a monitored server goes down.
*   **Safe upgrade path**: Add ArvanCloud to an existing installation from the installation script without resetting current bot settings or policies.

#### Creating an ArvanCloud API Key and granting domain access
To use ArvanCloud, create a **Machine User** in the ArvanCloud panel and grant it access to the domains you want to manage:

1. Sign in to the ArvanCloud user panel.
2. Open the account/IAM area and go to **Machine Users**.
3. Create a new Machine User and generate an Access Key/API Key for it.
4. In the access/permissions section, select the domains that the bot should manage.
5. For each selected domain, enable the DNS management role. If needed, also grant domain view/manage access so the bot can list and select the domains.
6. Add the generated key as an ArvanCloud account from the DNS account management menu in the installation script.

If domains do not appear in the bot, the Machine User usually does not have access to that domain or does not have the required DNS role enabled.

*   **🏷️ Zone & Record Aliases**: Assign friendly display names to both your zones (domains) and individual records for much easier identification and management.
*   **👥 Advanced In-Bot User Management**:
    *   **Super Admins** can manage **Regular Admins** directly within the bot.
*   **📤 Move & Copy Records**: Easily migrate DNS records between different zones, even across different Cloudflare accounts.
*   **🔄 Convert Record Types**: Change a record's type (e.g., from `A` to `CNAME`) on the fly.
*   **👥 Bulk Actions**: Delete or change the IP for multiple records at once.
*   **💾 Backup & Restore**: Create and restore `.json` backups for any of your zones.

### 🤖 General Bot & UX
*   **📄 Zone List Pagination**: No more endless scrolling if you manage many domains. The zone list (`/list`) is now paginated for easier and faster navigation.
*   **🚀 Quick Setup Wizard**: A step-by-step wizard guides new users through creating their first monitoring rule effortlessly.
*   **📊 `/status` Command**: Get an instant, real-time overview of the health of all your configured policies and monitors.
*   **🧠 Automatic Data Migration**: The bot intelligently detects old `config.json` files (including old notification structures) and automatically updates them to the new format without deleting user data.
*   **🐳 Smart & Safe Installation Script**: The management script warns you before overwriting settings on reinstall and offers to back up `config.json`.
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
*   **Install or Reinstall Bot:** Clones the repository, allows you to choose the desired version (latest or stable), prompts for initial configuration, and installs/runs the bot using Docker Compose.
*   **Update Bot from GitHub:** Fetches the latest code from GitHub. To apply the update, you should then run the "Install or Reinstall Bot" option again.
*   **Manage Core Configuration:** Lets you manage core settings, provider accounts, and admins from the script menu.
*   **View Live Logs:** Shows the real-time output of the bot for monitoring and debugging.
*   **Stop Bot / Start Bot:** Allows you to stop or start the bot's container without removing any data.
*   **Remove Bot Completely:** Stops the container and completely removes all associated data, config files, containers, and images.

---

## ⚙️ Configuration

All core bot settings are handled through the installation script and its management menu. You do not need to manually edit configuration files to add or update Cloudflare, ArvanCloud, or Hetzner Cloud accounts.

Useful script menus:

```text
Manage DNS Provider Accounts
├── Cloudflare
└── ArvanCloud

Manage Server Provider Accounts
└── Hetzner Cloud
```

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
