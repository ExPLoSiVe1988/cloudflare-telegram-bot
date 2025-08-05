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

HEADERS = { "Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json" }
DNS_RECORD_TYPES = [
    "A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "LOC", "SPF", "CERT", "DNSKEY",
    "DS", "NAPTR", "SMIMEA", "SSHFP", "SVCB", "TLSA", "URI"
]
RECORDS_PER_PAGE = 10

# --- I18N (Internationalization) Setup ---
translations = {}
def load_translations():
    global translations
    for lang in ['en', 'fa']:
        try:
            with open(f'{lang}.json', 'r', encoding='utf-8') as f:
                translations[lang] = json.load(f)
        except FileNotFoundError:
            print(f"FATAL: Translation file {lang}.json not found! Please create it.")
            exit(1)
        except json.JSONDecodeError:
            print(f"FATAL: Could not decode {lang}.json. Please check its syntax.")
            exit(1)

def get_text(key: str, lang: str, **kwargs):
    try:
        keys = key.split('.')
        text_template = translations.get(lang, translations.get('en', {}))
        for k in keys:
            text_template = text_template[k]
        return text_template.format(**kwargs)
    except (KeyError, AttributeError):
        return f"Untranslated key: {key}"

def get_user_lang(context: CallbackContext):
    return context.user_data.get('language', 'fa')

# --- Helper & API Functions ---
def is_admin(update: Update): return update.effective_user.id == TELEGRAM_ADMIN_ID
def chunk_list(lst, n): return [lst[i:i + n] for i in range(0, len(lst), n)]
def get_zone_id():
    r = requests.get("https://api.cloudflare.com/client/v4/zones", headers=HEADERS, params={"name": CF_TOKEN_NAME})
    return r.json()["result"][0]["id"] if r.ok and r.json().get("result") else None
def get_dns_records(zone_id):
    all_records, page = [], 1
    while True:
        r = requests.get(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records", headers=HEADERS, params={'per_page': 100, 'page': page})
        if not r.ok: return []
        data = r.json(); all_records.extend(data.get("result", []))
        if data['result_info']['page'] >= data['result_info']['total_pages']: break
        page += 1
    return all_records
def update_record(zone_id, rid, rtype, name, content, proxied):
    return requests.put(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{rid}", headers=HEADERS, json={"type": rtype, "name": name, "content": content, "ttl": 1, "proxied": proxied}).json()
def delete_record(zone_id, rid):
    return requests.delete(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{rid}", headers=HEADERS).json()
def create_record(zone_id, rtype, name, content, proxied):
    return requests.post(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records", headers=HEADERS, json={"type": rtype, "name": name, "content": content, "ttl": 1, "proxied": proxied}).json()

# --- Core Logic ---
def clear_state(context: CallbackContext):
    for key in ['is_bulk_mode', 'selected_records', 'search_query', 'is_searching', 'edit', 'add_step', 'confirm', 'is_bulk_ip_change', 'bulk_ip_confirm_details']:
        context.user_data.pop(key, None)

def display_records_list(update: Update, context: CallbackContext, page=0):
    query = update.callback_query; lang = get_user_lang(context)
    if 'all_records' not in context.user_data:
        zone_id = get_zone_id()
        if not zone_id: context.bot.send_message(update.effective_chat.id, get_text('messages.zone_id_not_found', lang)); return
        context.user_data['all_records'] = get_dns_records(zone_id)
    all_records = context.user_data.get('all_records', [])
    search_query = context.user_data.get('search_query')
    records_to_display = [r for r in all_records if search_query.lower() in r['name'].lower()] if search_query else all_records
    message_text = get_text('messages.search_results', lang, query=search_query) if search_query else get_text('messages.all_records_list', lang)
    if not records_to_display:
        kb = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="list")]]
        if query: query.edit_message_text(get_text('messages.no_records_found', lang), reply_markup=InlineKeyboardMarkup(kb))
        else: context.bot.send_message(update.effective_chat.id, get_text('messages.no_records_found', lang), reply_markup=InlineKeyboardMarkup(kb))
        return
    context.user_data["records"] = {r['id']: r for r in all_records}
    start_index, end_index = page * RECORDS_PER_PAGE, (page + 1) * RECORDS_PER_PAGE
    records_on_page = records_to_display[start_index:end_index]
    buttons = []
    is_bulk_mode, selected_records = context.user_data.get('is_bulk_mode', False), context.user_data.get('selected_records', [])
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
        buttons.append([InlineKeyboardButton(get_text('buttons.change_ip_selected', lang, count=count), callback_data="bulk_change_ip_start"), InlineKeyboardButton(get_text('buttons.delete_selected', lang, count=count), callback_data="bulk_delete_confirm")])
        buttons.append([InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data="list")])
    else:
        buttons.append([InlineKeyboardButton(get_text('buttons.add_record', lang), callback_data="add"), InlineKeyboardButton(get_text('buttons.search', lang), callback_data="search_start"), InlineKeyboardButton(get_text('buttons.bulk_actions', lang), callback_data="bulk_start")])
    reply_markup = InlineKeyboardMarkup(buttons)
    if query:
        try: query.edit_message_text(message_text, reply_markup=reply_markup)
        except Exception: pass
    else: context.bot.send_message(update.effective_chat.id, message_text, reply_markup=reply_markup)

def handle_callback(update: Update, context: CallbackContext):
    query = update.callback_query; query.answer()
    data = query.data.split('|'); cmd = data[0]
    command_handlers = {
        "list": list_records_command, "list_page": lambda u, c: display_records_list(u, c, page=int(data[1])),
        "select": select_record_details, "edit": edit_record_value, "toggle_proxy": toggle_proxy_confirm_view,
        "toggle_proxy_confirm": execute_toggle_proxy, "delete": delete_record_confirm_view,
        "delete_confirm": execute_delete_record, "confirm_change": confirm_change,
        "add": add_record_start, "add_type": add_record_set_type, "add_proxied": execute_add_record,
        "search_start": lambda u, c: search_command(u, c, from_callback=True),
        "bulk_start": lambda u, c: bulk_command(u, c, from_callback=True), "bulk_select": bulk_select_item,
        "bulk_delete_confirm": bulk_delete_confirm_view, "bulk_delete_execute": execute_bulk_delete,
        "bulk_change_ip_start": bulk_change_ip_start, "bulk_change_ip_execute": execute_bulk_change_ip,
        "set_lang": set_language
    }
    if cmd in command_handlers: command_handlers[cmd](update, context)

def handle_text(update: Update, context: CallbackContext):
    if not is_admin(update): return
    lang = get_user_lang(context); text = update.message.text.strip()
    if context.user_data.get('is_bulk_ip_change'):
        selected_ids = context.user_data.get('selected_records', [])
        context.user_data.pop('is_bulk_ip_change')
        context.user_data['bulk_ip_confirm_details'] = {'new_ip': text, 'record_ids': selected_ids}
        kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="bulk_change_ip_execute")], [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="bulk_start")]]
        update.message.reply_text(get_text('messages.bulk_confirm_change_ip', lang, count=len(selected_ids), new_ip=text), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return
    if context.user_data.get('is_searching'):
        clear_state(context); context.user_data['search_query'] = text
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
            context.user_data["new_name"] = f"{text}.{CF_TOKEN_NAME}"; context.user_data["add_step"] = "content"
            prompt_text = get_text('prompts.enter_ip', lang) if context.user_data.get("new_type") in ['A', 'AAAA'] else get_text('prompts.enter_content', lang)
            update.message.reply_text(prompt_text)
        elif step == "content":
            context.user_data["new_content"] = text; context.user_data.pop("add_step")
            kb = [[InlineKeyboardButton("DNS Only", callback_data="add_proxied|false")], [InlineKeyboardButton("Proxied", callback_data="add_proxied|true")]]
            update.message.reply_text(get_text('prompts.choose_proxy', lang), reply_markup=InlineKeyboardMarkup(kb))

def confirm_change(update: Update, context: CallbackContext):
    query = update.callback_query; query.answer(); lang = get_user_lang(context)
    info = context.user_data.pop("confirm", {})
    original_record = context.user_data.get("records", {}).get(info["id"], {})
    proxied_status = original_record.get("proxied", False)
    res = update_record(get_zone_id(), info["id"], info["type"], info["name"], info["new"], proxied_status)
    if res.get("success"):
        query.edit_message_text(get_text('messages.record_updated_successfully', lang, record_name=info['name']))
        list_records_command(update, context, from_callback=True)
    else: query.edit_message_text(get_text('messages.error_updating_record', lang, error=res.get('errors', [{}])[0].get('message', '')))

# --- Handler Functions for Callbacks ---
def select_record_details(update, context):
    lang = get_user_lang(context); rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    if not record: update.callback_query.edit_message_text(get_text('messages.no_records_found', lang)); return
    proxy_icon, proxy_text = ("☁️", get_text('messages.proxy_status_active', lang)) if record.get('proxied') else ("⬜️", get_text('messages.proxy_status_inactive', lang))
    kb = [[InlineKeyboardButton(get_text('buttons.edit_value', lang), callback_data=f"edit|{rid}")],
          [InlineKeyboardButton(get_text('buttons.toggle_proxy', lang), callback_data=f"toggle_proxy|{rid}")],
          [InlineKeyboardButton(get_text('buttons.delete', lang), callback_data=f"delete|{rid}")],
          [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="list")]]
    text = get_text('messages.record_details', lang, type=record['type'], name=record['name'], content=record['content'], proxy_status=proxy_text)
    update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

def edit_record_value(update, context):
    lang = get_user_lang(context); rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    context.user_data["edit"] = {"id": record["id"], "type": record["type"], "name": record["name"], "old": record["content"]}
    prompt_text = get_text('prompts.enter_ip', lang) if record['type'] in ['A', 'AAAA'] else get_text('prompts.enter_content', lang)
    update.callback_query.message.reply_text(prompt_text.format(f"`{record['name']}`"), parse_mode="Markdown")

def toggle_proxy_confirm_view(update, context):
    lang = get_user_lang(context); rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    current_status = get_text('messages.proxy_status_active', lang) if record.get('proxied') else get_text('messages.proxy_status_inactive', lang)
    new_status = get_text('messages.proxy_status_inactive', lang) if record.get('proxied') else get_text('messages.proxy_status_active', lang)
    kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"toggle_proxy_confirm|{rid}")], [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"select|{rid}")]]
    text = get_text('messages.confirm_proxy_toggle', lang, record_name=record['name'], current_status=current_status, new_status=new_status)
    update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

def execute_toggle_proxy(update, context):
    lang = get_user_lang(context); rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    new_proxied_status = not record.get('proxied', False)
    res = update_record(get_zone_id(), rid, record['type'], record['name'], record['content'], new_proxied_status)
    if res.get("success"):
        update.callback_query.answer(get_text('messages.proxy_toggled_successfully', lang, record_name=record['name']), show_alert=True)
        context.user_data['records'][rid]['proxied'] = new_proxied_status
        context.user_data['all_records'] = list(context.user_data['records'].values())
        select_record_details(update, context)
    else: update.callback_query.edit_message_text(get_text('messages.error_toggling_proxy', lang))

def delete_record_confirm_view(update, context):
    lang = get_user_lang(context); rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"delete_confirm|{rid}")], [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"select|{rid}")]]
    update.callback_query.edit_message_text(get_text('messages.confirm_delete_record', lang, record_name=record['name']), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

def execute_delete_record(update, context):
    lang = get_user_lang(context); rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid, {})
    if delete_record(get_zone_id(), rid).get("success"):
        update.callback_query.edit_message_text(get_text('messages.record_deleted_successfully', lang, record_name=record.get('name', 'N/A')))
        list_records_command(update, context, from_callback=True)
    else: update.callback_query.edit_message_text(get_text('messages.error_deleting_record', lang))

def add_record_start(update, context):
    lang = get_user_lang(context)
    buttons = [InlineKeyboardButton(t, callback_data=f"add_type|{t}") for t in DNS_RECORD_TYPES]
    buttons_3col = chunk_list(buttons, 3); buttons_3col.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="list")])
    update.callback_query.edit_message_text(get_text('prompts.choose_record_type', lang), reply_markup=InlineKeyboardMarkup(buttons_3col))

def add_record_set_type(update, context):
    context.user_data["new_type"] = update.callback_query.data.split('|')[1]; context.user_data["add_step"] = "name"
    update.callback_query.edit_message_text(get_text('prompts.enter_subdomain', lang))

def execute_add_record(update, context):
    lang = get_user_lang(context); proxied = update.callback_query.data.split('|')[1].lower() == "true"
    rtype, name, content = context.user_data.pop("new_type"), context.user_data.pop("new_name"), context.user_data.pop("new_content")
    res = create_record(get_zone_id(), rtype, name, content, proxied)
    if res.get("success"):
        update.callback_query.edit_message_text(get_text('messages.record_added_successfully', lang, rtype=rtype, name=name), parse_mode="Markdown")
        list_records_command(update, context, from_callback=True)
    else: update.callback_query.edit_message_text(get_text('messages.error_creating_record', lang, error=res.get('errors', [{}])[0].get('message', 'Unknown')))

def bulk_select_item(update, context):
    rid, page = update.callback_query.data.split('|')[1], int(update.callback_query.data.split('|')[2])
    selected = context.user_data.get('selected_records', []);
    if rid in selected: selected.remove(rid)
    else: selected.append(rid)
    context.user_data['selected_records'] = selected
    display_records_list(update, context, page=page)

def bulk_delete_confirm_view(update, context):
    lang = get_user_lang(context); selected_ids = context.user_data.get('selected_records', [])
    if not selected_ids: update.callback_query.answer(get_text('messages.bulk_no_selection', lang), show_alert=True); return
    kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="bulk_delete_execute")], [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="bulk_start")]]
    update.callback_query.edit_message_text(get_text('messages.bulk_confirm_delete', lang, count=len(selected_ids)), reply_markup=InlineKeyboardMarkup(kb))

def execute_bulk_delete(update, context):
    lang = get_user_lang(context); selected_ids = context.user_data.get('selected_records', [])
    query = update.callback_query; query.edit_message_text(get_text('messages.bulk_delete_progress', lang, count=len(selected_ids)))
    success, fail = 0, 0
    for rid in selected_ids:
        if delete_record(get_zone_id(), rid).get("success"): success += 1
        else: fail += 1
    msg = get_text('messages.bulk_delete_report', lang, success=success, fail=fail)
    kb = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="list")]]
    query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

def bulk_change_ip_start(update, context):
    lang = get_user_lang(context); selected_ids = context.user_data.get('selected_records', [])
    if not selected_ids: update.callback_query.answer(get_text('messages.bulk_no_selection', lang), show_alert=True); return
    context.user_data['is_bulk_ip_change'] = True
    update.callback_query.edit_message_text(get_text('messages.bulk_change_ip_prompt', lang, count=len(selected_ids)))

def execute_bulk_change_ip(update, context):
    lang = get_user_lang(context); query = update.callback_query
    details = context.user_data.pop('bulk_ip_confirm_details', {})
    if not details: query.edit_message_text(get_text('messages.internal_error', lang)); return
    new_ip, record_ids = details['new_ip'], details['record_ids']
    query.edit_message_text(get_text('messages.bulk_change_ip_progress', lang, count=len(record_ids), new_ip=new_ip), parse_mode="Markdown")
    success, fail, skipped = 0, 0, 0
    all_records_map, zone_id = context.user_data.get("records", {}), get_zone_id()
    for rid in record_ids:
        record = all_records_map.get(rid)
        if not record: fail += 1; continue
        if record['type'] in ['A', 'AAAA']:
            if update_record(zone_id, rid, record['type'], record['name'], new_ip, record.get('proxied', False)).get("success"): success += 1
            else: fail += 1
        else: skipped += 1
    msg = get_text('messages.bulk_change_ip_report', lang, success=success, skipped=skipped, fail=fail)
    kb = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="list")]]
    query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

# --- Command Handlers ---
def start_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
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
    if not is_admin(update): update.message.reply_text(get_text('messages.access_denied', lang=get_user_lang(context))); return
    clear_state(context); context.user_data.pop('all_records', None)
    display_records_list(update, context, page=0)

def search_command(update: Update, context: CallbackContext, from_callback=False):
    if not is_admin(update): return
    lang = get_user_lang(context); clear_state(context); context.user_data['is_searching'] = True
    text, kb = get_text('prompts.enter_search_query', lang), InlineKeyboardMarkup([[InlineKeyboardButton(get_text('buttons.cancel_search', lang), callback_data="list")]])
    if from_callback: update.callback_query.edit_message_text(text, reply_markup=kb)
    else: update.message.reply_text(text, reply_markup=kb)

def bulk_command(update: Update, context: CallbackContext, from_callback=False):
    if not is_admin(update): return
    clear_state(context); context.user_data['is_bulk_mode'] = True; context.user_data['selected_records'] = []
    context.user_data.pop('all_records', None)
    display_records_list(update, context, page=0)

def backup_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    lang = get_user_lang(context); zone_id = get_zone_id()
    if not zone_id: update.message.reply_text(get_text('messages.zone_id_not_found', lang)); return
    update.message.reply_text(get_text('messages.backup_in_progress', lang))
    records = get_dns_records(zone_id); backup_file = "dns_backup.json"
    with open(backup_file, "w") as f: json.dump(records, f, indent=2)
    with open(backup_file, "rb") as f: update.message.reply_document(f, filename="dns_records_backup.json")
    os.remove(backup_file)

def restore_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    update.message.reply_text(get_text('messages.restore_prompt', lang=get_user_lang(context)))

def handle_document(update: Update, context: CallbackContext):
    if not is_admin(update): return
    lang = get_user_lang(context); doc = update.message.document
    if not doc.file_name.endswith('.json'):
        update.message.reply_text(get_text('messages.invalid_file_format', lang)); return
    file, file_content = doc.get_file(), file.download_as_bytearray()
    try: backup_records = json.loads(file_content)
    except json.JSONDecodeError: update.message.reply_text(get_text('messages.invalid_json_content', lang)); return
    zone_id = get_zone_id()
    if not zone_id: update.message.reply_text(get_text('messages.zone_id_not_found', lang)); return
    update.message.reply_text(get_text('messages.restore_in_progress', lang))
    existing_map = {(r["type"], r["name"]): r for r in get_dns_records(zone_id)}
    restored, skipped, failed = 0, 0, 0
    for r in backup_records:
        if (r["type"], r["name"]) in existing_map: skipped += 1; continue
        if create_record(zone_id, r["type"], r["name"], r["content"], r.get("proxied", False)).get("success"): restored += 1
        else: failed += 1
    update.message.reply_text(get_text('messages.restore_report', lang, restored=restored, skipped=skipped, failed=failed))
    list_records_command(update, context)

def main():
    load_translations()
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(CommandHandler("language", language_command))
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
