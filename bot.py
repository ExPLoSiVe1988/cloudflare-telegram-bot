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
    "A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV",
    "LOC", "SPF", "CERT", "DNSKEY", "DS", "NAPTR",
    "SMIMEA", "SSHFP", "SVCB", "TLSA", "URI"
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
    # Get all records, not just the first page
    all_records = []
    page = 1
    while True:
        params = {'per_page': 100, 'page': page}
        r = requests.get(url, headers=HEADERS, params=params)
        if not r.ok:
            return []
        data = r.json()
        all_records.extend(data.get("result", []))
        if data['result_info']['page'] >= data['result_info']['total_pages']:
            break
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

# --- NEW FEATURE: PAGINATION & DISPLAY LOGIC ---
def display_records_list(update: Update, context: CallbackContext, page=0):
    """The core function to display records with pagination, search, and bulk mode."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    
    # Reset modes if not coming from a page turn
    if query and not query.data.startswith("list_page"):
        context.user_data.pop('is_bulk_mode', None)
        context.user_data.pop('selected_records', None)
        context.user_data.pop('search_query', None)
    
    # Fetch all records if not already in context
    if 'all_records' not in context.user_data:
        zone_id = get_zone_id()
        if not zone_id:
            context.bot.send_message(chat_id, "❌ Zone ID پیدا نشد.")
            return
        context.user_data['all_records'] = get_dns_records(zone_id)

    all_records = context.user_data.get('all_records', [])
    
    # Apply search filter if active
    search_query = context.user_data.get('search_query')
    if search_query:
        records_to_display = [r for r in all_records if search_query.lower() in r['name'].lower()]
        message_text = f"📄 نتایج جستجو برای «{search_query}»:"
    else:
        records_to_display = all_records
        message_text = "📄 لیست تمام رکوردها:"
        
    if not records_to_display:
        context.bot.send_message(chat_id, "هیچ رکوردی پیدا نشد.")
        return

    # Store records map for easy access
    context.user_data["records"] = {r['id']: r for r in all_records}
    
    # Pagination logic
    start_index = page * RECORDS_PER_PAGE
    end_index = start_index + RECORDS_PER_PAGE
    records_on_page = records_to_display[start_index:end_index]
    
    buttons = []
    is_bulk_mode = context.user_data.get('is_bulk_mode', False)
    selected_records = context.user_data.get('selected_records', [])

    for r in records_on_page:
        proxy_icon = "☁️" if r.get('proxied') else "⬜️"
        
        if is_bulk_mode:
            check_icon = "✅" if r['id'] in selected_records else "⬜️"
            button_text = f"{check_icon} {r['type']} {r['name']}"
            callback_data = f"bulk_select|{r['id']}|{page}"
        else:
            button_text = f"{proxy_icon} {r['type']} {r['name']}"
            callback_data = f"select|{r['id']}"
        
        buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    # Pagination buttons
    pagination_buttons = []
    if page > 0:
        pagination_buttons.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"list_page|{page - 1}"))
    if end_index < len(records_to_display):
        pagination_buttons.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"list_page|{page + 1}"))
    
    if pagination_buttons:
        buttons.append(pagination_buttons)

    # Main action buttons (bottom row)
    if is_bulk_mode:
        buttons.append([
            InlineKeyboardButton(f"🗑 حذف انتخاب شده‌ها ({len(selected_records)})", callback_data="bulk_delete_confirm"),
        ])
        buttons.append([InlineKeyboardButton("❌ لغو عملیات گروهی", callback_data="list")])
    else:
        buttons.append([
            InlineKeyboardButton("➕ افزودن رکورد", callback_data="add"),
            InlineKeyboardButton("🔎 جستجو", callback_data="search_start"),
            InlineKeyboardButton("👥 عملیات گروهی", callback_data="bulk_start")
        ])

    reply_markup = InlineKeyboardMarkup(buttons)
    
    if query:
        query.edit_message_text(message_text, reply_markup=reply_markup)
    else:
        context.bot.send_message(chat_id, message_text, reply_markup=reply_markup)


def list_records_command(update: Update, context: CallbackContext):
    """Handler for /list command. Clears state and shows first page."""
    if not is_admin(update):
        update.message.reply_text("❌ دسترسی ندارید.")
        return
    # Clear previous data
    context.user_data.pop('all_records', None)
    display_records_list(update, context, page=0)


def handle_callback(update: Update, context: CallbackContext):
    """Handles all callback queries from inline keyboards."""
    query = update.callback_query
    query.answer()
    data = query.data.split('|')
    cmd = data[0]

    records = context.user_data.get("records", {})
    
    # --- Navigation ---
    if cmd == "list":
        display_records_list(update, context, page=0)
    
    elif cmd == "list_page":
        page = int(data[1])
        display_records_list(update, context, page=page)

    # --- Record Actions ---
    elif cmd == "select":
        rid = data[1]
        record = records.get(rid)
        if not record:
            query.edit_message_text("❌ رکورد پیدا نشد.")
            return
        
        proxy_icon = "☁️" if record.get('proxied') else "⬜️"
        proxy_text = "فعال (نارنجی)" if record.get('proxied') else "غیرفعال (خاکستری)"
        
        kb = [
            [InlineKeyboardButton("📝 تغییر مقدار", callback_data=f"edit|{rid}")],
            [InlineKeyboardButton(f"تغییر وضعیت پروکسی {proxy_icon}", callback_data=f"toggle_proxy|{rid}")],
            [InlineKeyboardButton("🗑 حذف", callback_data=f"delete|{rid}")],
            [InlineKeyboardButton("↩️ بازگشت به لیست", callback_data="list")]
        ]
        text = (f"📛 `{record['type']} {record['name']}`\n"
                f"📝 مقدار: `{record['content']}`\n"
                f"☁️ پروکسی: `{proxy_text}`")
        query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    elif cmd == "edit":
        rid = data[1]
        record = records.get(rid)
        context.user_data["edit"] = {"id": record["id"], "type": record["type"], "name": record["name"], "old": record["content"]}
        
        prompt_text = "📥 مقدار (Content) جدید را برای `{}` وارد کنید:"
        if record['type'] in ['A', 'AAAA']:
            prompt_text = "📥 مقدار IP جدید را برای `{}` وارد کنید:"
        query.message.reply_text(prompt_text.format(f"`{record['name']}`"), parse_mode="Markdown")

    elif cmd == "toggle_proxy":
        rid = data[1]
        record = records.get(rid)
        zone_id = get_zone_id()
        new_proxied_status = not record.get('proxied', False)
        
        res = update_record(zone_id, rid, record['type'], record['name'], record['content'], new_proxied_status)
        
        if res.get("success"):
            # Update local record cache
            context.user_data['records'][rid]['proxied'] = new_proxied_status
            context.user_data['all_records'] = list(context.user_data['records'].values())
            
            # Refresh the view for the same record
            # Create a fake Update object to call the select handler
            class FakeUpdate:
                def __init__(self, q):
                    self.callback_query = q
            
            class FakeCallbackQuery:
                def __init__(self, q, d):
                    self.message = q.message
                    self.data = d
                def answer(self):
                    pass

            fake_query = FakeCallbackQuery(query, f"select|{rid}")
            handle_callback(FakeUpdate(fake_query), context)
        else:
            query.message.edit_text("❌ خطا در تغییر وضعیت پروکسی.")


    elif cmd == "delete":
        rid = data[1]
        record = records.get(rid)
        kb = [
            [InlineKeyboardButton("✅ تایید حذف", callback_data=f"delete_confirm|{rid}")],
            [InlineKeyboardButton("↩️ بازگشت", callback_data=f"select|{rid}")]
        ]
        query.edit_message_text(f"⚠️ حذف رکورد `{record['name']}` ؟", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    elif cmd == "delete_confirm":
        rid = data[1]
        record = records.get(rid, {})
        zone_id = get_zone_id()
        res = delete_record(zone_id, rid)
        if res.get("success"):
            context.user_data.pop('all_records', None) # Force refresh
            query.edit_message_text(f"✅ رکورد `{record.get('name', 'N/A')}` حذف شد.")
            display_records_list(update, context, page=0)
        else:
            query.edit_message_text("❌ خطا در حذف رکورد.")

    elif cmd == "confirm_change":
        confirm_change(update, context)

    # --- Add Record Flow ---
    elif cmd == "add":
        types = DNS_RECORD_TYPES
        buttons = [InlineKeyboardButton(t, callback_data=f"add_type|{t}") for t in types]
        buttons_3col = chunk_list(buttons, 3)
        buttons_3col.append([InlineKeyboardButton("↩️ بازگشت به لیست", callback_data="list")])
        query.edit_message_text("🆕 نوع تایپ رکورد را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(buttons_3col))

    elif cmd == "add_type":
        rtype = data[1]
        context.user_data["new_type"] = rtype
        context.user_data["add_step"] = "name"
        query.edit_message_text("📛 فقط نام ساب‌دامنه را وارد کن (بدون نام دامنه اصلی):")

    elif cmd == "add_proxied":
        proxied = data[1].lower() == "true"
        rtype = context.user_data.pop("new_type")
        name = context.user_data.pop("new_name")
        content = context.user_data.pop("new_content")
        zone_id = get_zone_id()
        res = create_record(zone_id, rtype, name, content, proxied)
        if res.get("success"):
            context.user_data.pop('all_records', None)
            query.edit_message_text(f"✅ رکورد `{rtype} {name}` با موفقیت افزوده شد.", parse_mode="Markdown")
            display_records_list(update, context, page=0)
        else:
            query.edit_message_text(f"❌ ساخت رکورد ناموفق بود. خطا: {res.get('errors', [{}])[0].get('message', 'Unknown')}")
    
    # --- NEW FEATURE: Search Flow ---
    elif cmd == "search_start":
        context.user_data['is_searching'] = True
        query.edit_message_text("🔎 لطفاً بخشی از نام رکورد مورد نظر را وارد کنید:")

    # --- NEW FEATURE: Bulk Actions Flow ---
    elif cmd == "bulk_start":
        context.user_data['is_bulk_mode'] = True
        context.user_data['selected_records'] = []
        display_records_list(update, context, page=0)
        
    elif cmd == "bulk_select":
        rid = data[1]
        page = int(data[2])
        selected = context.user_data.get('selected_records', [])
        if rid in selected:
            selected.remove(rid)
        else:
            selected.append(rid)
        context.user_data['selected_records'] = selected
        display_records_list(update, context, page=page)
        
    elif cmd == "bulk_delete_confirm":
        selected_ids = context.user_data.get('selected_records', [])
        if not selected_ids:
            query.answer("هیچ رکوردی انتخاب نشده است!", show_alert=True)
            return
        
        kb = [
            [InlineKeyboardButton(f"✅ بله، {len(selected_ids)} مورد حذف شود", callback_data="bulk_delete_execute")],
            [InlineKeyboardButton("❌ خیر، لغو", callback_data="bulk_start")]
        ]
        query.edit_message_text(f"⚠️ آیا از حذف {len(selected_ids)} رکورد انتخاب شده اطمینان دارید؟", reply_markup=InlineKeyboardMarkup(kb))
        
    elif cmd == "bulk_delete_execute":
        selected_ids = context.user_data.get('selected_records', [])
        zone_id = get_zone_id()
        
        success_count = 0
        fail_count = 0
        
        query.edit_message_text(f" در حال حذف {len(selected_ids)} رکورد... لطفاً صبر کنید.")
        
        for rid in selected_ids:
            res = delete_record(zone_id, rid)
            if res.get("success"):
                success_count += 1
            else:
                fail_count += 1
        
        context.user_data.pop('all_records', None)
        context.user_data.pop('is_bulk_mode', None)
        context.user_data.pop('selected_records', None)
        
        query.edit_message_text(f"عملیات گروهی تمام شد:\n✅ {success_count} رکورد با موفقیت حذف شد.\n❌ {fail_count} مورد ناموفق بود.")
        display_records_list(update, context, page=0)

def handle_text(update: Update, context: CallbackContext):
    if not is_admin(update): return

    text = update.message.text.strip()

    # --- Search Input ---
    if context.user_data.get('is_searching'):
        context.user_data.pop('is_searching')
        context.user_data['search_query'] = text
        display_records_list(update, context, page=0)
        return

    # --- Edit Input ---
    if "edit" in context.user_data:
        data = context.user_data.pop("edit")
        context.user_data["confirm"] = {"id": data["id"], "type": data["type"], "name": data["name"], "old": data["old"], "new": text}
        kb = [[InlineKeyboardButton("✅ تایید", callback_data="confirm_change")], [InlineKeyboardButton("↩️ بازگشت به لیست", callback_data="list")]]
        update.message.reply_text(f"🔄 `{data['old']}` ➡️ `{text}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    # --- Add Input ---
    if "add_step" in context.user_data:
        step = context.user_data["add_step"]
        if step == "name":
            context.user_data["new_name"] = f"{text}.{CF_TOKEN_NAME}"
            context.user_data["add_step"] = "content"
            record_type = context.user_data.get("new_type")
            prompt_text = "📥 مقدار (Content) را وارد کنید:"
            if record_type in ['A', 'AAAA']: prompt_text = "📥 مقدار IP را وارد کنید:"
            update.message.reply_text(prompt_text)
        elif step == "content":
            context.user_data["new_content"] = text
            context.user_data.pop("add_step")
            kb = [
                [InlineKeyboardButton("DNS Only", callback_data="add_proxied|false")],
                [InlineKeyboardButton("Proxied", callback_data="add_proxied|true")]
            ]
            update.message.reply_text("🌐 حالت پروکسی رکورد را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(kb))

def confirm_change(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    info = context.user_data.pop("confirm", {})
    zone_id = get_zone_id()
    original_record = context.user_data.get("records", {}).get(info["id"], {})
    proxied_status = original_record.get("proxied", False)

    res = update_record(zone_id, info["id"], info["type"], info["name"], info["new"], proxied=proxied_status)
    if res.get("success"):
        context.user_data.pop('all_records', None)
        query.edit_message_text("✅ مقدار با موفقیت به‌روزرسانی شد.")
        display_records_list(update, context, page=0)
    else:
        query.edit_message_text(f"❌ خطا در بروزرسانی. {res.get('errors', [{}])[0].get('message', '')}")

# --- Command Handlers ---
def start_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    update.message.reply_text("سلام! به ربات مدیریت کلادفلر خوش آمدید. برای شروع /list را بزنید.")

def main():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True, persistence=None)
    dp = updater.dispatcher

    # Command Handlers
    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(CommandHandler("list", list_records_command))
    dp.add_handler(CommandHandler("search", lambda u,c: u.message.reply_text("🔎 لطفاً بخشی از نام رکورد مورد نظر را وارد کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو جستجو", callback_data="list")]]))) and setattr(context.user_data, 'is_searching', True))
    dp.add_handler(CommandHandler("bulk", lambda u,c: setattr(context.user_data, 'is_bulk_mode', True) or setattr(context.user_data, 'selected_records', []) or display_records_list(u, c, page=0)))

    # Callback Query Handler (Main Router)
    dp.add_handler(CallbackQueryHandler(handle_callback))

    # Message Handler for text inputs
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
