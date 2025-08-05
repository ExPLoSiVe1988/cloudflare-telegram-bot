import os
import json
import requests
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Updater, CommandHandler, CallbackQueryHandler,
    MessageHandler, Filters, CallbackContext
)

load_dotenv()

# --- Configuration ---
CF_API_TOKEN = os.getenv("CF_API_TOKEN")
CF_TOKEN_NAME = os.getenv("CF_TOKEN_NAME")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_ID = int(os.getenv("TELEGRAM_ADMIN_ID"))

HEADERS = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json"
}

DNS_RECORD_TYPES = [
    "A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "LOC", "SPF", "CERT", "DNSKEY",
    "DS", "NAPTR", "SMIMEA", "SSHFP", "SVCB", "TLSA", "URI"
]
RECORDS_PER_PAGE = 10

# --- Helper Functions ---
def is_admin(update: Update):
    return update.effective_user.id == TELEGRAM_ADMIN_ID

def chunk_list(lst, n):
    return [lst[i:i + n] for i in range(0, len(lst), n)]

# --- Cloudflare API Functions ---
def get_zone_id():
    url = "https://api.cloudflare.com/client/v4/zones"
    params = {"name": CF_TOKEN_NAME}
    r = requests.get(url, headers=HEADERS, params=params)
    if r.ok and r.json().get("result"):
        return r.json()["result"][0]["id"]
    return None

def get_dns_records(zone_id):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    all_records = []
    page = 1
    while True:
        params = {'per_page': 100, 'page': page}
        r = requests.get(url, headers=HEADERS, params=params)
        if not r.ok: return []
        data = r.json()
        all_records.extend(data.get("result", []))
        if data['result_info']['page'] >= data['result_info']['total_pages']: break
        page += 1
    return all_records

def update_record(zone_id, record_id, record_type, name, content, proxied):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
    data = {"type": record_type, "name": name, "content": content, "ttl": 1, "proxied": proxied}
    return requests.put(url, headers=HEADERS, json=data).json()

def delete_record(zone_id, record_id):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
    return requests.delete(url, headers=HEADERS).json()

def create_record(zone_id, record_type, name, content, proxied):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    data = {"type": record_type, "name": name, "content": content, "ttl": 1, "proxied": proxied}
    return requests.post(url, headers=HEADERS, json=data).json()

# --- Core Logic Functions ---
def clear_state(context: CallbackContext):
    for key in ['is_bulk_mode', 'selected_records', 'search_query', 'is_searching', 'edit', 'add_step', 'new_type', 'new_name', 'new_content', 'confirm', 'is_bulk_ip_change', 'bulk_ip_confirm_details']:
        context.user_data.pop(key, None)

def display_records_list(update: Update, context: CallbackContext, page=0):
    query = update.callback_query
    chat_id = update.effective_chat.id
    
    if 'all_records' not in context.user_data:
        zone_id = get_zone_id()
        if not zone_id:
            context.bot.send_message(chat_id, "❌ Zone ID پیدا نشد."); return
        context.user_data['all_records'] = get_dns_records(zone_id)

    all_records = context.user_data.get('all_records', [])
    search_query = context.user_data.get('search_query')
    records_to_display = [r for r in all_records if search_query.lower() in r['name'].lower()] if search_query else all_records
    message_text = f"📄 نتایج جستجو برای «{search_query}»:" if search_query else "📄 لیست تمام رکوردها:"
    
    if not records_to_display:
        msg_to_send = "هیچ رکوردی برای نمایش وجود ندارد."
        kb = [[InlineKeyboardButton("↩️ بازگشت به لیست کامل", callback_data="list")]]
        if query: query.edit_message_text(msg_to_send, reply_markup=InlineKeyboardMarkup(kb))
        else: context.bot.send_message(chat_id, msg_to_send, reply_markup=InlineKeyboardMarkup(kb))
        return

    context.user_data["records"] = {r['id']: r for r in all_records}
    start_index, end_index = page * RECORDS_PER_PAGE, (page + 1) * RECORDS_PER_PAGE
    records_on_page = records_to_display[start_index:end_index]
    
    buttons = []
    is_bulk_mode = context.user_data.get('is_bulk_mode', False)
    selected_records = context.user_data.get('selected_records', [])

    for r in records_on_page:
        proxy_icon = "☁️" if r.get('proxied') else "⬜️"
        if is_bulk_mode:
            check_icon = "✅" if r['id'] in selected_records else "▫️"
            button_text, callback_data = f"{check_icon} {r['type']} {r['name']}", f"bulk_select|{r['id']}|{page}"
        else:
            button_text, callback_data = f"{proxy_icon} {r['type']} {r['name']}", f"select|{r['id']}"
        buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    pagination_buttons = []
    if page > 0: pagination_buttons.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"list_page|{page - 1}"))
    if end_index < len(records_to_display): pagination_buttons.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"list_page|{page + 1}"))
    if pagination_buttons: buttons.append(pagination_buttons)

    if is_bulk_mode:
        count = len(selected_records)
        buttons.append([
            InlineKeyboardButton(f"📝 تغییر IP انتخابی‌ها ({count})", callback_data="bulk_change_ip_start"),
            InlineKeyboardButton(f"🗑 حذف انتخابی‌ها ({count})", callback_data="bulk_delete_confirm")
        ])
        buttons.append([InlineKeyboardButton("❌ لغو", callback_data="list")])
    else:
        buttons.append([InlineKeyboardButton("➕ افزودن", callback_data="add"), InlineKeyboardButton("🔎 جستجو", callback_data="search_start"), InlineKeyboardButton("👥 گروهی", callback_data="bulk_start")])

    reply_markup = InlineKeyboardMarkup(buttons)
    if query:
        try: query.edit_message_text(message_text, reply_markup=reply_markup)
        except Exception: pass
    else:
        context.bot.send_message(chat_id, message_text, reply_markup=reply_markup)

def handle_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data.split('|')
    cmd = data[0]

    command_handlers = {
        "list": list_records_command, "list_page": lambda u, c: display_records_list(u, c, page=int(data[1])),
        "select": select_record_details, "edit": edit_record_value, "toggle_proxy": toggle_proxy_confirm_view,
        "toggle_proxy_confirm": execute_toggle_proxy, "delete": delete_record_confirm_view,
        "delete_confirm": execute_delete_record, "confirm_change": confirm_change,
        "add": add_record_start, "add_type": add_record_set_type, "add_proxied": execute_add_record,
        "search_start": lambda u, c: search_command(u, c, from_callback=True), # Corrected Line
        "bulk_start": lambda u, c: bulk_command(u, c, from_callback=True),     # Corrected Line
        "bulk_select": bulk_select_item,
        "bulk_delete_confirm": bulk_delete_confirm_view, "bulk_delete_execute": execute_bulk_delete,
        "bulk_change_ip_start": bulk_change_ip_start, "bulk_change_ip_execute": execute_bulk_change_ip
    }
    if cmd in command_handlers:
        command_handlers[cmd](update, context)

def handle_text(update: Update, context: CallbackContext):
    if not is_admin(update): return
    text = update.message.text.strip()
    
    if context.user_data.get('is_bulk_ip_change'):
        selected_ids = context.user_data.get('selected_records', [])
        context.user_data.pop('is_bulk_ip_change')
        context.user_data['bulk_ip_confirm_details'] = {'new_ip': text, 'record_ids': selected_ids}
        kb = [[InlineKeyboardButton("✅ بله، تایید", callback_data="bulk_change_ip_execute")], [InlineKeyboardButton("❌ لغو", callback_data="bulk_start")]]
        update.message.reply_text(f"⚠️ آیا از تغییر IP برای {len(selected_ids)} رکورد به `{text}` اطمینان دارید؟", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    if context.user_data.get('is_searching'):
        clear_state(context)
        context.user_data['search_query'] = text
        display_records_list(update, context, page=0)
        return

    if "edit" in context.user_data:
        data = context.user_data.pop("edit")
        context.user_data["confirm"] = {"id": data["id"], "type": data["type"], "name": data["name"], "old": data["old"], "new": text}
        kb = [[InlineKeyboardButton("✅ تایید", callback_data="confirm_change")], [InlineKeyboardButton("↩️ بازگشت", callback_data="list")]]
        update.message.reply_text(f"🔄 `{data['old']}` ➡️ `{text}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    if "add_step" in context.user_data:
        step = context.user_data["add_step"]
        if step == "name":
            context.user_data["new_name"] = f"{text}.{CF_TOKEN_NAME}"
            context.user_data["add_step"] = "content"
            record_type = context.user_data.get("new_type")
            prompt_text = "📥 مقدار IP را وارد کنید:" if record_type in ['A', 'AAAA'] else "📥 مقدار (Content) را وارد کنید:"
            update.message.reply_text(prompt_text)
        elif step == "content":
            context.user_data["new_content"] = text
            context.user_data.pop("add_step")
            kb = [[InlineKeyboardButton("DNS Only", callback_data="add_proxied|false")], [InlineKeyboardButton("Proxied", callback_data="add_proxied|true")]]
            update.message.reply_text("🌐 حالت پروکسی رکورد را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(kb))

def confirm_change(update: Update, context: CallbackContext):
    query = update.callback_query; query.answer()
    info = context.user_data.pop("confirm", {})
    original_record = context.user_data.get("records", {}).get(info["id"], {})
    proxied_status = original_record.get("proxied", False)
    res = update_record(get_zone_id(), info["id"], info["type"], info["name"], info["new"], proxied_status)
    if res.get("success"):
        query.edit_message_text("✅ مقدار با موفقیت به‌روزرسانی شد.")
        list_records_command(update, context, from_callback=True)
    else:
        query.edit_message_text(f"❌ خطا در بروزرسانی: {res.get('errors', [{}])[0].get('message', '')}")

# --- Handler Functions for Callbacks (for cleaner code) ---
def select_record_details(update, context):
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    if not record: update.callback_query.edit_message_text("❌ رکورد پیدا نشد."); return
    proxy_icon, proxy_text = ("☁️", "فعال (نارنجی)") if record.get('proxied') else ("⬜️", "غیرفعال (خاکستری)")
    kb = [[InlineKeyboardButton("📝 تغییر مقدار", callback_data=f"edit|{rid}")],
          [InlineKeyboardButton(f"تغییر وضعیت پروکسی {proxy_icon}", callback_data=f"toggle_proxy|{rid}")],
          [InlineKeyboardButton("🗑 حذف", callback_data=f"delete|{rid}")],
          [InlineKeyboardButton("↩️ بازگشت به لیست", callback_data="list")]]
    text = f"📛 `{record['type']} {record['name']}`\n📝 مقدار: `{record['content']}`\n☁️ پروکسی: `{proxy_text}`"
    update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

def edit_record_value(update, context):
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    context.user_data["edit"] = {"id": record["id"], "type": record["type"], "name": record["name"], "old": record["content"]}
    prompt_text = "📥 مقدار IP جدید را برای `{}` وارد کنید:" if record['type'] in ['A', 'AAAA'] else "📥 مقدار (Content) جدید را برای `{}` وارد کنید:"
    update.callback_query.message.reply_text(prompt_text.format(f"`{record['name']}`"), parse_mode="Markdown")

def toggle_proxy_confirm_view(update, context):
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    current_status = "فعال (نارنجی)" if record.get('proxied') else "غیرفعال (خاکستری)"
    new_status = "غیرفعال (خاکستری)" if record.get('proxied') else "فعال (نارنجی)"
    kb = [[InlineKeyboardButton("✅ بله، تغییر بده", callback_data=f"toggle_proxy_confirm|{rid}")], [InlineKeyboardButton("❌ خیر، لغو", callback_data=f"select|{rid}")]]
    text = f"⚠️ تغییر وضعیت پروکسی؟\n\nرکورد: `{record['name']}`\nوضعیت فعلی: **{current_status}**\nوضعیت جدید: **{new_status}**"
    update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

def execute_toggle_proxy(update, context):
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    new_proxied_status = not record.get('proxied', False)
    res = update_record(get_zone_id(), rid, record['type'], record['name'], record['content'], new_proxied_status)
    if res.get("success"):
        update.callback_query.answer("✅ وضعیت پروکسی با موفقیت تغییر کرد.", show_alert=True)
        context.user_data['records'][rid]['proxied'] = new_proxied_status
        context.user_data['all_records'] = list(context.user_data['records'].values())
        select_record_details(update, context) # Refresh view
    else: update.callback_query.edit_message_text("❌ خطا در تغییر وضعیت پروکسی.")

def delete_record_confirm_view(update, context):
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    kb = [[InlineKeyboardButton("✅ تایید حذف", callback_data=f"delete_confirm|{rid}")], [InlineKeyboardButton("↩️ بازگشت", callback_data=f"select|{rid}")]]
    update.callback_query.edit_message_text(f"⚠️ حذف رکورد `{record['name']}` ؟", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

def execute_delete_record(update, context):
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid, {})
    if delete_record(get_zone_id(), rid).get("success"):
        update.callback_query.edit_message_text(f"✅ رکورد `{record.get('name', 'N/A')}` حذف شد.")
        list_records_command(update, context, from_callback=True)
    else: update.callback_query.edit_message_text("❌ خطا در حذف رکورد.")

def add_record_start(update, context):
    buttons = [InlineKeyboardButton(t, callback_data=f"add_type|{t}") for t in DNS_RECORD_TYPES]
    buttons_3col = chunk_list(buttons, 3)
    buttons_3col.append([InlineKeyboardButton("↩️ بازگشت", callback_data="list")])
    update.callback_query.edit_message_text("🆕 نوع تایپ رکورد را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(buttons_3col))

def add_record_set_type(update, context):
    context.user_data["new_type"] = update.callback_query.data.split('|')[1]
    context.user_data["add_step"] = "name"
    update.callback_query.edit_message_text("📛 فقط نام ساب‌دامنه را وارد کن:")

def execute_add_record(update, context):
    proxied = update.callback_query.data.split('|')[1].lower() == "true"
    rtype, name, content = context.user_data.pop("new_type"), context.user_data.pop("new_name"), context.user_data.pop("new_content")
    res = create_record(get_zone_id(), rtype, name, content, proxied)
    if res.get("success"):
        update.callback_query.edit_message_text(f"✅ رکورد `{rtype} {name}` افزوده شد.", parse_mode="Markdown")
        list_records_command(update, context, from_callback=True)
    else: update.callback_query.edit_message_text(f"❌ ساخت رکورد ناموفق بود: {res.get('errors', [{}])[0].get('message', 'Unknown')}")

def bulk_select_item(update, context):
    rid, page = update.callback_query.data.split('|')[1], int(update.callback_query.data.split('|')[2])
    selected = context.user_data.get('selected_records', [])
    if rid in selected: selected.remove(rid)
    else: selected.append(rid)
    context.user_data['selected_records'] = selected
    display_records_list(update, context, page=page)

def bulk_delete_confirm_view(update, context):
    selected_ids = context.user_data.get('selected_records', [])
    if not selected_ids: update.callback_query.answer("هیچ رکوردی انتخاب نشده است!", show_alert=True); return
    kb = [[InlineKeyboardButton(f"✅ بله، {len(selected_ids)} مورد حذف شود", callback_data="bulk_delete_execute")], [InlineKeyboardButton("❌ خیر، لغو", callback_data="bulk_start")]]
    update.callback_query.edit_message_text(f"⚠️ آیا از حذف {len(selected_ids)} رکورد انتخاب شده اطمینان دارید؟", reply_markup=InlineKeyboardMarkup(kb))

def execute_bulk_delete(update, context):
    selected_ids = context.user_data.get('selected_records', [])
    query = update.callback_query
    query.edit_message_text(f"⏳ در حال حذف {len(selected_ids)} رکورد...")
    success, fail = 0, 0
    zone_id = get_zone_id()
    for rid in selected_ids:
        if delete_record(zone_id, rid).get("success"): success += 1
        else: fail += 1
    msg = f"عملیات حذف گروهی تمام شد:\n\n✅ **{success}** رکورد با موفقیت حذف شد.\n❌ **{fail}** مورد ناموفق بود."
    kb = [[InlineKeyboardButton("↩️ نمایش لیست بروز شده", callback_data="list")]]
    reply_markup = InlineKeyboardMarkup(kb)
    query.edit_message_text(msg, reply_markup=reply_markup, parse_mode="Markdown")

def bulk_change_ip_start(update, context):
    selected_ids = context.user_data.get('selected_records', [])
    if not selected_ids: update.callback_query.answer("هیچ رکوردی انتخاب نشده است!", show_alert=True); return
    context.user_data['is_bulk_ip_change'] = True
    update.callback_query.edit_message_text(f"📥 لطفا IP جدید را برای {len(selected_ids)} رکورد انتخاب شده وارد کنید:")

def execute_bulk_change_ip(update, context):
    query = update.callback_query
    details = context.user_data.pop('bulk_ip_confirm_details', {})
    if not details:
        query.edit_message_text("❌ خطای داخلی، اطلاعات یافت نشد."); return
    
    new_ip, record_ids = details['new_ip'], details['record_ids']
    query.edit_message_text(f"⏳ در حال تغییر IP برای {len(record_ids)} رکورد به `{new_ip}`...", parse_mode="Markdown")
    
    success, fail, skipped = 0, 0, 0
    all_records_map = context.user_data.get("records", {})
    zone_id = get_zone_id()
    
    for rid in record_ids:
        record = all_records_map.get(rid)
        if not record: fail += 1; continue
        if record['type'] in ['A', 'AAAA']:
            res = update_record(zone_id, rid, record['type'], record['name'], new_ip, record.get('proxied', False))
            if res.get("success"): success += 1
            else: fail += 1
        else: skipped += 1
        
    msg = (f"عملیات تغییر IP گروهی تمام شد:\n\n"
           f"✅ **{success}** رکورد با موفقیت آپدیت شد.\n"
           f"⏭ **{skipped}** رکورد (نوع غیر IP) نادیده گرفته شد.\n"
           f"❌ **{fail}** مورد ناموفق بود.")
    kb = [[InlineKeyboardButton("↩️ نمایش لیست بروز شده", callback_data="list")]]
    reply_markup = InlineKeyboardMarkup(kb)
    query.edit_message_text(msg, reply_markup=reply_markup, parse_mode="Markdown")


# --- Command Handlers ---
def start_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    update.message.reply_text("سلام! به ربات مدیریت کلادفلر خوش آمدید.\nبرای شروع /list را بزنید.")

def list_records_command(update: Update, context: CallbackContext, from_callback=False):
    if not is_admin(update): update.message.reply_text("❌ دسترسی ندارید."); return
    clear_state(context)
    context.user_data.pop('all_records', None)
    display_records_list(update, context, page=0)

def search_command(update: Update, context: CallbackContext, from_callback=False):
    if not is_admin(update): return
    clear_state(context)
    context.user_data['is_searching'] = True
    text, kb = "🔎 لطفاً بخشی از نام رکورد مورد نظر را وارد کنید:", InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو جستجو", callback_data="list")]])
    if from_callback:
        update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        update.message.reply_text(text, reply_markup=kb)

def bulk_command(update: Update, context: CallbackContext, from_callback=False):
    if not is_admin(update): return
    clear_state(context)
    context.user_data['is_bulk_mode'] = True
    context.user_data['selected_records'] = []
    context.user_data.pop('all_records', None)
    display_records_list(update, context, page=0)

def backup_command(update: Update, context: CallbackContext):
    if not is_admin(update): update.message.reply_text("⛔️ دسترسی ندارید."); return
    zone_id = get_zone_id()
    if not zone_id: update.message.reply_text("❌ Zone ID پیدا نشد."); return
    update.message.reply_text("⏳ در حال تهیه بکاپ...")
    records = get_dns_records(zone_id)
    backup_file = "dns_backup.json"
    with open(backup_file, "w") as f: json.dump(records, f, indent=2)
    with open(backup_file, "rb") as f: update.message.reply_document(f, filename="dns_records_backup.json")
    os.remove(backup_file)

def restore_command(update: Update, context: CallbackContext):
    if not is_admin(update): update.message.reply_text("⛔️ دسترسی ندارید."); return
    update.message.reply_text("برای بازیابی، فایل `dns_backup.json` را ارسال کنید.")

def handle_document(update: Update, context: CallbackContext):
    if not is_admin(update): return
    doc = update.message.document
    if not doc.file_name.endswith('.json'):
        update.message.reply_text("❌ فایل ارسال شده باید با فرمت .json باشد."); return
    file, file_content = doc.get_file(), file.download_as_bytearray()
    try: backup_records = json.loads(file_content)
    except json.JSONDecodeError: update.message.reply_text("❌ محتوای فایل JSON معتبر نیست."); return
    zone_id = get_zone_id()
    if not zone_id: update.message.reply_text("❌ Zone ID پیدا نشد."); return
    update.message.reply_text("⏳ در حال پردازش فایل و بازیابی رکوردها...")
    existing_map = {(r["type"], r["name"]): r for r in get_dns_records(zone_id)}
    restored, skipped, failed = 0, 0, 0
    for r in backup_records:
        if (r["type"], r["name"]) in existing_map: skipped += 1; continue
        if create_record(zone_id, r["type"], r["name"], r["content"], r.get("proxied", False)).get("success"): restored += 1
        else: failed += 1
    update.message.reply_text(f"🔁 عملیات بازیابی تمام شد:\n✅ افزوده شده: {restored}\n⏭ تکراری: {skipped}\n❌ ناموفق: {failed}")
    list_records_command(update, context)

def main():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(CommandHandler("list", list_records_command))
    dp.add_handler(CommandHandler("search", search_command))
    dp.add_handler(CommandHandler("bulk", bulk_command))
    dp.add_handler(CommandHandler("backup", backup_command))
    dp.add_handler(CommandHandler("restore", restore_command))
    dp.add_handler(CallbackQueryHandler(handle_callback))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))
    dp.add_handler(MessageHandler(Filters.document.mime_type("application/json"), handle_document))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
