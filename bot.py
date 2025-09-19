import os
import json
import httpx
import asyncio
import logging
import socket
import ipaddress
import random
import pickle
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, error
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, PicklePersistence
)
import check_host

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

def get_flag_emoji(country_code: str) -> str:
    """Converts a two-letter country code to its flag emoji."""
    if not country_code or len(country_code) != 2:
        return "🏳️" 
    
    offset = 127397
    return chr(ord(country_code[0].upper()) + offset) + chr(ord(country_code[1].upper()) + offset)

# --- Helper Functions ---
CONFIG_FILE = "config.json"

def escape_markdown_v2(text: str) -> str:
    """Escapes characters for Telegram's MarkdownV2 parser."""
    if not isinstance(text, str):
        text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

def is_valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False
    
def escape_markdown_v2(text: str) -> str:
    """Escapes characters for Telegram's MarkdownV2 parser."""
    if not isinstance(text, str):
        text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

def load_config():
    try:
        if not os.path.exists(CONFIG_FILE):
            logger.info(f"{CONFIG_FILE} not found. Creating a new one with default structure.")
            default_config = {
                "notifications": {"enabled": True, "chat_ids": []},
                "failover_policies": [],
                "load_balancer_policies": []
            }
            save_config(default_config)
            return default_config

        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)

        config_needs_migration = False
        if "load_balancer_policies" not in config:
            config_needs_migration = "load_balancer_policies" not in config or \
                                 any("load_balancer" in p for p in config.get("failover_policies", []))
        
        if config_needs_migration:
            logger.warning("Legacy configuration format detected. Starting automatic migration...")
            
            backup_filename = f"{CONFIG_FILE}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
            with open(backup_filename, 'w', encoding='utf-8') as backup_f:
                json.dump(config, backup_f, indent=2, ensure_ascii=False)
            logger.info(f"Successfully created a backup at: {backup_filename}")

            if "load_balancer_policies" not in config:
                config["load_balancer_policies"] = []

            migrated_lb_policies = []
            failover_policies_copy = config.get("failover_policies", []).copy()
            
            for policy in failover_policies_copy:
                if "load_balancer" in policy:
                    if policy["load_balancer"].get("enabled"):
                        lb_config = policy["load_balancer"]
                        new_lb_policy = {
                            "policy_name": f"{policy.get('policy_name', 'Unnamed')} LB",
                            "ips": lb_config.get("ips", []),
                            "rotation_interval_hours": lb_config.get("rotation_interval_hours", 6),
                            "check_port": policy.get("check_port", 443),
                            "account_nickname": policy.get("account_nickname"),
                            "zone_name": policy.get("zone_name"),
                            "record_names": policy.get("record_names", []),
                            "enabled": True,
                            "monitoring_nodes": policy.get("backup_monitoring_nodes", []),
                            "threshold": policy.get("backup_threshold", 1)
                        }
                        migrated_lb_policies.append(new_lb_policy)
                    
                    del policy["load_balancer"]
            
            config["failover_policies"] = failover_policies_copy
            config["load_balancer_policies"].extend(migrated_lb_policies)
            
            logger.info("Migration complete. Saving new configuration file.")
            save_config(config)
            
        return config

    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.error(f"FATAL: Could not read or decode {CONFIG_FILE}. Error: {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred while loading config: {e}", exc_info=True)
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

def get_current_token(context: ContextTypes.DEFAULT_TYPE):
    account_nickname = context.user_data.get('selected_account_nickname')
    return CF_ACCOUNTS.get(account_nickname)

# --- ADD THE NEW FUNCTION HERE ---
def reset_policy_health_status(context: ContextTypes.DEFAULT_TYPE, policy_name: str):
    """Resets the health status for a specific policy to its default state."""
    if 'health_status' in context.bot_data and policy_name in context.bot_data['health_status']:
        logger.info(f"Resetting health status for policy '{policy_name}' due to configuration change.")
        del context.bot_data['health_status'][policy_name]

async def set_bot_commands(application: Application):
    """Sets the bot's command list for the Telegram UI."""
    commands = [
        BotCommand("start", "▶️ Start Bot / شروع مجدد ربات"),
        BotCommand("language", "🌐 Change Language / تغییر زبان"),
        BotCommand("list", "🗂️ List Domains & Records / لیست دامنه‌ها و رکوردها"),
        BotCommand("search", "🔎 Search for a Record / جستجوی رکورد"),
        BotCommand("bulk", "👥 Bulk Actions on Records / عملیات گروهی روی رکوردها"),
        BotCommand("add", "➕ Add a New Record / افزودن رکورد جدید"),
        BotCommand("backup", "💾 Backup Records / تهیه بکاپ از رکوردها"),
        BotCommand("restore", "🔁 Restore Records / بازیابی رکوردها از بکاپ"),
        BotCommand("settings", "⚙️ Monitoring & Failover Settings / تنظیمات مانیتورینگ")
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Bot commands have been set successfully.")
    except Exception as e:
        logger.error(f"Failed to set bot commands: {e}")

async def policy_force_update_nodes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forces a refresh of the Check-Host.net node list by clearing the cache."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    context.bot_data.pop('all_nodes', None)
    context.bot_data.pop('countries', None)
    context.bot_data.pop('nodes_last_updated', None)
    
    logger.info(f"User {update.effective_user.id} forced a manual refresh of the node list.")
    await query.answer("Node list cache cleared. Refreshing...", show_alert=True)
    
    await display_countries_for_selection(update, context, page=0)

async def update_check_host_nodes_job(context: ContextTypes.DEFAULT_TYPE):
    """A scheduled job to periodically refresh the list of Check-Host.net nodes."""
    logger.info("--- [JOB] Starting scheduled Check-Host.net node list update ---")
    
    nodes_data = await check_host.get_nodes()
    if not nodes_data:
        logger.error("[JOB] Failed to fetch updated node list from Check-Host.net. Will try again on the next run.")
        return
        
    countries = {}
    for node_id, info in nodes_data.items():
        country_code = info.get('location', 'UN').lower()
        country_name = info.get('country', 'Unknown')
        if country_code not in countries:
            countries[country_code] = {'name': country_name, 'nodes': []}
        countries[country_code]['nodes'].append(node_id)
    
    context.bot_data['all_nodes'] = nodes_data
    context.bot_data['countries'] = dict(sorted(countries.items(), key=lambda item: item[1]['name']))
    context.bot_data['nodes_last_updated'] = datetime.now()
    
    logger.info(f"--- [JOB] Successfully updated and cached {len(nodes_data)} Check-Host.net nodes. ---")

async def clear_zone_cache_for_all_users(persistence: PicklePersistence, zone_id: str):
    """Clears the 'all_records' cache for a specific zone_id across all users."""
    logger.info(f"CACHE CLEAR: Clearing cache for zone_id {zone_id} for all users.")
    user_data_copy = (await persistence.get_user_data()).copy()
    
    for chat_id, data in user_data_copy.items():
        if data.get('selected_zone_id') == zone_id:
            user_dirty = False
            if 'all_records' in data:
                del data['all_records']
                user_dirty = True
            if 'records_in_view' in data:
                del data['records_in_view']
                user_dirty = True
            
            if user_dirty:
                await persistence.update_user_data(chat_id, data)
                logger.info(f"CACHE CLEAR: Cleared cache for user {chat_id}.")
                
    logger.info(f"CACHE CLEAR: Finished clearing cache for zone_id {zone_id}.")

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
    """Fetches all DNS records for a given zone ID, with robust pagination handling."""
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    all_records, page = [], 1
    
    while True:
        res = await api_request(token, "get", url, params={'per_page': 100, 'page': page})
        
        if not res.get("success"):
            logger.error(f"API request failed for get_dns_records on zone {zone_id}, page {page}.")
            break
            
        data = res.get("result", [])
        if not data:
            break

        all_records.extend(data)
        
        result_info = res.get('result_info', {})
        total_pages = result_info.get('total_pages', 1)
        current_page = result_info.get('page', 1)
        
        if current_page >= total_pages:
            break
            
        page += 1
        
        if page > 100: 
            logger.warning("get_dns_records exceeded 100 pages, breaking loop to prevent infinite loop.")
            break

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
async def switch_dns_ip(context: ContextTypes.DEFAULT_TYPE, policy: dict, to_ip: str) -> int:
    """
    Switches DNS records for a given policy to the target IP (to_ip).
    It updates any record that is not already pointing to the target IP.
    Returns the number of successful updates.
    """
    policy_name = policy.get('policy_name', 'Unnamed Policy')
    logger.info(f"DNS SWITCH: For policy '{policy_name}', ensuring records point to '{to_ip}'.")
    
    account_nickname = policy.get('account_nickname')
    token = CF_ACCOUNTS.get(account_nickname)
    if not token:
        logger.error(f"DNS SWITCH FAILED for '{policy_name}': No token found for account '{account_nickname}'.")
        return 0

    zones = await get_all_zones(token)
    zone_id = next((z['id'] for z in zones if z['name'] == policy['zone_name']), None)
    if not zone_id:
        logger.error(f"DNS SWITCH FAILED for '{policy_name}': Could not find zone_id for '{policy['zone_name']}'.")
        return 0

    all_records = await get_dns_records(token, zone_id)
    success_count = 0
    records_to_update = policy.get('record_names', [])
    
    for record in all_records:
        short_name = get_short_name(record['name'], policy['zone_name'])
        if short_name in records_to_update and record['content'] != to_ip:
            logger.info(f"DNS SWITCH: Updating record '{record['name']}' from '{record['content']}' to '{to_ip}'...")
            res = await update_record(
                token, zone_id, record['id'], record['type'],
                record['name'], to_ip, record.get('proxied', False)
            )
            if res.get("success"):
                logger.info(f"SUCCESS: Record '{record['name']}' updated.")
                success_count += 1
            else:
                error_msg = res.get('errors', [{}])[0].get('message', 'Unknown error')
                logger.error(f"DNS SWITCH FAILED: Could not update record '{record['name']}'. Reason: {error_msg}")

    if success_count > 0:
        await clear_zone_cache_for_all_users(context.application.persistence, zone_id)

    return success_count

async def check_ip_health(ip: str, port: int, timeout: int = 5) -> bool:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except (socket.gaierror, ConnectionRefusedError, asyncio.TimeoutError, OSError):
        return False

async def send_notification(context: ContextTypes.DEFAULT_TYPE, message_key: str, add_settings_button: bool = False, **kwargs):
    """
    Sends a localized notification message to all configured admin chat IDs,
    with automatic MarkdownV2 escaping for variables.
    """
    config = load_config()
    if config is None or not config.get("notifications", {}).get("enabled", False):
        return

    all_notification_ids = set(config.get("notifications", {}).get("chat_ids", [])) | ADMIN_IDS
    if not all_notification_ids:
        return

    safe_kwargs = {}
    for key, value in kwargs.items():
        if isinstance(value, (str, int, float)):
            safe_kwargs[key] = escape_markdown_v2(str(value))
        else:
            safe_kwargs[key] = value

    user_data = await context.application.persistence.get_user_data()
    
    for chat_id in all_notification_ids:
        lang = user_data.get(chat_id, {}).get('language', 'fa')
        message = get_text(message_key, lang, **safe_kwargs)
        
        reply_markup = None
        if add_settings_button:
            button_text = get_text('buttons.go_to_settings', lang)
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(button_text, callback_data="go_to_settings_from_alert")]])

        try:
            await context.bot.send_message(
                chat_id=chat_id, 
                text=message, 
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Failed to send notification to {chat_id}: {e}", exc_info=True)

async def get_ip_health_with_cache(context: ContextTypes.DEFAULT_TYPE, ip: str, port: int, nodes: list, threshold: int, cache: dict) -> bool:
    """
    Checks IP health using a temporary cache for the current job run.
    This prevents re-checking the same IP within a single job execution.
    Returns a boolean for health status.
    """
    cache_key = (ip, port, tuple(sorted(nodes or [])), threshold)
    
    if cache_key in cache:
        logger.info(f"Using CACHED health status for {ip}:{port}: {'Online' if cache[cache_key] else 'Offline'}")
        return cache[cache_key]

    is_online = False 
    use_advanced_monitoring = all([nodes, threshold])

    if use_advanced_monitoring:
        check_results = await check_host.perform_check(ip, port, nodes)
        if check_results is None:
            context.bot_data.setdefault('check_host_failure_count', 0)
            context.bot_data['check_host_failure_count'] += 1
            if context.bot_data['check_host_failure_count'] >= 3:
                await send_notification(context, 'messages.check_host_api_down_alert', add_settings_button=True)
            logger.warning(f"Could not get check results for {ip}. Assuming it's online to be safe.")
            is_online = True 
        else:
            context.bot_data['check_host_failure_count'] = 0
            failures = sum(1 for result in check_results.values() if not result)
            is_online = failures < threshold
            logger.info(f"IP '{ip}' check complete. Failures: {failures}/{len(nodes)}. Threshold: {threshold}. Online: {is_online}")
    else:
        is_online = await check_ip_health(ip, port)
        logger.info(f"IP '{ip}' simple check complete. Online: {is_online}")

    cache[cache_key] = is_online
    return is_online

async def get_ip_health_with_cache(context: ContextTypes.DEFAULT_TYPE, ip: str, port: int, nodes: list, threshold: int, cache: dict) -> bool:
    """
    Checks IP health using a temporary cache for the current job run.
    This prevents re-checking the same IP within a single job execution.
    Returns a boolean for health status.
    """
    if not ip or not port: return False
    
    cache_key = (ip, port, tuple(sorted(nodes or [])), threshold)
    
    if cache_key in cache:
        logger.info(f"Using CACHED health status for {ip}:{port}: {'Online' if cache[cache_key] else 'Offline'}")
        return cache[cache_key]

    is_online = False 
    
    use_advanced_monitoring = all([nodes, threshold])

    if use_advanced_monitoring:
        check_results = await check_host.perform_check(ip, port, nodes)
        if check_results is None:
            context.bot_data.setdefault('check_host_failure_count', 0)
            context.bot_data['check_host_failure_count'] += 1
            if context.bot_data['check_host_failure_count'] >= 3:
                await send_notification(context, 'messages.check_host_api_down_alert', add_settings_button=True)
            logger.warning(f"Could not get check results for {ip}. Assuming it's online to be safe.")
            is_online = True 
        else:
            context.bot_data['check_host_failure_count'] = 0
            failures = sum(1 for result in check_results.values() if not result)
            is_online = failures < threshold
            logger.info(f"IP '{ip}' check complete. Failures: {failures}/{len(nodes)}. Threshold: {threshold}. Online: {is_online}")
    else: 
        is_online = await check_ip_health(ip, port)
        logger.info(f"IP '{ip}' simple check complete. Online: {is_online}")

    cache[cache_key] = is_online
    return is_online

async def health_check_job(context: ContextTypes.DEFAULT_TYPE):
    if health_check_lock.locked():
        logger.warning("Health check job is already running. Skipping this execution.")
        return
        
    async with health_check_lock:
        try:
            logger.info("--- [HEALTH CHECK] Job Started (Integrated Hybrid Mode v2) ---")
            config = load_config()
            if config is None:
                logger.error("--- [HEALTH CHECK] HALTED: Config file is corrupted.")
                return

            if 'health_status' not in context.bot_data:
                context.bot_data['health_status'] = {}
            status_data = context.bot_data['health_status']
            
            ping_cache = {}
            lb_active_ips_map = {}

            # === STAGE 1: PROCESS LOAD BALANCER POLICIES (MASTER SYSTEM) ===
            logger.info("--- [HEALTH CHECK] Stage 1: Processing Load Balancer Policies ---")
            lb_policies = [p for p in config.get("load_balancer_policies", []) if p.get('enabled', True)]
            
            for policy in lb_policies:
                policy_name = policy.get('policy_name', 'Unnamed LB')
                record_names = policy.get('record_names', [])
                zone_name = policy.get('zone_name')
                
                policy_status = status_data.setdefault(policy_name, {'lb_next_rotation_time': None})
                
                healthy_lb_ips = []
                port_to_check = policy.get('check_port', 443)
                lb_nodes = policy.get('monitoring_nodes')
                lb_threshold = policy.get('threshold')

                for ip in policy.get('ips', []):
                    if await get_ip_health_with_cache(context, ip, port_to_check, lb_nodes, lb_threshold, ping_cache):
                        healthy_lb_ips.append(ip)
                
                if not healthy_lb_ips:
                    logger.warning(f"All IPs for LB policy '{policy_name}' are down! No action taken.")
                    policy_status['lb_next_rotation_time'] = None
                    continue

                actual_ip_on_cf = None
                token = CF_ACCOUNTS.get(policy.get('account_nickname'))
                if token and zone_name and record_names:
                    zones = await get_all_zones(token)
                    zone_id = next((z['id'] for z in zones if z['name'] == zone_name), None)
                    if zone_id:
                        all_dns_records = await get_dns_records(token, zone_id)
                        actual_record = next((r for r in all_dns_records if get_short_name(r['name'], zone_name) == record_names[0]), None)
                        if actual_record: actual_ip_on_cf = actual_record['content']

                if not actual_ip_on_cf:
                    logger.warning(f"Could not determine current IP for LB policy '{policy_name}'. Skipping.")
                    continue

                now = datetime.now()
                time_to_rotate = False
                if policy_status.get('lb_next_rotation_time'):
                    try:
                        next_rotation_time = datetime.fromisoformat(policy_status['lb_next_rotation_time'])
                        if now >= next_rotation_time: time_to_rotate = True
                    except ValueError: time_to_rotate = True
                else: time_to_rotate = True
                
                if actual_ip_on_cf not in healthy_lb_ips:
                    logger.warning(f"Current IP {actual_ip_on_cf} for LB '{policy_name}' is unhealthy. Forcing switch.")
                    time_to_rotate = True

                ip_to_set = actual_ip_on_cf
                if time_to_rotate:
                    logger.info(f"Rotation triggered for LB '{policy_name}'.")
                    try:
                        current_index = healthy_lb_ips.index(actual_ip_on_cf)
                        next_index = (current_index + 1) % len(healthy_lb_ips)
                    except ValueError: next_index = 0
                    
                    next_ip = healthy_lb_ips[next_index]
                    if actual_ip_on_cf != next_ip:
                         await switch_dns_ip(context, policy, to_ip=next_ip)
                         ip_to_set = next_ip
                    
                    min_h = policy.get('rotation_min_hours', 1.0)
                    max_h = policy.get('rotation_max_hours', min_h)
                    random_delay_seconds = random.uniform(min_h * 3600, max_h * 3600)
                    next_rotation_time = now + timedelta(seconds=random_delay_seconds)
                    policy_status['lb_next_rotation_time'] = next_rotation_time.isoformat()
                    logger.info(f"Next rotation for '{policy_name}' is scheduled for {next_rotation_time.strftime('%Y-%m-%d %H:%M:%S')}.")
                
                for rec_name in record_names:
                    lb_active_ips_map[(zone_name, rec_name)] = ip_to_set

            # === STAGE 2: PROCESS FAILOVER POLICIES (SAFETY NET SYSTEM) ===
            logger.info("--- [HEALTH CHECK] Stage 2: Processing Failover Policies ---")
            failover_policies = [p for p in config.get("failover_policies", []) if p.get('enabled', True)]
            
            for policy in failover_policies:
                policy_name = policy.get('policy_name', 'Unnamed')
                record_names = policy.get('record_names', [])
                zone_name = policy.get('zone_name')
                
                record_key = (zone_name, record_names[0]) if record_names else None
                is_hybrid_mode = record_key in lb_active_ips_map

                if is_hybrid_mode:
                    logger.info(f"Policy '{policy_name}' is in HYBRID mode.")
                    effective_primary_ip = lb_active_ips_map[record_key]
                    
                    lb_policy_ref = next((p for p in lb_policies if p.get('zone_name') == zone_name and record_names[0] in p.get('record_names', [])), None)
                    if not lb_policy_ref: continue

                    is_lb_ip_online = await get_ip_health_with_cache(
                        context, effective_primary_ip, lb_policy_ref.get('check_port', 443),
                        lb_policy_ref.get('monitoring_nodes', []), lb_policy_ref.get('threshold', 1), ping_cache
                    )

                    if not is_lb_ip_online:
                        logger.warning(f"HYBRID FAILOVER: Active LB IP '{effective_primary_ip}' for '{policy_name}' is DOWN. Switching to Failover backups.")
                        
                        backup_ips = policy.get('backup_ips', [])
                        backup_nodes = policy.get('backup_monitoring_nodes', [])
                        backup_threshold = policy.get('backup_threshold', 1)
                        port_to_check = policy.get('check_port', 443)
                        next_healthy_backup = None

                        for backup_ip in backup_ips:
                            if await get_ip_health_with_cache(context, backup_ip, port_to_check, backup_nodes, backup_threshold, ping_cache):
                                next_healthy_backup = backup_ip
                                break
                        
                        if next_healthy_backup:
                            await switch_dns_ip(context, policy, to_ip=next_healthy_backup)
                            await send_notification(context, 'messages.failover_notification_message', policy_name=policy_name, from_ip=effective_primary_ip, to_ip=next_healthy_backup, add_settings_button=True)

                else:
                    primary_ip = policy.get('primary_ip')
                    backup_ips = policy.get('backup_ips', [])
                    port_to_check = policy.get('check_port', 443)
                    
                    if not all([primary_ip, backup_ips, record_names, zone_name]): continue

                    policy_status = status_data.setdefault(policy_name, {'critical_alert_sent': False, 'uptime_start': None, 'downtime_start': None})

                    actual_ip_on_cf = None
                    token = CF_ACCOUNTS.get(policy.get('account_nickname'))
                    if token:
                        zones = await get_all_zones(token)
                        zone_id = next((z['id'] for z in zones if z['name'] == zone_name), None)
                        if zone_id:
                            all_dns_records = await get_dns_records(token, zone_id)
                            actual_record = next((r for r in all_dns_records if get_short_name(r['name'], zone_name) == record_names[0]), None)
                            if actual_record: actual_ip_on_cf = actual_record['content']
                    
                    if not actual_ip_on_cf: continue

                    primary_nodes = policy.get('primary_monitoring_nodes')
                    primary_threshold = policy.get('primary_threshold')
                    backup_nodes = policy.get('backup_monitoring_nodes', primary_nodes)
                    backup_threshold = policy.get('backup_threshold', primary_threshold)
                    
                    nodes_to_use = primary_nodes if actual_ip_on_cf == primary_ip else backup_nodes
                    threshold_to_use = primary_threshold if actual_ip_on_cf == primary_ip else backup_threshold

                    is_current_ip_online = await get_ip_health_with_cache(context, actual_ip_on_cf, port_to_check, nodes_to_use, threshold_to_use, ping_cache)

                    if not is_current_ip_online:
                        if not policy_status.get('downtime_start'):
                            policy_status['downtime_start'] = datetime.now().isoformat()
                            await send_notification(context, 'messages.server_alert_notification', policy_name=policy_name, ip=actual_ip_on_cf, add_settings_button=True)
                            continue
                        
                        downtime_dt = datetime.fromisoformat(policy_status['downtime_start'])
                        failover_minutes = policy.get("failover_minutes", 2.0)
                        
                        if (datetime.now() - downtime_dt) < timedelta(minutes=failover_minutes):
                            logger.info(f"'{policy_name}' is still within its grace period. Waiting...")
                            continue

                        logger.warning(f"FAILOVER TRIGGERED for '{policy_name}' on IP '{actual_ip_on_cf}'. Searching for backup.")
                        next_healthy_backup = None
                        for backup_ip in backup_ips:
                            if backup_ip == actual_ip_on_cf: continue
                            if await get_ip_health_with_cache(context, backup_ip, port_to_check, backup_nodes, backup_threshold, ping_cache):
                                next_healthy_backup = backup_ip; break
                        
                        if next_healthy_backup:
                            await switch_dns_ip(context, policy, to_ip=next_healthy_backup)
                            await send_notification(context, 'messages.failover_notification_message', policy_name=policy_name, from_ip=actual_ip_on_cf, to_ip=next_healthy_backup, add_settings_button=True)
                            policy_status['downtime_start'] = None
                        elif not policy_status.get('critical_alert_sent', False):
                            await send_notification(context, 'messages.failover_notification_all_down', policy_name=policy_name, primary_ip=primary_ip, backup_ips=", ".join(backup_ips), add_settings_button=True)
                            policy_status['critical_alert_sent'] = True
                    else:
                        if policy_status.get('downtime_start'):
                            await send_notification(context, 'messages.server_recovered_notification', policy_name=policy_name, ip=actual_ip_on_cf)
                        policy_status['downtime_start'] = None
                        if policy_status.get('critical_alert_sent'): policy_status['critical_alert_sent'] = False

                    is_primary_online = await get_ip_health_with_cache(context, primary_ip, port_to_check, primary_nodes, primary_threshold, ping_cache)
                    if is_primary_online and actual_ip_on_cf != primary_ip:
                        if not policy_status.get('uptime_start'):
                            policy_status['uptime_start'] = datetime.now().isoformat()
                            if policy.get('auto_failback', True):
                                failback_minutes = policy.get('failback_minutes', 5.0)
                                await send_notification(context, 'messages.failback_alert_notification', policy_name=policy_name, primary_ip=primary_ip, failback_minutes=failback_minutes)
                        
                        elif policy.get('auto_failback', True):
                            uptime_dt = datetime.fromisoformat(policy_status['uptime_start'])
                            failback_minutes = policy.get('failback_minutes', 5.0)
                            if (datetime.now() - uptime_dt) >= timedelta(minutes=failback_minutes):
                                await switch_dns_ip(context, policy, to_ip=primary_ip)
                                await send_notification(context, 'messages.failback_executed_notification', policy_name=policy_name, primary_ip=primary_ip, add_settings_button=True)
                                policy_status.pop('uptime_start', None)
                    elif not is_primary_online:
                         policy_status.pop('uptime_start', None)

            await context.application.persistence.flush()
            logger.info("--- [HEALTH CHECK] Job Finished ---")
        except Exception as e:
            logger.error(f"!!! [HEALTH CHECK] An unhandled exception in health_check_job !!!", exc_info=True)

# --- State Management ---
def clear_state(context: ContextTypes.DEFAULT_TYPE, preserve=None):
    if preserve is None: preserve = []
    if 'language' not in preserve: preserve.append('language')
    preserved_data = {key: context.user_data[key] for key in preserve if key in context.user_data}
    context.user_data.clear()
    context.user_data.update(preserved_data)

# --- Display Logic ---
async def display_records_for_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Displays a paginated list of A/AAAA records for policy selection with robust error handling."""
    query = update.callback_query
    lang = get_user_lang(context)
    
    is_editing = context.user_data.get('is_editing_policy_records', False)
    policy_type = context.user_data.get('editing_policy_type') if is_editing else context.user_data.get('add_policy_type')
    
    try:
        if is_editing:
            policy_index = context.user_data['edit_policy_index']
            config = load_config()
            policy_list_key = 'load_balancer_policies' if policy_type == 'lb' else 'failover_policies'
            policy = config[policy_list_key][policy_index]
            account_nickname, zone_name = policy['account_nickname'], policy['zone_name']
        else:
            account_nickname = context.user_data['new_policy_data']['account_nickname']
            zone_name = context.user_data['new_policy_data']['zone_name']
    except (KeyError, IndexError, TypeError):
        logger.error("Failed to retrieve policy data in display_records_for_selection context.", exc_info=True)
        if query: await query.edit_message_text(get_text('messages.session_expired_error', lang))
        return

    if 'policy_all_records' not in context.user_data or context.user_data.get('current_selection_zone') != zone_name:
        if query: await query.edit_message_text(get_text('messages.fetching_records', lang))
        token = CF_ACCOUNTS.get(account_nickname)
        if not token:
            if query: await query.edit_message_text(get_text('messages.error_no_token', lang)); return

        zones = await get_all_zones(token)
        zone_id = next((z['id'] for z in zones if z['name'] == zone_name), None)
        if not zone_id:
            if query: await query.edit_message_text(get_text('messages.error_no_zone_id', lang)); return
            
        all_records_raw = await get_dns_records(token, zone_id)
        
        context.user_data['policy_all_records'] = [r for r in all_records_raw if r['type'] in ['A', 'AAAA']]
        context.user_data['current_selection_zone'] = zone_name

    all_records = context.user_data.get('policy_all_records', [])
    selected_records = context.user_data.get('policy_selected_records', [])
    
    if not all_records:
        if query: await query.edit_message_text(f"No A or AAAA records found in zone '{zone_name}'. Cannot create/edit policy.")
        return

    start_index = page * RECORDS_PER_PAGE
    end_index = start_index + RECORDS_PER_PAGE
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
    if pagination_buttons: buttons.append(pagination_buttons)

    buttons.append([InlineKeyboardButton(get_text('buttons.confirm_selection', lang, count=len(selected_records)), callback_data="policy_confirm_records")])
    
    try:
        if query:
            await query.edit_message_text(get_text('prompts.select_records_for_policy', lang), reply_markup=InlineKeyboardMarkup(buttons))
    except telegram_error.BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error("A BadRequest error occurred in display_records_for_selection", exc_info=True)

# --- Node Selection Display Logic ---
NODES_PER_PAGE = 12

async def display_countries_for_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """
    Displays a paginated list of countries.
    It intelligently fetches/refreshes the node list from cache or API.
    """
    query = update.callback_query
    lang = get_user_lang(context)
    
    now = datetime.now()
    nodes_last_updated = context.bot_data.get('nodes_last_updated')
    should_refresh_nodes = 'all_nodes' not in context.bot_data or \
                           not nodes_last_updated or \
                           (now - nodes_last_updated) > timedelta(hours=24)

    if should_refresh_nodes:
        if query: await query.edit_message_text(get_text('messages.fetching_locations_message', lang))
        logger.info("Node list is stale or missing. Fetching latest nodes...")
        
        nodes_data = await check_host.get_nodes()
        
        if not nodes_data:
            if query: await query.edit_message_text(get_text('messages.fetching_locations_error', lang))
            return
        
        countries = {}
        for node_id, info in nodes_data.items():
            country_code = info.get('location', 'UN').lower()
            country_name = info.get('country', 'Unknown')
            if country_code not in countries:
                countries[country_code] = {'name': country_name, 'nodes': []}
            countries[country_code]['nodes'].append(node_id)
        
        context.bot_data['all_nodes'] = nodes_data
        context.bot_data['countries'] = dict(sorted(countries.items(), key=lambda item: item[1]['name']))
        context.bot_data['nodes_last_updated'] = now
        logger.info(f"Successfully fetched and cached {len(nodes_data)} nodes.")

    countries = context.bot_data.get('countries', {})
    country_codes = list(countries.keys())
    
    start_index = page * NODES_PER_PAGE
    end_index = start_index + NODES_PER_PAGE
    page_countries = country_codes[start_index:end_index]

    buttons = []
    
    selected_nodes = context.user_data.get('policy_selected_nodes', [])
    header_text = "Selected Nodes:\n"
    if selected_nodes:
        selected_cities = []
        for node_id in sorted(selected_nodes)[:10]:
            node_info = context.bot_data.get('all_nodes', {}).get(node_id, {})
            city = node_info.get('city', 'Unknown')
            country = node_info.get('country', 'Unknown')
            selected_cities.append(f"`{city} ({country})`")
        header_text += ", ".join(selected_cities)
        if len(selected_nodes) > 10:
            header_text += f" ...and {len(selected_nodes) - 10} more."
    else:
        header_text += "_None_"
    
    message_text = f"{header_text}\n\n" + get_text('messages.select_country_message', lang)

    action_buttons = [
        InlineKeyboardButton(get_text('buttons.select_all_nodes', lang), callback_data="policy_nodes_select_all_global"),
        InlineKeyboardButton(get_text('buttons.clear_all_nodes', lang), callback_data="policy_nodes_clear_all_global")
    ]
    buttons.append(action_buttons)
    buttons.append([InlineKeyboardButton(get_text('buttons.force_update_nodes', lang), callback_data="policy_force_update_nodes")])

    row = []
    for code in page_countries:
        country_info = countries[code]
        flag = get_flag_emoji(code)
        country_name = f"{flag} {country_info['name']}"
        row.append(InlineKeyboardButton(country_name, callback_data=f"policy_select_country|{code}|0"))
        if len(row) == 2: buttons.append(row); row = []
    if row: buttons.append(row)

    pagination_buttons = []
    if page > 0:
        pagination_buttons.append(InlineKeyboardButton(get_text('buttons.back_button', lang), callback_data=f"policy_country_page|{page - 1}"))
    if end_index < len(country_codes):
        pagination_buttons.append(InlineKeyboardButton(get_text('buttons.next_button', lang), callback_data=f"policy_country_page|{page + 1}"))
    if pagination_buttons: buttons.append(pagination_buttons)

    buttons.append([InlineKeyboardButton(get_text('buttons.confirm_selection_button', lang, count=len(selected_nodes)), callback_data="policy_confirm_nodes")])
    
    policy_type = context.user_data.get('editing_policy_type')
    policy_index = context.user_data.get('edit_policy_index')

    if not all([policy_type, policy_index is not None]):
        if query: await query.edit_message_text(get_text('messages.session_expired_error', lang)); return

    back_button_callback = f"lb_policy_edit|{policy_index}" if policy_type == 'lb' else f"failover_policy_edit|{policy_index}"
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_edit_menu_button', lang), callback_data=back_button_callback)])
    
    try:
        if query:
            await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    except telegram_error.BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error in display_countries_for_selection: {e}")

NODES_PER_PAGE = 12

async def display_nodes_for_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, country_code: str, page: int = 0):
    """Displays a paginated list of nodes for a selected country with corrected callbacks."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    countries = context.bot_data.get('countries', {})
    country_info = countries.get(country_code)
    if not country_info:
        await query.edit_message_text(get_text('messages.internal_error', lang)); return

    all_node_ids_in_country = sorted(country_info['nodes'])
    selected_nodes = context.user_data.get('policy_selected_nodes', [])
    
    start_index = page * NODES_PER_PAGE
    end_index = start_index + NODES_PER_PAGE
    page_nodes = all_node_ids_in_country[start_index:end_index]

    buttons = []
    for node_id in page_nodes:
        check_icon = "✅" if node_id in selected_nodes else "▫️"
        city = context.bot_data['all_nodes'][node_id].get('city', 'Unknown')
        button_text = f"{check_icon} {city}"
        buttons.append([InlineKeyboardButton(button_text, callback_data=f"policy_toggle_node|{country_code}|{page}|{node_id}")])

    pagination_buttons = []
    if page > 0:
        pagination_buttons.append(InlineKeyboardButton(get_text('buttons.back_button', lang), callback_data=f"policy_nodes_page|{country_code}|{page - 1}"))
    if end_index < len(all_node_ids_in_country):
        pagination_buttons.append(InlineKeyboardButton(get_text('buttons.next_button', lang), callback_data=f"policy_nodes_page|{country_code}|{page + 1}"))
    if pagination_buttons:
        buttons.append(pagination_buttons)

    buttons.append([InlineKeyboardButton(get_text('buttons.confirm_selection_button', lang, count=len(selected_nodes)), callback_data="policy_confirm_nodes")])
    
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_countries_button', lang), callback_data="policy_country_page|0")])

    header_text = "Selected Nodes:\n"
    if selected_nodes:
        selected_cities = []
        for node_id in sorted(selected_nodes)[:10]:
            node_info = context.bot_data.get('all_nodes', {}).get(node_id, {})
            city = node_info.get('city', 'Unknown')
            country = node_info.get('country', 'Unknown')
            selected_cities.append(f"`{city} ({country})`")
        header_text += ", ".join(selected_cities)
        if len(selected_nodes) > 10:
            header_text += f" ...and {len(selected_nodes) - 10} more."
    else:
        header_text += "_None_"
    
    message_text = f"{header_text}\n\n" + get_text('messages.select_nodes_message', lang, country_name=country_info['name'])

    try:
        await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    except telegram_error.BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error in display_nodes_for_selection: {e}")

async def display_account_list(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
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
    query = update.callback_query
    lang = get_user_lang(context)
    token = get_current_token(context)
    zone_id = context.user_data.get('selected_zone_id')
    zone_name = context.user_data.get('selected_zone_name')

    if not token or not zone_id:
        await list_records_command(update, context)
        return
    
    context.user_data['current_page'] = page

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
async def policy_nodes_select_all_global_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Selects ALL available nodes from ALL countries and refreshes the view."""
    query = update.callback_query
    await query.answer()
    
    if 'all_nodes' not in context.bot_data:
        await query.edit_message_text(get_text('messages.fetching_locations_message', get_user_lang(context)))
        await display_countries_for_selection(update, context, page=0)
        return

    all_nodes = context.bot_data.get('all_nodes', {})
    all_node_ids = list(all_nodes.keys())
    context.user_data['policy_selected_nodes'] = all_node_ids
    
    await display_countries_for_selection(update, context, page=0)

async def policy_nodes_clear_all_global_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clears ALL selected nodes and refreshes the view."""
    query = update.callback_query
    await query.answer()
    
    context.user_data['policy_selected_nodes'] = []
    
    await display_countries_for_selection(update, context, page=0)

async def policy_nodes_select_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Selects all nodes for the current country view."""
    query = update.callback_query
    _, country_code, page_str = query.data.split('|')
    
    country_info = context.bot_data['countries'][country_code]
    all_node_ids_in_country = set(country_info['nodes'])
    
    selected_nodes = set(context.user_data.get('policy_selected_nodes', []))
    selected_nodes.update(all_node_ids_in_country)
    context.user_data['policy_selected_nodes'] = list(selected_nodes)
    
    await display_nodes_for_selection(update, context, country_code, page=int(page_str))

async def policy_nodes_clear_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clears all selected nodes for the current country view."""
    query = update.callback_query
    _, country_code, page_str = query.data.split('|')

    country_info = context.bot_data['countries'][country_code]
    all_node_ids_in_country = set(country_info['nodes'])

    selected_nodes = set(context.user_data.get('policy_selected_nodes', []))
    selected_nodes.difference_update(all_node_ids_in_country)
    context.user_data['policy_selected_nodes'] = list(selected_nodes)

    await display_nodes_for_selection(update, context, country_code, page=int(page_str))

async def go_to_settings_from_alert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'Go to Settings' button from an alert message, sending the menu as a new message."""
    query = update.callback_query
    
    try:
        if query:
            await query.answer()
    except telegram_error.BadRequest as e:
        if "Query is too old" in str(e):
            logger.info("Ignoring 'Query is too old' error from an alert button.")
            pass
        else:
            logger.error("A BadRequest error occurred in go_to_settings_from_alert_callback", exc_info=True)
    
    await show_settings_menu(update, context, force_new_message=True)

async def policy_edit_nodes_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the node selection process for ANY policy type."""
    query = update.callback_query
    await query.answer()
    
    policy_type = context.user_data.get('editing_policy_type')
    policy_index = context.user_data.get('edit_policy_index')

    if not all([policy_type, policy_index is not None]):
        await query.edit_message_text("Error: Session expired. Please start over."); return
    
    monitoring_type = query.data.split('|')[1]
    context.user_data['monitoring_type'] = monitoring_type
    
    config = load_config()
    
    if policy_type == 'lb':
        policy = config['load_balancer_policies'][policy_index]
        context.user_data['policy_selected_nodes'] = policy.get('monitoring_nodes', [])
    else:
        policy = config['failover_policies'][policy_index]
        if monitoring_type == 'primary':
            context.user_data['policy_selected_nodes'] = policy.get('primary_monitoring_nodes', [])
        else:
            context.user_data['policy_selected_nodes'] = policy.get('backup_monitoring_nodes', [])
    
    await display_countries_for_selection(update, context, page=0)

async def policy_country_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles pagination for the country list."""
    query = update.callback_query
    page = int(query.data.split('|')[1])
    await display_countries_for_selection(update, context, page=page)

async def policy_select_country_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles country selection and shows nodes for it."""
    query = update.callback_query
    _, country_code, page_str = query.data.split('|')
    await display_nodes_for_selection(update, context, country_code, page=int(page_str))

async def policy_nodes_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles pagination for the node list."""
    query = update.callback_query
    _, country_code, page_str = query.data.split('|')
    await display_nodes_for_selection(update, context, country_code, page=int(page_str))

async def policy_toggle_node_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles the selection of a monitoring node."""
    query = update.callback_query
    _, country_code, page_str, node_id = query.data.split('|')
    
    selected_nodes = context.user_data.get('policy_selected_nodes', [])
    if node_id in selected_nodes:
        selected_nodes.remove(node_id)
    else:
        selected_nodes.append(node_id)
    context.user_data['policy_selected_nodes'] = selected_nodes
    
    await display_nodes_for_selection(update, context, country_code, page=int(page_str))

async def policy_confirm_nodes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = get_user_lang(context)
    await query.answer()

    selected_nodes = context.user_data.get('policy_selected_nodes', [])
    policy_type = context.user_data.get('editing_policy_type')
    policy_index = context.user_data.get('edit_policy_index')
    monitoring_type = context.user_data.get('monitoring_type')

    if not all([policy_type, policy_index is not None, monitoring_type]):
        await query.edit_message_text("Error: Session expired. Please start over."); return

    config = load_config()
    
    if policy_type == 'lb':
        config['load_balancer_policies'][policy_index]['monitoring_nodes'] = selected_nodes
    else:
        if monitoring_type == 'primary':
            config['failover_policies'][policy_index]['primary_monitoring_nodes'] = selected_nodes
        else:
            config['failover_policies'][policy_index]['backup_monitoring_nodes'] = selected_nodes
    
    if not selected_nodes:
        if policy_type == 'lb':
            config['load_balancer_policies'][policy_index].pop('threshold', None)
        elif monitoring_type == 'primary':
            config['failover_policies'][policy_index].pop('primary_threshold', None)
        else:
            config['failover_policies'][policy_index].pop('backup_threshold', None)
        
        save_config(config)
        await query.answer("All monitoring nodes for this group have been cleared.", show_alert=True)
        
        view_callback = lb_policy_edit_callback if policy_type == 'lb' else failover_policy_edit_callback
        await view_callback(update, context)
        return

    save_config(config)
    context.user_data['awaiting_threshold'] = True
    
    type_text = get_text(f'messages.monitoring_type_{monitoring_type}', lang, default=monitoring_type)
    await query.edit_message_text(get_text('messages.nodes_updated_message', lang, monitoring_type=type_text, count=len(selected_nodes)))

async def remove_notification_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    context.user_data['awaiting_notification_admin_id'] = True
    
    await query.edit_message_text(get_text('prompts.enter_admin_id_to_add', lang))

async def policy_records_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split('|')[1])
    await display_records_for_selection(update, context, page=page)

async def policy_select_record_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    is_editing = context.user_data.get('is_editing_policy_records', False)
    
    if is_editing:
        policy_type = context.user_data.get('editing_policy_type')
        policy_index = context.user_data.get('edit_policy_index')

        if not all([policy_type, policy_index is not None]):
            await query.edit_message_text("Error: Session expired. Please start over."); return

        config = load_config()
        policy_list_key = 'load_balancer_policies' if policy_type == 'lb' else 'failover_policies'
        
        config[policy_list_key][policy_index]['record_names'] = selected_records
        save_config(config)
        
        await query.edit_message_text(f"✅ Record names for {policy_type.upper()} policy updated successfully!")
        await asyncio.sleep(1)

        for key in ['is_editing_policy_records', 'policy_all_records', 'policy_selected_records', 'current_selection_zone']:
            context.user_data.pop(key, None)

        view_callback = lb_policy_view_callback if policy_type == 'lb' else failover_policy_view_callback
        await view_callback(update, context)

    else:
        if not selected_records:
            await query.answer("Please select at least one record.", show_alert=True); return

        policy_type = context.user_data.get('add_policy_type')
        data = context.user_data['new_policy_data']
        data['record_names'] = selected_records
        
        for key in ['policy_all_records', 'policy_selected_records', 'current_selection_zone']:
            context.user_data.pop(key, None)

        if policy_type == 'lb':
            config = load_config()
            config['load_balancer_policies'].append(data)
            save_config(config)
            
            policy_name = data.get('policy_name', '')
            for key in ['add_policy_step', 'new_policy_data', 'add_policy_type']: context.user_data.pop(key, None)
            
            await query.edit_message_text(get_text('messages.policy_added_successfully', lang, name=policy_name))
            await settings_lb_policies_callback(update, context)

        elif policy_type == 'failover':
            context.user_data['add_policy_step'] = 'auto_failback'
            buttons = [[
                InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="policy_set_failback|true"),
                InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="policy_set_failback|false")
            ]]
            await query.edit_message_text(get_text('prompts.ask_auto_failback', lang), reply_markup=InlineKeyboardMarkup(buttons))

async def policy_set_failback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await settings_failover_policies_callback(update, context)

async def failover_policy_toggle_failback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        
        await failover_policy_view_callback(update, context)

    except (IndexError, ValueError):
        await query.edit_message_text(get_text('messages.error_generic_request', lang))

async def failover_policy_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        
        await failover_policy_view_callback(update, context)

    except (IndexError, ValueError):
        await query.edit_message_text(get_text('messages.error_generic_request', lang))

async def sync_now_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Runs the health check job once to update the status, and then runs the sync
    function on demand from a button press. It handles the callback query correctly
    to avoid timeouts and shows the final policy list.
    """
    query = update.callback_query
    await query.answer(text="Starting health check and sync...", show_alert=False)
    
    try:
        await query.edit_message_text("⏳ Step 1/2: Running an immediate health check...")
        await health_check_job(context)

        await query.edit_message_text("⏳ Step 2/2: Syncing DNS records with the latest status...")
        await sync_dns_with_config(context)
        
        await query.edit_message_text("✅ Sync complete!")
        await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"An error occurred during sync_now_callback: {e}", exc_info=True)
        try:
            await query.edit_message_text(f"❌ An error occurred during the process.")
        except Exception:
            pass
        return

    await settings_failover_policies_callback(update, context)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lang = get_user_lang(context)
    text = update.message.text.strip()

    # --- STATE 1: Awaiting a specific input ---
    if context.user_data.get('awaiting_notification_admin_id'):
        context.user_data.pop('awaiting_notification_admin_id')
        if not text.isdigit():
            await update.message.reply_text(get_text('messages.invalid_id_numeric', lang))
        else:
            admin_id = int(text)
            config = load_config()
            config.setdefault("notifications", {"enabled": True, "chat_ids": []})['chat_ids'].append(admin_id)
            save_config(config)
            await update.message.reply_text(get_text('messages.admin_id_added', lang, admin_id=admin_id))
        await show_settings_notifications_menu(update, context)
        return

    elif context.user_data.get('awaiting_threshold'):
        if not text.isdigit() or int(text) < 1:
            await update.message.reply_text(get_text('messages.threshold_invalid_message', lang)); return
        threshold = int(text)
        
        selected_nodes_count = len(context.user_data.get('policy_selected_nodes', []))
        if threshold > selected_nodes_count:
            await update.message.reply_text(get_text('messages.threshold_too_high_message', lang, threshold=threshold, count=selected_nodes_count)); return
        
        policy_type = context.user_data.get('editing_policy_type')
        policy_index = context.user_data.get('edit_policy_index')
        monitoring_type = context.user_data.get('monitoring_type')
        
        if not all([policy_type, policy_index is not None, monitoring_type]):
             await update.message.reply_text(get_text('messages.session_expired_error', lang)); return

        config = load_config()
        selected_nodes = context.user_data.get('policy_selected_nodes', [])
        
        if policy_type == 'lb':
            config['load_balancer_policies'][policy_index]['monitoring_nodes'] = selected_nodes
            config['load_balancer_policies'][policy_index]['threshold'] = threshold
        else:
            if monitoring_type == 'primary':
                config['failover_policies'][policy_index]['primary_monitoring_nodes'] = selected_nodes
                config['failover_policies'][policy_index]['primary_threshold'] = threshold
            else:
                config['failover_policies'][policy_index]['backup_monitoring_nodes'] = selected_nodes
                config['failover_policies'][policy_index]['backup_threshold'] = threshold
        save_config(config)
        
        for key in ['awaiting_threshold', 'monitoring_type', 'policy_selected_nodes']: context.user_data.pop(key, None)
        
        if policy_type == 'failover' and monitoring_type == 'primary':
            buttons = [
                [InlineKeyboardButton(get_text('buttons.copy_settings_yes', lang), callback_data=f"copy_monitoring_confirm|{policy_index}")],
                [InlineKeyboardButton(get_text('buttons.copy_settings_no', lang), callback_data=f"failover_policy_edit|{policy_index}")]
            ]
            await update.message.reply_text(get_text('prompts.ask_copy_monitoring_settings', lang), reply_markup=InlineKeyboardMarkup(buttons))
        
        else:
            type_text = get_text(f'messages.monitoring_type_{monitoring_type}', lang, default=monitoring_type)
            await update.message.reply_text(get_text('messages.threshold_updated_message', lang, monitoring_type=type_text, threshold=threshold))
            await asyncio.sleep(1)
            
            edit_callback = lb_policy_edit_callback if policy_type == 'lb' else failover_policy_edit_callback
            await edit_callback(update, context, force_new_message=True)
        return

    # --- STATE 2: Editing a policy field ---
    elif 'edit_policy_field' in context.user_data:
        field = context.user_data.pop('edit_policy_field')
        policy_type = context.user_data.get('editing_policy_type')
        policy_index = context.user_data.get('edit_policy_index')

        if policy_index is None or not policy_type:
            await update.message.reply_text(get_text('messages.session_expired_error', lang)); return

        if field == 'rotation_interval_range':
            parts = [p.strip() for p in text.split(',') if p.strip()]
            try:
                if len(parts) == 1 and float(parts[0]) > 0:
                    min_h = max_h = float(parts[0])
                elif len(parts) == 2 and float(parts[0]) > 0 and float(parts[1]) > 0 and float(parts[1]) >= float(parts[0]):
                    min_h = float(parts[0])
                    max_h = float(parts[1])
                else:
                    raise ValueError("Invalid format")
            except (ValueError, IndexError):
                context.user_data['edit_policy_field'] = 'rotation_interval_range'
                await update.message.reply_text(get_text('messages.invalid_number_range', lang, default="Invalid format. Please enter a single number (e.g., 2) or a range (e.g., 1,3).")); return
            
            config = load_config()
            policy = config['load_balancer_policies'][policy_index]
            policy['rotation_min_hours'] = min_h
            policy['rotation_max_hours'] = max_h
            policy.pop('rotation_interval_hours', None)
            save_config(config)
            
            field_name = get_text('field_names.rotation_interval_hours', lang)
            await update.message.reply_text(get_text('messages.policy_field_updated', lang, field=field_name))
            await lb_policy_view_callback(update, context, force_new_message=True)
            return
        
        value_to_save = None
        if field == 'primary_ip':
            if not is_valid_ip(text): await update.message.reply_text(get_text('messages.invalid_ip', lang)); return
            value_to_save = text
        elif field in ['ips', 'backup_ips']:
            ips = [ip.strip() for ip in text.split(',') if ip.strip()]
            if not ips or not all(is_valid_ip(ip) for ip in ips): await update.message.reply_text(get_text('messages.invalid_ip', lang)); return
            value_to_save = ips
        elif field == 'check_port':
            if not text.isdigit() or not (1 <= int(text) <= 65535): await update.message.reply_text(get_text('messages.invalid_port', lang)); return
            value_to_save = int(text)
        elif field in ['failover_minutes', 'failback_minutes']:
             if not text.replace('.', '', 1).isdigit() or float(text) <= 0: await update.message.reply_text(get_text('messages.invalid_number', lang)); return
             value_to_save = float(text)
        else:
            value_to_save = text

        config = load_config()
        policy_list_key = 'load_balancer_policies' if policy_type == 'lb' else 'failover_policies'
        
        policy_name_to_reset = config[policy_list_key][policy_index].get('policy_name')
        if field in ['primary_ip', 'ips', 'policy_name']:
            reset_policy_health_status(context, policy_name_to_reset)
        
        config[policy_list_key][policy_index][field] = value_to_save
        save_config(config)

        field_name = get_text(f'field_names.{field}', lang, default=field)
        await update.message.reply_text(get_text('messages.policy_field_updated', lang, field=field_name))
        
        if policy_type == 'lb':
            await lb_policy_view_callback(update, context, force_new_message=True)
        else:
            await failover_policy_view_callback(update, context, force_new_message=True)
        return

    # --- STATE 3: Adding a new policy ---
    elif 'add_policy_step' in context.user_data:
        step = context.user_data['add_policy_step']
        data = context.user_data['new_policy_data']
        policy_type = context.user_data.get('add_policy_type')

        if policy_type == 'failover':
            if step == 'name':
                data['policy_name'] = text
                context.user_data['add_policy_step'] = 'primary_ip'
                await update.message.reply_text(get_text('prompts.enter_primary_ip', lang))
            elif step == 'primary_ip':
                if not is_valid_ip(text):
                    await update.message.reply_text(get_text('messages.invalid_ip', lang)); return
                data['primary_ip'] = text
                context.user_data['add_policy_step'] = 'backup_ips'
                await update.message.reply_text(get_text('prompts.enter_backup_ip', lang))
            elif step == 'backup_ips':
                ips = [ip.strip() for ip in text.split(',') if ip.strip()]
                if not ips or not all(is_valid_ip(ip) for ip in ips):
                    await update.message.reply_text(get_text('messages.invalid_ip', lang)); return
                data['backup_ips'] = ips
                context.user_data['add_policy_step'] = 'check_port'
                await update.message.reply_text(get_text('prompts.enter_check_port', lang))
            elif step == 'check_port':
                if not text.isdigit() or not (1 <= int(text) <= 65535):
                    await update.message.reply_text(get_text('messages.invalid_port', lang)); return
                data['check_port'] = int(text)
                context.user_data['add_policy_step'] = 'failover_minutes'
                await update.message.reply_text(get_text('prompts.enter_failover_minutes', lang))
            elif step == 'failover_minutes':
                if not text.replace('.', '', 1).isdigit() or float(text) <= 0:
                    await update.message.reply_text(get_text('messages.invalid_number', lang)); return
                data['failover_minutes'] = float(text)
                context.user_data['add_policy_step'] = 'account_nickname'
                buttons = [[InlineKeyboardButton(n, callback_data=f"failover_policy_set_account|{n}")] for n in CF_ACCOUNTS.keys()]
                await update.message.reply_text(get_text('prompts.choose_cf_account', lang), reply_markup=InlineKeyboardMarkup(buttons))
            elif step == 'failback_minutes':
                if not text.replace('.', '', 1).isdigit() or float(text) <= 0:
                    await update.message.reply_text(get_text('messages.invalid_number', lang)); return
                data['failback_minutes'] = float(text)
                config = load_config()
                config['failover_policies'].append(data)
                save_config(config)
                for key in list(context.user_data.keys()):
                    if key != 'language':
                        context.user_data.pop(key)
                await update.message.reply_text(get_text('messages.policy_added_successfully', lang, name=data['policy_name']))
                await settings_failover_policies_callback(update, context)

        elif policy_type == 'lb':
            if step == 'name':
                data['policy_name'] = text
                context.user_data['add_policy_step'] = 'ips'
                await update.message.reply_text(get_text('prompts.enter_lb_ips', lang))
            elif step == 'ips':
                ips = [ip.strip() for ip in text.split(',') if ip.strip()]
                if not ips or not all(is_valid_ip(ip) for ip in ips): await update.message.reply_text(get_text('messages.invalid_ip', lang)); return
                data['ips'] = ips
                context.user_data['add_policy_step'] = 'rotation_interval_hours'
                await update.message.reply_text(get_text('prompts.enter_lb_interval', lang))
            elif step == 'rotation_interval_hours':
                parts = [p.strip() for p in text.split(',')]
                try:
                    if len(parts) == 1 and float(parts[0]) > 0:
                        data['rotation_min_hours'] = float(parts[0])
                        data['rotation_max_hours'] = float(parts[0])
                    elif len(parts) == 2 and float(parts[0]) > 0 and float(parts[1]) > 0 and float(parts[1]) >= float(parts[0]):
                        data['rotation_min_hours'] = float(parts[0])
                        data['rotation_max_hours'] = float(parts[1])
                    else:
                        raise ValueError("Invalid format")
                except (ValueError, IndexError):
                    await update.message.reply_text("فرمت نامعتبر. لطفاً یک عدد (مثلاً 2) یا یک بازه (مثلاً 1,3) وارد کنید."); return

                context.user_data['add_policy_step'] = 'check_port'
                await update.message.reply_text(get_text('prompts.enter_check_port', lang))
            elif step == 'check_port':
                if not text.isdigit() or not (1 <= int(text) <= 65535): await update.message.reply_text(get_text('messages.invalid_port', lang)); return
                data['check_port'] = int(text)
                context.user_data['add_policy_step'] = 'account_nickname'
                buttons = [[InlineKeyboardButton(n, callback_data=f"lb_policy_set_account|{n}")] for n in CF_ACCOUNTS.keys()]
                await update.message.reply_text(get_text('prompts.choose_cf_account', lang), reply_markup=InlineKeyboardMarkup(buttons))
        return

    # --- STATE 4: Other Text Flows (Search, Bulk Edit, Add Record from main menu) ---
    elif context.user_data.get('is_searching'):
        context.user_data.pop('is_searching')
        context.user_data['search_query'] = text
        context.user_data['records_in_view'] = [r for r in context.user_data.get('all_records', []) if text.lower() in r['name'].lower()]
        await display_records_list(update, context)
    elif context.user_data.get('is_searching_ip'):
        context.user_data.pop('is_searching_ip')
        context.user_data['search_ip_query'] = text
        context.user_data['records_in_view'] = [r for r in context.user_data.get('all_records', []) if r['content'] == text]
        await display_records_list(update, context)
    elif context.user_data.get('is_bulk_ip_change'):
        selected_ids = context.user_data.get('selected_records', [])
        context.user_data.pop('is_bulk_ip_change')
        context.user_data['bulk_ip_confirm_details'] = {'new_ip': text, 'record_ids': selected_ids}
        kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="bulk_change_ip_execute")],
          [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="bulk_cancel")]]
        safe_new_ip = escape_markdown_v2(text)
        await update.message.reply_text(
            get_text('messages.bulk_confirm_change_ip', lang, count=len(selected_ids), new_ip=f"`{safe_new_ip}`"),
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="MarkdownV2"
    )
    elif "edit" in context.user_data:
        data = context.user_data.pop("edit")
        context.user_data["confirm"] = {"id": data["id"], "type": data["type"], "name": data["name"], "old": data["old"], "new": text}
        kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="confirm_change")],
          [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_records_list")]]

        safe_old = escape_markdown_v2(data['old'])
        safe_new = escape_markdown_v2(text)
        await update.message.reply_text(f"🔄 `{safe_old}` ➡️ `{safe_new}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode="MarkdownV2")
    elif "add_step" in context.user_data:
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
                await update.message.reply_text(get_text('messages.subdomain_exists', lang), reply_markup=InlineKeyboardMarkup(buttons))
            else:
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
    safe_name = escape_markdown_v2(record['name'])
    safe_content = escape_markdown_v2(record['content'])
    text = get_text('messages.record_details', lang, type=record['type'], name=f"`{safe_name}`", content=f"`{safe_content}`", proxy_status=proxy_text)
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="MarkdownV2")

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
    safe_record_name = escape_markdown_v2(record['name'])
    text = get_text('messages.confirm_proxy_toggle', lang, record_name=f"`{safe_record_name}`", current_status=current_status, new_status=new_status)
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="MarkdownV2")

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
    safe_record_name = escape_markdown_v2(record['name'])
    await update.callback_query.edit_message_text(get_text('messages.confirm_delete_record', lang, record_name=f"`{safe_record_name}`"),
                                                  reply_markup=InlineKeyboardMarkup(kb), parse_mode="MarkdownV2")

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
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="MarkdownV2")
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
    safe_new_ip = escape_markdown_v2(new_ip)
    await query.edit_message_text(get_text('messages.bulk_change_ip_progress', lang, count=len(record_ids), new_ip=f"`{safe_new_ip}`"),
                                  parse_mode="MarkdownV2")
                                  
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
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="MarkdownV2")
    clear_state(context, preserve=['language', 'selected_account_nickname', 'all_zones', 'selected_zone_id', 'selected_zone_name'])

async def set_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang_code = update.callback_query.data.split('|')[1]
    context.user_data['language'] = lang_code
    await update.callback_query.edit_message_text(get_text('messages.language_changed', lang_code))
    await asyncio.sleep(1)
    await list_records_command(update, context)

# --- Settings Callbacks ---
async def send_policy_edit_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int, policy_index: int):
    """Builds and sends the policy edit menu as a new message."""
    user_data = await context.application.persistence.get_user_data()
    lang = user_data.get(chat_id, {}).get('language', 'fa')

    config = load_config()
    policy = config['failover_policies'][policy_index]
    lb_config = policy.get('load_balancer', {})
    is_lb_enabled = lb_config.get('enabled', False)
    
    lb_button_key = 'buttons.manage_load_balancer_on' if is_lb_enabled else 'buttons.manage_load_balancer_off'
    lb_button_text = get_text(lb_button_key, lang)

    buttons = [
        [
            InlineKeyboardButton(get_text('buttons.edit_policy_name', lang), callback_data=f"policy_edit_field|policy_name"),
            InlineKeyboardButton(get_text('buttons.edit_policy_port', lang), callback_data=f"policy_edit_field|check_port")
        ],
        [
            InlineKeyboardButton(get_text('buttons.edit_policy_primary_ip', lang), callback_data=f"policy_edit_field|primary_ip"),
            InlineKeyboardButton(get_text('buttons.edit_policy_backup_ip', lang), callback_data=f"policy_edit_field|backup_ips")
        ],
        [
            InlineKeyboardButton(get_text('buttons.edit_failover_minutes', lang), callback_data=f"policy_edit_field|failover_minutes"),
            InlineKeyboardButton(get_text('buttons.edit_failback_minutes', lang), callback_data=f"policy_edit_field|failback_minutes")
        ],
        [InlineKeyboardButton(get_text('buttons.edit_policy_records', lang), callback_data=f"policy_edit_field|record_names")],
        [InlineKeyboardButton(get_text('buttons.edit_primary_monitoring', lang), callback_data="policy_edit_nodes|primary")],
        [InlineKeyboardButton(get_text('buttons.edit_backup_monitoring', lang), callback_data="policy_edit_nodes|backup")],
        [InlineKeyboardButton(lb_button_text, callback_data=f"lb_menu|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"policy_view|{policy_index}")]
    ]
    
    text = get_text('messages.edit_policy_menu', lang)
    reply_markup = InlineKeyboardMarkup(buttons)
    
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

async def send_lb_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int, policy_index: int):
    """Builds and sends the Load Balancer menu as a new message."""
    user_data = await context.application.persistence.get_user_data()
    lang = user_data.get(chat_id, {}).get('language', 'fa')
    
    config = load_config()
    policy = config['failover_policies'][policy_index]
    lb_config = policy.get('load_balancer', {})
    
    is_enabled = lb_config.get('enabled', False)
    status_text = get_text('buttons.lb_status_enabled', lang) if is_enabled else get_text('buttons.lb_status_disabled', lang)
    
    buttons = [
        [InlineKeyboardButton(status_text, callback_data=f"lb_toggle_enabled|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.lb_ips', lang), callback_data=f"lb_edit_field|ips")],
        [InlineKeyboardButton(get_text('buttons.lb_interval', lang), callback_data=f"lb_edit_field|interval")],
        [InlineKeyboardButton(get_text('buttons.back_to_edit_menu_button', lang), callback_data=f"policy_edit|{policy_index}")]
    ]
    
    header = get_text('messages.lb_menu_header', lang, name=policy.get('policy_name', 'N/A'))
    reply_markup = InlineKeyboardMarkup(buttons)

    await context.bot.send_message(chat_id=chat_id, text=header, reply_markup=reply_markup)

async def lb_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the Load Balancer management menu for a policy."""
    query = update.callback_query
    lang = get_user_lang(context)
    
    if query and query.message:
        await query.answer()
    
    try:
        policy_index = int(query.data.split('|')[1])
        context.user_data['edit_policy_index'] = policy_index
    except (IndexError, ValueError):
        if query: await query.edit_message_text("Error: Policy not found."); return

    config = load_config()
    policy = config['failover_policies'][policy_index] 
    lb_config = policy.get('load_balancer', {})
    
    is_enabled = lb_config.get('enabled', False)
    status_text = get_text('buttons.lb_status_enabled', lang) if is_enabled else get_text('buttons.lb_status_disabled', lang)
    
    buttons = [
        [InlineKeyboardButton(status_text, callback_data=f"lb_toggle_enabled|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.lb_ips', lang), callback_data=f"lb_edit_field|ips")],
        [InlineKeyboardButton(get_text('buttons.lb_interval', lang), callback_data=f"lb_edit_field|interval")],
        [InlineKeyboardButton(get_text('buttons.back_to_edit_menu_button', lang), callback_data=f"policy_edit|{policy_index}")]
    ]
    
    header = get_text('messages.lb_menu_header', lang, name=policy.get('policy_name', 'N/A'))
    reply_markup = InlineKeyboardMarkup(buttons)

    try:
        if query and query.message:
            await query.edit_message_text(header, reply_markup=reply_markup)
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=header, reply_markup=reply_markup)
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.error(f"An error occurred in lb_menu_callback: {e}")

async def lb_toggle_enabled_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles the enabled status of the Load Balancer for a policy."""
    query = update.callback_query
    lang = get_user_lang(context)
    
    policy_index = int(query.data.split('|')[1])
    config = load_config()
    policy = config['failover_policies'][policy_index]
    
    if 'load_balancer' not in policy:
        policy['load_balancer'] = {'enabled': False, 'ips': [], 'rotation_interval_hours': 6}
        
    is_enabled = policy['load_balancer'].get('enabled', False)
    policy['load_balancer']['enabled'] = not is_enabled
    save_config(config)
    
    status_key = "ON" if not is_enabled else "OFF"
    await query.answer(get_text('messages.lb_status_changed', lang, status=status_key), show_alert=True)
    
    await lb_menu_callback(update, context)

async def lb_edit_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of editing a Load Balancer field (IPs or interval)."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    field = query.data.split('|')[1]
    if field == 'ips':
        context.user_data['awaiting_lb_ips'] = True
        await query.edit_message_text(get_text('prompts.enter_lb_ips', lang))
    elif field == 'interval':
        context.user_data['awaiting_lb_interval'] = True
        await query.edit_message_text(get_text('prompts.enter_lb_interval', lang))

async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    lang = get_user_lang(context)
    
    buttons = [
        [InlineKeyboardButton(get_text('buttons.manage_failover_policies', lang), callback_data="settings_failover_policies")],
        [InlineKeyboardButton(get_text('buttons.manage_lb_policies', lang), callback_data="settings_lb_policies")],
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
    query = update.callback_query
    await query.answer()
    
    clear_state(context)
    
    await display_account_list(update, context, force_new_message=True)
async def go_to_settings_from_startup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await show_settings_menu(update, context, force_new_message=True)

async def go_to_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_settings_menu(update, context)

async def back_to_settings_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('edit_policy_index', None)
    context.user_data.pop('editing_policy_type', None)
    
    await show_settings_menu(update, context)

async def settings_failover_policies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
    query = update.callback_query
    if query: await query.answer()
    lang = get_user_lang(context)

    try:
        if query: await query.answer()
    except telegram_error.BadRequest as e:
        if "Query is too old" not in str(e): raise e
    
    context.user_data.pop('edit_policy_index', None)
    context.user_data.pop('editing_policy_type', None)
    
    config = load_config()
    policies = config.get("failover_policies", [])
    buttons = []
    
    if not policies:
        policies_text = get_text('messages.no_policies', lang)
    else:
        policies_text = ""
        for i, policy in enumerate(policies):
            policies_text += f"*{i+1}. {policy.get('policy_name', 'N/A')}*\n`{policy.get('primary_ip', 'N/A')}`\n\n"
            buttons.append([InlineKeyboardButton(f"{i+1}. {policy.get('policy_name', 'N/A')}", callback_data=f"failover_policy_view|{i}")])
            
    text = get_text('messages.policies_list_menu', lang, policies_text=policies_text)
    buttons.append([InlineKeyboardButton(get_text('buttons.add_policy', lang), callback_data="failover_policy_add_start")])
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")])
    
    try:
        if query: await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    except telegram_error.BadRequest as e:
        if "Message is not modified" not in str(e): raise e

# --- LOAD BALANCER POLICY MANAGEMENT (NEW FUNCTIONS) ---
async def lb_policy_add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of adding a new Load Balancing policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    context.user_data['add_policy_type'] = 'lb'
    context.user_data['new_policy_data'] = {}
    context.user_data['add_policy_step'] = 'name'
    await query.edit_message_text(get_text('prompts.enter_lb_policy_name', lang))

async def lb_policy_set_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles account selection when adding a new LB policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    if context.user_data.get('add_policy_type') != 'lb':
        await query.edit_message_text("Error: Invalid context. Please start over."); return

    nickname = query.data.split('|')[1]
    context.user_data['new_policy_data']['account_nickname'] = nickname
    token = CF_ACCOUNTS.get(nickname)
    if not token:
        await query.edit_message_text("Error: Account token not found."); return
        
    await query.edit_message_text("Fetching zones, please wait...")
    zones = await get_all_zones(token)
    if not zones:
        await query.edit_message_text("No zones found for this account."); return
        
    buttons = [[InlineKeyboardButton(zone['name'], callback_data=f"lb_policy_set_zone|{zone['name']}")] for zone in zones]
    context.user_data['add_policy_step'] = 'zone_name'
    await query.edit_message_text(get_text('prompts.choose_zone_for_policy', lang), reply_markup=InlineKeyboardMarkup(buttons))

async def lb_policy_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    query = update.callback_query
    if query: await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = context.user_data.get('edit_policy_index')
        if policy_index is None and query and '|' in query.data:
            policy_index = int(query.data.split('|')[1])

        if policy_index is None:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=get_text('messages.session_expired_error', lang)); return
        
        context.user_data['edit_policy_index'] = policy_index
        context.user_data['editing_policy_type'] = 'lb'
    except (IndexError, ValueError):
        await context.bot.send_message(chat_id=update.effective_chat.id, text=get_text('messages.error_policy_not_found', lang)); return

    buttons = [
        [InlineKeyboardButton(get_text('buttons.edit_lb_policy_name', lang), callback_data=f"lb_policy_edit_field|policy_name"),
         InlineKeyboardButton(get_text('buttons.edit_lb_port', lang), callback_data=f"lb_policy_edit_field|check_port")],
        [InlineKeyboardButton(get_text('buttons.edit_lb_ips', lang), callback_data=f"lb_policy_edit_field|ips"),
         InlineKeyboardButton(get_text('buttons.edit_lb_interval', lang), callback_data=f"lb_policy_edit_field|rotation_interval_range")],
        [InlineKeyboardButton(get_text('buttons.edit_lb_records', lang), callback_data=f"lb_policy_edit_field|record_names")],
        [InlineKeyboardButton(get_text('buttons.edit_lb_monitoring', lang), callback_data="policy_edit_nodes|lb")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"lb_policy_view|{policy_index}")]
    ]
    
    text = get_text('messages.edit_lb_policy_menu', lang)
    reply_markup = InlineKeyboardMarkup(buttons)

    try:
        if query and not force_new_message:
            await query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)
    except telegram_error.BadRequest as e:
        if "Message is not modified" not in str(e): raise e

async def lb_policy_edit_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the selection of a field to edit in an LB policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    field_to_edit = query.data.split('|')[1]
    
    if field_to_edit == 'record_names':
        context.user_data['is_editing_policy_records'] = True
        policy_index = context.user_data['edit_policy_index']
        config = load_config()
        policy = config['load_balancer_policies'][policy_index]
        context.user_data['policy_selected_records'] = policy.get('record_names', [])
        await display_records_for_selection(update, context, page=0)
        return
    
    if field_to_edit == 'rotation_interval_range':
        context.user_data['edit_policy_field'] = 'rotation_interval_range'
        await query.edit_message_text(get_text('prompts.enter_new_lb_interval', lang))
        return

    context.user_data['edit_policy_field'] = field_to_edit
    
    prompt_key = f"prompts.enter_new_lb_{field_to_edit}"
    default_prompt_key = f"prompts.enter_new_{field_to_edit}"
    
    await query.edit_message_text(get_text(prompt_key, lang, default=get_text(default_prompt_key, lang, default=f"Enter new value for {field_to_edit}")))

async def lb_policy_set_zone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles zone selection when adding a new LB policy."""
    query = update.callback_query
    await query.answer()
    
    if context.user_data.get('add_policy_type') != 'lb':
        await query.edit_message_text(get_text('messages.session_expired_error', get_user_lang(context)))
        return

    zone_name = query.data.split('|')[1]
    context.user_data['new_policy_data']['zone_name'] = zone_name
    
    context.user_data['policy_selected_records'] = []
    context.user_data.pop('policy_all_records', None)
    context.user_data.pop('current_selection_zone', None)
    
    await display_records_for_selection(update, context, page=0)

async def settings_lb_policies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
    query = update.callback_query
    if query: await query.answer()
    lang = get_user_lang(context)

    try:
        if query: await query.answer()
    except telegram_error.BadRequest as e:
        if "Query is too old" not in str(e): raise e
    
    context.user_data.pop('edit_policy_index', None)
    context.user_data.pop('editing_policy_type', None)

    config = load_config()
    policies = config.get("load_balancer_policies", [])
    buttons = []
    
    if not policies:
        policies_text = get_text('messages.no_lb_policies', lang)
    else:
        policies_text = ""
        for i, policy in enumerate(policies):
            ips_str = ", ".join(policy.get('ips', []))
            policies_text += f"*{i+1}. {policy.get('policy_name', 'N/A')}*\n`{ips_str}`\n\n"
            buttons.append([InlineKeyboardButton(f"{i+1}. {policy.get('policy_name', 'N/A')}", callback_data=f"lb_policy_view|{i}")])
            
    text = get_text('messages.lb_policies_list_menu', lang, policies_text=policies_text)
    buttons.append([InlineKeyboardButton(get_text('buttons.add_lb_policy', lang), callback_data="lb_policy_add_start")])
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")])
    
    try:
        if query: await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    except telegram_error.BadRequest as e:
        if "Message is not modified" not in str(e): raise e

async def lb_policy_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    query = update.callback_query
    if query: await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = context.user_data.get('edit_policy_index')
        if policy_index is None and query and '|' in query.data:
            policy_index = int(query.data.split('|')[1])

        if policy_index is None:
            if query: await query.edit_message_text(get_text('messages.session_expired_error', lang))
            else: await context.bot.send_message(chat_id=update.effective_chat.id, text=get_text('messages.session_expired_error', lang))
            return
        
        config = load_config()
        policies = config.get('load_balancer_policies', [])

        if not (0 <= policy_index < len(policies)):
            if query: await query.edit_message_text(get_text('messages.error_policy_list_changed', lang))
            await asyncio.sleep(1)
            await settings_lb_policies_callback(update, context)
            return
            
        policy = policies[policy_index]
        context.user_data['edit_policy_index'] = policy_index
        context.user_data['editing_policy_type'] = 'lb'
    except (IndexError, ValueError):
        if query: await query.edit_message_text(get_text('messages.error_policy_not_found', lang))
        return
        
    is_enabled = policy.get('enabled', True)
    status_text = get_text('messages.status_enabled', lang) if is_enabled else get_text('messages.status_disabled', lang)
    
    min_h = policy.get('rotation_min_hours')
    max_h = policy.get('rotation_max_hours')
    if min_h and max_h:
        if min_h == max_h:
            interval_text = f"`{get_text('messages.interval_display_fixed', lang, hours=min_h)}`"
        else:
            interval_text = f"`{get_text('messages.interval_display_random', lang, min_hours=min_h, max_hours=max_h)}`"
    else:
        interval_h = policy.get('rotation_interval_hours', 'N/A')
        interval_text = f"`{interval_h} hours`"

    ips_str = "\n".join([f"`{escape_markdown_v2(ip)}`" for ip in policy.get('ips', [])]) if policy.get('ips') else '`' + get_text('messages.not_set', lang, default='Not set') + '`'
    records_str = f"`{escape_markdown_v2(', '.join(policy.get('record_names', [])))}`" if policy.get('record_names') else '`' + get_text('messages.not_set', lang, default='Not set') + '`'
    
    details_parts = [
        f"*{get_text('policy_fields.name', lang)}:* `{escape_markdown_v2(policy.get('policy_name', 'N/A'))}`",
        f"*{get_text('policy_fields.status', lang)}:* {status_text}",
        f"\n*{get_text('policy_fields.ip_pool', lang)}:*\n{ips_str}\n",
        f"*{get_text('policy_fields.rotation_interval', lang)}:* {interval_text}",
        f"*{get_text('policy_fields.health_check_port', lang)}:* `{escape_markdown_v2(policy.get('check_port', 'N/A'))}`",
        f"*{get_text('policy_fields.account', lang)}:* `{escape_markdown_v2(policy.get('account_nickname', 'N/A'))}`",
        f"*{get_text('policy_fields.zone', lang)}:* `{escape_markdown_v2(policy.get('zone_name', 'N/A'))}`",
        f"*{get_text('policy_fields.monitored_records', lang)}:* {records_str}"
    ]
    details_text = "\n".join(details_parts)
    
    toggle_btn_text = get_text('buttons.disable_policy', lang) if is_enabled else get_text('buttons.enable_policy', lang)
    
    buttons = [
        [InlineKeyboardButton(get_text('buttons.edit_policy', lang), callback_data=f"lb_policy_edit|{policy_index}"),
         InlineKeyboardButton(toggle_btn_text, callback_data=f"lb_policy_toggle|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.delete_policy', lang), callback_data=f"lb_policy_delete|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="settings_lb_policies")]
    ]
    
    try:
        if query and not force_new_message:
            await query.edit_message_text(details_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="MarkdownV2")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=details_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="MarkdownV2")
    except error.BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error(f"A BadRequest error occurred in lb_policy_view_callback: {e}", exc_info=True)

async def lb_policy_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles the enabled status of a Load Balancing policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = int(query.data.split('|')[1])
        config = load_config()

        is_enabled = config['load_balancer_policies'][policy_index].get('enabled', True)
        config['load_balancer_policies'][policy_index]['enabled'] = not is_enabled
        save_config(config)

        policy_name = config['load_balancer_policies'][policy_index].get('policy_name', 'N/A')
        new_status_key = 'status_enabled' if not is_enabled else 'status_disabled'
        new_status_text = get_text(new_status_key, lang)
        
        await query.answer(
            text=get_text('messages.policy_status_changed', lang, name=policy_name, status=new_status_text),
            show_alert=True
        )
        
        await lb_policy_view_callback(update, context)
    except (IndexError, ValueError):
        await query.edit_message_text("Error: Could not toggle LB policy status.")

async def lb_policy_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks for confirmation before deleting an LB policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    try:
        policy_index = int(query.data.split('|')[1])
        config = load_config()
        policy_name = config['load_balancer_policies'][policy_index]['policy_name']
    except (IndexError, ValueError):
        await query.edit_message_text(get_text('messages.error_policy_not_found', lang)); return
        
    buttons = [
        [InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"lb_policy_delete_confirm|{policy_index}")],
        
        [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"lb_policy_view|{policy_index}")]
    ]

    try:
        await query.edit_message_text(get_text('messages.confirm_delete_policy', lang, name=policy_name), reply_markup=InlineKeyboardMarkup(buttons))
    except telegram_error.BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error in lb_policy_delete_callback: {e}")
async def lb_policy_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes an LB policy after confirmation and correctly refreshes the list."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    try:
        policy_index = int(query.data.split('|')[1])
        config = load_config()
        policy = config['load_balancer_policies'].pop(policy_index)
        save_config(config)
    except (IndexError, ValueError):
        await query.edit_message_text(get_text('messages.error_policy_not_found', lang)); return
        
    await query.answer(get_text('messages.policy_deleted_successfully', lang, name=policy['policy_name']), show_alert=True)
    
    context.user_data.pop('edit_policy_index', None)
    context.user_data.pop('editing_policy_type', None)

    await settings_lb_policies_callback(update, context)

async def settings_notifications_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    try:
        if query: await query.answer()
    except telegram_error.BadRequest as e:
        if "Query is too old" not in str(e): raise e

    if query:
        await query.answer()
    await show_settings_notifications_menu(update, context)

async def show_settings_notifications_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def failover_policy_add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of adding a new Failover policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    context.user_data['add_policy_type'] = 'failover'
    
    context.user_data['new_policy_data'] = {}
    context.user_data['add_policy_step'] = 'name'
    await query.edit_message_text(get_text('prompts.enter_policy_name', lang))

async def failover_policy_set_zone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles zone selection when adding a new Failover policy."""
    query = update.callback_query
    await query.answer()
    
    if context.user_data.get('add_policy_type') != 'failover':
        await query.edit_message_text(get_text('messages.session_expired_error', get_user_lang(context)))
        return

    zone_name = query.data.split('|')[1]
    context.user_data['new_policy_data']['zone_name'] = zone_name
    
    context.user_data['policy_selected_records'] = []
    context.user_data.pop('policy_all_records', None)
    context.user_data.pop('current_selection_zone', None)
    
    await display_records_for_selection(update, context, page=0)

async def failover_policy_set_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles account selection when adding a new Failover policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    if context.user_data.get('add_policy_type') != 'failover':
        await query.edit_message_text(get_text('messages.session_expired_error', lang)); return

    nickname = query.data.split('|')[1]
    context.user_data['new_policy_data']['account_nickname'] = nickname
    token = CF_ACCOUNTS.get(nickname)
    if not token:
        await query.edit_message_text(get_text('messages.error_no_token', lang)); return
        
    await query.edit_message_text(get_text('messages.fetching_zones', lang, default="Fetching zones..."))
    zones = await get_all_zones(token)
    if not zones:
        await query.edit_message_text(get_text('messages.no_zones_found', lang)); return
        
    buttons = [[InlineKeyboardButton(z['name'], callback_data=f"failover_policy_set_zone|{z['name']}")] for z in zones]
    context.user_data['add_policy_step'] = 'zone_name'
    await query.edit_message_text(get_text('prompts.choose_zone_for_policy', lang), reply_markup=InlineKeyboardMarkup(buttons))

async def failover_policy_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    query = update.callback_query
    if query: await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = context.user_data.get('edit_policy_index')
        if policy_index is None and query and '|' in query.data:
            policy_index = int(query.data.split('|')[1])
            
        if policy_index is None:
            if query: await query.edit_message_text(get_text('messages.session_expired_error', lang))
            else: await context.bot.send_message(chat_id=update.effective_chat.id, text=get_text('messages.session_expired_error', lang)); return
        
        config = load_config()
        policies = config.get('failover_policies', [])

        if not (0 <= policy_index < len(policies)):
            if query: await query.edit_message_text(get_text('messages.error_policy_list_changed', lang))
            await asyncio.sleep(1)
            await settings_failover_policies_callback(update, context); return
            
        policy = policies[policy_index]
        context.user_data['edit_policy_index'] = policy_index
        context.user_data['editing_policy_type'] = 'failover'
    except (IndexError, ValueError):
        if query: await query.edit_message_text(get_text('messages.error_policy_not_found', lang)); return

    is_enabled = policy.get('enabled', True)
    status_text = get_text('messages.status_enabled', lang) if is_enabled else get_text('messages.status_disabled', lang)
    is_failback_enabled = policy.get('auto_failback', False)
    failback_status_text = get_text('messages.status_enabled', lang) if is_failback_enabled else get_text('messages.status_disabled', lang)
    
    details_parts = [
        f"*{get_text('policy_fields.name', lang)}:* `{escape_markdown_v2(policy.get('policy_name', 'N/A'))}`",
        f"*{get_text('policy_fields.primary_ip', lang)}:* `{escape_markdown_v2(policy.get('primary_ip', 'N/A'))}` \\(Port: `{escape_markdown_v2(policy.get('check_port', 'N/A'))}`\\)",
        f"*{get_text('policy_fields.backup_ips', lang)}:* `{escape_markdown_v2(', '.join(policy.get('backup_ips', [])))}`",
        f"*{get_text('policy_fields.account', lang)}:* `{escape_markdown_v2(policy.get('account_nickname', 'N/A'))}`",
        f"*{get_text('policy_fields.zone', lang)}:* `{escape_markdown_v2(policy.get('zone_name', 'N/A'))}`",
        f"*{get_text('policy_fields.monitored_records', lang)}:* `{escape_markdown_v2(', '.join(policy.get('record_names', [])))}`",
        f"*{get_text('policy_fields.status', lang)}:* {status_text}",
        f"*{get_text('policy_fields.auto_failback', lang)}:* {failback_status_text}"
    ]
    details_text = "\n".join(details_parts)
    
    toggle_btn = get_text('buttons.disable_policy', lang) if is_enabled else get_text('buttons.enable_policy', lang)
    toggle_fb_btn = get_text('buttons.disable_failback', lang) if is_failback_enabled else get_text('buttons.enable_failback', lang)
    
    buttons = [
        [InlineKeyboardButton(get_text('buttons.edit_policy', lang), callback_data=f"failover_policy_edit|{policy_index}"),
         InlineKeyboardButton(toggle_btn, callback_data=f"failover_policy_toggle|{policy_index}")],
        [InlineKeyboardButton(toggle_fb_btn, callback_data=f"failover_policy_toggle_failback|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.delete_policy', lang), callback_data=f"failover_policy_delete|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="settings_failover_policies")]
    ]
    
    try:
        if query and not force_new_message:
            await query.edit_message_text(details_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="MarkdownV2")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=details_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="MarkdownV2")
    except error.BadRequest as e:
        if "Message is not modified" not in str(e): raise e

async def failover_policy_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    query = update.callback_query
    if query: await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = context.user_data.get('edit_policy_index')
        if policy_index is None and query and '|' in query.data:
            policy_index = int(query.data.split('|')[1])

        if policy_index is None:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=get_text('messages.session_expired_error', lang)); return
        
        context.user_data['edit_policy_index'] = policy_index
        context.user_data['editing_policy_type'] = 'failover'
    except (IndexError, ValueError):
        await context.bot.send_message(chat_id=update.effective_chat.id, text=get_text('messages.error_policy_not_found', lang)); return

    buttons = [
        [InlineKeyboardButton(get_text('buttons.edit_policy_name', lang), callback_data=f"failover_policy_edit_field|policy_name"),
         InlineKeyboardButton(get_text('buttons.edit_policy_port', lang), callback_data=f"failover_policy_edit_field|check_port")],
        [InlineKeyboardButton(get_text('buttons.edit_policy_primary_ip', lang), callback_data=f"failover_policy_edit_field|primary_ip"),
         InlineKeyboardButton(get_text('buttons.edit_policy_backup_ip', lang), callback_data=f"failover_policy_edit_field|backup_ips")],
        [InlineKeyboardButton(get_text('buttons.edit_failover_minutes', lang), callback_data=f"failover_policy_edit_field|failover_minutes"),
         InlineKeyboardButton(get_text('buttons.edit_failback_minutes', lang), callback_data=f"failover_policy_edit_field|failback_minutes")],
        [InlineKeyboardButton(get_text('buttons.edit_policy_records', lang), callback_data=f"failover_policy_edit_field|record_names")],
        [InlineKeyboardButton(get_text('buttons.edit_primary_monitoring', lang), callback_data="policy_edit_nodes|primary")],
        [InlineKeyboardButton(get_text('buttons.edit_backup_monitoring', lang), callback_data="policy_edit_nodes|backup")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"failover_policy_view|{policy_index}")]
    ]
    
    text = get_text('messages.edit_policy_menu', lang)
    reply_markup = InlineKeyboardMarkup(buttons)
    
    try:
        if query and not force_new_message:
            await query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)
    except telegram_error.BadRequest as e:
        if "Message is not modified" not in str(e): raise e

async def copy_monitoring_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Copies monitoring settings from primary to backup for a Failover policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = int(query.data.split('|')[1])
        config = load_config()
        policy = config['failover_policies'][policy_index]

        primary_nodes = policy.get('primary_monitoring_nodes', [])
        primary_threshold = policy.get('primary_threshold')
        
        policy['backup_monitoring_nodes'] = primary_nodes
        if primary_threshold:
            policy['backup_threshold'] = primary_threshold
            
        save_config(config)
        
        await query.answer("✅ Settings copied to backup IPs successfully!", show_alert=True)
        
        context.user_data['edit_policy_index'] = policy_index
        context.user_data['editing_policy_type'] = 'failover'
        await failover_policy_edit_callback(update, context)

    except (IndexError, ValueError, KeyError):
        await query.edit_message_text(get_text('messages.error_generic_request', lang))

async def policy_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = get_user_lang(context)
    if query and query.message:
        await query.answer()
    
    try:
        policy_index = int(query.data.split('|')[1])
        context.user_data['edit_policy_index'] = policy_index
    except (IndexError, ValueError):
        if query: await query.edit_message_text("Error: Policy not found."); return

    config = load_config()
    policy = config['failover_policies'][policy_index]
    lb_config = policy.get('load_balancer', {})
    is_lb_enabled = lb_config.get('enabled', False)
    
    lb_button_key = 'buttons.manage_load_balancer_on' if is_lb_enabled else 'buttons.manage_load_balancer_off'
    lb_button_text = get_text(lb_button_key, lang)

    buttons = [
        [
            InlineKeyboardButton(get_text('buttons.edit_policy_name', lang), callback_data=f"policy_edit_field|policy_name"),
            InlineKeyboardButton(get_text('buttons.edit_policy_port', lang), callback_data=f"policy_edit_field|check_port")
        ],
        [
            InlineKeyboardButton(get_text('buttons.edit_policy_primary_ip', lang), callback_data=f"policy_edit_field|primary_ip"),
            InlineKeyboardButton(get_text('buttons.edit_policy_backup_ip', lang), callback_data=f"policy_edit_field|backup_ips")
        ],
        [
            InlineKeyboardButton(get_text('buttons.edit_failover_minutes', lang), callback_data=f"policy_edit_field|failover_minutes"),
            InlineKeyboardButton(get_text('buttons.edit_failback_minutes', lang), callback_data=f"policy_edit_field|failback_minutes")
        ],
        [InlineKeyboardButton(get_text('buttons.edit_policy_records', lang), callback_data=f"policy_edit_field|record_names")],
        [InlineKeyboardButton(get_text('buttons.edit_primary_monitoring', lang), callback_data="policy_edit_nodes|primary")],
        [InlineKeyboardButton(get_text('buttons.edit_backup_monitoring', lang), callback_data="policy_edit_nodes|backup")],
        [InlineKeyboardButton(lb_button_text, callback_data=f"lb_menu|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"policy_view|{policy_index}")]
    ]
    
    text = get_text('messages.edit_policy_menu', lang)
    reply_markup = InlineKeyboardMarkup(buttons)
    
    if query and query.message:
        await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)

async def failover_policy_edit_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def failover_policy_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks for confirmation before deleting a Failover policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    try:
        policy_index = int(query.data.split('|')[1])
        config = load_config()
        policy_name = config['failover_policies'][policy_index]['policy_name']
    except (IndexError, ValueError):
        await query.edit_message_text(get_text('messages.error_policy_not_found', lang)); return
        
    buttons = [
        [InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"failover_policy_delete_confirm|{policy_index}")],
        
        [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"failover_policy_view|{policy_index}")]
    ]
    
    try:
        await query.edit_message_text(get_text('messages.confirm_delete_policy', lang, name=policy_name), reply_markup=InlineKeyboardMarkup(buttons))
    except telegram_error.BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error in failover_policy_delete_callback: {e}")

async def failover_policy_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes a Failover policy after confirmation and correctly refreshes the list."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    try:
        policy_index = int(query.data.split('|')[1])
        config = load_config()
        policy = config['failover_policies'].pop(policy_index)
        save_config(config)
    except (IndexError, ValueError):
        await query.edit_message_text(get_text('messages.error_policy_not_found', lang)); return
        
    await query.answer(get_text('messages.policy_deleted_successfully', lang, name=policy['policy_name']), show_alert=True)
    
    context.user_data.pop('edit_policy_index', None)
    context.user_data.pop('editing_policy_type', None)

    await settings_failover_policies_callback(update, context)

# --- Command Handlers ---
async def clear_monitoring_state_on_startup(application: Application):
    """
    Resets transient health status fields on startup but preserves the
    last known active_ip_index to maintain the failover state.
    """
    logger.info("Resetting transient monitoring state on startup...")
    if 'health_status' in application.bot_data:
        for policy_name, status in application.bot_data['health_status'].items():
            status['downtime_start'] = None
            status['uptime_start'] = None
            status['critical_alert_sent'] = False
        logger.info("Transient monitoring state has been reset.")
    else:
        logger.info("No monitoring state found to reset.")

async def sync_dns_with_config(context: ContextTypes.DEFAULT_TYPE):
    """
    On startup, syncs DNS records for failover policies to their primary IP.
    This provides a predictable starting state. It runs silently within a try-except block
    to prevent any startup failures.
    """
    logger.info("--- [JOB] Starting Startup DNS Sync with Config ---")
    
    try:
        config = load_config()
        if config is None:
            logger.error("Sync failed: Could not load config file."); return

        for policy in config.get("failover_policies", []):
            if not policy.get('enabled', True):
                continue

            policy_name = policy.get('policy_name', 'Unnamed Policy')
            primary_ip = policy.get('primary_ip')
            
            target_ip = primary_ip

            account_nickname = policy.get('account_nickname')
            token = CF_ACCOUNTS.get(account_nickname)
            zone_name = policy.get('zone_name')
            record_names = policy.get('record_names', [])

            if not all([target_ip, token, zone_name, record_names]):
                logger.warning(f"Skipping sync for policy '{policy_name}' due to incomplete configuration.")
                continue

            zones = await get_all_zones(token)
            zone_id = next((z['id'] for z in zones if z['name'] == zone_name), None)
            if not zone_id:
                logger.warning(f"Skipping sync for policy '{policy_name}': Could not find zone ID."); continue

            all_dns_records = await get_dns_records(token, zone_id)
            for record in all_dns_records:
                short_name = get_short_name(record['name'], zone_name)
                if short_name in record_names and record.get('content') != target_ip:
                    logger.info(f"Sync: Record '{record['name']}' (IP: {record.get('content')}) does not match primary IP '{target_ip}'. Updating...")
                    await update_record(
                        token, zone_id, record['id'], record['type'],
                        record['name'], target_ip, record.get('proxied', False)
                    )
        
        logger.info("--- [JOB] Startup DNS Sync Finished ---")
    except Exception as e:
        logger.error(f"!!! An unexpected error occurred during sync_dns_with_config job !!!", exc_info=True)

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

async def post_startup_tasks(application: Application):
    """
    Runs all necessary tasks after the application has been fully initialized.
    This function is called by the `post_init` argument in Application.builder().
    """
    await set_bot_commands(application)
    await clear_monitoring_state_on_startup(application)
    
    job_queue = application.job_queue
    
    if not job_queue.get_jobs_by_name("startup_sync_job"):
        job_queue.run_once(sync_dns_with_config, 5, name="startup_sync_job")
        
    if not job_queue.get_jobs_by_name("update_nodes_job"):
        job_queue.run_repeating(
            update_check_host_nodes_job,
            interval=timedelta(hours=24), 
            first=timedelta(seconds=20),
            name="update_nodes_job"
        )
        
    if not job_queue.get_jobs_by_name("health_check_job"):
        job_queue.run_repeating(health_check_job, interval=310, first=15, name="health_check_job")

def main():
    """Starts the bot."""
    persistence_file = "bot_data.pickle"
    if os.path.exists(persistence_file) and os.path.getsize(persistence_file) == 0:
        logger.warning(
            f"'{persistence_file}' is empty. Initializing with a valid empty structure."
        )
        initial_data = {
            "user_data": {},
            "chat_data": {},
            "bot_data": {},
            "conversations": {},
        }
        with open(persistence_file, "wb") as f:
            pickle.dump(initial_data, f)
        
    load_translations()

    try:
        config = load_config()
        if config:
            config.setdefault("notifications", {"enabled": True, "chat_ids": []})
            notification_ids = set(config["notifications"].get("chat_ids", []))
            
            initial_admins = {int(admin_id) for admin_id in os.getenv("TELEGRAM_ADMIN_IDS", "").split(',') if admin_id.strip().isdigit()}
            
            if not initial_admins.issubset(notification_ids):
                logger.info("Adding .env admins to the notification list for the first time.")
                for admin_id in initial_admins:
                    if admin_id not in notification_ids:
                        config["notifications"]["chat_ids"].append(admin_id)
                save_config(config)
    except Exception as e:
        logger.error(f"Could not auto-add admins to notification list: {e}")
    
    persistence = PicklePersistence(filepath=persistence_file)
    
    application = Application.builder() \
        .token(TELEGRAM_BOT_TOKEN) \
        .persistence(persistence) \
        .post_init(post_startup_tasks) \
        .build()
       
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
    
    # === Specific Callback Handlers (Corrected & Ordered) ===

    # --- Main Navigation & Record Management ---
    application.add_handler(CallbackQueryHandler(select_account_callback, pattern="^select_account\|"))
    application.add_handler(CallbackQueryHandler(back_to_accounts_callback, pattern="^back_to_accounts$"))
    application.add_handler(CallbackQueryHandler(select_zone_callback, pattern="^select_zone\|"))
    application.add_handler(CallbackQueryHandler(back_to_zones_callback, pattern="^back_to_zones$"))
    application.add_handler(CallbackQueryHandler(list_page_callback, pattern="^list_page\|"))
    application.add_handler(CallbackQueryHandler(refresh_list_callback, pattern="^refresh_list$"))
    application.add_handler(CallbackQueryHandler(back_to_records_list_callback, pattern="^back_to_records_list$"))
    application.add_handler(CallbackQueryHandler(select_callback, pattern="^select\|"))
    application.add_handler(CallbackQueryHandler(edit_callback, pattern="^edit\|"))
    application.add_handler(CallbackQueryHandler(confirm_change_callback, pattern="^confirm_change$"))
    application.add_handler(CallbackQueryHandler(toggle_proxy_callback, pattern="^toggle_proxy\|"))
    application.add_handler(CallbackQueryHandler(toggle_proxy_confirm_callback, pattern="^toggle_proxy_confirm\|"))
    application.add_handler(CallbackQueryHandler(delete_callback, pattern="^delete\|"))
    application.add_handler(CallbackQueryHandler(delete_confirm_callback, pattern="^delete_confirm\|"))
    application.add_handler(CallbackQueryHandler(add_callback, pattern="^add$"))
    application.add_handler(CallbackQueryHandler(add_type_callback, pattern="^add_type\|"))
    application.add_handler(CallbackQueryHandler(add_proxied_callback, pattern="^add_proxied\|"))
    application.add_handler(CallbackQueryHandler(add_retry_name_callback, pattern="^add_retry_name$"))
    application.add_handler(CallbackQueryHandler(search_menu_callback, pattern="^search_menu$"))
    application.add_handler(CallbackQueryHandler(search_by_name_callback, pattern="^search_by_name$"))
    application.add_handler(CallbackQueryHandler(search_by_ip_callback, pattern="^search_by_ip$"))
    
    # --- Bulk Actions ---
    application.add_handler(CallbackQueryHandler(bulk_start_callback, pattern="^bulk_start$"))
    application.add_handler(CallbackQueryHandler(bulk_cancel_callback, pattern="^bulk_cancel$"))
    application.add_handler(CallbackQueryHandler(bulk_select_all_callback, pattern="^bulk_select_all\|"))
    application.add_handler(CallbackQueryHandler(bulk_select_callback, pattern="^bulk_select\|"))
    application.add_handler(CallbackQueryHandler(bulk_delete_confirm_callback, pattern="^bulk_delete_confirm$"))
    application.add_handler(CallbackQueryHandler(bulk_delete_execute_callback, pattern="^bulk_delete_execute$"))
    application.add_handler(CallbackQueryHandler(bulk_change_ip_start_callback, pattern="^bulk_change_ip_start$"))
    application.add_handler(CallbackQueryHandler(bulk_change_ip_execute_callback, pattern="^bulk_change_ip_execute$"))

    # --- Language & Settings Menus ---
    application.add_handler(CallbackQueryHandler(set_lang_callback, pattern="^set_lang\|"))
    application.add_handler(CallbackQueryHandler(go_to_settings_callback, pattern="^go_to_settings$"))
    application.add_handler(CallbackQueryHandler(back_to_settings_main_callback, pattern="^back_to_settings_main$"))
    application.add_handler(CallbackQueryHandler(go_to_settings_from_alert_callback, pattern="^go_to_settings_from_alert$"))
    application.add_handler(CallbackQueryHandler(go_to_main_list_callback, pattern="^go_to_main_list$"))
    application.add_handler(CallbackQueryHandler(go_to_settings_from_startup_callback, pattern="^go_to_settings_from_startup$"))
    application.add_handler(CallbackQueryHandler(sync_now_callback, pattern="^sync_now$"))
    
    # --- Notification Settings ---
    application.add_handler(CallbackQueryHandler(settings_notifications_callback, pattern="^settings_notifications$"))
    application.add_handler(CallbackQueryHandler(toggle_notifications_callback, pattern="^toggle_notifications$"))
    application.add_handler(CallbackQueryHandler(add_notification_admin_callback, pattern="^add_notification_admin$"))
    application.add_handler(CallbackQueryHandler(remove_notification_admin_callback, pattern="^remove_notification_admin$"))
    application.add_handler(CallbackQueryHandler(confirm_remove_admin_callback, pattern="^confirm_remove_admin\|"))

    # --- Failover Policy Management ---
    application.add_handler(CallbackQueryHandler(copy_monitoring_confirm_callback, pattern="^copy_monitoring_confirm\|"))
    application.add_handler(CallbackQueryHandler(settings_failover_policies_callback, pattern="^settings_failover_policies$"))
    application.add_handler(CallbackQueryHandler(failover_policy_view_callback, pattern="^failover_policy_view\|"))
    application.add_handler(CallbackQueryHandler(failover_policy_edit_callback, pattern="^failover_policy_edit\|"))
    application.add_handler(CallbackQueryHandler(failover_policy_add_start_callback, pattern="^failover_policy_add_start$"))
    application.add_handler(CallbackQueryHandler(failover_policy_delete_callback, pattern="^failover_policy_delete\|"))
    application.add_handler(CallbackQueryHandler(failover_policy_delete_confirm_callback, pattern="^failover_policy_delete_confirm\|"))
    application.add_handler(CallbackQueryHandler(failover_policy_edit_field_callback, pattern="^failover_policy_edit_field\|"))
    application.add_handler(CallbackQueryHandler(failover_policy_toggle_callback, pattern="^failover_policy_toggle\|"))
    application.add_handler(CallbackQueryHandler(failover_policy_toggle_failback_callback, pattern="^failover_policy_toggle_failback\|"))
    application.add_handler(CallbackQueryHandler(failover_policy_set_account_callback, pattern="^failover_policy_set_account\|"))
    application.add_handler(CallbackQueryHandler(failover_policy_set_zone_callback, pattern="^failover_policy_set_zone\|"))

    # --- Load Balancer Policy Management ---
    application.add_handler(CallbackQueryHandler(settings_lb_policies_callback, pattern="^settings_lb_policies$"))
    application.add_handler(CallbackQueryHandler(lb_policy_view_callback, pattern="^lb_policy_view\|"))
    application.add_handler(CallbackQueryHandler(lb_policy_edit_callback, pattern="^lb_policy_edit\|"))
    application.add_handler(CallbackQueryHandler(lb_policy_add_start_callback, pattern="^lb_policy_add_start$"))
    application.add_handler(CallbackQueryHandler(lb_policy_delete_callback, pattern="^lb_policy_delete\|"))
    application.add_handler(CallbackQueryHandler(lb_policy_delete_confirm_callback, pattern="^lb_policy_delete_confirm\|"))
    application.add_handler(CallbackQueryHandler(lb_policy_edit_field_callback, pattern="^lb_policy_edit_field\|"))
    application.add_handler(CallbackQueryHandler(lb_policy_toggle_callback, pattern="^lb_policy_toggle\|"))
    application.add_handler(CallbackQueryHandler(lb_policy_set_account_callback, pattern="^lb_policy_set_account\|"))
    application.add_handler(CallbackQueryHandler(lb_policy_set_zone_callback, pattern="^lb_policy_set_zone\|"))

        # --- Shared/Generic Policy Handlers (Nodes & Records Selection) ---
    application.add_handler(CallbackQueryHandler(policy_force_update_nodes_callback, pattern="^policy_force_update_nodes$"))
    application.add_handler(CallbackQueryHandler(policy_edit_nodes_start_callback, pattern="^policy_edit_nodes\|"))
    application.add_handler(CallbackQueryHandler(policy_country_page_callback, pattern="^policy_country_page\|"))
    application.add_handler(CallbackQueryHandler(policy_select_country_callback, pattern="^policy_select_country\|"))
    application.add_handler(CallbackQueryHandler(policy_nodes_page_callback, pattern="^policy_nodes_page\|"))
    application.add_handler(CallbackQueryHandler(policy_toggle_node_callback, pattern="^policy_toggle_node\|"))
    application.add_handler(CallbackQueryHandler(policy_confirm_nodes_callback, pattern="^policy_confirm_nodes$"))  
    application.add_handler(CallbackQueryHandler(policy_nodes_select_all_global_callback, pattern="^policy_nodes_select_all_global$"))
    application.add_handler(CallbackQueryHandler(policy_nodes_clear_all_global_callback, pattern="^policy_nodes_clear_all_global$"))    
    application.add_handler(CallbackQueryHandler(policy_records_page_callback, pattern="^policy_records_page\|"))
    application.add_handler(CallbackQueryHandler(policy_select_record_callback, pattern="^policy_select_record\|"))
    application.add_handler(CallbackQueryHandler(policy_confirm_records_callback, pattern="^policy_confirm_records$"))
    application.add_handler(CallbackQueryHandler(policy_set_failback_callback, pattern="^policy_set_failback\|"))
    
    # --- Message Handlers (Should be last) ---
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.Document.MimeType("application/json"), handle_document))
    
    logger.info("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()
