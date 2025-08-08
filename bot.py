import os
import json
import httpx
import asyncio
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

load_dotenv()

# --- Configuration ---
CF_API_TOKEN = os.getenv("CF_API_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_ID = int(os.getenv("TELEGRAM_ADMIN_ID"))

HEADERS = { "Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json" }
DNS_RECORD_TYPES = [
    "A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "LOC", "SPF", "CERT", "DNSKEY",
    "DS", "NAPTR", "SMIMEA", "SSHFP", "SVCB", "TLSA", "URI"
]
RECORDS_PER_PAGE = 10

# --- Asynchronous HTTP Client ---
async_client = httpx.AsyncClient(headers=HEADERS, timeout=20.0)

# --- I18N Setup ---
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

def get_user_lang(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.get('language', 'fa')

# --- API Functions (Async) ---
def is_admin(update: Update): return update.effective_user.id == TELEGRAM_ADMIN_ID
def chunk_list(lst, n): return [lst[i:i + n] for i in range(0, len(lst), n)]

async def get_all_zones():
    all_zones, page = [], 1
    try:
        while True:
            r = await async_client.get("https://api.cloudflare.com/client/v4/zones", params={'per_page': 50, 'page': page})
            if not r.is_success: return []
            data = r.json()
            all_zones.extend(data.get("result", []))
            if data['result_info']['page'] >= data['result_info']['total_pages']: break
            page += 1
        return all_zones
    except (httpx.RequestError, json.JSONDecodeError):
        return []

async def get_dns_records(zone_id):
    all_records, page = [], 1
    try:
        while True:
            r = await async_client.get(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records", params={'per_page': 100, 'page': page})
            if not r.is_success: return []
            data = r.json()
            all_records.extend(data.get("result", []))
            if data['result_info']['page'] >= data['result_info']['total_pages']: break
            page += 1
        return all_records
    except (httpx.RequestError, json.JSONDecodeError):
        return []

async def api_request(method, url, **kwargs):
    try:
        r = await async_client.request(method, url, **kwargs)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        try:
            return e.response.json()
        except json.JSONDecodeError:
            return {"success": False, "errors": [{"message": e.response.text or f"HTTP Error: {e.response.status_code}"}]}
    except (httpx.RequestError, json.JSONDecodeError) as e:
        return {"success": False, "errors": [{"message": str(e)}]}

async def update_record(zone_id, rid, rtype, name, content, proxied):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{rid}"
    payload = {"type": rtype, "name": name, "content": content, "ttl": 1, "proxied": proxied}
    return await api_request("put", url, json=payload)

async def delete_record(zone_id, rid):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{rid}"
    return await api_request("delete", url)

async def create_record(zone_id, rtype, name, content, proxied):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    payload = {"type": rtype, "name": name, "content": content, "ttl": 1, "proxied": proxied}
    return await api_request("post", url, json=payload)


# --- State & Display Logic ---
def clear_state(context: ContextTypes.DEFAULT_TYPE, full_reset=False):
    keys_to_clear = ['is_bulk_mode', 'selected_records', 'search_query', 'is_searching', 'edit', 'add_step', 'confirm', 'is_bulk_ip_change', 'bulk_ip_confirm_details', 'new_type', 'new_name', 'new_content']
    if full_reset:
        keys_to_clear.extend(['selected_zone_id', 'selected_zone_name', 'all_records', 'all_zones'])
    for key in keys_to_clear:
        context.user_data.pop(key, None)

async def display_zones_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = get_user_lang(context)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    zones = await get_all_zones()
    if not zones:
        msg = get_text('messages.no_zones_found', lang)
        if query: await query.edit_message_text(msg)
        else: await update.message.reply_text(msg)
        return
    context.user_data['all_zones'] = {z['id']: z for z in zones}
    buttons = [[InlineKeyboardButton(zone['name'], callback_data=f"select_zone|{zone['id']}")] for zone in zones]
    text = get_text('messages.choose_zone', lang)
    if query: await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def display_records_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    query = update.callback_query
    lang = get_user_lang(context)
    zone_id = context.user_data.get('selected_zone_id')
    zone_name = context.user_data.get('selected_zone_name')
    if not zone_id:
        await list_records_command(update, context)
        return
    if 'all_records' not in context.user_data:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
        context.user_data['all_records'] = await get_dns_records(zone_id)
    all_records = context.user_data.get('all_records', [])
    search_query = context.user_data.get('search_query')
    records_to_display = [r for r in all_records if search_query.lower() in r['name'].lower()] if search_query else all_records
    message_text = get_text('messages.search_results', lang, query=search_query, zone_name=f"`{zone_name}`") if search_query else get_text('messages.all_records_list', lang, zone_name=f"`{zone_name}`")
    
    if not records_to_display:
        kb_list = [[InlineKeyboardButton(get_text('buttons.back_to_zones', lang), callback_data="list")]]
        msg_text = get_text('messages.no_records_found_search' if search_query else 'messages.no_records_found', lang)
        if query: await query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(kb_list))
        else: await context.bot.send_message(update.effective_chat.id, msg_text, reply_markup=InlineKeyboardMarkup(kb_list))
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
        buttons.append([InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data="bulk_cancel")])
    else:
        buttons.append([
            InlineKeyboardButton(get_text('buttons.add_record', lang), callback_data="add"),
            InlineKeyboardButton(get_text('buttons.search', lang), callback_data="search_start"),
            InlineKeyboardButton(get_text('buttons.bulk_actions', lang), callback_data="bulk_start")
        ])
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_zones', lang), callback_data="list")])
    reply_markup = InlineKeyboardMarkup(buttons)
    try:
        if query:
            await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await context.bot.send_message(update.effective_chat.id, message_text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception:
        pass

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('|')
    cmd = data[0]
    callback_function_name = f"{cmd}_callback"
    if callback_function_name in globals():
        await globals()[callback_function_name](update, context)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lang = get_user_lang(context)
    text = update.message.text.strip()
    if context.user_data.get('is_bulk_ip_change'):
        selected_ids = context.user_data.get('selected_records', [])
        context.user_data.pop('is_bulk_ip_change')
        context.user_data['bulk_ip_confirm_details'] = {'new_ip': text, 'record_ids': selected_ids}
        kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="bulk_change_ip_execute")], [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="bulk_start")]]
        await update.message.reply_text(get_text('messages.bulk_confirm_change_ip', lang, count=len(selected_ids), new_ip=f"`{text}`"), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return
    if context.user_data.get('is_searching'):
        context.user_data.pop('is_searching')
        context.user_data['search_query'] = text
        await display_records_list(update, context, page=0)
        return
    if "edit" in context.user_data:
        data = context.user_data.pop("edit")
        context.user_data["confirm"] = {"id": data["id"], "type": data["type"], "name": data["name"], "old": data["old"], "new": text}
        kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="confirm_change")], [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"select_zone|{context.user_data['selected_zone_id']}")]]
        await update.message.reply_text(f"🔄 `{data['old']}` ➡️ `{text}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return
    if "add_step" in context.user_data:
        step = context.user_data["add_step"]
        zone_name = context.user_data.get('selected_zone_name', 'your_domain.com')
        if step == "name":
            new_name = zone_name if text.strip() == "@" else f"{text.strip()}.{zone_name}"
            context.user_data["new_name"] = new_name
            context.user_data["add_step"] = "content"
            prompt_text_key = 'prompts.enter_ip' if context.user_data.get("new_type") in ['A', 'AAAA'] else 'prompts.enter_content'
            prompt_text = get_text(prompt_text_key, lang, name=f"`{new_name}`")
            await update.message.reply_text(prompt_text)
        elif step == "content":
            context.user_data["new_content"] = text
            context.user_data.pop("add_step")
            kb = [[InlineKeyboardButton("DNS Only", callback_data="add_proxied|false")], [InlineKeyboardButton("Proxied", callback_data="add_proxied|true")]]
            await update.message.reply_text(get_text('prompts.choose_proxy', lang), reply_markup=InlineKeyboardMarkup(kb))

# --- Callback Handlers (ادامه کد بدون تغییر) ...
async def list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE): await list_records_command(update, context, from_callback=True)
async def select_zone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    zone_id = query.data.split('|')[1]
    all_zones = context.user_data.get('all_zones', {})
    zone = all_zones.get(zone_id)
    if not zone:
        await query.edit_message_text("Error: Zone not found.")
        return
    clear_state(context, full_reset=False)
    context.user_data['selected_zone_id'] = zone_id
    context.user_data['selected_zone_name'] = zone['name']
    await display_records_list(update, context, page=0)
async def list_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE): await display_records_list(update, context, page=int(update.callback_query.data.split('|')[1]))
async def select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    if not record:
        await update.callback_query.edit_message_text(get_text('messages.no_records_found', lang))
        return
    proxy_icon, proxy_text = ("☁️", get_text('messages.proxy_status_active', lang)) if record.get('proxied') else ("⬜️", get_text('messages.proxy_status_inactive', lang))
    kb = [[InlineKeyboardButton(get_text('buttons.edit_value', lang), callback_data=f"edit|{rid}")],
          [InlineKeyboardButton(get_text('buttons.toggle_proxy', lang), callback_data=f"toggle_proxy|{rid}")],
          [InlineKeyboardButton(get_text('buttons.delete', lang), callback_data=f"delete|{rid}")],
          [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"select_zone|{context.user_data['selected_zone_id']}")]]
    text = get_text('messages.record_details', lang, type=record['type'], name=f"`{record['name']}`", content=f"`{record['content']}`", proxy_status=proxy_text)
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
async def edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    if not record:
        await update.callback_query.message.reply_text(get_text('messages.internal_error', lang))
        return
    context.user_data["edit"] = {"id": record["id"], "type": record["type"], "name": record["name"], "old": record["content"]}
    prompt_key = 'prompts.enter_ip' if record['type'] in ['A', 'AAAA'] else 'prompts.enter_content'
    prompt_text = get_text(prompt_key, lang, name=f"`{record['name']}`")
    await update.callback_query.message.reply_text(prompt_text, parse_mode="Markdown")
async def toggle_proxy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    current_status = get_text('messages.proxy_status_active', lang) if record.get('proxied') else get_text('messages.proxy_status_inactive', lang)
    new_status = get_text('messages.proxy_status_inactive', lang) if record.get('proxied') else get_text('messages.proxy_status_active', lang)
    kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"toggle_proxy_confirm|{rid}")], [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"select|{rid}")]]
    text = get_text('messages.confirm_proxy_toggle', lang, record_name=f"`{record['name']}`", current_status=current_status, new_status=new_status)
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
async def toggle_proxy_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    new_proxied_status = not record.get('proxied', False)
    res = await update_record(context.user_data['selected_zone_id'], rid, record['type'], record['name'], record['content'], new_proxied_status)
    if res.get("success"):
        await update.callback_query.answer(get_text('messages.proxy_toggled_successfully', lang, record_name=record['name']), show_alert=True)
        context.user_data['records'][rid]['proxied'] = new_proxied_status
        context.user_data['all_records'] = list(context.user_data['records'].values())
        await select_callback(update, context)
    else:
        error_msg = res.get('errors', [{}])[0].get('message', get_text('messages.error_toggling_proxy', lang))
        await update.callback_query.answer(error_msg, show_alert=True)
async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"delete_confirm|{rid}")], [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"select|{rid}")]]
    await update.callback_query.edit_message_text(get_text('messages.confirm_delete_record', lang, record_name=f"`{record['name']}`"), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
async def delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid, {})
    res = await delete_record(context.user_data['selected_zone_id'], rid)
    if res.get("result", {}).get("id") == rid or res.get("success"):
        await update.callback_query.edit_message_text(get_text('messages.record_deleted_successfully', lang, record_name=f"`{record.get('name', 'N/A')}`"), parse_mode="Markdown")
        context.user_data.pop('all_records', None)
        await asyncio.sleep(1)
        await display_records_list(update, context)
    else:
        await update.callback_query.edit_message_text(get_text('messages.error_deleting_record', lang))
async def confirm_change_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    query = update.callback_query
    info = context.user_data.pop("confirm", {})
    original_record = context.user_data.get("records", {}).get(info["id"], {})
    proxied_status = original_record.get("proxied", False)
    res = await update_record(context.user_data['selected_zone_id'], info["id"], info["type"], info["name"], info["new"], proxied_status)
    if res.get("success"):
        await query.edit_message_text(get_text('messages.record_updated_successfully', lang, record_name=f"`{info['name']}`"), parse_mode="Markdown")
        context.user_data.pop('all_records', None)
        await asyncio.sleep(1)
        await display_records_list(update, context)
    else:
        error_msg = res.get('errors', [{}])[0].get('message', 'Unknown error')
        await query.edit_message_text(get_text('messages.error_updating_record', lang, error=error_msg))
async def add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    buttons = [InlineKeyboardButton(t, callback_data=f"add_type|{t}") for t in DNS_RECORD_TYPES]
    buttons_3col = chunk_list(buttons, 3)
    buttons_3col.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"select_zone|{context.user_data['selected_zone_id']}")])
    await update.callback_query.edit_message_text(get_text('prompts.choose_record_type', lang), reply_markup=InlineKeyboardMarkup(buttons_3col))
async def add_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_type"] = update.callback_query.data.split('|')[1]
    context.user_data["add_step"] = "name"
    await update.callback_query.edit_message_text(get_text('prompts.enter_subdomain', get_user_lang(context)))
async def add_proxied_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    proxied = update.callback_query.data.split('|')[1].lower() == "true"
    rtype, name, content = context.user_data.pop("new_type"), context.user_data.pop("new_name"), context.user_data.pop("new_content")
    res = await create_record(context.user_data['selected_zone_id'], rtype, name, content, proxied)
    if res.get("success"):
        await update.callback_query.edit_message_text(get_text('messages.record_added_successfully', lang, rtype=rtype, name=f"`{name}`"), parse_mode="Markdown")
        context.user_data.pop('all_records', None)
        await asyncio.sleep(1)
        await display_records_list(update, context)
    else:
        error_msg = res.get('errors', [{}])[0].get('message', 'Unknown error')
        await update.callback_query.edit_message_text(get_text('messages.error_creating_record', lang, error=error_msg))
async def search_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE): await search_command(update, context, from_callback=True)
async def bulk_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE): await bulk_command(update, context, from_callback=True)
async def bulk_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(context)
    await display_records_list(update, context)
async def bulk_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rid, page = update.callback_query.data.split('|')[1], int(update.callback_query.data.split('|')[2])
    selected = context.user_data.get('selected_records', [])
    if rid in selected:
        selected.remove(rid)
    else:
        selected.append(rid)
    context.user_data['selected_records'] = selected
    await display_records_list(update, context, page=page)
async def bulk_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    selected_ids = context.user_data.get('selected_records', [])
    if not selected_ids:
        await update.callback_query.answer(get_text('messages.bulk_no_selection', lang), show_alert=True)
        return
    kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="bulk_delete_execute")], [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="bulk_start")]]
    await update.callback_query.edit_message_text(get_text('messages.bulk_confirm_delete', lang, count=len(selected_ids)), reply_markup=InlineKeyboardMarkup(kb))
async def bulk_delete_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    selected_ids = context.user_data.get('selected_records', [])
    query = update.callback_query
    await query.edit_message_text(get_text('messages.bulk_delete_progress', lang, count=len(selected_ids)))
    success, fail = 0, 0
    zone_id = context.user_data['selected_zone_id']
    for rid in selected_ids:
        res = await delete_record(zone_id, rid)
        if res.get("result", {}).get("id") == rid or res.get("success"):
            success += 1
        else:
            fail += 1
        await asyncio.sleep(0.3)
    msg = get_text('messages.bulk_delete_report', lang, success=success, fail=fail)
    kb = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"select_zone|{zone_id}")]]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    clear_state(context)
    context.user_data.pop('all_records', None)
async def bulk_change_ip_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    selected_ids = context.user_data.get('selected_records', [])
    if not selected_ids:
        await update.callback_query.answer(get_text('messages.bulk_no_selection', lang), show_alert=True)
        return
    context.user_data['is_bulk_ip_change'] = True
    await update.callback_query.edit_message_text(get_text('messages.bulk_change_ip_prompt', lang, count=len(selected_ids)))
async def bulk_change_ip_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    query = update.callback_query
    details = context.user_data.pop('bulk_ip_confirm_details', {})
    if not details:
        await query.edit_message_text(get_text('messages.internal_error', lang))
        return
    new_ip, record_ids = details['new_ip'], details['record_ids']
    await query.edit_message_text(get_text('messages.bulk_change_ip_progress', lang, count=len(record_ids), new_ip=f"`{new_ip}`"), parse_mode="Markdown")
    success, fail, skipped = 0, 0, 0
    all_records_map, zone_id = context.user_data.get("records", {}), context.user_data['selected_zone_id']
    for rid in record_ids:
        record = all_records_map.get(rid)
        if not record:
            fail += 1
            continue
        if record['type'] in ['A', 'AAAA']:
            res = await update_record(zone_id, rid, record['type'], record['name'], new_ip, record.get('proxied', False))
            if res.get("success"):
                success += 1
            else:
                fail += 1
        else:
            skipped += 1
        await asyncio.sleep(0.3)
    msg = get_text('messages.bulk_change_ip_report', lang, success=success, skipped=skipped, fail=fail)
    kb = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"select_zone|{zone_id}")]]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    clear_state(context)
    context.user_data.pop('all_records', None)
async def set_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang_code = update.callback_query.data.split('|')[1]
    context.user_data['language'] = lang_code
    await update.callback_query.edit_message_text(get_text('messages.language_changed', lang_code))
    await asyncio.sleep(1)
    await list_records_command(update, context, from_callback=True)

# --- Command Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await language_command(update, context)
async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    kb = [[InlineKeyboardButton("🇮🇷 فارسی", callback_data="set_lang|fa"), InlineKeyboardButton("🇬🇧 English", callback_data="set_lang|en")]]
    await update.message.reply_text(get_text('messages.choose_language', lang), reply_markup=InlineKeyboardMarkup(kb))
async def list_records_command(update: Update, context: ContextTypes.DEFAULT_TYPE, from_callback=False):
    if not is_admin(update):
        await update.message.reply_text(get_text('messages.access_denied', lang=get_user_lang(context)))
        return
    clear_state(context, full_reset=True)
    await display_zones_list(update, context)
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE, from_callback=False):
    if not is_admin(update): return
    lang = get_user_lang(context)
    zone_id = context.user_data.get('selected_zone_id')
    if not zone_id:
        msg = get_text('messages.no_zone_selected', lang)
        if from_callback: await update.callback_query.answer(msg, show_alert=True)
        else: await update.message.reply_text(msg)
        return
    
    clear_state(context)
    context.user_data['is_searching'] = True
    text = get_text('prompts.enter_search_query', lang)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text('buttons.cancel_search', lang), callback_data=f"select_zone|{zone_id}")]])
    
    if from_callback:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)
async def bulk_command(update: Update, context: ContextTypes.DEFAULT_TYPE, from_callback=False):
    if not is_admin(update): return
    lang = get_user_lang(context)
    zone_id = context.user_data.get('selected_zone_id')
    if not zone_id:
        msg = get_text('messages.no_zone_selected', lang)
        if from_callback: await update.callback_query.answer(msg, show_alert=True)
        else: await update.message.reply_text(msg)
        return
    clear_state(context)
    context.user_data['is_bulk_mode'] = True
    context.user_data['selected_records'] = []
    await display_records_list(update, context, page=0)
async def add_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lang = get_user_lang(context)
    zone_id = context.user_data.get('selected_zone_id')
    if not zone_id:
        await update.message.reply_text(get_text('messages.no_zone_selected', lang))
        return
    clear_state(context)
    buttons = [InlineKeyboardButton(t, callback_data=f"add_type|{t}") for t in DNS_RECORD_TYPES]
    buttons_3col = chunk_list(buttons, 3)
    buttons_3col.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"select_zone|{zone_id}")])
    await update.message.reply_text(get_text('prompts.choose_record_type', lang), reply_markup=InlineKeyboardMarkup(buttons_3col))
async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lang = get_user_lang(context)
    zone_id = context.user_data.get('selected_zone_id')
    if not zone_id:
        await update.message.reply_text(get_text('messages.no_zone_selected', lang))
        return
    await update.message.reply_text(get_text('messages.backup_in_progress', lang))
    records = await get_dns_records(zone_id)
    if not records:
        await update.message.reply_text(get_text('messages.no_records_found', lang))
        return
    backup_file = f"{context.user_data['selected_zone_name']}_backup.json"
    try:
        with open(backup_file, "w", encoding='utf-8') as f:
            json.dump(records, f, indent=2)
        with open(backup_file, "rb") as f:
            await update.message.reply_document(f, filename=backup_file)
    finally:
        if os.path.exists(backup_file):
            os.remove(backup_file)
async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lang = get_user_lang(context)
    zone_id = context.user_data.get('selected_zone_id')
    if not zone_id:
        await update.message.reply_text(get_text('messages.no_zone_selected_for_restore', lang))
        return
    await update.message.reply_text(get_text('messages.restore_prompt', lang))
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lang = get_user_lang(context)
    doc = update.message.document
    if not doc.file_name.endswith('.json'):
        await update.message.reply_text(get_text('messages.invalid_file_format', lang))
        return
    zone_id = context.user_data.get('selected_zone_id')
    if not zone_id:
        await update.message.reply_text(get_text('messages.no_zone_selected_for_restore', lang))
        return
    
    file = await doc.get_file()
    file_content = await file.download_as_bytearray()
    try:
        backup_records = json.loads(file_content)
    except json.JSONDecodeError:
        await update.message.reply_text(get_text('messages.invalid_json_content', lang))
        return
    await update.message.reply_text(get_text('messages.restore_in_progress', lang))
    existing_records = await get_dns_records(zone_id)
    existing_map = {(r["type"], r["name"]): r for r in existing_records}
    restored, skipped, failed = 0, 0, 0
    for r in backup_records:
        if (r["type"], r["name"]) in existing_map:
            skipped += 1
            continue
        res = await create_record(zone_id, r["type"], r["name"], r["content"], r.get("proxied", False))
        if res.get("success"):
            restored += 1
        else:
            failed += 1
        await asyncio.sleep(0.3)
    await update.message.reply_text(get_text('messages.restore_report', lang, restored=restored, skipped=skipped, failed=failed))
    context.user_data.pop('all_records', None)
    await display_records_list(update, context)

async def shutdown_client(app: Application):
    await async_client.aclose()
    print("Async client closed.")

def main():
    load_translations()
    
    # --- Correct way to build the application with shutdown hook ---
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_shutdown(shutdown_client)
        .build()
    )
    
    # --- Handlers ---
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("language", language_command))
    application.add_handler(CommandHandler("list", list_records_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("bulk", bulk_command))
    application.add_handler(CommandHandler("add", add_command_handler))
    application.add_handler(CommandHandler("backup", backup_command))
    application.add_handler(CommandHandler("restore", restore_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.Document.MimeType("application/json"), handle_document))
    
    print("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()
