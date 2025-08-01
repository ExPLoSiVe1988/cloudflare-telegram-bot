import os
import json
import requests
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
)
from telegram.ext import (
    Updater, CommandHandler, CallbackQueryHandler,
    MessageHandler, Filters, CallbackContext
)

load_dotenv()

CF_API_TOKEN = os.getenv("CF_API_TOKEN")
CF_TOKEN_NAME = os.getenv("CF_TOKEN_NAME")  # تغییر از CF_ZONE_NAME
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_ID = int(os.getenv("TELEGRAM_ADMIN_ID"))

HEADERS = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json"
}

def is_admin(update: Update):
    return update.effective_user.id == TELEGRAM_ADMIN_ID

def get_zone_id():
    r = requests.get(
        "https://api.cloudflare.com/client/v4/zones",
        headers=HEADERS,
        params={"name": CF_TOKEN_NAME}
    )
    if r.ok and r.json().get("result"):
        return r.json()["result"][0]["id"]
    return None

def get_dns_records(zone_id):
    r = requests.get(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
        headers=HEADERS
    )
    if r.ok:
        return r.json().get("result", [])
    return []

def update_record(zone_id, record_id, record_type, name, content, proxied):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
    data = {
        "type": record_type,
        "name": name,
        "content": content,
        "ttl": 120,
        "proxied": proxied
    }
    return requests.put(url, headers=HEADERS, json=data).json()

def delete_record(zone_id, record_id):
    return requests.delete(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}",
        headers=HEADERS
    ).json()

def create_record(zone_id, record_type, name, content, proxied):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    data = {
        "type": record_type,
        "name": name,
        "content": content,
        "ttl": 120,
        "proxied": proxied
    }
    return requests.post(url, headers=HEADERS, json=data).json()

def chunk_list(lst, n):
    return [lst[i:i + n] for i in range(0, len(lst), n)]

def list_records(update: Update, context: CallbackContext):
    if not is_admin(update):
        update.message.reply_text("❌ دسترسی ندارید.")
        return

    zone_id = get_zone_id()
    if not zone_id:
        update.message.reply_text("❌ Zone ID پیدا نشد.")
        return

    records = get_dns_records(zone_id)
    context.user_data["records"] = {r['id']: r for r in records}

    if not records:
        update.message.reply_text("هیچ رکوردی پیدا نشد.")
        return

    buttons = [
        InlineKeyboardButton(f"{r['type']} {r['name']}", callback_data=f"select|{r['id']}")
        for r in records
    ]
    buttons_1col = [[btn] for btn in buttons]
    update.message.reply_text(
        "📄 لیست رکوردها:",
        reply_markup=InlineKeyboardMarkup(buttons_1col)
    )

def handle_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data.split('|')

    if not data:
        return

    cmd = data[0]
    records = context.user_data.get("records", {})

    if cmd == "select":
        rid = data[1]
        record = records.get(rid)
        if not record:
            query.message.edit_text("❌ رکورد پیدا نشد.")
            return
        kb = [
            [InlineKeyboardButton("📝 ویرایش", callback_data=f"edit|{rid}")],
            [InlineKeyboardButton("🗑 حذف", callback_data=f"delete|{rid}")],
            [InlineKeyboardButton("❌ لغو", callback_data="cancel")]
        ]
        text = f"📛 `{record['type']} {record['name']}`\nمقدار فعلی: `{record['content']}`"
        query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    elif cmd == "edit":
        rid = data[1]
        record = records.get(rid)
        context.user_data["edit"] = {
            "id": record["id"],
            "type": record["type"],
            "name": record["name"],
            "old": record["content"]
        }
        query.message.reply_text(
            f"📥 مقدار IP جدید را وارد کنید برای `{record['name']}` :",
            parse_mode="Markdown"
        )

    elif cmd == "delete":
        rid = data[1]
        record = records.get(rid)
        kb = [
            [InlineKeyboardButton("✅ تایید حذف", callback_data=f"delete_confirm|{rid}")],
            [InlineKeyboardButton("❌ لغو", callback_data="cancel")]
        ]
        query.message.edit_text(
            f"⚠️ حذف رکورد `{record['name']}` ؟",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown"
        )

    elif cmd == "delete_confirm":
        rid = data[1]
        record = records.get(rid)
        zone_id = get_zone_id()
        res = delete_record(zone_id, rid)
        if res.get("success"):
            records = get_dns_records(zone_id)
            context.user_data["records"] = {r['id']: r for r in records}
            buttons = [
                InlineKeyboardButton(f"{r['type']} {r['name']}", callback_data=f"select|{r['id']}")
                for r in records
            ]
            buttons_1col = [[btn] for btn in buttons]
            query.message.edit_text(
                f"✅ رکورد `{record['name']}` حذف شد.\n\n📄 لیست رکوردها :",
                reply_markup=InlineKeyboardMarkup(buttons_1col),
                parse_mode="Markdown"
            )
        else:
            query.message.edit_text("❌ خطا در حذف رکورد.")

    elif cmd == "cancel":
        zone_id = get_zone_id()
        if not zone_id:
            query.message.edit_text("❌ Zone ID پیدا نشد.")
            return

        records = get_dns_records(zone_id)
        context.user_data["records"] = {r['id']: r for r in records}
        buttons = [
            InlineKeyboardButton(f"{r['type']} {r['name']}", callback_data=f"select|{r['id']}")
            for r in records
        ]
        buttons_1col = [[btn] for btn in buttons]
        query.message.edit_text(
            "📄 لیست رکوردها:",
            reply_markup=InlineKeyboardMarkup(buttons_1col)
        )

    elif cmd == "confirm_change":
        confirm_change(update, context)

    elif cmd == "add_proxied":
        proxied = data[1].lower() == "true"
        rtype = context.user_data.pop("new_type")
        name = context.user_data.pop("new_name")
        content = context.user_data.pop("new_content")
        zone_id = get_zone_id()
        res = create_record(zone_id, rtype, name, content, proxied)
        if res.get("success"):
            subdomain = name
            kb = [[InlineKeyboardButton(subdomain, callback_data=f"copy_sub|{subdomain}")]]
            query.message.edit_text(
                f"✅ رکورد `{rtype} {subdomain}` افزوده شد.\nProxied: {proxied}",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="Markdown"
            )
        else:
            query.message.edit_text("❌ ساخت رکورد ناموفق بود.")

    elif cmd == "copy_sub":
        subdomain = '|'.join(data[1:])
        query.message.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"`{subdomain}`",
            parse_mode="Markdown"
        )
        query.answer("متن برای شما ارسال شد، حالا می‌توانید کپی کنید.", show_alert=True)

    elif cmd == "add_type":
        handle_add_type(update, context)

def handle_text(update: Update, context: CallbackContext):
    if not is_admin(update):
        update.message.reply_text("⛔️ دسترسی ندارید.")
        return

    if "edit" in context.user_data:
        data = context.user_data.pop("edit")
        context.user_data["confirm"] = {
            "id": data["id"],
            "type": data["type"],
            "name": data["name"],
            "old": data["old"],
            "new": update.message.text.strip()
        }
        kb = [[InlineKeyboardButton("✅ تایید", callback_data="confirm_change")]]
        update.message.reply_text(
            f"🔄 `{data['old']}` ➡️ `{update.message.text.strip()}`",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown"
        )
        return

    if "add_step" in context.user_data:
        step = context.user_data["add_step"]
        if step == "name":
            sub = update.message.text.strip()
            domain = CF_TOKEN_NAME
            context.user_data["new_name"] = f"{sub}.{domain}"
            context.user_data["add_step"] = "content"
            update.message.reply_text("📥 مقدار IP را وارد کن:")
        elif step == "content":
            context.user_data["new_content"] = update.message.text.strip()
            context.user_data["add_step"] = None
            kb = [
                [InlineKeyboardButton("DNS Only", callback_data="add_proxied|false")],
                [InlineKeyboardButton("Proxied", callback_data="add_proxied|true")]
            ]
            update.message.reply_text(
                "🌐 حالت رکورد را انتخاب کنید:",
                reply_markup=InlineKeyboardMarkup(kb)
            )

def confirm_change(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    info = context.user_data.pop("confirm", {})
    zone_id = get_zone_id()
    res = update_record(zone_id, info["id"], info["type"], info["name"], info["new"], proxied=False)
    if res.get("success"):
        query.message.reply_text("✅ مقدار وارد شده با موفقیت به‌روزرسانی شد.")
    else:
        query.message.reply_text("❌ خطا در بروزرسانی.")

def start_add(update: Update, context: CallbackContext):
    if not is_admin(update):
        update.message.reply_text("⛔️ دسترسی ندارید.")
        return
    context.user_data["add_step"] = "type"
    types = [
        "A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV",
        "LOC", "SPF", "CERT", "DNSKEY", "DS", "NAPTR",
        "SMIMEA", "SSHFP", "SVCB", "TLSA", "URI"
    ]
    buttons = [InlineKeyboardButton(t, callback_data=f"add_type|{t}") for t in types]
    buttons_3col = chunk_list(buttons, 3)  # 3 ستون برای نوع رکورد
    update.message.reply_text("🆕 نوع تایپ رکورد را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(buttons_3col))

def handle_add_type(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data.split('|')
    if len(data) != 2:
        return
    _, rtype = data
    context.user_data["new_type"] = rtype
    context.user_data["add_step"] = "name"
    query.edit_message_text("📛 فقط نام ساب‌دامنه را وارد کن:")

def backup(update: Update, context: CallbackContext):
    if not is_admin(update):
        update.message.reply_text("⛔️ دسترسی ندارید.")
        return
    zone_id = get_zone_id()
    records = get_dns_records(zone_id)
    with open("dns_backup.json", "w") as f:
        json.dump(records, f, indent=2)
    with open("dns_backup.json", "rb") as f:
        update.message.reply_document(InputFile(f, filename="backup.json"))

def restore(update: Update, context: CallbackContext):
    if not is_admin(update):
        update.message.reply_text("⛔️ دسترسی ندارید.")
        return
    zone_id = get_zone_id()
    try:
        with open("dns_backup.json", "r") as f:
            backup_records = json.load(f)
    except Exception as e:
        update.message.reply_text(f"❌ خطا در خواندن فایل: {e}")
        return
    existing_records = get_dns_records(zone_id)
    existing_map = {(r["type"], r["name"]): r for r in existing_records}
    restored, skipped, failed = 0, 0, 0
    for r in backup_records:
        key = (r["type"], r["name"])
        if key in existing_map:
            skipped += 1
            continue
        res = create_record(zone_id, r["type"], r["name"], r["content"], r.get("proxied", False))
        if res.get("success"):
            restored += 1
        else:
            failed += 1
    update.message.reply_text(
        f"🔁 Restore تمام شد:\n✅ افزوده شده: {restored}\n⏭ تکراری: {skipped}\n❌ ناموفق: {failed}"
    )

def main():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CallbackQueryHandler(handle_add_type, pattern=r"^add_type\|"))

    dp.add_handler(CommandHandler("list", list_records))
    dp.add_handler(CommandHandler("add", start_add))
    dp.add_handler(CommandHandler("backup", backup))
    dp.add_handler(CommandHandler("restore", restore))

    dp.add_handler(CallbackQueryHandler(handle_callback))

    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
