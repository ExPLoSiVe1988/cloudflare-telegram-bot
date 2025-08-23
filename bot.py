import os
import json
import httpx
import asyncio
import logging
import socket
import ipaddress
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, PicklePersistence
)

load_dotenv()

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Global Lock for Health Check ---
health_check_lock = asyncio.Lock()

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

raw_admin_ids = os.getenv("TELEGRAM_ADMIN_IDS", "").split(',')
ADMIN_IDS = {int(admin_id.strip()) for admin_id in raw_admin_ids if admin_id.strip().isdigit()}

raw_accounts = os.getenv("CF_ACCOUNTS", "").split(',')
CF_ACCOUNTS = {}
for acc in raw_accounts:
    if ':' in acc:
        nickname, token = acc.split(':', 1)
        CF_ACCOUNTS[nickname.strip()] = token.strip()

BACKUP_DIR = "backups"
DNS_RECORD_TYPES = [
    "A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "LOC", "SPF", "CERT", "DNSKEY",
    "DS", "NAPTR", "SMIMEA", "SSHFP", "SVCB", "TLSA", "URI"
]
RECORDS_PER_PAGE = 5

# --- I18N Setup ---
translations = {}
def load_translations():
    global translations
    for lang in ['en', 'fa']:
        try:
            with open(f'{lang}.json', 'r', encoding='utf-8') as f:
                translations[lang] = json.load(f)
        except FileNotFoundError:
            logger.fatal(f"Translation file {lang}.json not found! Please create it.")
            exit(1)
        except json.JSONDecodeError:
            logger.fatal(f"Could not decode {lang}.json. Please check its syntax.")
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

# --- Helper Functions ---
CONFIG_FILE = "config.json"

def is_valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False

def load_config():
    try:
        if not os.path.exists(CONFIG_FILE):
            logger.info(f"{CONFIG_FILE} not found. Creating a new one with default structure.")
            default_config = {"notifications": {"enabled": True, "chat_ids": []}, "failover_policies": []}
            save_config(default_config)
            return default_config
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.error(f"FATAL: Could not decode {CONFIG_FILE}. The file is corrupted. Health checks will be skipped.")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred while loading config: {e}")
        return None

def save_config(config_data):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)

def get_short_name(full_name: str, zone_name: str) -> str:
    if full_name == zone_name:
        return "@"
    return full_name.removesuffix(f".{zone_name}")

def is_admin(update: Update):
    return update.effective_user.id in ADMIN_IDS

def chunk_list(lst, n): return [lst[i:i + n] for i in range(0, len(lst), n)]

def get_current_token(context: ContextTypes.DEFAULT_TYPE):
    account_nickname = context.user_data.get('selected_account_nickname')
    return CF_ACCOUNTS.get(account_nickname)

# --- API Functions ---
async def api_request(token: str, method: str, url: str, **kwargs):
    if not token:
        return {"success": False, "errors": [{"message": "No API token selected for the request."}]}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.request(method, url, headers=headers, **kwargs)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        try: return e.response.json()
        except json.JSONDecodeError: return {"success": False, "errors": [{"message": e.response.text or f"HTTP Error: {e.response.status_code}"}]}
    except (httpx.RequestError, json.JSONDecodeError) as e: return {"success": False, "errors": [{"message": str(e)}]}

async def get_all_zones(token: str):
    url = "https://api.cloudflare.com/client/v4/zones"
    all_zones, page = [], 1
    while True:
        res = await api_request(token, "get", url, params={'per_page': 50, 'page': page})
        if not res.get("success"): return []
        data = res.get("result", [])
        all_zones.extend(data)
        if res.get('result_info', {}).get('page', 1) >= res.get('result_info', {}).get('total_pages', 1): break
        page += 1
    return all_zones

async def get_dns_records(token: str, zone_id: str):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    all_records, page = [], 1
    while True:
        res = await api_request(token, "get", url, params={'per_page': 100, 'page': page})
        if not res.get("success"): return []
        data = res.get("result", [])
        all_records.extend(data)
        if res.get('result_info', {}).get('page', 1) >= res.get('result_info', {}).get('total_pages', 1): break
        page += 1
    return all_records

async def update_record(token: str, zone_id, rid, rtype, name, content, proxied):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{rid}"
    payload = {"type": rtype, "name": name, "content": content, "ttl": 1, "proxied": proxied}
    return await api_request(token, "put", url, json=payload)

async def delete_record(token: str, zone_id, rid):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{rid}"
    return await api_request(token, "delete", url)

async def create_record(token: str, zone_id, rtype, name, content, proxied):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    payload = {"type": rtype, "name": name, "content": content, "ttl": 1, "proxied": proxied}
    return await api_request(token, "post", url, json=payload)

# --- Health Check & Failover Functions ---
async def perform_failback(context: ContextTypes.DEFAULT_TYPE, policy: dict):
    """Switches DNS records back from backup to primary IP."""
    policy_name = policy.get('policy_name', 'Unnamed Policy')
    logger.info(f"--- Performing Failback for policy: {policy_name} ---")
    
    account_nickname = policy.get('account_nickname')
    token = CF_ACCOUNTS.get(account_nickname)
    if not token:
        logger.error(f"FAILBACK FAILED for '{policy_name}': No token found for account nickname: '{account_nickname}'.")
        return

    zones = await get_all_zones(token)
    zone_id = next((z['id'] for z in zones if z['name'] == policy['zone_name']), None)
    if not zone_id:
        logger.error(f"FAILBACK FAILED for '{policy_name}': Could not find zone_id for zone: '{policy['zone_name']}'.")
        return

    all_records = await get_dns_records(token, zone_id)
    success_count = 0
    records_to_update = policy.get('record_names', [])
    backup_ip = policy.get('backup_ip')
    primary_ip = policy.get('primary_ip')
    
    logger.info(f"FAILBACK: Checking for records {records_to_update} with backup IP {backup_ip} to switch to {primary_ip}")

    for record in all_records:
        short_name = get_short_name(record['name'], policy['zone_name'])
        if short_name in records_to_update and record['content'] == backup_ip:
            logger.info(f"FAILBACK MATCH FOUND: Updating record '{record['name']}' from '{backup_ip}' to '{primary_ip}'")
            res = await update_record(
                token, zone_id, record['id'], record['type'],
                record['name'], primary_ip, record.get('proxied', False)
            )
            if res.get("success"):
                logger.info(f"SUCCESS: Record {record['name']} updated for failback.")
                success_count += 1
            else:
                error_msg = res.get('errors', [{}])[0].get('message', 'Unknown error')
                logger.error(f"FAILBACK FAILED: Could not update record {record['name']}. Reason: {error_msg}")

    
    logger.info(f"FAILBACK: Total successful updates: {success_count}")

    if success_count > 0:
        logger.info("FAILBACK: Preparing to send success notification...")
        await send_notification(context, 'messages.failback_executed_notification', 
                                policy_name=policy_name, 
                                primary_ip=primary_ip)
        
        if 'dns_cache_invalidated_zones' not in context.bot_data:
            context.bot_data['dns_cache_invalidated_zones'] = set()
        context.bot_data['dns_cache_invalidated_zones'].add(zone_id)
        logger.info(f"FAILBACK: Set cache invalidation flag for zone_id: {zone_id}")

    else:
        logger.warning(f"FAILBACK FINISHED for '{policy_name}', but no records were updated. They might have been changed manually or had a different backup IP.")

    status_data = context.bot_data.setdefault('health_status', {})
    if policy_name in status_data:
        status_data.pop(policy_name)
    
    await context.application.persistence.flush()
    logger.info(f"Persistence flushed. Status for policy '{policy_name}' has been reset.")
    
    logger.info("--- Failback Process Finished ---")

async def check_ip_health(ip: str, port: int, timeout: int = 5) -> bool:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except (socket.gaierror, ConnectionRefusedError, asyncio.TimeoutError, OSError):
        return False

async def send_notification(context: ContextTypes.DEFAULT_TYPE, message_key: str, **kwargs):
    """
    Sends a localized notification message to all configured admin chat IDs.
    It automatically includes the main admins from the .env file.
    """
    config = load_config()
    if config is None or not config.get("notifications", {}).get("enabled", False):
        return

    config_chat_ids = set(config.get("notifications", {}).get("chat_ids", []))
    
    env_admin_ids = ADMIN_IDS
    
    all_notification_ids = config_chat_ids.union(env_admin_ids)
    
    if not all_notification_ids:
        logger.warning("No recipients for notification (neither in config.json nor .env).")
        return

    user_data = await context.application.persistence.get_user_data()
    
    for chat_id in all_notification_ids:
        lang = user_data.get(chat_id, {}).get('language', 'fa')
        message = get_text(message_key, lang, **kwargs)
        try:
            await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send notification to {chat_id}: {e}")

async def perform_failover(context: ContextTypes.DEFAULT_TYPE, policy: dict):
    """Updates DNS records based on the failover policy and sets a cache invalidation flag."""
    policy_name = policy.get('policy_name', 'Unnamed Policy')
    logger.info(f"--- Performing Failover for policy: {policy_name} ---")
    
    account_nickname = policy.get('account_nickname')
    token = CF_ACCOUNTS.get(account_nickname)
    if not token:
        logger.error(f"FAILOVER FAILED for '{policy_name}': No token found for account nickname: '{account_nickname}'.")
        return

    logger.info(f"Found token for account '{account_nickname}'.")

    zones = await get_all_zones(token)
    zone_id = next((z['id'] for z in zones if z['name'] == policy['zone_name']), None)
    if not zone_id:
        logger.error(f"FAILOVER FAILED for '{policy_name}': Could not find zone_id for zone: '{policy['zone_name']}'.")
        return

    logger.info(f"Found zone_id '{zone_id}' for zone '{policy['zone_name']}'.")

    all_records = await get_dns_records(token, zone_id)
    success_count = 0
    records_to_update = policy.get('record_names', [])
    logger.info(f"Will check for records: {records_to_update} with IP {policy['primary_ip']}")
    
    for record in all_records:
        short_name = get_short_name(record['name'], policy['zone_name'])
        
        if short_name in records_to_update and record['content'] == policy['primary_ip']:
            logger.info(f"MATCH FOUND! Updating record: {record['name']} from {record['content']} to {policy['backup_ip']}")
            res = await update_record(
                token, zone_id, record['id'], record['type'],
                record['name'], policy['backup_ip'], record.get('proxied', False)
            )
            if res.get("success"):
                logger.info(f"SUCCESS: Record {record['name']} updated.")
                success_count += 1
            else:
                error_msg = res.get('errors', [{}])[0].get('message', 'Unknown error')
                logger.error(f"FAILOVER FAILED: Could not update record {record['name']}. Reason: {error_msg}")

    if success_count > 0:
        await send_notification(
            context, 
            'messages.failover_notification_message', 
            policy_name=policy_name, 
            primary_ip=policy['primary_ip'], 
            backup_ip=policy['backup_ip']
        )
        
        if 'dns_cache_invalidated_zones' not in context.bot_data:
            context.bot_data['dns_cache_invalidated_zones'] = set()
        context.bot_data['dns_cache_invalidated_zones'].add(zone_id)
        logger.info(f"Set cache invalidation flag for zone_id: {zone_id}")

    else:
        logger.warning(f"FAILOVER FINISHED for '{policy_name}', but no records were updated.")

    status_data = context.bot_data.setdefault('health_status', {})
    if policy_name in status_data:
        status_data[policy_name]['failover_active'] = True
    
    await context.application.persistence.flush()
    logger.info(f"Persistence flushed. Failover status for '{policy_name}' is now saved.")
    
    logger.info("--- Failover Process Finished ---")

async def health_check_job(context: ContextTypes.DEFAULT_TYPE):
    """
    The main background job that checks IPs, triggers failovers, and handles automatic failbacks.
    """
    if health_check_lock.locked():
        logger.warning("Health check job is already running. Skipping this execution.")
        return
        
    async with health_check_lock:
        try:
            logger.info("--- Health Check Job Started (Lock Acquired) ---")
            
            config = load_config()
            if config is None:
                logger.warning("Config could not be loaded. Skipping.")
                return

            if 'health_status' not in context.bot_data:
                context.bot_data['health_status'] = {}
            status_data = context.bot_data['health_status']
            
            logger.info(f"Current status_data before check: {status_data}")
            
            policies = config.get("failover_policies", [])
            if not policies:
                logger.info("No failover policies defined. Skipping.")
                return
                
            for policy in policies:
                if not policy.get('enabled', True):
                    logger.info(f"Skipping disabled policy: '{policy.get('policy_name', 'N/A')}'")
                    continue
                
                policy_name = policy.get('policy_name', 'Unnamed Policy')
                primary_ip = policy['primary_ip']
                
                policy_status = status_data.setdefault(policy_name, 
                    {'status': 'monitoring', 'first_downtime': None, 'first_uptime': None, 'failover_active': False})
                
                is_online = await check_ip_health(primary_ip, policy['check_port'])
                logger.info(f"Health check for '{policy_name}' ({primary_ip}:{policy['check_port']}) -> is_online: {is_online}")

                if is_online:
                    policy_status['first_downtime'] = None

                    if policy_status.get('status') == 'down':
                        logger.info(f"IP {primary_ip} for policy '{policy_name}' is back ONLINE.")
                        await send_notification(
                        context, 
                        'messages.server_recovered_notification', 
                        policy_name=policy_name, 
                        primary_ip=primary_ip)
                        policy_status['status'] = 'recovered'
                        
                        if policy_status.get('failover_active') and policy.get('auto_failback', False):
                            policy_status['first_uptime'] = datetime.now().isoformat()
                            failback_minutes = policy.get('failback_minutes', 5)
                            await send_notification(context, 'messages.failback_alert_notification', 
                                                    policy_name=policy_name, primary_ip=primary_ip, failback_minutes=failback_minutes)
                        else:
                            await send_notification(context, 'messages.server_recovered_notification', 
                                                    policy_name=policy_name, primary_ip=primary_ip)

                    elif policy_status.get('status') == 'recovered' and policy_status.get('failover_active') and policy.get('auto_failback', False):
                        first_uptime_str = policy_status.get('first_uptime')
                        if isinstance(first_uptime_str, str):
                            first_uptime_dt = datetime.fromisoformat(first_uptime_str)
                            uptime_duration = datetime.now() - first_uptime_dt
                            failback_minutes = policy.get('failback_minutes', 5)
                            
                            logger.info(f"Policy '{policy_name}' is in recovery. Uptime duration: {uptime_duration}.")
                            if uptime_duration >= timedelta(minutes=failback_minutes):
                                logger.info(f"!!! FAILBACK TRIGGERED for policy '{policy_name}' !!!")
                                await perform_failback(context, policy)
                    
                    else: 
                        policy_status['status'] = 'up'

                else: # IP is offline
                    policy_status['first_uptime'] = None 

                    if policy_status.get('status') != 'down':
                        logger.warning(f"First detection of downtime for policy '{policy_name}'. Setting status to 'down'.")
                        policy_status['status'] = 'down'
                        policy_status['first_downtime'] = datetime.now().isoformat()
        
                        await send_notification(context, 'messages.server_alert_notification', 
                                policy_name=policy_name, 
                                primary_ip=primary_ip)
                    
                    else: 
                        logger.info(f"Policy '{policy_name}' was already in 'down' state. Checking failover conditions.")
                        
                        first_downtime_obj = policy_status.get('first_downtime')
                        if isinstance(first_downtime_obj, str) and not policy_status.get('failover_active'):
                            first_downtime_dt = datetime.fromisoformat(first_downtime_obj)
                            downtime_duration = datetime.now() - first_downtime_dt
                            logger.info(f"Downtime for policy '{policy_name}': {downtime_duration}.")
                            
                            failover_minutes = policy.get("failover_minutes", 2)
                            if downtime_duration >= timedelta(minutes=failover_minutes):
                                logger.info(f"!!! FAILOVER TRIGGERED for policy '{policy_name}' !!!")
                                await perform_failover(context, policy)
                            else:
                                logger.info(f"Downtime is less than {failover_minutes} minutes. Waiting...")
                        else:
                            logger.info(f"Failover for policy '{policy_name}' is already active or first_downtime is not a valid string. Skipping.")
            
            logger.info(f"Current status_data after check: {status_data}")
            logger.info("--- Health Check Job Finished ---")

        except Exception as e:
            logger.error("!!! An unhandled exception occurred in health_check_job !!!", exc_info=True)

# --- State Management ---
def clear_state(context: ContextTypes.DEFAULT_TYPE, preserve=None):
    if preserve is None: preserve = []
    if 'language' not in preserve: preserve.append('language')
    preserved_data = {key: context.user_data[key] for key in preserve if key in context.user_data}
    context.user_data.clear()
    context.user_data.update(preserved_data)

# --- Display Logic ---
async def display_records_for_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """
    Displays a paginated list of DNS records for the user to select for a policy.
    This function works for both adding a new policy and editing an existing one.
    """
    query = update.callback_query
    lang = get_user_lang(context)
    
    is_editing = context.user_data.get('is_editing_policy_records', False)
    try:
        if is_editing:
            policy_index = context.user_data['edit_policy_index']
            config = load_config()
            policy = config['failover_policies'][policy_index]
            account_nickname = policy['account_nickname']
            zone_name = policy['zone_name']
        else:
            account_nickname = context.user_data['new_policy_data']['account_nickname']
            zone_name = context.user_data['new_policy_data']['zone_name']
    except (KeyError, IndexError):
        await query.edit_message_text("Error: Could not retrieve policy data. Please start over.")
        return

    if 'policy_all_records' not in context.user_data or context.user_data.get('current_selection_zone') != zone_name:
        await query.edit_message_text("Fetching records, please wait...")
        token = CF_ACCOUNTS.get(account_nickname)
        if not token:
            await query.edit_message_text("Error: Account token not found."); return

        zones = await get_all_zones(token)
        zone_id = next((z['id'] for z in zones if z['name'] == zone_name), None)
        if not zone_id:
            await query.edit_message_text("Error: Could not find zone ID."); return
            
        all_records = await get_dns_records(token, zone_id)
        context.user_data['policy_all_records'] = [r for r in all_records if r['type'] in ['A', 'AAAA']]
        context.user_data['current_selection_zone'] = zone_name # ذخیره نام دامنه فعلی برای مدیریت کش

    all_records = context.user_data.get('policy_all_records', [])
    selected_records = context.user_data.get('policy_selected_records', [])
    
    records_per_page = 5
    start_index = page * records_per_page
    end_index = start_index + records_per_page
    records_on_page = all_records[start_index:end_index]

    buttons = []
    for record in records_on_page:
        short_name = get_short_name(record['name'], zone_name)
        check_icon = "✅" if short_name in selected_records else "▫️"
        button_text = f"{check_icon} {record['type']} {short_name} ({record['content']})"
        buttons.append([InlineKeyboardButton(button_text, callback_data=f"policy_select_record|{short_name}|{page}")])

    pagination_buttons = []
    if page > 0:
        pagination_buttons.append(InlineKeyboardButton(get_text('buttons.previous', lang), callback_data=f"policy_records_page|{page - 1}"))
    if end_index < len(all_records):
        pagination_buttons.append(InlineKeyboardButton(get_text('buttons.next', lang), callback_data=f"policy_records_page|{page + 1}"))
    if pagination_buttons:
        buttons.append(pagination_buttons)

    buttons.append([InlineKeyboardButton(get_text('buttons.confirm_selection', lang, count=len(selected_records)), callback_data="policy_confirm_records")])
    
    text = get_text('prompts.select_records_for_policy', lang)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def display_account_list(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    """
    Displays the list of configured Cloudflare accounts for the user to choose from.
    Can either edit an existing message or send a new one.
    """
    lang = get_user_lang(context)
    buttons = [[InlineKeyboardButton(nickname, callback_data=f"select_account|{nickname}")] for nickname in CF_ACCOUNTS.keys()]
    text = get_text('messages.choose_account', lang)
    reply_markup = InlineKeyboardMarkup(buttons)
    
    query = update.callback_query
    
    if query and not force_new_message:
        try:
            await query.edit_message_text(text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error editing message in display_account_list: {e}")
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)
    else:
        chat_id = update.effective_chat.id
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

async def display_zones_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = get_user_lang(context)
    token = get_current_token(context)
    if not token:
        await display_account_list(update, context)
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    zones = await get_all_zones(token)
    if not zones:
        msg = get_text('messages.no_zones_found', lang)
        buttons = [[InlineKeyboardButton(get_text('buttons.back_to_accounts', lang), callback_data="back_to_accounts")]]
        if query: await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
        else: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
        return
    context.user_data['all_zones'] = {z['id']: z for z in zones}
    buttons = [[InlineKeyboardButton(zone['name'], callback_data=f"select_zone|{zone['id']}")] for zone in zones]
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_accounts', lang), callback_data="back_to_accounts")])
    text = get_text('messages.choose_zone', lang)
    if query: await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def display_records_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    """Displays a paginated list of DNS records for the selected zone."""
    query = update.callback_query
    lang = get_user_lang(context)
    token = get_current_token(context)
    zone_id = context.user_data.get('selected_zone_id')
    zone_name = context.user_data.get('selected_zone_name')

    if not token or not zone_id:
        await list_records_command(update, context)
        return
    
    context.user_data['current_page'] = page

    invalidated_zones = context.bot_data.get('dns_cache_invalidated_zones', set())
    if zone_id in invalidated_zones:
        logger.info(f"Cache for zone {zone_id} is invalidated by a background job. Forcing a refresh.")
        context.user_data.pop('all_records', None)
        context.user_data.pop('records', None)
        invalidated_zones.remove(zone_id)
        context.bot_data['dns_cache_invalidated_zones'] = invalidated_zones

    if 'all_records' not in context.user_data:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
        context.user_data['all_records'] = await get_dns_records(token, zone_id)
    
    all_records = context.user_data.get('all_records', [])
    records_in_view = context.user_data.get('records_in_view', all_records)
    
    search_query = context.user_data.get('search_query')
    search_ip_query = context.user_data.get('search_ip_query')

    header_text = f"_{context.user_data.get('selected_account_nickname', 'N/A')}_ / `{zone_name}`"
    message_text = get_text('messages.all_records_list', lang)
    
    if search_query:
        message_text = get_text('messages.search_results', lang, query=search_query)
    elif search_ip_query:
        message_text = get_text('messages.search_results_ip', lang, query=search_ip_query)
    
    buttons = []
    
    if not records_in_view:
        buttons.extend([
            [InlineKeyboardButton(get_text('buttons.add_record', lang), callback_data="add")],
            [InlineKeyboardButton(get_text('buttons.back_to_zones', lang), callback_data="back_to_zones")]
        ])
        msg_text = get_text('messages.no_records_found_search' if (search_query or search_ip_query) else 'messages.no_records_found', lang)
        full_message = f"{header_text}\n\n{msg_text}"
        if query: await query.edit_message_text(full_message, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        else: await context.bot.send_message(update.effective_chat.id, full_message, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        return

    context.user_data["records"] = {r['id']: r for r in all_records}
    start_index, end_index = page * RECORDS_PER_PAGE, (page + 1) * RECORDS_PER_PAGE
    records_on_page = records_in_view[start_index:end_index]
    is_bulk_mode, selected_records = context.user_data.get('is_bulk_mode', False), context.user_data.get('selected_records', [])

    if is_bulk_mode:
        all_ids_in_view = {r['id'] for r in records_in_view}
        all_selected = all_ids_in_view.issubset(set(selected_records)) if all_ids_in_view else False
        select_all_text = get_text('buttons.deselect_all', lang) if all_selected else get_text('buttons.select_all', lang)
        buttons.append([InlineKeyboardButton(select_all_text, callback_data=f"bulk_select_all|{page}")])

    for r in records_on_page:
        short_name = get_short_name(r['name'], zone_name)
        proxy_icon = "☁️" if r.get('proxied') else "⬜️"
        if is_bulk_mode:
            check_icon = "✅" if r['id'] in selected_records else "▫️"
            button_text, callback_data = f"{check_icon} {r['type']} {short_name}", f"bulk_select|{r['id']}|{page}"
        else:
            button_text, callback_data = f"{proxy_icon} {r['type']} {short_name}", f"select|{r['id']}"
        buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    pagination_buttons = []
    if page > 0: pagination_buttons.append(InlineKeyboardButton(get_text('buttons.previous', lang), callback_data=f"list_page|{page - 1}"))
    if end_index < len(records_in_view): pagination_buttons.append(InlineKeyboardButton(get_text('buttons.next', lang), callback_data=f"list_page|{page + 1}"))
    if pagination_buttons: buttons.append(pagination_buttons)
    
    if is_bulk_mode:
        count = len(selected_records)
        buttons.append([InlineKeyboardButton(get_text('buttons.change_ip_selected', lang, count=count), callback_data="bulk_change_ip_start"), 
                        InlineKeyboardButton(get_text('buttons.delete_selected', lang, count=count), callback_data="bulk_delete_confirm")])
        buttons.append([InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data="bulk_cancel")])
    else:
        buttons.append([
            InlineKeyboardButton(get_text('buttons.add_record', lang), callback_data="add"),
            InlineKeyboardButton(get_text('buttons.search', lang), callback_data="search_menu"),
            InlineKeyboardButton(get_text('buttons.bulk_actions', lang), callback_data="bulk_start")
        ])
        buttons.append([
            InlineKeyboardButton(get_text('buttons.refresh', lang), callback_data="refresh_list"),
            InlineKeyboardButton(get_text('buttons.settings', lang), callback_data="go_to_settings")
        ])
        
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_zones', lang), callback_data="back_to_zones")])
    reply_markup = InlineKeyboardMarkup(buttons)
    full_message = f"{header_text}\n\n{message_text}"
    
    try:
        if query:
            await query.edit_message_text(full_message, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await context.bot.send_message(update.effective_chat.id, full_message, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception:
        pass

# --- Callback Handlers ---
async def remove_notification_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a list of current notification admins to be removed."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    config = load_config()
    
    admin_ids = config.get("notifications", {}).get("chat_ids", [])
    
    if not admin_ids:
        await query.answer(get_text('messages.no_admins_to_remove', lang), show_alert=True)
        return
        
    buttons = []
    for admin_id in admin_ids:
        buttons.append([InlineKeyboardButton(str(admin_id), callback_data=f"confirm_remove_admin|{admin_id}")])
    
    buttons.append([InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data="settings_notifications")])
    
    await query.edit_message_text(
    get_text('prompts.select_admin_to_remove', lang),
    reply_markup=InlineKeyboardMarkup(buttons)
)

async def confirm_remove_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes the selected admin from the notification list."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    admin_id_to_remove = int(query.data.split('|')[1])
    config = load_config()
    
    if admin_id_to_remove in config['notifications']['chat_ids']:
        config['notifications']['chat_ids'].remove(admin_id_to_remove)
        save_config(config)
        await query.answer(get_text('messages.admin_id_removed', lang, admin_id=admin_id_to_remove), show_alert=True)
    
    await show_settings_notifications_menu(update, context)

async def add_notification_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of adding a new admin for notifications."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    context.user_data['awaiting_notification_admin_id'] = True
    
    await query.edit_message_text(get_text('prompts.enter_admin_id_to_add', lang))

async def policy_records_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles pagination for the record selection list."""
    query = update.callback_query
    await query.answer()
    page = int(query.data.split('|')[1])
    await display_records_for_selection(update, context, page=page)

async def policy_select_record_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles toggling the selection of a record."""
    query = update.callback_query
    await query.answer()
    _, short_name, page_str = query.data.split('|')
    page = int(page_str)
    
    selected_records = context.user_data.get('policy_selected_records', [])
    if short_name in selected_records:
        selected_records.remove(short_name)
    else:
        selected_records.append(short_name)
    context.user_data['policy_selected_records'] = selected_records
    
    await display_records_for_selection(update, context, page=page)

async def policy_confirm_records_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finalizes the policy creation or edit after records are selected."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    selected_records = context.user_data.get('policy_selected_records', [])
    if not selected_records:
        await query.answer("Please select at least one record.", show_alert=True); return

    is_editing = context.user_data.get('is_editing_policy_records', False)
    
    if is_editing:
        policy_index = context.user_data['edit_policy_index']
        config = load_config()
        config['failover_policies'][policy_index]['record_names'] = selected_records
        save_config(config)
        
        context.user_data.pop('is_editing_policy_records', None)
        context.user_data.pop('policy_all_records', None)
        context.user_data.pop('policy_selected_records', None)
        context.user_data.pop('current_selection_zone', None)
        
        await query.edit_message_text("✅ Record names updated successfully!")
        
        await policy_view_callback(update, context)
        
    else:
        data = context.user_data['new_policy_data']
        data['record_names'] = selected_records
        
        context.user_data.pop('policy_all_records', None)
        context.user_data.pop('policy_selected_records', None)
        context.user_data.pop('current_selection_zone', None)
        
        context.user_data['add_policy_step'] = 'auto_failback'
        buttons = [
            [
                InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="policy_set_failback|true"),
                InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="policy_set_failback|false")
            ]
        ]
        await query.edit_message_text(get_text('prompts.ask_auto_failback', lang), reply_markup=InlineKeyboardMarkup(buttons))

async def policy_set_failback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets the auto_failback option for a new policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    choice = query.data.split('|')[1]
    auto_failback_enabled = (choice == 'true')
    
    context.user_data['new_policy_data']['auto_failback'] = auto_failback_enabled
    
    if auto_failback_enabled:
        context.user_data['add_policy_step'] = 'failback_minutes'
        await query.edit_message_text(get_text('prompts.enter_failback_minutes', lang))
    else:
        context.user_data['new_policy_data']['failback_minutes'] = 5
        
        config = load_config()
        config['failover_policies'].append(context.user_data['new_policy_data'])
        save_config(config)
        
        policy_name = context.user_data['new_policy_data'].get('policy_name', '')
        context.user_data.pop('add_policy_step')
        context.user_data.pop('new_policy_data')
        
        await query.edit_message_text(get_text('messages.policy_added_successfully', lang, name=policy_name))
        await settings_policies_callback(update, context)

async def policy_toggle_failback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles the 'auto_failback' status of a policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = int(query.data.split('|')[1])
        config = load_config()
        if config is None: raise IndexError

        is_failback_enabled = config['failover_policies'][policy_index].get('auto_failback', True)
        
        config['failover_policies'][policy_index]['auto_failback'] = not is_failback_enabled
        save_config(config)

        policy_name = config['failover_policies'][policy_index].get('policy_name', 'N/A')
        new_status_key = 'status_enabled' if not is_failback_enabled else 'status_disabled'
        new_status_text = get_text(new_status_key, lang)
        
        await query.answer(
            text=get_text('messages.failback_status_changed', lang, name=policy_name, status=new_status_text),
            show_alert=True
        )
        
        await policy_view_callback(update, context)

    except (IndexError, ValueError):
        await query.edit_message_text("Error: Could not toggle auto-failback status.")

async def policy_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles the 'enabled' status of a policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = int(query.data.split('|')[1])
        config = load_config()
        if config is None: raise IndexError

        is_enabled = config['failover_policies'][policy_index].get('enabled', True)
        
        config['failover_policies'][policy_index]['enabled'] = not is_enabled
        save_config(config)

        policy_name = config['failover_policies'][policy_index].get('policy_name', 'N/A')
        new_status_key = 'status_enabled' if not is_enabled else 'status_disabled'
        new_status_text = get_text(new_status_key, lang)
        
        await query.answer(
            text=get_text('messages.policy_status_changed', lang, name=policy_name, status=new_status_text),
            show_alert=True
        )
        
        await policy_view_callback(update, context)

    except (IndexError, ValueError):
        await query.edit_message_text("Error: Could not toggle policy status.")

async def sync_now_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs the sync function on demand from a button press."""
    query = update.callback_query
    
    await query.answer(text="Sync process started...", show_alert=False)
    
    await query.edit_message_text("⏳ Syncing with Cloudflare, please wait...")
    
    await sync_dns_with_config(context.application)
    
    await settings_policies_callback(update, context)
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
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

    if context.user_data.get('awaiting_notification_admin_id'):
        context.user_data.pop('awaiting_notification_admin_id')
        
        if not text.isdigit():
            await update.message.reply_text("Invalid ID. Please enter a numeric ID only.")
            await show_settings_notifications_menu(update, context)
            return
            
        admin_id = int(text)
        config = load_config()
        
        if admin_id in config['notifications']['chat_ids']:
            await update.message.reply_text(get_text('messages.admin_id_already_exists', lang))
        else:
            config['notifications']['chat_ids'].append(admin_id)
            save_config(config)
            await update.message.reply_text(get_text('messages.admin_id_added', lang, admin_id=admin_id))

        await show_settings_notifications_menu(update, context)
        return

    if 'edit_policy_index' in context.user_data and 'edit_policy_field' in context.user_data:
        policy_index = context.user_data['edit_policy_index']
        field = context.user_data['edit_policy_field']
        
        if field in ['primary_ip', 'backup_ip'] and not is_valid_ip(text):
            await update.message.reply_text(get_text('messages.invalid_ip', lang)); return
            
        if field == 'check_port' and (not text.isdigit() or not (1 <= int(text) <= 65535)):
            await update.message.reply_text(get_text('messages.invalid_port', lang)); return

        if field in ['failover_minutes', 'failback_minutes']:
            try:
                value = float(text)
                if value <= 0:
                    await update.message.reply_text("Please enter a positive number for minutes."); return
            except ValueError:
                await update.message.reply_text("Please enter a valid number (e.g., 2 or 1.5)."); return

        config = load_config()
        if config is None:
            await update.message.reply_text("Error: Could not load config file."); return

        try:
            value_to_save = text
            if field == 'check_port': value_to_save = int(text)
            elif field in ['failover_minutes', 'failback_minutes']: value_to_save = float(text)
            elif field == 'record_names': value_to_save = [r.strip() for r in text.split(',') if r.strip()]
            
            config['failover_policies'][policy_index][field] = value_to_save
            save_config(config)
        except IndexError:
            await update.message.reply_text("Error: Policy could not be updated."); return

        context.user_data.pop('edit_policy_index')
        context.user_data.pop('edit_policy_field')
        
        buttons = [
            [InlineKeyboardButton(get_text('buttons.sync_now', lang), callback_data="sync_now")],
            [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="settings_policies")]
        ]
        await update.message.reply_text(
            get_text('messages.policy_field_updated_prompt_sync', lang, field=field),
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if 'add_policy_step' in context.user_data:
        step = context.user_data['add_policy_step']
        data = context.user_data['new_policy_data']
        
        if step == 'name':
            data['policy_name'] = text
            context.user_data['add_policy_step'] = 'primary_ip'
            await update.message.reply_text(get_text('prompts.enter_primary_ip', lang))
        elif step == 'primary_ip':
            if not is_valid_ip(text): await update.message.reply_text(get_text('messages.invalid_ip', lang)); return
            data['primary_ip'] = text
            context.user_data['add_policy_step'] = 'backup_ip'
            await update.message.reply_text(get_text('prompts.enter_backup_ip', lang))
        elif step == 'backup_ip':
            if not is_valid_ip(text): await update.message.reply_text(get_text('messages.invalid_ip', lang)); return
            data['backup_ip'] = text
            context.user_data['add_policy_step'] = 'check_port'
            await update.message.reply_text(get_text('prompts.enter_check_port', lang))
        elif step == 'check_port':
            if not text.isdigit() or not (1 <= int(text) <= 65535): await update.message.reply_text(get_text('messages.invalid_port', lang)); return
            data['check_port'] = int(text)
            context.user_data['add_policy_step'] = 'failover_minutes'
            await update.message.reply_text(get_text('prompts.enter_failover_minutes', lang))
        
        elif step == 'failover_minutes':
            if not text.replace('.', '', 1).isdigit() or float(text) <= 0:
                await update.message.reply_text("Please enter a valid positive number for minutes."); return
            data['failover_minutes'] = float(text)
            context.user_data['add_policy_step'] = 'account_nickname'
            buttons = [[InlineKeyboardButton(nickname, callback_data=f"policy_set_account|{nickname}")] for nickname in CF_ACCOUNTS.keys()]
            await update.message.reply_text(get_text('prompts.choose_cf_account', lang), reply_markup=InlineKeyboardMarkup(buttons))

        elif step == 'failback_minutes':
            if not text.replace('.', '', 1).isdigit() or float(text) <= 0:
                await update.message.reply_text("Please enter a valid positive number for minutes."); return
            data['failback_minutes'] = float(text)
            config = load_config()
            config['failover_policies'].append(data)
            save_config(config)
            context.user_data.pop('add_policy_step')
            context.user_data.pop('new_policy_data')
            await update.message.reply_text(get_text('messages.policy_added_successfully', lang, name=data['policy_name']))
            await settings_policies_callback(update, context)
        return

    if context.user_data.get('is_searching'):
        context.user_data.pop('is_searching')
        context.user_data['search_query'] = text
        context.user_data['records_in_view'] = [r for r in context.user_data.get('all_records', []) if text.lower() in r['name'].lower()]
        await display_records_list(update, context); return
        
    if context.user_data.get('is_searching_ip'):
        context.user_data.pop('is_searching_ip')
        context.user_data['search_ip_query'] = text
        context.user_data['records_in_view'] = [r for r in context.user_data.get('all_records', []) if r['content'] == text]
        await display_records_list(update, context); return
        
    if context.user_data.get('is_bulk_ip_change'):
        selected_ids = context.user_data.get('selected_records', [])
        context.user_data.pop('is_bulk_ip_change')
        context.user_data['bulk_ip_confirm_details'] = {'new_ip': text, 'record_ids': selected_ids}
        kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="bulk_change_ip_execute")],
              [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="bulk_cancel")]]
        await update.message.reply_text(get_text('messages.bulk_confirm_change_ip', lang, count=len(selected_ids), new_ip=f"`{text}`"),
                                        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"); return
                                        
    if "edit" in context.user_data:
        data = context.user_data.pop("edit")
        context.user_data["confirm"] = {"id": data["id"], "type": data["type"], "name": data["name"], "old": data["old"], "new": text}
        kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="confirm_change")],
              [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_records_list")]]
        await update.message.reply_text(f"🔄 `{data['old']}` ➡️ `{text}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"); return
        
    if "add_step" in context.user_data:
        step = context.user_data["add_step"]
        zone_name = context.user_data.get('selected_zone_name', 'your_domain.com')
        if step == "name":
            new_name = zone_name if text.strip() == "@" else f"{text.strip()}.{zone_name}"
            all_records = context.user_data.get('all_records', [])
            existing_record = next((r for r in all_records if r['name'].lower() == new_name.lower()), None)
            if existing_record:
                context.user_data.pop("add_step", None)
                buttons = [[InlineKeyboardButton(get_text('buttons.try_another_name', lang), callback_data="add_retry_name")],
                           [InlineKeyboardButton(get_text('buttons.edit_value', lang), callback_data=f"edit|{existing_record['id']}")],
                           [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_records_list")]]
                await update.message.reply_text(get_text('messages.subdomain_exists', lang), reply_markup=InlineKeyboardMarkup(buttons)); return
            context.user_data["new_name"] = new_name
            context.user_data["add_step"] = "content"
            prompt_text_key = 'prompts.enter_ip' if context.user_data.get("new_type") in ['A', 'AAAA'] else 'prompts.enter_content'
            prompt_text = get_text(prompt_text_key, lang, name=f"`{text.strip()}`")
            await update.message.reply_text(prompt_text, parse_mode="Markdown")
        elif step == "content":
            context.user_data["new_content"] = text
            context.user_data.pop("add_step")
            kb = [[InlineKeyboardButton("DNS Only", callback_data="add_proxied|false")], [InlineKeyboardButton("Proxied", callback_data="add_proxied|true")]]
            await update.message.reply_text(get_text('prompts.choose_proxy', lang), reply_markup=InlineKeyboardMarkup(kb))

async def select_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    nickname = query.data.split('|')[1]
    clear_state(context)
    context.user_data['selected_account_nickname'] = nickname
    await display_zones_list(update, context)

async def back_to_accounts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(context, preserve=[])
    await display_account_list(update, context)

async def refresh_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('all_records', None)
    context.user_data.pop('records_in_view', None)
    context.user_data.pop('search_query', None)
    context.user_data.pop('search_ip_query', None)
    await display_records_list(update, context)

async def select_zone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    zone_id = query.data.split('|')[1]
    all_zones = context.user_data.get('all_zones', {})
    zone = all_zones.get(zone_id)
    if not zone:
        await query.edit_message_text("Error: Zone not found."); return
    clear_state(context, preserve=['language', 'selected_account_nickname', 'all_zones'])
    context.user_data['selected_zone_id'] = zone_id
    context.user_data['selected_zone_name'] = zone['name']
    await display_records_list(update, context)

async def back_to_zones_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(context, preserve=['language', 'selected_account_nickname', 'all_zones'])
    await display_zones_list(update, context)

async def back_to_records_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_page = context.user_data.get('current_page', 0)
    preserve_keys = ['language', 'selected_account_nickname', 'all_zones', 'selected_zone_id', 'selected_zone_name', 'all_records', 'records']
    clear_state(context, preserve=preserve_keys)
    await display_records_list(update, context, page=current_page)

async def list_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE): await display_records_list(update, context, page=int(update.callback_query.data.split('|')[1]))

async def select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    if not record:
        await update.callback_query.edit_message_text(get_text('messages.no_records_found', lang)); return
    proxy_text = get_text('messages.proxy_status_active', lang) if record.get('proxied') else get_text('messages.proxy_status_inactive', lang)
    kb = [[InlineKeyboardButton(get_text('buttons.edit_value', lang), callback_data=f"edit|{rid}")],
          [InlineKeyboardButton(get_text('buttons.toggle_proxy', lang), callback_data=f"toggle_proxy|{rid}")],
          [InlineKeyboardButton(get_text('buttons.delete', lang), callback_data=f"delete|{rid}")],
          [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_records_list")]]
    text = get_text('messages.record_details', lang, type=record['type'], name=f"`{record['name']}`", content=f"`{record['content']}`", proxy_status=proxy_text)
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    if not record:
        await update.callback_query.message.reply_text(get_text('messages.internal_error', lang)); return
    context.user_data["edit"] = {"id": record["id"], "type": record["type"], "name": record["name"], "old": record["content"]}
    zone_name = context.user_data.get('selected_zone_name', '')
    record_short_name = get_short_name(record['name'], zone_name)
    prompt_key = 'prompts.enter_ip' if record['type'] in ['A', 'AAAA'] else 'prompts.enter_content'
    prompt_text = get_text(prompt_key, lang, name=f"`{record_short_name}`")
    await update.callback_query.message.reply_text(prompt_text, parse_mode="Markdown")

async def toggle_proxy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    current_status = get_text('messages.proxy_status_active', lang) if record.get('proxied') else get_text('messages.proxy_status_inactive', lang)
    new_status = get_text('messages.proxy_status_inactive', lang) if record.get('proxied') else get_text('messages.proxy_status_active', lang)
    kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"toggle_proxy_confirm|{rid}")],
          [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"select|{rid}")]]
    text = get_text('messages.confirm_proxy_toggle', lang, record_name=f"`{record['name']}`", current_status=current_status, new_status=new_status)
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def toggle_proxy_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    token = get_current_token(context)
    if not token: return
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    new_proxied_status = not record.get('proxied', False)
    res = await update_record(token, context.user_data['selected_zone_id'], rid, record['type'], record['name'], record['content'], new_proxied_status)
    if res.get("success"):
        await update.callback_query.answer(get_text('messages.proxy_toggled_successfully', lang, record_name=record['name']), show_alert=True)
        for i, r in enumerate(context.user_data['all_records']):
            if r['id'] == rid: context.user_data['all_records'][i]['proxied'] = new_proxied_status; break
        await select_callback(update, context)
    else:
        error_msg = res.get('errors', [{}])[0].get('message', get_text('messages.error_toggling_proxy', lang))
        await update.callback_query.answer(error_msg, show_alert=True)

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"delete_confirm|{rid}")],
          [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"select|{rid}")]]
    await update.callback_query.edit_message_text(get_text('messages.confirm_delete_record', lang, record_name=f"`{record['name']}`"),
                                                  reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    token = get_current_token(context)
    if not token: return
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid, {})
    res = await delete_record(token, context.user_data['selected_zone_id'], rid)
    if res.get("success"):
        await update.callback_query.edit_message_text(get_text('messages.record_deleted_successfully', lang, record_name=f"`{record.get('name', 'N/A')}`"),
                                                      parse_mode="Markdown")
        context.user_data.pop('all_records', None)
        await asyncio.sleep(1)
        await display_records_list(update, context)
    else:
        await update.callback_query.edit_message_text(get_text('messages.error_deleting_record', lang))

async def confirm_change_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    token = get_current_token(context)
    if not token: return
    query = update.callback_query
    info = context.user_data.pop("confirm", {})
    original_record = context.user_data.get("records", {}).get(info["id"], {})
    proxied_status = original_record.get("proxied", False)
    res = await update_record(token, context.user_data['selected_zone_id'], info["id"], info["type"], info["name"], info["new"], proxied_status)
    if res.get("success"):
        await query.edit_message_text(get_text('messages.record_updated_successfully', lang, record_name=f"`{info['name']}`"), parse_mode="Markdown")
        context.user_data.pop('all_records', None)
        await asyncio.sleep(1)
        await display_records_list(update, context)
    else:
        error_msg = res.get('errors', [{}])[0].get('message', 'Unknown error')
        await query.edit_message_text(get_text('messages.error_updating_record', lang, error=error_msg))

async def add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lang = get_user_lang(context)
    query = update.callback_query
    message_source = query.message if query else update.message
    zone_id = context.user_data.get('selected_zone_id')
    if not zone_id:
        msg = get_text('messages.no_zone_selected', lang)
        if query: await query.answer(msg, show_alert=True)
        else: await message_source.reply_text(msg)
        return
    clear_state(context, preserve=['language', 'selected_account_nickname', 'all_zones', 'selected_zone_id', 'selected_zone_name', 'all_records', 'records'])
    buttons = [InlineKeyboardButton(t, callback_data=f"add_type|{t}") for t in DNS_RECORD_TYPES]
    buttons_3col = chunk_list(buttons, 3)
    buttons_3col.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_records_list")])
    reply_markup = InlineKeyboardMarkup(buttons_3col)
    text = get_text('prompts.choose_record_type', lang)
    if query: await query.edit_message_text(text, reply_markup=reply_markup)
    else: await message_source.reply_text(text, reply_markup=reply_markup)

async def add_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_type"] = update.callback_query.data.split('|')[1]
    context.user_data["add_step"] = "name"
    await update.callback_query.edit_message_text(get_text('prompts.enter_subdomain', get_user_lang(context)))

async def add_proxied_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    token = get_current_token(context)
    if not token: return
    proxied = update.callback_query.data.split('|')[1].lower() == "true"
    rtype, name, content = context.user_data.pop("new_type"), context.user_data.pop("new_name"), context.user_data.pop("new_content")
    res = await create_record(token, context.user_data['selected_zone_id'], rtype, name, content, proxied)
    if res.get("success"):
        await update.callback_query.edit_message_text(get_text('messages.record_added_successfully', lang, rtype=rtype, name=f"`{name}`"), parse_mode="Markdown")
        context.user_data.pop('all_records', None)
        await asyncio.sleep(1)
        await display_records_list(update, context)
    else:
        error_msg = res.get('errors', [{}])[0].get('message', 'Unknown error')
        await update.callback_query.edit_message_text(get_text('messages.error_creating_record', lang, error=error_msg))

async def add_retry_name_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    context.user_data["add_step"] = "name"
    await update.callback_query.edit_message_text(get_text('prompts.enter_subdomain', lang))

async def search_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    query = update.callback_query
    buttons = [[InlineKeyboardButton(get_text('buttons.search_by_name', lang), callback_data="search_by_name")],
               [InlineKeyboardButton(get_text('buttons.search_by_ip', lang), callback_data="search_by_ip")],
               [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_records_list")]]
    await query.edit_message_text(get_text('messages.search_menu', lang), reply_markup=InlineKeyboardMarkup(buttons))

async def search_by_name_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    query = update.callback_query
    clear_state(context, preserve=['language', 'selected_account_nickname', 'all_zones', 'selected_zone_id', 'selected_zone_name', 'all_records', 'records'])
    context.user_data['is_searching'] = True
    text = get_text('prompts.enter_search_query', lang)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text('buttons.cancel_search', lang), callback_data="back_to_records_list")]])
    await query.edit_message_text(text, reply_markup=kb)

async def search_by_ip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    query = update.callback_query
    clear_state(context, preserve=['language', 'selected_account_nickname', 'all_zones', 'selected_zone_id', 'selected_zone_name', 'all_records', 'records'])
    context.user_data['is_searching_ip'] = True
    text = get_text('prompts.enter_search_query_ip', lang)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text('buttons.cancel_search', lang), callback_data="back_to_records_list")]])
    await query.edit_message_text(text, reply_markup=kb)

async def bulk_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_view = context.user_data.get('records_in_view')
    preserve_keys = ['language', 'selected_account_nickname', 'all_zones', 'selected_zone_id', 'selected_zone_name', 'all_records', 'records']
    if current_view is not None:
        preserve_keys.extend(['records_in_view', 'search_query', 'search_ip_query'])
    clear_state(context, preserve=preserve_keys)
    context.user_data['is_bulk_mode'] = True
    context.user_data['selected_records'] = []
    await display_records_list(update, context)

async def bulk_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_page = context.user_data.get('current_page', 0)
    preserve_keys = ['language', 'selected_account_nickname', 'all_zones', 'selected_zone_id', 'selected_zone_name', 'all_records', 'records']
    if context.user_data.get('records_in_view') is not None:
        preserve_keys.extend(['records_in_view', 'search_query', 'search_ip_query'])
    clear_state(context, preserve=preserve_keys)
    await display_records_list(update, context, page=current_page)

async def bulk_select_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    page = int(query.data.split('|')[1])
    selected_records = context.user_data.get('selected_records', [])
    records_in_view = context.user_data.get('records_in_view', context.user_data.get('all_records', []))
    all_ids_in_view = {r['id'] for r in records_in_view}
    current_selected_set = set(selected_records)
    if all_ids_in_view.issubset(current_selected_set):
        current_selected_set.difference_update(all_ids_in_view)
    else:
        current_selected_set.update(all_ids_in_view)
    context.user_data['selected_records'] = list(current_selected_set)
    await display_records_list(update, context, page=page)

async def bulk_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rid, page = update.callback_query.data.split('|')[1], int(update.callback_query.data.split('|')[2])
    selected = context.user_data.get('selected_records', [])
    if rid in selected: selected.remove(rid)
    else: selected.append(rid)
    context.user_data['selected_records'] = selected
    await display_records_list(update, context, page=page)

async def bulk_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    selected_ids = context.user_data.get('selected_records', [])
    if not selected_ids:
        await update.callback_query.answer(get_text('messages.bulk_no_selection', lang), show_alert=True); return
    kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="bulk_delete_execute")],
          [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="bulk_start")]]
    await update.callback_query.edit_message_text(get_text('messages.bulk_confirm_delete', lang, count=len(selected_ids)),
                                                  reply_markup=InlineKeyboardMarkup(kb))

async def bulk_delete_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    token = get_current_token(context)
    if not token: return
    selected_ids = context.user_data.get('selected_records', [])
    query = update.callback_query
    await query.edit_message_text(get_text('messages.bulk_delete_progress', lang, count=len(selected_ids)))
    success, fail = 0, 0
    zone_id = context.user_data['selected_zone_id']
    for rid in selected_ids:
        res = await delete_record(token, zone_id, rid)
        if res.get("success"): success += 1
        else: fail += 1
        await asyncio.sleep(0.3)
    msg = get_text('messages.bulk_delete_report', lang, success=success, fail=fail)
    kb = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_records_list")]]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    clear_state(context, preserve=['language', 'selected_account_nickname', 'all_zones', 'selected_zone_id', 'selected_zone_name'])

async def bulk_change_ip_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    selected_ids = context.user_data.get('selected_records', [])
    if not selected_ids:
        await update.callback_query.answer(get_text('messages.bulk_no_selection', lang), show_alert=True); return
    context.user_data['is_bulk_ip_change'] = True
    await update.callback_query.edit_message_text(get_text('messages.bulk_change_ip_prompt', lang, count=len(selected_ids)))

async def bulk_change_ip_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    token = get_current_token(context)
    if not token: return
    query = update.callback_query
    details = context.user_data.pop('bulk_ip_confirm_details', {})
    if not details:
        await query.edit_message_text(get_text('messages.internal_error', lang)); return
    new_ip, record_ids = details['new_ip'], details['record_ids']
    await query.edit_message_text(get_text('messages.bulk_change_ip_progress', lang, count=len(record_ids), new_ip=f"`{new_ip}`"),
                                  parse_mode="Markdown")
    success, fail, skipped = 0, 0, 0
    all_records_map, zone_id = context.user_data.get("records", {}), context.user_data['selected_zone_id']
    for rid in record_ids:
        record = all_records_map.get(rid)
        if not record: fail += 1; continue
        if record['type'] in ['A', 'AAAA']:
            res = await update_record(token, zone_id, rid, record['type'], record['name'], new_ip, record.get('proxied', False))
            if res.get("success"): success += 1
            else: fail += 1
        else: skipped += 1
        await asyncio.sleep(0.3)
    msg = get_text('messages.bulk_change_ip_report', lang, success=success, skipped=skipped, fail=fail)
    kb = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_records_list")]]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    clear_state(context, preserve=['language', 'selected_account_nickname', 'all_zones', 'selected_zone_id', 'selected_zone_name'])

async def set_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang_code = update.callback_query.data.split('|')[1]
    context.user_data['language'] = lang_code
    await update.callback_query.edit_message_text(get_text('messages.language_changed', lang_code))
    await asyncio.sleep(1)
    await list_records_command(update, context)

# --- Settings Callbacks ---
async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    """Builds and sends the main settings menu, either as a new message or by editing."""
    lang = get_user_lang(context)
    
    buttons = [
        [InlineKeyboardButton(get_text('buttons.manage_policies', lang), callback_data="settings_policies")],
        [InlineKeyboardButton(get_text('buttons.manage_notifications', lang), callback_data="settings_notifications")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_records_list")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    text = get_text('messages.settings_menu', lang)

    query = update.callback_query
    
    if query and not force_new_message:
        try:
            await query.edit_message_text(text, reply_markup=reply_markup)
        except Exception:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)
    else:
        chat_id = update.effective_chat.id
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

async def go_to_main_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'Manage Records' button from the startup sync message."""
    query = update.callback_query
    await query.answer()
    
    clear_state(context)
    
    await display_account_list(update, context, force_new_message=True)
async def go_to_settings_from_startup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'Go to Settings' button from the startup sync message."""
    query = update.callback_query
    await query.answer()
    
    await show_settings_menu(update, context, force_new_message=True)

async def go_to_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_settings_menu(update, context)

async def back_to_settings_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_settings_menu(update, context)

async def settings_policies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    lang = get_user_lang(context)
    config = load_config()
    if config is None:
        error_message = "❌ Error: The configuration file is corrupted. Please check the logs."
        if query: await query.edit_message_text(error_message)
        else: await update.message.reply_text(error_message)
        return
    policies = config.get("failover_policies", [])
    buttons = []
    if not policies: policies_text = get_text('messages.no_policies', lang)
    else:
        policies_text = ""
        for i, policy in enumerate(policies):
            policies_text += f"*{i+1}. {policy['policy_name']}*\n`{policy['primary_ip']}` -> `{policy['backup_ip']}`\n\n"
            buttons.append([InlineKeyboardButton(f"{i+1}. {policy['policy_name']}", callback_data=f"policy_view|{i}")])
    text = get_text('messages.policies_list_menu', lang, policies_text=policies_text)
    buttons.append([InlineKeyboardButton(get_text('buttons.add_policy', lang), callback_data="policy_add_start")])
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")])
    reply_markup = InlineKeyboardMarkup(buttons)
    if query: await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else: await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def settings_notifications_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback entry point for notification settings menu."""
    query = update.callback_query
    if query:
        await query.answer()
    await show_settings_notifications_menu(update, context)

async def show_settings_notifications_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Builds and sends the notification settings menu."""
    lang = get_user_lang(context)
    config = load_config()
    
    is_enabled = config.get("notifications", {}).get("enabled", True)
    status_text = get_text('buttons.notifications_on', lang) if is_enabled else get_text('buttons.notifications_off', lang)
    
    buttons = [
        [InlineKeyboardButton(status_text, callback_data="toggle_notifications")],
        [
            InlineKeyboardButton(get_text('buttons.add_admin_id', lang), callback_data="add_notification_admin"),
            InlineKeyboardButton(get_text('buttons.remove_admin_id', lang), callback_data="remove_notification_admin")
        ],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")]
    ]
    
    text = get_text('messages.notifications_menu', lang)
    reply_markup = InlineKeyboardMarkup(buttons)
    
    query = update.callback_query
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)

async def toggle_notifications_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    config = load_config()
    if config is None: return
    is_enabled = config.get("notifications", {}).get("enabled", True)
    config["notifications"]["enabled"] = not is_enabled
    save_config(config)
    status_key = 'on' if not is_enabled else 'off'
    await query.answer(get_text(f'messages.notification_status_changed', lang, status=status_key), show_alert=True)
    await settings_notifications_callback(update, context)

async def policy_add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    context.user_data['new_policy_data'] = {}
    context.user_data['add_policy_step'] = 'name'
    await query.edit_message_text(get_text('prompts.enter_policy_name', lang))

async def policy_set_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    nickname = query.data.split('|')[1]
    context.user_data['new_policy_data']['account_nickname'] = nickname
    token = CF_ACCOUNTS.get(nickname)
    if not token: await query.edit_message_text("Error: Account token not found."); return
    await query.edit_message_text("Fetching zones, please wait...")
    zones = await get_all_zones(token)
    if not zones: await query.edit_message_text("No zones found for this account."); return
    buttons = [[InlineKeyboardButton(zone['name'], callback_data=f"policy_set_zone|{zone['name']}")] for zone in zones]
    context.user_data['add_policy_step'] = 'zone_name'
    await query.edit_message_text(get_text('prompts.choose_zone_for_policy', lang), reply_markup=InlineKeyboardMarkup(buttons))

async def policy_set_zone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the selection of a zone for the new policy and starts the record selection process."""
    query = update.callback_query
    await query.answer()
    
    zone_name = query.data.split('|')[1]
    context.user_data['new_policy_data']['zone_name'] = zone_name
    
    context.user_data['policy_selected_records'] = []
    
    await display_records_for_selection(update, context, page=0)

async def policy_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays details of a specific policy with options to edit, delete, or toggle statuses."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = int(query.data.split('|')[1])
        config = load_config()
        if config is None or not (0 <= policy_index < len(config['failover_policies'])): raise IndexError
        policy = config['failover_policies'][policy_index]
    except (IndexError, ValueError):
        await query.edit_message_text("Error: Policy not found or config file is corrupted."); return

    is_enabled = policy.get('enabled', True)
    status_text = get_text('messages.status_enabled', lang) if is_enabled else get_text('messages.status_disabled', lang)
    
    is_failback_enabled = policy.get('auto_failback', False)
    failback_status_text = get_text('messages.status_enabled', lang) if is_failback_enabled else get_text('messages.status_disabled', lang)
    
    records_str = ", ".join(policy.get('record_names', []))
    details_text = get_text(
        'messages.policy_details', lang,
        name=policy.get('policy_name', 'N/A'),
        primary_ip=policy.get('primary_ip', 'N/A'),
        port=policy.get('check_port', 'N/A'),
        backup_ip=policy.get('backup_ip', 'N/A'),
        account=policy.get('account_nickname', 'N/A'),
        zone=policy.get('zone_name', 'N/A'),
        records=records_str
    )
    details_text += get_text('messages.policy_status', lang, status=status_text)
    details_text += get_text('messages.failback_status', lang, status=failback_status_text)
    
    toggle_button_text = get_text('buttons.disable_policy', lang) if is_enabled else get_text('buttons.enable_policy', lang)
    toggle_failback_button_text = get_text('buttons.disable_failback', lang) if is_failback_enabled else get_text('buttons.enable_failback', lang)
    
    buttons = [
        [
            InlineKeyboardButton(get_text('buttons.edit_policy', lang), callback_data=f"policy_edit|{policy_index}"),
            InlineKeyboardButton(toggle_button_text, callback_data=f"policy_toggle|{policy_index}")
        ],
        [InlineKeyboardButton(toggle_failback_button_text, callback_data=f"policy_toggle_failback|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.delete_policy', lang), callback_data=f"policy_delete|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="settings_policies")]
    ]
    
    await query.edit_message_text(details_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def policy_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a menu to edit different parts of a policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = int(query.data.split('|')[1])
    except (IndexError, ValueError):
        await query.edit_message_text("Error: Policy not found."); return

    context.user_data['edit_policy_index'] = policy_index

    buttons = [
        [
            InlineKeyboardButton(get_text('buttons.edit_policy_name', lang), callback_data=f"policy_edit_field|policy_name"),
            InlineKeyboardButton(get_text('buttons.edit_policy_port', lang), callback_data=f"policy_edit_field|check_port")
        ],
        [
            InlineKeyboardButton(get_text('buttons.edit_policy_primary_ip', lang), callback_data=f"policy_edit_field|primary_ip"),
            InlineKeyboardButton(get_text('buttons.edit_policy_backup_ip', lang), callback_data=f"policy_edit_field|backup_ip")
        ],
        [
            InlineKeyboardButton(get_text('buttons.edit_failover_minutes', lang), callback_data=f"policy_edit_field|failover_minutes"),
            InlineKeyboardButton(get_text('buttons.edit_failback_minutes', lang), callback_data=f"policy_edit_field|failback_minutes")
        ],
        [InlineKeyboardButton(get_text('buttons.edit_policy_records', lang), callback_data=f"policy_edit_field|record_names")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"policy_view|{policy_index}")]
    ]
    
    await query.edit_message_text(get_text('messages.edit_policy_menu', lang), reply_markup=InlineKeyboardMarkup(buttons))

async def policy_edit_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks the user for a new value or starts the record selection process for editing."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    field_to_edit = query.data.split('|')[1]
    
    if field_to_edit == 'record_names':
        context.user_data['is_editing_policy_records'] = True
        
        policy_index = context.user_data['edit_policy_index']
        config = load_config()
        policy = config['failover_policies'][policy_index]
        context.user_data['policy_selected_records'] = policy.get('record_names', [])
        
        await display_records_for_selection(update, context, page=0)
        return
    
    context.user_data['edit_policy_field'] = field_to_edit
    prompt_key = f"prompts.enter_new_{field_to_edit}"
    await query.edit_message_text(get_text(prompt_key, lang))

async def policy_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    try:
        policy_index = int(query.data.split('|')[1])
        config = load_config()
        policy_name = config['failover_policies'][policy_index]['policy_name']
    except (IndexError, ValueError): await query.edit_message_text("Error: Policy not found."); return
    buttons = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"policy_delete_confirm|{policy_index}")],
               [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"policy_view|{policy_index}")]]
    await query.edit_message_text(get_text('messages.confirm_delete_policy', lang, name=policy_name), reply_markup=InlineKeyboardMarkup(buttons))

async def policy_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    try:
        policy_index = int(query.data.split('|')[1])
        config = load_config()
        policy = config['failover_policies'].pop(policy_index)
        save_config(config)
    except (IndexError, ValueError): await query.edit_message_text("Error: Policy not found."); return
    await query.answer(get_text('messages.policy_deleted_successfully', lang, name=policy['policy_name']), show_alert=True)
    await settings_policies_callback(update, context)

# --- Command Handlers ---
async def clear_monitoring_state_on_startup(application: Application):
    """Clears only the health_status from bot_data on startup to prevent stale state."""
    logger.info("Clearing monitoring state (health_status) on startup...")
    if 'health_status' in application.bot_data:
        application.bot_data.pop('health_status')
    logger.info("Monitoring state cleared.")

async def sync_dns_with_config(application: Application):
    """
    Runs on startup or on demand to ensure DNS records match the primary_ip in config.json.
    It will UPDATE existing records to the correct primary_ip.
    """
    logger.info("--- Starting DNS Sync with Config on Startup ---")
    config = load_config()
    if config is None:
        logger.error("Sync failed: Could not load the config file.")
        return

    sync_results = {}

    for policy in config.get("failover_policies", []):
        policy_name = policy.get('policy_name', 'Unnamed Policy')
        primary_ip = policy.get('primary_ip')
        account_nickname = policy.get('account_nickname')
        zone_name = policy.get('zone_name')
        record_names = policy.get('record_names', [])
        
        if not all([primary_ip, account_nickname, zone_name, record_names]):
            logger.warning(f"Sync for policy '{policy_name}' skipped: required fields are missing.")
            continue

        sync_results[policy_name] = {'updated': 0, 'checked': len(record_names), 'errors': 0}
        
        token = CF_ACCOUNTS.get(account_nickname)
        if not token:
            logger.error(f"Sync for '{policy_name}' failed: No token found for account nickname '{account_nickname}'.")
            sync_results[policy_name]['errors'] = len(record_names)
            continue

        zones = await get_all_zones(token)
        zone_id = next((z['id'] for z in zones if z['name'] == zone_name), None)
        if not zone_id:
            logger.error(f"Sync for '{policy_name}' failed: Could not find zone '{zone_name}'.")
            sync_results[policy_name]['errors'] = len(record_names)
            continue

        all_records = await get_dns_records(token, zone_id)
        
        for record in all_records:
            short_name = get_short_name(record['name'], zone_name)
            
            if short_name in record_names and record.get('content') != primary_ip:
                logger.info(f"Sync: Record '{record['name']}' (IP: {record.get('content')}) does not match primary IP '{primary_ip}'. Updating...")
                
                res = await update_record(
                    token, zone_id, record['id'], record['type'],
                    record['name'], primary_ip, record.get('proxied', False)
                )

                if res.get("success"):
                    logger.info(f"Sync successful: Record '{record['name']}' was updated to {primary_ip}.")
                    sync_results[policy_name]['updated'] += 1
                else:
                    error_msg = res.get('errors', [{}])[0].get('message', 'Unknown error')
                    logger.error(f"Sync failed: Could not update record '{record['name']}'. Reason: {error_msg}")
                    sync_results[policy_name]['errors'] += 1
    
    notification_chat_ids = config.get("notifications", {}).get("chat_ids", [])
    if not notification_chat_ids:
        logger.info("No notification admins configured. Skipping sync summary.")
        logger.info("--- DNS Sync with Config Finished ---")
        return

    user_data = await application.persistence.get_user_data()

    for chat_id in notification_chat_ids:
        lang = user_data.get(chat_id, {}).get('language', 'fa')
        
        summary_message = get_text('messages.sync_summary_header', lang)
        has_changes = False
        
        for name, result in sync_results.items():
            if result['updated'] > 0 or result['errors'] > 0:
                has_changes = True
            summary_message += get_text(
                'messages.sync_policy_summary',
                lang,
                name=name,
                checked=result['checked'],
                updated=result['updated'],
                errors=result['errors']
            )

        if not has_changes and sync_results:
            summary_message += get_text('messages.sync_no_changes', lang)
        elif not sync_results:
            summary_message += get_text('messages.sync_no_policies', lang)

        buttons = [
            [
                InlineKeyboardButton(get_text('buttons.manage_records', lang), callback_data="go_to_main_list"),
                InlineKeyboardButton(get_text('buttons.go_to_settings', lang), callback_data="go_to_settings_from_startup")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(buttons)

        try:
            await application.bot.send_message(chat_id=chat_id, text=summary_message, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send sync summary to {chat_id} (lang: {lang}): {e}")

    logger.info("--- DNS Sync with Config Finished ---")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lang = get_user_lang(context)
    await update.message.reply_text(get_text('messages.welcome', lang))
    await language_command(update, context)

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    kb = [[InlineKeyboardButton("🇮🇷 فارسی", callback_data="set_lang|fa"), InlineKeyboardButton("🇬🇧 English", callback_data="set_lang|en")]]
    await update.message.reply_text(get_text('messages.choose_language', lang), reply_markup=InlineKeyboardMarkup(kb))

async def list_records_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    clear_state(context)
    await display_account_list(update, context)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text(get_text('messages.search_menu_command_message', get_user_lang(context)))

async def bulk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not context.user_data.get('selected_zone_id'):
        await update.message.reply_text(get_text('messages.no_zone_selected', get_user_lang(context))); return
    await bulk_start_callback(update, context)

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await add_callback(update, context)

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lang = get_user_lang(context)
    token = get_current_token(context)
    zone_id = context.user_data.get('selected_zone_id')
    if not token or not zone_id:
        await update.message.reply_text(get_text('messages.no_zone_selected', lang)); return
    await update.message.reply_text(get_text('messages.backup_in_progress', lang))
    records = await get_dns_records(token, zone_id)
    if not records:
        await update.message.reply_text(get_text('messages.no_records_found', lang)); return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_file_name = f"{context.user_data.get('selected_account_nickname', 'cf')}_{context.user_data['selected_zone_name']}_backup.json"
    backup_file_path = os.path.join(BACKUP_DIR, backup_file_name)
    try:
        with open(backup_file_path, "w", encoding='utf-8') as f: json.dump(records, f, indent=2)
        with open(backup_file_path, "rb") as f: await update.message.reply_document(f, filename=backup_file_name)
    finally:
        if os.path.exists(backup_file_path): os.remove(backup_file_path)

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lang = get_user_lang(context)
    if not context.user_data.get('selected_zone_id'):
        await update.message.reply_text(get_text('messages.no_zone_selected_for_restore', lang)); return
    await update.message.reply_text(get_text('messages.restore_prompt', lang))

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await show_settings_menu(update, context)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lang = get_user_lang(context)
    token = get_current_token(context)
    doc = update.message.document
    if not doc.file_name.endswith('.json'):
        await update.message.reply_text(get_text('messages.invalid_file_format', lang)); return
    zone_id = context.user_data.get('selected_zone_id')
    if not token or not zone_id:
        await update.message.reply_text(get_text('messages.no_zone_selected_for_restore', lang)); return
    file = await doc.get_file()
    file_content = await file.download_as_bytearray()
    try: backup_records = json.loads(file_content)
    except json.JSONDecodeError: await update.message.reply_text(get_text('messages.invalid_json_content', lang)); return
    await update.message.reply_text(get_text('messages.restore_in_progress', lang))
    existing_records = await get_dns_records(token, zone_id)
    existing_map = {(r["type"], r["name"]): r for r in existing_records}
    restored, skipped, failed = 0, 0, 0
    for r in backup_records:
        if (r["type"], r["name"]) in existing_map: skipped += 1; continue
        res = await create_record(token, zone_id, r["type"], r["name"], r["content"], r.get("proxied", False))
        if res.get("success"): restored += 1
        else: failed += 1
        await asyncio.sleep(0.3)
    await update.message.reply_text(get_text('messages.restore_report', lang, restored=restored, skipped=skipped, failed=failed))
    context.user_data.pop('all_records', None)
    await display_records_list(update, context)

def main():
    """Start the bot."""
    load_translations()
    
    persistence = PicklePersistence(filepath="bot_data.pickle")
    
    application = Application.builder() \
        .token(TELEGRAM_BOT_TOKEN) \
        .persistence(persistence) \
        .post_init(sync_dns_with_config) \
        .post_init(clear_monitoring_state_on_startup) \
        .build()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).post_init(sync_dns_with_config).build()
    
    # --- JobQueue Setup for Health Checks ---
    job_name = "health_check_job"
    if not application.job_queue.get_jobs_by_name(job_name):
        application.job_queue.run_repeating(health_check_job, interval=120, first=15, name=job_name)
       
    # Command Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("language", language_command))
    application.add_handler(CommandHandler("list", list_records_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("bulk", bulk_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("backup", backup_command))
    application.add_handler(CommandHandler("restore", restore_command))
    application.add_handler(CommandHandler("settings", settings_command))
    
    # Specific Callback Handlers
    application.add_handler(CallbackQueryHandler(add_notification_admin_callback, pattern="^add_notification_admin$"))
    application.add_handler(CallbackQueryHandler(remove_notification_admin_callback, pattern="^remove_notification_admin$"))
    application.add_handler(CallbackQueryHandler(confirm_remove_admin_callback, pattern="^confirm_remove_admin\|"))
    application.add_handler(CallbackQueryHandler(policy_records_page_callback, pattern="^policy_records_page\|"))
    application.add_handler(CallbackQueryHandler(policy_select_record_callback, pattern="^policy_select_record\|"))
    application.add_handler(CallbackQueryHandler(policy_confirm_records_callback, pattern="^policy_confirm_records$"))
    application.add_handler(CallbackQueryHandler(policy_set_failback_callback, pattern="^policy_set_failback\|"))
    application.add_handler(CallbackQueryHandler(policy_toggle_failback_callback, pattern="^policy_toggle_failback\|"))
    application.add_handler(CallbackQueryHandler(policy_toggle_callback, pattern="^policy_toggle\|"))
    application.add_handler(CallbackQueryHandler(policy_toggle_callback, pattern="^policy_toggle\|"))
    application.add_handler(CallbackQueryHandler(go_to_main_list_callback, pattern="^go_to_main_list$"))
    application.add_handler(CallbackQueryHandler(go_to_settings_from_startup_callback, pattern="^go_to_settings_from_startup$"))
    application.add_handler(CallbackQueryHandler(sync_now_callback, pattern="^sync_now$"))
    application.add_handler(CallbackQueryHandler(policy_view_callback, pattern="^policy_view\|"))
    application.add_handler(CallbackQueryHandler(policy_edit_callback, pattern="^policy_edit\|"))
    application.add_handler(CallbackQueryHandler(policy_edit_field_callback, pattern="^policy_edit_field\|"))
    application.add_handler(CallbackQueryHandler(policy_delete_callback, pattern="^policy_delete\|"))
    application.add_handler(CallbackQueryHandler(policy_delete_confirm_callback, pattern="^policy_delete_confirm\|"))
    application.add_handler(CallbackQueryHandler(policy_set_account_callback, pattern="^policy_set_account\|"))
    application.add_handler(CallbackQueryHandler(policy_set_zone_callback, pattern="^policy_set_zone\|"))
    application.add_handler(CallbackQueryHandler(policy_add_start_callback, pattern="^policy_add_start$"))
    application.add_handler(CallbackQueryHandler(back_to_settings_main_callback, pattern="^back_to_settings_main$"))
    application.add_handler(CallbackQueryHandler(go_to_settings_callback, pattern="^go_to_settings$"))
    application.add_handler(CallbackQueryHandler(add_retry_name_callback, pattern="^add_retry_name$"))
    application.add_handler(CallbackQueryHandler(settings_policies_callback, pattern="^settings_policies$"))
    application.add_handler(CallbackQueryHandler(settings_notifications_callback, pattern="^settings_notifications$"))
    application.add_handler(CallbackQueryHandler(toggle_notifications_callback, pattern="^toggle_notifications$"))
    
    # Generic Callback Handler
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Message Handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.Document.MimeType("application/json"), handle_document))
    
    logger.info("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()
