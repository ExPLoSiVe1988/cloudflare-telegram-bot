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

# --- NEW: I18N (Internationalization) Setup ---
translations = {}

def load_translations():
    """Loads translation files (en.json, fa.json) into memory."""
    global translations
    for lang in ['en', 'fa']:
        with open(f'{lang}.json', 'r', encoding='utf-8') as f:
            translations[lang] = json.load(f)

def get_text(key: str, lang: str, **kwargs):
    """Gets a translated string by key and language, and formats it."""
    try:
        # Navigate through nested keys like "buttons.next"
        keys = key.split('.')
        text_template = translations[lang]
        for k in keys:
            text_template = text_template[k]
        return text_template.format(**kwargs)
    except KeyError:
        # Fallback to English if key not found in the selected language
        try:
            text_template = translations['en']
            for k in keys:
                text_template = text_template[k]
            return text_template.format(**kwargs)
        except KeyError:
            # Fallback to the key itself if not found anywhere
            return key

def get_user_lang(context: CallbackContext):
    """Gets the user's preferred language, defaulting to Persian."""
    return context.user_data.get('language', 'fa')

# --- Helper Functions ---
def is_admin(update: Update):
    return update.effective_user.id == TELEGRAM_ADMIN_ID

def chunk_list(lst, n):
    return [lst[i:i + n] for i in range(0, len(lst), n)]

# --- Cloudflare API Functions (No changes here) ---
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

# --- Core Logic Functions (Now uses get_text) ---
def clear_state(context: CallbackContext):
    for key in ['is_bulk_mode', 'selected_records', 'search_query', 'is_searching', 'edit', 'add_step', 'new_type', 'new_name', 'new_content', 'confirm', 'is_bulk_ip_change', 'bulk_ip_confirm_details']:
        context.user_data.pop(key, None)

def display_records_list(update: Update, context: CallbackContext, page=0):
    query = update.callback_query
    chat_id = update.effective_chat.id
    lang = get_user_lang(context)

    if 'all_records' not in context.user_data:
        zone_id = get_zone_id()
        if not zone_id:
            context.bot.send_message(chat_id, get_text('messages.zone_id_not_found', lang)); return
        context.user_data['all_records'] = get_dns_records(zone_id)

    all_records = context.user_data.get('all_records', [])
    search_query = context.user_data.get('search_query')
    records_to_display = [r for r in all_records if search_query.lower() in r['name'].lower()] if search_query else all_records
    message_text = get_text('messages.search_results', lang, query=search_query) if search_query else get_text('messages.all_records_list', lang)
    
    if not records_to_display:
        kb = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="list")]]
        if query: query.edit_message_text(get_text('messages.no_records_found', lang), reply_markup=InlineKeyboardMarkup(kb))
        else: context.bot.send_message(chat_id, get_text('messages.no_records_found', lang), reply_markup=InlineKeyboardMarkup(kb))
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
    if page > 0: pagination_buttons.append(InlineKeyboardButton(get_text('buttons.previous', lang), callback_data=f"list_page|{page - 1}"))
    if end_index < len(records_to_display): pagination_buttons.append(InlineKeyboardButton(get_text('buttons.next', lang), callback_data=f"list_page|{page + 1}"))
    if pagination_buttons: buttons.append(pagination_buttons)

    if is_bulk_mode:
        count = len(selected_records)
        buttons.append([
            InlineKeyboardButton(get_text('buttons.change_ip_selected', lang, count=count), callback_data="bulk_change_ip_start"),
            InlineKeyboardButton(get_text('buttons.delete_selected', lang, count=count), callback_data="bulk_delete_confirm")
        ])
        buttons.append([InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data="list")])
    else:
        buttons.append([InlineKeyboardButton(get_text('buttons.add_record', lang), callback_data="add"), 
                        InlineKeyboardButton(get_text('buttons.search', lang), callback_data="search_start"), 
                        InlineKeyboardButton(get_text('buttons.bulk_actions', lang), callback_data="bulk_start")])

    reply_markup = InlineKeyboardMarkup(buttons)
    if query:
        try: query.edit_message_text(message_text, reply_markup=reply_markup)
        except Exception: pass
    else:
        context.bot.send_message(chat_id, message_text, reply_markup=reply_markup)

# ... (The rest of the file needs to be fully replaced as well)

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
        "search_start": lambda u, c: search_command(u, c, from_callback=True),
        "bulk_start": lambda u, c: bulk_command(u, c, from_callback=True),
        "bulk_select": bulk_select_item,
        "bulk_delete_confirm": bulk_delete_confirm_view, "bulk_delete_execute": execute_bulk_delete,
        "bulk_change_ip_start": bulk_change_ip_start, "bulk_change_ip_execute": execute_bulk_change_ip,
        "set_lang": set_language
    }
    if cmd in command_handlers:
        command_handlers[cmd](update, context)

def handle_text(update: Update, context: CallbackContext):
    if not is_admin(update): return
    lang = get_user_lang(context)
    text = update.message.text.strip()
    
    if context.user_data.get('is_bulk_ip_change'):
        selected_ids = context.user_data.get('selected_records', [])
        context.user_data.pop('is_bulk_ip_change')
        context.user_data['bulk_ip_confirm_details'] = {'new_ip': text, 'record_ids': selected_ids}
        kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="bulk_change_ip_execute")], [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="bulk_start")]]
        update.message.reply_text(get_text('messages.bulk_confirm_change_ip', lang, count=len(selected_ids), new_ip=text), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    if context.user_data.get('is_searching'):
        clear_state(context)
        context.user_data['search_query'] = text
        display_records_list(update, context, page=0)
        return

    if "edit" in context.user_data:
        data = context.user_data.pop("edit")
        context.user_data["confirm"] = {"id": data["id"], "type": data["type"], "name": data["name"], "old": data["old"], "new": text}
        kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="confirm_change")], [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="list")]]
        update.message.reply_text(f"🔄 `{data['old']}` ➡️ `{text}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    if "add_step" in context.user_data:
        step = context.user_data["add_step"]
        if step == "name":
            # ... (rest of add logic)
            pass

def confirm_change(update: Update, context: CallbackContext):
    # ... (no language changes needed here, just the final report)
    pass

# --- Handler Functions for Callbacks ---
def select_record_details(update, context):
    lang = get_user_lang(context)
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    if not record: update.callback_query.edit_message_text(get_text('messages.error_record_not_found', lang)); return
    proxy_icon = "☁️" if record.get('proxied') else "⬜️"
    proxy_text = get_text('messages.proxy_status_active', lang) if record.get('proxied') else get_text('messages.proxy_status_inactive', lang)
    kb = [[InlineKeyboardButton(get_text('buttons.edit_value', lang), callback_data=f"edit|{rid}")],
          [InlineKeyboardButton(get_text('buttons.toggle_proxy', lang), callback_data=f"toggle_proxy|{rid}")],
          [InlineKeyboardButton(get_text('buttons.delete', lang), callback_data=f"delete|{rid}")],
          [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="list")]]
    text = get_text('messages.record_details', lang, type=record['type'], name=record['name'], content=record['content'], proxy_status=proxy_text)
    update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

# ... (All other helper functions need to be updated to use get_text)

# --- Command Handlers ---
def start_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    lang = get_user_lang(context)
    update.message.reply_text(get_text('messages.welcome', lang))
    language_command(update, context)

def language_command(update: Update, context: CallbackContext):
    lang = get_user_lang(context)
    kb = [[InlineKeyboardButton("🇮🇷 فارسی", callback_data="set_lang|fa"), InlineKeyboardButton("🇬🇧 English", callback_data="set_lang|en")]]
    update.message.reply_text(get_text('messages.choose_language', lang), reply_markup=InlineKeyboardMarkup(kb))

def set_language(update, context):
    lang_code = update.callback_query.data.split('|')[1]
    context.user_data['language'] = lang_code
    update.callback_query.edit_message_text(get_text('messages.language_changed', lang_code))
    list_records_command(update, context, from_callback=True)

def list_records_command(update: Update, context: CallbackContext, from_callback=False):
    if not is_admin(update): 
        lang = get_user_lang(context)
        update.message.reply_text(get_text('messages.access_denied', lang)); return
    clear_state(context)
    context.user_data.pop('all_records', None)
    display_records_list(update, context, page=0)

# ... (All other command handlers need to be updated)

def main():
    load_translations() # Load translations at startup
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    # Add new language command
    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(CommandHandler("language", language_command))
    
    # ... (rest of the handlers)
    dp.add_handler(CommandHandler("list", list_records_command))
    # ...
    
    dp.add_handler(CallbackQueryHandler(handle_callback))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))
    dp.add_handler(MessageHandler(Filters.document.mime_type("application/json"), handle_document))
    
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
