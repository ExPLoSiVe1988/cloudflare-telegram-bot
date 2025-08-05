<!-- Persian README link -->
<div align="right">
  <a href="README-FA.md">🇮🇷 نسخه فارسی</a>
</div>

# Cloudflare Telegram Bot

A powerful and professional tool for managing your Cloudflare DNS records directly through Telegram. With advanced features like multi-language support, instant search, and bulk actions, this bot evolves from a simple utility into a complete management assistant.

---

## Features

### Basic Management
*   **Full Record Management:** View, edit, create, and delete all types of DNS records.
*   **Action Confirmation:** A final confirmation step for critical operations like deleting or editing prevents user errors.
*   **Backup and Restore:** Create a `json` backup file of all your records and restore it when needed.
*   **System Notifications:** Receive messages on Telegram after a successful installation or update.

### ✨ Advanced Features
*   **🌐 Multi-Language Support:** The bot is fully bilingual, offering its entire interface in both **English** and **Persian (فارسی)**. Use the `/language` command to switch at any time.
*   **🗂 Pagination:** Say goodbye to long lists! Records are now neatly organized into pages, easily navigable with "Next" and "Previous" buttons.
*   **🔎 Instant Search:** Using the `/search` command, find any record in an instant just by typing a part of its name.
*   **☁️ Proxy Status Toggle:** Toggle the Cloudflare proxy status (orange/grey cloud) for any record directly from the bot's menu with a single click.
*   **👥 Bulk Actions:** Enter `/bulk` mode to select multiple records (`✅`) and perform an operation (like delete or change IP) on all of them at once.
*   **🕹 Smooth Navigation:** With a "Back" button in all sub-menus, quickly return to the main list without retyping commands.


## ⚙️ Installation and Setup

### Run the Installation Script
Execute the following command in your server's terminal:
```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/main/install.sh)
```

### Script Options
| Action | Option |
|:---|:---|
| Full bot installation | `Install Bot` |
| Update the bot from GitHub | `Update Bot` |
| Completely remove the bot | `Delete Bot` |
| Exit the script | `Exit` |

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

### Required Information
| Variable | Description |
|:---|:---|
| `CF_API_TOKEN` | Cloudflare API Token (with DNS edit permissions) |
| `CF_TOKEN_NAME` | Your root domain name (e.g., `example.com`) |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token (from @BotFather) |
| `TELEGRAM_ADMIN_ID` | The numerical Telegram ID of the admin account |

### Creating a Cloudflare API Token
| Step | Instructions |
|:---|:---|
| **1** | Log in to your Cloudflare account: https://dash.cloudflare.com/profile/api-tokens |
| **2** | Click on `Create Token`. |
| **3** | Use the `Edit zone DNS` template or create a custom token with the following permissions: <br> • **Permissions:** `Zone` > `DNS` > `Edit` <br> • **Zone Resources:** `Include` > `All zones` (or select specific domains) |
| **4** | Copy the generated API Token and save it in a secure place. |

### Managing with PM2
The bot is automatically managed by PM2.
| Command | Usage |
|:---|:---|
| `pm2 logs cfbot` | View live logs of the bot |
| `pm2 restart cfbot`| Manually restart the bot |


-----

### 👨‍💻 Developer
*   GitHub: [@ExPLoSiVe1988](https://github.com/ExPLoSiVe1988)
*   Telegram: [t.me/H_ExPLoSiVe](https://t.me/H_ExPLoSiVe)

-----

### 💖 Support / Donate
If you find this project useful, please consider supporting its development by donating:

| Cryptocurrency | Address |
|:---|:---|
| 🟣 **Ethereum (ETH - ERC20)** | `0x157F3Eb423A241ccefb2Ddc120eF152ce4a736eF` |
| 🔵 **Tron (TRX - TRC20)** | `TEdu5VsNNvwjCRJpJJ7zhjXni8Y6W5qAqk` |
| 🟢 **Tether (USDT - TRC20)** | `TN3cg5RM5JLEbnTgK5CU95uLQaukybPhtR` |

🙏 Thank you for your support! 🚀
```
