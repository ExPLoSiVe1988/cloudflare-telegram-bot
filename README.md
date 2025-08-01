# Cloudflare Telegram Bot

این ربات به شما اجازه می‌دهد رکوردهای دامنه کلودفلر خود را از طریق تلگرام مدیریت کنید.

## ✨ قابلیت‌ها

  * مدیریت رکوردهای DNS دامنه
  * مشاهده لیست رکوردها
  * ویرایش IP هر رکورد با تأیید نهایی
  * ساخت و حذف رکورد جدید
  * گرفتن بکاپ و ری‌استور کردن رکوردها
  * پیام‌های اطلاع‌رسانی نصب و آپدیت برای مدیر

## ⚙️ نصب و راه‌اندازی

### اجرای اسکریپت نصب

```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/main/install.sh)
```

### گزینه‌های داخل اسکریپت

| عملکرد | گزینه |
|:---|:---|
| نصب کامل ربات | Install Bot |
| بروزرسانی ربات از گیت‌هاب | Update Bot |
| حذف کامل ربات و فایل‌ها | Delete Bot |
| خروج | Exit |

### دستورات ربات

| دستور | توضیح |
|:---|:---|
| `/list` | نمایش تمامی ساب‌دامنه‌ها |
| `/add` | ثبت ساب‌دامین جدید |
| `/backup` | بکاپ گرفتن از کل رکوردها |
| `/restore` | بازگردانی رکوردهای پاک شده |

### اطلاعات مورد نیاز

| متغیر | توضیح |
|:---|:---|
| `CF_API_TOKEN` | توکن API کلودفلر |
| `CF_ZONE_NAME` | نام دامنه (مثلاً `example.com`) |
| `TELEGRAM_BOT_TOKEN` | توکن ربات تلگرام |
| `TELEGRAM_ADMIN_ID` | آیدی عددی تلگرام ادمین (برای ارسال پیام‌ها) |

### 1. ایجاد API Token در Cloudflare
| گام | توضیحات |
|:---|:---|
| 1 | وارد حساب Cloudflare خود شوید: https://dash.cloudflare.com/profile/api-tokens |
| 2 | روی Create Token کلیک کن. |
| 3 | از قالب آماده‌ی Edit zone DNS استفاده کن یا توکن سفارشی با دسترسی‌های زیر بساز: <br> * Permissions: <br> &nbsp;&nbsp;&nbsp;&nbsp; Zone > DNS > Edit <br> &nbsp;&nbsp;&nbsp;&nbsp; <br> * Zone Resources: <br> &nbsp;&nbsp;&nbsp;&nbsp; Include > All zones یا فقط دامنه‌های مورد نظر |
| 4 | API Token تولیدشده را کپی کن و جایی امن ذخیره کن. |

### مدیریت با PM2

ربات به صورت خودکار با PM2 اجرا می‌شود. برخی دستورات مهم:

| دستور | کاربرد |
|:---|:---|
| `pm2 logs cfbot` | مشاهده لاگ زنده ربات |
| `pm2 restart cfbot` | ری‌استارت دستی ربات |
| `pm2 delete cfbot` | حذف دستی ربات از PM2 (نیازی نیست، از منوی اسکریپت هم میشه) |

### پیام‌های سیستمی

بعد از نصب یا آپدیت موفق، ربات پیام زیر را به ادمین تلگرام ارسال می‌کند:

  * نصب: 🚀 Cloudflare bot installed and running successfully.
  * آپدیت: ✅ Cloudflare bot updated to latest version. 🔄

-----

### 👨‍💻 توسعه‌دهنده

  * GitHub: [@ExPLoSiVe1988](https://github.com/ExPLoSiVe1988)
  * Telegram: [t.me/H\_ExPLoSiVe](https://t.me/H_ExPLoSiVe)

-----

-----

-----

-----

### 💖 Support / Donate

If you find this project useful, please consider supporting me by donating to one of the wallets below:

| Cryptocurrency | Address |
|:---|:---|
| 🟣 **Ethereum (ETH - ERC20)** | `0x157F3Eb423A241ccefb2Ddc120eF152ce4a736eF` |
| 🔵 **Tron (TRX - TRC20)** | `TEdu5VsNNvwjCRJpJJ7zhjXni8Y6W5qAqk` |
| 🟢 **Tether (USDT - TRC20)** | `TN3cg5RM5JLEbnTgK5CU95uLQaukybPhtR` |

-----

🙏 Thank you for your support\! 🚀
