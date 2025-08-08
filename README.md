<!-- Persian README link -->
<div align="right">
  <a href="README-FA.md">🇮🇷 نسخه فارسی</a>
</div>

# Cloudflare Telegram Bot

A powerful and professional Telegram bot that acts as a complete control panel for managing your Cloudflare DNS records. With advanced features like multi-zone support, instant search, and bulk actions, this bot transforms DNS management into a fast and intuitive experience.

---

## ✨ Features

### Core Functionality
*   **Multi-Zone Support:** The bot automatically detects all zones in your Cloudflare account, allowing you to switch and manage multiple domains seamlessly.
*   **Full Record Management:** View, edit, create, and delete all types of DNS records.
*   **Action Confirmation:** A final confirmation step for critical operations like deleting or editing prevents accidental mistakes.
*   **Backup and Restore:** Create a `.json` backup of all records for a specific zone and restore them later, intelligently skipping duplicates.
*   **Installation Script Notifications:** Receive messages on Telegram after a successful installation or update via the provided script.

### 🚀 Advanced & User-Friendly Features
*   **🌐 Multi-Language Support:** The bot is fully bilingual, offering its entire interface in both **English** and **Persian (فارسی)**. Use the `/language` command to switch at any time.
*   **🗂 Pagination:** Say goodbye to endless scrolling! Records are neatly organized into pages, easily navigable with "Next" and "Previous" buttons.
*   **🔎 Instant Search:** Using the `/search` command, find any record in an instant just by typing a part of its name.
*   **☁️ Proxy Status Toggle:** Toggle the Cloudflare proxy status (orange/grey cloud) for any A or AAAA record directly from the bot's menu with a single tap.
*   **👥 Bulk Actions:** Enter `/bulk` mode to select multiple records (`✅`) and perform an operation (like delete or change IP) on all of them at once.
*   **🕹 Smooth Navigation:** With "Back" buttons in all sub-menus, quickly return to the previous screen without retyping commands.


## ⚙️ Installation and Setup

### Prerequisites
*   A Linux server (Ubuntu, Debian, CentOS, etc.)
*   `curl` and `bash` (usually pre-installed)
*   `python3` and `pip`
*   `pm2` for process management (the script will try to install it)

### 1. Run the Installation Script
Execute the following command in your server's terminal:
```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/main/install.sh)
```
The script will guide you through the installation, update, or removal process.

### 2. Script Options
| Action | Option |
|:---|:---|
| Full bot installation | `Install Bot` |
| Update the bot from GitHub | `Update Bot` |
| Completely remove the bot | `Delete Bot` |
| Exit the script | `Exit` |

### 3. Required Information
During installation, the script will ask for the following variables, which will be saved in a `.env` file:

| Variable | Description |
|:---|:---|
| `CF_API_TOKEN` | Cloudflare API Token (with DNS edit permissions). |
| `TELEGRAM_BOT_TOKEN` | Your Telegram Bot Token (from @BotFather). |
| `TELEGRAM_ADMIN_ID` | The numerical Telegram ID of the admin account (you). |
> **Note:** You do **not** need to provide a domain name. The bot will fetch all available zones from your account.

---

## 🤖 Bot Usage

### Bot Commands
| Command | Description |
|:---|:---|
| `/start` | Starts the bot and shows the language selection menu. |
| `/language` | Changes the bot's language (English/Persian). |
| `/list` | The main command to select a zone and view its DNS records. |
| `/search`| Searches for records within the currently selected zone. |
| `/bulk` | Enters Bulk Actions mode for the selected zone. |
| `/add` | Starts the process of adding a new DNS record to the selected zone. |
| `/backup` | Creates a backup of all DNS records for the selected zone. |
| `/restore`| Restores records from a backup file to the selected zone. |


### Creating a Cloudflare API Token
| Step | Instructions |
|:---|:---|
| **1** | Log in to your Cloudflare account and go to: https://dash.cloudflare.com/profile/api-tokens |
| **2** | Click `Create Token`. |
| **3** | Use the `Edit zone DNS` template, or create a custom token with these permissions: <br> • **Permissions:** `Zone` > `DNS` > `Edit` <br> • **Zone Resources:** `Include` > `All zones` |
| **4** | Copy the generated API Token and save it. You'll need it during installation. |

### Managing with PM2
The installation script uses PM2 to keep the bot running.
| Command | Usage |
|:---|:---|
| `pm2 logs cfbot` | View live logs of the bot for debugging. |
| `pm2 restart cfbot`| Manually restart the bot after changes. |


-----

### 👨‍💻 Developer
*   GitHub: [@ExPLoSiVe1988](https://github.com/ExPLoSiVe1988)
*   Telegram: [t.me/H_ExPLoSiVe](https://t.me/H_ExPLoSiVe)

-----

### 💖 Support / Donate
If you find this project useful, please consider supporting its development:

| Cryptocurrency | Address |
|:---|:---|
| 🟣 **Ethereum (ETH - ERC20)** | `0x157F3Eb423A241ccefb2Ddc120eF152ce4a736eF` |
| 🔵 **Tron (TRX - TRC20)** | `TEdu5VsNNvwjCRJpJJ7zhjXni8Y6W5qAqk` |
| 🟢 **Tether (USDT - TRC20)** | `TN3cg5RM5JLEbnTgK5CU95uLQaukybPhtR` |

🙏 Thank you for your support! 🚀
