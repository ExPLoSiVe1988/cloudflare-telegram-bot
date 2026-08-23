<div align="center"><strong><a href="README.md">فارسی</a></strong> | <strong><a href="README-EN.md">English</a></strong> | <strong><a href="README-CH.md">中文</a></strong></div><br/>

# Cloudflare Management Bot 🐳

A powerful Telegram bot for complete DNS record management, intelligent monitoring, and server management. Equipped with three independent **monitoring systems**, **automatic failover**, and **intelligent load balancing**, this bot is your comprehensive solution to ensure maximum uptime and optimal performance. The bot is compatible with **Cloudflare**, **ArvanCloud**, and **Hetzner Cloud**, providing domain management, DNS records, and cloud server capabilities.

---

<div align="center">
<a href="https://www.youtube.com/watch?v=OOQ9rtHqeFQ" target="_blank">
<img src="https://img.youtube.com/vi/OOQ9rtHqeFQ/hqdefault.jpg" alt="Complete Tutorial Video" width="320"></a>
<p><strong>Click the image above to watch the complete tutorial video</strong></p>
</div>

## ✨ Features

### 🚀 Comprehensive and Advanced Monitoring

*   👁️ **Independent Monitoring**: Monitor any IP or domain (like a database server) independently and receive instant alerts when offline.
*   🎯 **Targeted Notification System:**
    *   For **each rule (Failover/LB) or each independent monitor**, define alert recipients (personal users or Telegram groups) in **completely separate** manner.
    *   Alerts reach only responsible people and teams, preventing spam and alert fatigue.
*   📍 **Centralized Monitoring Groups**: Define reusable collections of monitoring cities (e.g., "Europe", "Asia") with error thresholds and easily assign them to any monitor or rule.
*   🛡️ **Automatic Failover (High Availability)**: When the primary server goes down, the bot automatically changes DNS records to a healthy backup IP.
*   🚦 **Advanced Weighted Load Balancing:**
    *   **Set weights for IPs**: Distribute traffic based on server capacity (example: `1.1.1.1:2`).
    *   **Two smart algorithms**: Choose between **weighted random** and **weighted round-robin** (default) algorithms.
*   📊 **Advanced Reporting and Analysis:**
    *   **Time-based Analysis**: Load balancer reports now show **total time (in hours) and percentage** of activity for each IP.
    *   Generate monitoring reports for custom time ranges and manage automatic log deletion rules.
*   **🏷️ Display Names (Aliases) for Domains and Records**: Assign custom display names to your domains and records for easier identification and management.
*   **👥 Advanced User Management from Within the Bot**:
    *   **Main admins** can manage **regular admins** directly from within the bot.
*   **📤 Record Transfer and Copy**: Easily move DNS records between different domains or even different Cloudflare accounts.
*   **🔄 Record Type Conversion**: Change a record type (e.g., from `A` to `CNAME`) instantly.
*   **👥 Bulk Operations**: Delete multiple records simultaneously or change their IPs.
*   **💾 Backup and Recovery**: Create `.json` backups of your domain records and recover them.

### 🤖 General Features and User Experience

*   **📄 Domain List Pagination**: If you have many domains, you won't face a long list anymore. The domain list (`/list`) is now displayed with pagination.
*   **🚀 Quick Setup Wizard**: A step-by-step guide that easily helps new users create their first monitoring rule.
*   **📊 `/status` Command**: With a simple command, get a summary of online/offline status of all your rules and monitors in real-time.
*   **🧠 Automatic Data Migration**: The bot intelligently detects old `config.json` files (with old notification structure) and upgrades them to the new structure without losing user data.
*   **🐳 Smart and Secure Installation Script**: The management script now warns you before overwriting settings during reinstallation and offers to backup your `config.json`.
*   **🎨 Redesigned User Interface (HTML)**, **Multi-account Support**, and **Multilingual**.

### ⚙️ Advanced DNS and User Management

### 🌐 Cloudflare + ArvanCloud Support

*   **Multi-Provider DNS Management**: Add and manage Cloudflare and ArvanCloud accounts through the installation script.
*   **Select ArvanCloud Domains and Records**: List ArvanCloud domains, view DNS records, and select `A` records for monitoring rules.
*   **Provider-Compatible Failover and Load Balancing**: Previous Cloudflare rules continue working without changes, and new ArvanCloud rules can automatically change DNS records when the server goes down.
*   **Secure Upgrade of Previous Installations**: You can add ArvanCloud to your current bot through the installation script without resetting previous settings or rules.

### 🌐 ArvanCloud Features

| Description | Feature |
|:---|:---|
| Manage ArvanCloud accounts alongside Cloudflare | **Add and Manage Accounts** |
| View domains that the Machine User has access to | **List Domains** |
| View DNS records of ArvanCloud domains | **Display DNS Records** |
| Select `A` records for Failover and Load Balancer rules | **Select A Records** |
| When the primary server goes down, the record IP changes automatically | **Automatic IP Change** |
| All Cloudflare rules and settings are preserved | **Preserve Previous Settings** |
| Add ArvanCloud to previous installations without resetting `config.json` | **Add Without Reset** |

### 🌐 Getting ArvanCloud API Key and Granting Domain Access

To use ArvanCloud, you need to create a **Machine User** in the ArvanCloud panel and grant it access to your desired domains:

1. Log in to your ArvanCloud control panel.
2. Go to Account / IAM section and open **Machine Users**.
3. Create a new Machine User.
4. For the created Machine User, generate an Access Key/API Key.
5. In the access section, select the domains you want the bot to manage.
6. For the selected domains, enable DNS management access. If domains don't appear in the bot, also enable domain view/management access for the same Machine User.
7. Add the created key through the installation script and from the DNS account management section as an ArvanCloud account.

> If the API Key is valid but domains don't appear in the bot, usually the Machine User hasn't been granted domain access or the DNS role hasn't been enabled for those domains.

### 🟧 Hetzner Cloud Account Management

> For management operations like server creation/deletion, Rescue, Snapshot, and Primary IP, the Hetzner token must have Read & Write access. The Hetzner Cloud account is added through the installation script and the server account management section.

### 🟧 Hetzner Cloud Features

| Description | Feature |
|:---|:---|
| View servers with status, IP, and Protection flag | **List Servers** |
| Plan, monthly/hourly price, data center, location, image, Primary IP, and traffic | **Server Details** |
| Calculate approximate cost for the current month | **Cost Calculation** |
| Traffic consumption alert based on billable traffic up to 20 terabytes | **Traffic Alert** |
| Power on, reboot, shutdown, and force shutdown | **Server Control** |
| Complete system reset | **Complete Reset** |
| Reset root user password and rescue mode | **Reset Password** |
| Choose name, plan, image, and location | **Create New Server** |
| Change server name | **Rename Server** |
| Delete with safe confirmation and Disable Protection support | **Delete Server** |
| List, create, delete, Enable/Disable Protection, and show volume | **Snapshot Management** |
| List, display Rules, Attach and Detach to server | **Firewall Management** |
| Manage Primary IP and Floating IP including create, Assign/Detach, and Protection settings | **IP Management** |
| 5-item pagination for servers, snapshots, and firewalls | **Pagination** |

---

<div align="center"><h3>💖 Support the Project</h3><p>If this project has been helpful to you, please support it by giving it a star on GitHub!</p><a href="https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot/stargazers"><img src="https://img.shields.io/github/stars/ExPLoSiVe1988/cloudflare-telegram-bot?style=for-the-badge&logo=github&color=FFDD00&logoColor=black" alt="Star the project on GitHub"></a></div>

## 🚀 Installation and Setup

This bot is designed to run with Docker. The provided script automates all setup steps.

### Automatic Installation

You can install either the latest stable version or the development version. For most users, **installing the latest stable version** is recommended.

```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/main/install.sh)
```

Run the following command in your server terminal. Please replace `<VERSION>` with the latest version number available on the [Releases](https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot/releases) page (e.g., `v4.1.2`).

```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/<VERSION>/install.sh)
```

The script provides you with a complete management menu:

*   **Install or Reinstall Bot:** Downloads the repository, allows you to select your desired version (latest or stable), asks for initial settings, and installs and runs the bot with Docker Compose.
*   **Update Bot from GitHub:** Fetches the latest code from GitHub. To apply the update, you must run the "Install or Reinstall Bot" option again afterwards.
*   **Manage Core Configuration:** Provides access to manage core settings, accounts, and admins through the script menu.
*   **View Live Logs:** Displays live bot logs for monitoring and debugging.
*   **Stop Bot / Start Bot:** Allows you to stop or restart the bot container without losing data.
*   **Remove Bot Completely:** Stops the container and completely removes all data, config files, containers, and related images.

---

## ⚙️ Configuration

All main bot settings are configured through the installation script and management menu. There's no need to manually edit configuration files to add or modify Cloudflare, ArvanCloud, and Hetzner Cloud Manager accounts.

Important paths within the script:

```text
Manage DNS Provider Accounts
├── Cloudflare
└── ArvanCloud
Manage Server Provider Accounts
└── Hetzner Cloud
```

---

## 🤖 Bot Management

If you prefer to use commands directly, navigate to the project folder (`cloudflare-telegram-bot`) and use the following `docker-compose` commands:

| Command | Function |
| :--- | :--- |
| `docker-compose logs -f` | **View live logs** |
| `docker-compose pull && docker-compose up -d` | **Update to the latest version** |
| `docker-compose down` | **Stop and remove container** |

---

### Required API Token Permissions

For each Cloudflare account, your API token needs the following permissions:

| Type | Resource | Access |
| :--- | :--- | :--- |
| **Zone** | **DNS** | `Edit` |
| **Zone** | **Zone** | `Read` |

Go to the [API Tokens](https://dash.cloudflare.com/profile/api-tokens) page and create a custom token with these two permissions enabled for `All zones`.

---

### 👨‍💻 Developer and Financial Support

*   GitHub: [@ExPLoSiVe1988](https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot)
*   Telegram: [@H_ExPLoSiVe](https://t.me/H_ExPLoSiVe)
*   Telegram Channel: [@Botgineer](https://t.me/Botgineer)

---

### 💖 Financial Support (Donate)

If this project has been helpful, please support its development with a donation:

### [Support via Payment Link](https://reymit.ir/botgineer)

| Currency Type | Address |
|:---|:---|
| 🔷 **Ethereum (ETH - ERC20)** | `0x157F3Eb423A241ccefb2Ddc120eF152ce4a736eF` |
| 🔴 **Tron (TRX - TRC20)** | `TEdu5VsNNvwjCRJpJJ7zhjXni8Y6W5qAqk` |
| 🟢 **Tether (USDT - BEP20)** | `0x78C406B501c4895627CC22F6653AD66163294D60` |

🙏 Thank you for your support! 🚀
