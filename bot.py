import os
import json
import httpx
import asyncio
import logging
import socket
import ipaddress
import random
import pickle
import html
import check_host
import copy
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, error
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, PicklePersistence
)
from helpers import (
    load_translations,
    get_text, get_user_lang, load_config, save_config, 
    send_or_edit, escape_html, send_notification
)

# --- Persistent Log File Management ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "monitoring_log.json")

def load_monitoring_log():
    """Loads the monitoring log from its dedicated JSON file, handling corrupted files."""
    if not os.path.exists(LOG_FILE):
        return []
    
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content:
                logger.warning(f"{LOG_FILE} is empty. Returning empty list.")
                return []
            return json.loads(content)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Could not read or decode {LOG_FILE}: {e}. The file seems corrupted.")
        
        try:
            corrupted_backup_path = f"{LOG_FILE}.corrupted.{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            os.rename(LOG_FILE, corrupted_backup_path)
            logger.warning(f"Corrupted log file has been backed up to: {corrupted_backup_path}")
        except Exception as backup_e:
            logger.error(f"Could not back up or remove corrupted log file: {backup_e}")
        return []

def save_monitoring_log(log_data):
    """Saves the monitoring log to its dedicated JSON file."""
    try:

        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.error(f"Could not write to {LOG_FILE}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred in save_monitoring_log: {e}", exc_info=True)

# --- Initial Setup ---
load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

try:
    tz_str = os.getenv("TIMEZONE", "UTC")
    USER_TIMEZONE = ZoneInfo(tz_str)
except Exception:
    USER_TIMEZONE = ZoneInfo("UTC")

health_check_lock = asyncio.Lock()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

raw_super_admin_ids = os.getenv("TELEGRAM_ADMIN_IDS", "").split(',')
SUPER_ADMIN_IDS = {int(admin_id.strip()) for admin_id in raw_super_admin_ids if admin_id.strip().isdigit()}

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
ZONES_PER_PAGE = 10

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
    if not country_code or len(country_code) != 2:
        return "🏳️" 
    offset = 127397
    return chr(ord(country_code[0].upper()) + offset) + chr(ord(country_code[1].upper()) + offset)

# --- Helper Functions ---
CONFIG_FILE = "config.json"

async def send_or_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None, parse_mode="HTML", force_new_message: bool = False):
    """
    Unified helper to send or edit messages, correctly handling real, dummy, and text-based updates.
    """
    chat_id = update.effective_chat.id
    query = update.callback_query
    
    if getattr(query, 'is_dummy', False):
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"Failed to send message for a dummy update: {e}", exc_info=True)
        return

    if force_new_message:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"Failed to send new message (forced): {e}", exc_info=True)
        return

    if query and query.message:
        try:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except error.BadRequest as e:
            if "Message is not modified" in str(e):
                try: await query.answer()
                except Exception: pass
            else:
                logger.warning(f"Failed to edit message: {e}. Falling back to sending a new message.")
                await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"An unexpected error occurred while trying to edit a message: {e}", exc_info=True)
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        return

    try:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"Failed to send message as a fallback: {e}", exc_info=True)

def escape_html(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    return html.escape(text)

def normalize_ip_list(ip_list: list) -> list:
    normalized = []
    if not ip_list:
        return []
    for item in ip_list:
        if isinstance(item, str):
            normalized.append({"ip": item, "weight": 1})
        elif isinstance(item, dict) and "ip" in item:
            normalized.append({"ip": item["ip"], "weight": item.get("weight", 1)})
    return normalized

def is_valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False
    
def load_config():
    try:
        if not os.path.exists(CONFIG_FILE):
            logger.info(f"{CONFIG_FILE} not found. Creating a new one.")
            default_config = {
                "notifications": {"enabled": True, "recipients": {"__default__": []}},
                "failover_policies": [],
                "load_balancer_policies": [],
                "admins": [],
                "zone_aliases": {},
                "record_aliases": {},
                "log_retention_days": 30,
                "monitoring_groups": {},
                "standalone_monitors": []
            }
            save_config(default_config)
            return default_config

        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        migrated = False
        
        notifications = config.setdefault("notifications", {"enabled": True})
        if "chat_ids" in notifications or "notification_groups" in config:
            logger.warning("Old notification config structure detected. Starting automatic migration...")
            migrated = True
            
            recipients_map = notifications.setdefault("recipients", {})
            
            if "chat_ids" in notifications:
                default_list = set(recipients_map.get("__default__", []))
                default_list.update(notifications["chat_ids"])
                recipients_map["__default__"] = sorted(list(default_list))
                del notifications["chat_ids"]

            if "notification_groups" in config:
                notification_groups = config.get("notification_groups", {})
                all_items = config.get("failover_policies", []) + config.get("load_balancer_policies", []) + config.get("standalone_monitors", [])
                for item in all_items:
                    group_name = item.pop("notification_group", None)
                    if group_name and group_name in notification_groups:
                        item_name = item.get("policy_name") or item.get("monitor_name")
                        if item_name:
                            prefix = "__policy__" if "policy_name" in item else "__monitor__"
                            recipient_key = f"{prefix}{item_name}"
                            item_list = set(recipients_map.get(recipient_key, []))
                            item_list.update(notification_groups[group_name])
                            recipients_map[recipient_key] = sorted(list(item_list))
                del config["notification_groups"]
            
            logger.info("Notification config migration completed successfully.")

        config.setdefault("monitoring_groups", {})
        def find_or_create_group(nodes, threshold):
            if not nodes: return None
            for name, data in config["monitoring_groups"].items():
                if set(data.get("nodes", [])) == set(nodes) and data.get("threshold") == threshold:
                    return name
            new_group_name = f"migrated_group_{len(config['monitoring_groups']) + 1}"
            config["monitoring_groups"][new_group_name] = {"nodes": nodes, "threshold": threshold}
            logger.info(f"Migration: Created new monitoring group '{new_group_name}'.")
            return new_group_name

        for policy in config.get("failover_policies", []):
            if "primary_monitoring_nodes" in policy and "primary_monitoring_group" not in policy:
                migrated = True
                group_name = find_or_create_group(policy.get("primary_monitoring_nodes"), policy.get("primary_threshold"))
                if group_name: policy["primary_monitoring_group"] = group_name
                policy.pop("primary_monitoring_nodes", None)
                policy.pop("primary_threshold", None)
            if "backup_monitoring_nodes" in policy and "backup_monitoring_group" not in policy:
                migrated = True
                group_name = find_or_create_group(policy.get("backup_monitoring_nodes"), policy.get("backup_threshold"))
                if group_name: policy["backup_monitoring_group"] = group_name
                policy.pop("backup_monitoring_nodes", None)
                policy.pop("backup_threshold", None)

        for policy in config.get("load_balancer_policies", []):
            if "monitoring_nodes" in policy and "monitoring_group" not in policy:
                migrated = True
                group_name = find_or_create_group(policy.get("monitoring_nodes"), policy.get("threshold"))
                if group_name: policy["monitoring_group"] = group_name
                policy.pop("monitoring_nodes", None)
                policy.pop("threshold", None)

        if "admins" not in config: config["admins"] = []; migrated = True
        if "zone_aliases" not in config: config["zone_aliases"] = {}; migrated = True
        if "record_aliases" not in config: config["record_aliases"] = {}; migrated = True
        if "log_retention_days" not in config: config["log_retention_days"] = 30; migrated = True
        config.setdefault("standalone_monitors", [])

        if migrated:
            logger.info("Migration process finished. Saving updated configuration file.")
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
    return "@" if full_name == zone_name else full_name.removesuffix(f".{zone_name}")

def is_admin(update: Update) -> bool:
    user_id = update.effective_user.id
    if user_id in SUPER_ADMIN_IDS:
        return True
    config = load_config()
    if not config:
        return False
    admin_list = config.get("admins", [])
    return user_id in admin_list

def is_super_admin(update: Update) -> bool:
    return update.effective_user.id in SUPER_ADMIN_IDS

def chunk_list(lst, n): return [lst[i:i + n] for i in range(0, len(lst), n)]

def get_current_token(context: ContextTypes.DEFAULT_TYPE):
    return CF_ACCOUNTS.get(context.user_data.get('selected_account_nickname'))

def reset_policy_health_status(context: ContextTypes.DEFAULT_TYPE, policy_name: str):
    if 'health_status' in context.bot_data and policy_name in context.bot_data['health_status']:
        logger.info(f"Resetting health status for policy '{policy_name}' due to config change.")
        del context.bot_data['health_status'][policy_name]

async def set_bot_commands(application: Application):
    commands = [
        BotCommand("start", "▶️ Start Bot / شروع مجدد ربات"),
        BotCommand("wizard", "🚀 Setup Wizard / راه‌اندازی سریع"),
        BotCommand("language", "🌐 Change Language / تغییر زبان"),
        BotCommand("list", "🗂️ List Domains & Records / لیست دامنه‌ها و رکوردها"),
        BotCommand("search", "🔎 Search for a Record / جستجوی رکورد"),
        BotCommand("status", "📊 Get System Status / دریافت وضعیت سیستم"),
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
    logger.info("--- [JOB] Starting scheduled Check-Host.net node list update ---")
    
    nodes_data = await check_host.get_nodes()
    if not nodes_data:
        logger.error("[JOB] Failed to fetch updated node list from Check-Host.net.")
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
async def clear_commands_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A temporary command to forcefully delete all bot commands."""
    if not is_super_admin(update): return
    try:
        await context.bot.delete_my_commands()
        await update.message.reply_text("All bot commands have been forcefully deleted from all scopes.")
        logger.info(f"User {update.effective_user.id} cleared all bot commands.")
    except Exception as e:
        await update.message.reply_text(f"Failed to delete commands: {e}")
        logger.error(f"Failed to delete commands: {e}")

async def api_request(token: str, method: str, url: str, **kwargs):
    if not token:
        return {"success": False, "errors": [{"message": "No API token selected."}]}
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
        if not res.get("success"):
            logger.error(f"API request failed for get_dns_records on zone {zone_id}, page {page}.")
            break
        data = res.get("result", [])
        if not data:
            break
        all_records.extend(data)
        result_info = res.get('result_info', {})
        if result_info.get('page', 1) >= result_info.get('total_pages', 1):
            break
        page += 1
        if page > 100: 
            logger.warning("get_dns_records exceeded 100 pages, breaking loop.")
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
    policy_name = policy.get('policy_name', 'Unnamed Policy')
    logger.info(f"DNS SWITCH: For policy '{policy_name}', ensuring records point to '{to_ip}'.")
    
    account_nickname = policy.get('account_nickname')
    token = CF_ACCOUNTS.get(account_nickname)
    if not token:
        logger.error(f"DNS SWITCH FAILED for '{policy_name}': No token for account '{account_nickname}'.")
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
                logger.error(f"DNS SWITCH FAILED for '{record['name']}'. Reason: {error_msg}")

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

async def get_ip_health_with_cache(context: ContextTypes.DEFAULT_TYPE, ip: str, check_details: dict) -> tuple[bool, bool]:
    """
    Checks IP health using the details provided in the check_details dictionary.
    """
    if not ip:
        return True, True

    is_online = True
    service_failed = False

    check_type = check_details.get('check_type', 'tcp')
    port = check_details.get('check_port')
    nodes = check_details.get('nodes', [])
    threshold = check_details.get('threshold')

    if not port:
        logger.warning(f"No check_port defined for IP {ip}. Assuming online.")
        return True, True

    use_advanced_monitoring = bool(nodes and threshold)

    if use_advanced_monitoring:
        logger.info(f"IP '{ip}' advanced {check_type.upper()} check started...")
        check_results = None
        
        if check_type == 'http':
            path = check_details.get('check_path', '/')
            protocol = 'https' if check_details.get('check_protocol', 'https') != 'http' else 'http'
            check_results = await check_host.perform_http_check(ip, port, path, protocol, nodes)
        else:
            check_results = await check_host.perform_check(ip, port, nodes)
        
        if check_results is None:
            logger.warning(f"Could not get check results for {ip}. Assuming online as a fail-safe.")
            is_online = True
            service_failed = True
        else:
            failures = sum(1 for result in check_results.values() if not result)
            is_online = failures < threshold
            logger.info(f"IP '{ip}' check complete. Failures: {failures}/{len(nodes)}. Threshold: {threshold}. Online: {is_online}")
    else: 
        is_online = await check_ip_health(ip, port)
        logger.info(f"IP '{ip}' simple check complete. Online: {is_online}")

    return is_online, service_failed

async def gather_all_ips_to_check(config: dict) -> dict:
    """
    Gathers all unique IPs that need a health check from all policies and monitors.
    Returns a dictionary of check details keyed by IP address.
    """
    unique_checks = {}
    all_policies = config.get("load_balancer_policies", []) + config.get("failover_policies", [])
    monitoring_groups = config.get("monitoring_groups", {})

    # Process IPs from Failover and Load Balancer policies
    for policy in all_policies:
        if not policy.get('enabled', True):
            continue

        is_failover = 'primary_ip' in policy
        
        ips_in_policy = []
        if is_failover:
            ips_in_policy.append(policy.get('primary_ip'))
            ips_in_policy.extend(policy.get('backup_ips', []))
        else:
            ips_in_policy.extend([item['ip'] for item in normalize_ip_list(policy.get('ips', []))])

        for ip in set(filter(None, ips_in_policy)):
            if ip in unique_checks:
                continue
            
            group_name = None
            if is_failover:
                group_name = policy.get('primary_monitoring_group') if ip == policy.get('primary_ip') else policy.get('backup_monitoring_group')
            else:
                group_name = policy.get('monitoring_group')
                
            if group_name and group_name in monitoring_groups:
                group_data = monitoring_groups[group_name]
                unique_checks[ip] = {
                    "ip": ip,
                    "check_port": policy.get("check_port"),
                    "check_type": policy.get("check_type", "tcp"),
                    "check_path": policy.get("check_path", "/"),
                    "check_protocol": policy.get("check_protocol", "https"),
                    "nodes": group_data.get("nodes", []),
                    "threshold": group_data.get("threshold", 1)
                }

    # Process IPs from Standalone Monitors
    standalone_monitors = config.get("standalone_monitors", [])
    for monitor in standalone_monitors:
        if not monitor.get('enabled', True):
            continue
            
        ip_to_check = monitor.get('ip')
        group_name = monitor.get('monitoring_group')
        
        if ip_to_check and group_name and group_name in monitoring_groups and ip_to_check not in unique_checks:
            group_data = monitoring_groups[group_name]
            unique_checks[ip_to_check] = {
                "ip": ip_to_check,
                "check_port": monitor.get("check_port"),
                "check_type": monitor.get("check_type", "tcp"),
                "check_path": monitor.get("check_path", "/"),
                "check_protocol": monitor.get("check_protocol", "https"),
                "nodes": group_data.get("nodes", []),
                "threshold": group_data.get("threshold", 1)
            }

    return unique_checks

async def perform_health_checks(context: ContextTypes.DEFAULT_TYPE, unique_checks: dict) -> tuple[dict, bool]:
    """
    Performs concurrent health checks for all provided IPs.
    Returns a dictionary of health results and a boolean indicating if any monitoring service failed.
    """
    checks_to_perform = list(unique_checks.values())
    tasks = [get_ip_health_with_cache(context, check['ip'], check) for check in checks_to_perform]
    
    logger.info(f"Dispatching {len(tasks)} health checks concurrently...")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    health_results = {}
    service_failure_detected = False
    
    for i, check in enumerate(checks_to_perform):
        ip = check['ip']
        if isinstance(results[i], Exception):
            health_results[ip] = True 
            service_failure_detected = True
            logger.error(f"Health check for IP {ip} failed with an exception: {results[i]}", exc_info=isinstance(results[i], Exception))
        else:
            is_online, service_failed_for_ip = results[i]
            health_results[ip] = is_online
            if service_failed_for_ip:
                service_failure_detected = True

    return health_results, service_failure_detected

async def health_check_job(context: ContextTypes.DEFAULT_TYPE):
    async with health_check_lock:
        load_translations()
        monitoring_log = load_monitoring_log()
        
        try:
            logger.info("--- [HEALTH CHECK] Job Started ---")
            config = load_config()
            if config is None:
                logger.error("--- [HEALTH CHECK] HALTED: Config file is corrupted."); return
            
            # STAGE 0: Gather IPs and perform all health checks at once
            unique_checks = await gather_all_ips_to_check(config)
            if not unique_checks:
                logger.info("--- [HEALTH CHECK] No policies or monitors to check. Job Finished ---")
                return

            health_results, service_failure_detected_in_job = await perform_health_checks(context, unique_checks)

            context.bot_data['last_health_results'] = health_results
            context.bot_data['last_health_check_time'] = datetime.now()
            now_iso = datetime.now().isoformat()
            for ip, is_online in health_results.items():
                monitoring_log.append({
                    "timestamp": now_iso, "event_type": "IP_STATUS",
                    "ip": ip, "status": "UP" if is_online else "DOWN"
                })

            raw_super_admin_ids = os.getenv("TELEGRAM_ADMIN_IDS", "").split(',')
            SUPER_ADMIN_IDS = {int(admin_id.strip()) for admin_id in raw_super_admin_ids if admin_id.strip().isdigit()}
            if service_failure_detected_in_job:
                new_failure_count = context.bot_data.get('check_host_failure_count', 0) + 1
                context.bot_data['check_host_failure_count'] = new_failure_count
                logger.warning(f"Check-Host service failed for one or more IPs. Failure count is now: {new_failure_count}.")
                if new_failure_count == 3:
                    await send_notification(context, SUPER_ADMIN_IDS, 'messages.check_host_api_down_alert', add_settings_button=True)
            elif context.bot_data.get('check_host_failure_count', 0) > 0:
                context.bot_data['check_host_failure_count'] = 0
                logger.info("Check-Host service has recovered.")

            if 'health_status' not in context.bot_data: context.bot_data['health_status'] = {}
            status_data = context.bot_data['health_status']
            lb_active_ips_map = {}
            recipients_map = config.setdefault("notifications", {}).setdefault("recipients", {})
            recipients_map.setdefault("__default__", [])

            # === STAGE 1: PROCESS LOAD BALANCER POLICIES ===
            logger.info("--- [HEALTH CHECK] Stage 1: Processing Load Balancer Policies ---")
            lb_policies = [p for p in config.get("load_balancer_policies", []) if p.get('enabled', True)]
            for policy in lb_policies:
                policy_name = policy.get('policy_name', 'Unnamed LB')
                if not policy.get('enabled', True):
                    continue

                if policy.get('maintenance_mode', False):
                    logger.info(f"Policy '{policy_name}' is in maintenance mode. Skipping rotations.")
                    status_data.setdefault(policy_name, {})['active_ip'] = get_text('messages.status_in_maintenance', 'fa')
                    continue
    
                else:
                    current_status = status_data.get(policy_name, {}).get('active_ip')
                if current_status == get_text('messages.status_in_maintenance', 'fa'):
                    logger.info(f"Policy '{policy_name}' exited maintenance mode. Clearing status.")
                    status_data[policy_name].pop('active_ip', None)
                record_names = policy.get('record_names', [])
                zone_name = policy.get('zone_name')
                policy_status = status_data.setdefault(policy_name, {'lb_next_rotation_time': None})
                
                healthy_lb_ips_with_weights = [ip_info for ip_info in normalize_ip_list(policy.get('ips',[])) if health_results.get(ip_info['ip'], True)]
                
                if not healthy_lb_ips_with_weights:
                    logger.warning(f"All IPs for LB policy '{policy_name}' are down! No action taken.")
                    policy_status['lb_next_rotation_time'] = None
                    policy_status['active_ip'] = "All Down"
                    continue

                actual_ip_on_cf = None
                token = CF_ACCOUNTS.get(policy.get('account_nickname'))
                if token and zone_name and record_names:
                    zones = await get_all_zones(token)
                    zone_id = next((z['id'] for z in zones if z['name'] == zone_name), None)
                    if zone_id:
                        all_dns_records = await get_dns_records(token, zone_id)
                        actual_record = next((r for r in all_dns_records if get_short_name(r['name'], zone_name) in record_names), None)
                        if actual_record: actual_ip_on_cf = actual_record['content']

                if not actual_ip_on_cf:
                    logger.warning(f"Could not determine current IP for LB policy '{policy_name}'. Assuming first healthy IP.")
                    actual_ip_on_cf = healthy_lb_ips_with_weights[0]['ip']

                logger.info(f"LB POLICY '{policy_name}': Checking. Current IP on CF: {actual_ip_on_cf}")
                logger.info(f"LB POLICY '{policy_name}': Healthy IPs in pool: {[ip['ip'] for ip in healthy_lb_ips_with_weights]}")
                
                now = datetime.now()
                next_rotation_time_str = policy_status.get('lb_next_rotation_time')
                next_rotation_time = datetime.fromisoformat(next_rotation_time_str) if next_rotation_time_str else None
                logger.info(f"LB POLICY '{policy_name}': Now: {now}, Next Scheduled Rotation: {next_rotation_time}")

                time_to_rotate = False
                if next_rotation_time:
                    if now >= next_rotation_time:
                        time_to_rotate = True
                else:
                    time_to_rotate = True

                ip_to_set = actual_ip_on_cf
                if time_to_rotate:
                    rotation_algo = policy.get('rotation_algorithm', 'random')
                    logger.info(f"Rotation triggered for LB '{policy_name}' using '{rotation_algo}' algorithm.")
                    chosen_ip = None
                    if rotation_algo == 'round_robin':
                        policy_status.setdefault('wrr_state', {})
                        wrr_state = policy_status['wrr_state']
                        total_weight = sum(ip_info['weight'] for ip_info in healthy_lb_ips_with_weights)
                        if total_weight > 0:
                            for ip_info in healthy_lb_ips_with_weights:
                                ip = ip_info['ip']
                                wrr_state[ip] = wrr_state.get(ip, 0) + ip_info['weight']
                            best_server_ip, max_weight = None, -1
                            for ip_info in healthy_lb_ips_with_weights:
                                ip = ip_info['ip']
                                if wrr_state.get(ip, 0) > max_weight:
                                    max_weight, best_server_ip = wrr_state[ip], ip
                            if best_server_ip:
                                wrr_state[best_server_ip] -= total_weight
                                chosen_ip = best_server_ip
                    else:
                        selection_pool = [ip['ip'] for ip in healthy_lb_ips_with_weights for _ in range(ip['weight'])]
                        if selection_pool: chosen_ip = random.choice(selection_pool)

                    logger.info(f"LB POLICY '{policy_name}': Algorithm chose IP: {chosen_ip}")
                    
                    if chosen_ip and actual_ip_on_cf != chosen_ip:
                        monitoring_log.append({
                            "timestamp": now_iso,
                            "event_type": "LB_ROTATION",
                            "policy_name": policy_name,
                            "from_ip": actual_ip_on_cf,
                            "to_ip": chosen_ip
                        })
                        await switch_dns_ip(context, policy, to_ip=chosen_ip)
                        ip_to_set = chosen_ip
                        logger.info(f"Switched DNS for '{policy_name}' to choice: {chosen_ip}")
                    elif chosen_ip:
                        logger.info(f"Algorithm choice resulted in the same IP ({chosen_ip}). No DNS change needed.")

                    min_h, max_h = policy.get('rotation_min_hours', 1.0), policy.get('rotation_max_hours', policy.get('rotation_min_hours', 1.0))
                    delay = random.uniform(min_h * 3600, max_h * 3600)
                    next_rotation = now + timedelta(seconds=delay)
                    policy_status['lb_next_rotation_time'] = next_rotation.isoformat()
                    logger.info(f"Next rotation for '{policy_name}' scheduled for {next_rotation.strftime('%Y-%m-%d %H:%M:%S')}.")
                    
                policy_status['active_ip'] = ip_to_set

                for rec_name in record_names:
                    lb_active_ips_map[(zone_name, rec_name)] = ip_to_set

            # === STAGE 2: PROCESS FAILOVER POLICIES ===
            logger.info("--- [HEALTH CHECK] Stage 2: Processing Failover Policies ---")
            failover_policies = [p for p in config.get("failover_policies", []) if p.get('enabled', True)]
            for policy in failover_policies:
                policy_name = policy.get('policy_name', 'Unnamed')
                if not policy.get('enabled', True):
                    continue

                if policy.get('maintenance_mode', False):
                    logger.info(f"Policy '{policy_name}' is in maintenance mode. Skipping alerts and actions.")
                    status_data.setdefault(policy_name, {})['active_ip'] = get_text('messages.status_in_maintenance', 'fa')
                    status_data[policy_name].pop('downtime_start', None)
                    status_data[policy_name].pop('uptime_start', None)
                    continue
                record_names = policy.get('record_names', [])
                zone_name = policy.get('zone_name')
                record_key = (zone_name, record_names[0]) if record_names else None
                is_hybrid_mode = record_key in lb_active_ips_map

                if is_hybrid_mode:
                    effective_primary_ip = lb_active_ips_map[record_key]
                    is_lb_ip_online = health_results.get(effective_primary_ip, True)
                    if not is_lb_ip_online:
                        logger.warning(f"HYBRID FAILOVER: Active LB IP '{effective_primary_ip}' for '{policy_name}' is DOWN. Switching to Failover backups.")
                        backup_ips = policy.get('backup_ips', [])
                        next_healthy_backup = next((ip for ip in backup_ips if health_results.get(ip, True)), None)
                        if next_healthy_backup:
                            monitoring_log.append({
                                "timestamp": now_iso,
                                "event_type": "FAILOVER",
                                "policy_name": policy_name,
                                "from_ip": effective_primary_ip,
                                "to_ip": next_healthy_backup,
                                "mode": "hybrid"
                            })
                            await switch_dns_ip(context, policy, to_ip=next_healthy_backup)
                            
                            recipients_to_notify = set(SUPER_ADMIN_IDS)
                            recipient_key = f"__policy__{policy_name}"
                            if recipient_key in recipients_map: recipients_to_notify.update(recipients_map[recipient_key])
                            else: recipients_to_notify.update(recipients_map["__default__"])
                            
                            await send_notification(context, recipients_to_notify, 'messages.failover_notification_message', policy_name=policy_name, from_ip=effective_primary_ip, to_ip=next_healthy_backup, add_settings_button=True)
                else:
                    primary_ip = policy.get('primary_ip')
                    backup_ips = policy.get('backup_ips', [])
                    if not all([primary_ip, backup_ips, record_names, zone_name]):
                        continue
                    
                    policy_status = status_data.setdefault(policy_name, {'critical_alert_sent': False, 'uptime_start': None, 'downtime_start': None})
                    
                    actual_ip_on_cf = None
                    token = CF_ACCOUNTS.get(policy.get('account_nickname'))
                    if token:
                        zones = await get_all_zones(token)
                        zone_id = next((z['id'] for z in zones if z['name'] == zone_name), None)
                        if zone_id:
                            all_dns_records = await get_dns_records(token, zone_id)
                            if all_dns_records is not None:
                                actual_record = next((r for r in all_dns_records if get_short_name(r['name'], zone_name) in record_names), None)
                                if actual_record:
                                    actual_ip_on_cf = actual_record['content']
                    
                    if not actual_ip_on_cf:
                        logger.warning(f"Could not determine current IP for Failover policy '{policy_name}'. Skipping.")
                        continue

                    is_primary_online = health_results.get(primary_ip, True)
                    
                    recipients_to_notify = set(SUPER_ADMIN_IDS)
                    recipient_key = f"__policy__{policy_name}"
                    if recipient_key in recipients_map: recipients_to_notify.update(recipients_map[recipient_key])
                    else: recipients_to_notify.update(recipients_map["__default__"])

                    if is_primary_online:
                        if policy_status.get('downtime_start') or policy_status.get('critical_alert_sent'):
                             await send_notification(context, recipients_to_notify, 'messages.server_recovered_notification', policy_name=policy_name, ip=primary_ip)
                        
                        policy_status['downtime_start'] = None
                        policy_status['critical_alert_sent'] = False

                        if actual_ip_on_cf != primary_ip:
                            is_on_valid_backup = actual_ip_on_cf in backup_ips
                            
                            if not policy.get('auto_failback', True):
                                policy_status.pop('uptime_start', None)
                                logger.info(f"Primary IP for '{policy_name}' is online, but auto-failback is disabled. No action taken.")
                            
                            elif is_on_valid_backup:
                                if not policy_status.get('uptime_start'):
                                    policy_status['uptime_start'] = datetime.now().isoformat()
                                    await send_notification(context, recipients_to_notify, 'messages.failback_alert_notification', 
                                                            policy_name=policy_name, primary_ip=primary_ip, 
                                                            failback_minutes=policy.get('failback_minutes', 5.0))
                                
                                uptime_dt = datetime.fromisoformat(policy_status['uptime_start'])
                                if (datetime.now() - uptime_dt) >= timedelta(minutes=policy.get('failback_minutes', 5.0)):
                                    logger.info(f"FAILBACK TRIGGERED for '{policy_name}'. Switching to primary IP {primary_ip}.")
                                    monitoring_log.append({"timestamp": now_iso, "event_type": "FAILBACK", "policy_name": policy_name, "to_ip": primary_ip})
                                    await switch_dns_ip(context, policy, to_ip=primary_ip)
                                    await send_notification(context, recipients_to_notify, 'messages.failback_executed_notification', 
                                                            policy_name=policy_name, primary_ip=primary_ip, add_settings_button=True)
                                    policy_status.pop('uptime_start', None)
                            
                            else:
                                logger.warning(f"SELF-HEALING: DNS for '{policy_name}' points to an invalid IP ({actual_ip_on_cf}) while primary is online. Correcting immediately.")
                                monitoring_log.append({"timestamp": now_iso, "event_type": "FAILBACK", "policy_name": policy_name, "to_ip": primary_ip, "mode": "self-heal"})
                                await switch_dns_ip(context, policy, to_ip=primary_ip)
                                policy_status.pop('uptime_start', None)
                        
                        else:
                            policy_status.pop('uptime_start', None)

                    else:
                        policy_status.pop('uptime_start', None)
                        is_current_ip_online = health_results.get(actual_ip_on_cf, True)
                        is_on_valid_backup = actual_ip_on_cf in backup_ips

                        if is_current_ip_online and not is_on_valid_backup and actual_ip_on_cf != primary_ip:
                            logger.warning(f"SELF-HEALING: DNS for '{policy_name}' points to an invalid IP ({actual_ip_on_cf}) while primary is offline. Finding a valid backup.")
                            is_current_ip_online = False 

                        if is_current_ip_online and is_on_valid_backup:
                            logger.info(f"Policy '{policy_name}' is stable on backup IP {actual_ip_on_cf}.")
                            policy_status['downtime_start'] = None
                            policy_status['critical_alert_sent'] = False
                        
                        else:
                            if actual_ip_on_cf == primary_ip and not policy_status.get('downtime_start'):
                                policy_status['downtime_start'] = datetime.now().isoformat()
                                await send_notification(context, recipients_to_notify, 'messages.server_alert_notification', 
                                                        add_settings_button=True, policy_name=policy_name, ip=primary_ip)
                            
                            downtime_start_str = policy_status.get('downtime_start')
                            if downtime_start_str:
                                downtime_dt = datetime.fromisoformat(downtime_start_str)
                                elapsed_seconds = (datetime.now() - downtime_dt).total_seconds()
                                required_seconds = policy.get("failover_minutes", 2.0) * 60
                                if elapsed_seconds < required_seconds:
                                    logger.info(f"'{policy_name}' is still within its grace period. Waiting...")
                                    continue

                            next_healthy_backup = next((ip for ip in backup_ips if health_results.get(ip, True)), None)

                            if next_healthy_backup:
                                if next_healthy_backup != actual_ip_on_cf:
                                    logger.warning(f"FAILOVER TRIGGERED for '{policy_name}'. Switching from {actual_ip_on_cf} to {next_healthy_backup}.")
                                    monitoring_log.append({"timestamp": now_iso, "event_type": "FAILOVER", "policy_name": policy_name, "from_ip": actual_ip_on_cf, "to_ip": next_healthy_backup, "mode": "standard"})
                                    await switch_dns_ip(context, policy, to_ip=next_healthy_backup)
                                    await send_notification(context, recipients_to_notify, 'messages.failover_notification_message', 
                                                            add_settings_button=True, policy_name=policy_name, from_ip=actual_ip_on_cf, to_ip=next_healthy_backup)
                                policy_status['downtime_start'] = None
                                policy_status['critical_alert_sent'] = False
                            
                            else:
                                if not policy_status.get('critical_alert_sent', False):
                                    logger.error(f"CRITICAL ALERT for '{policy_name}': Primary and all backup IPs are down.")
                                    await send_notification(context, recipients_to_notify, 'messages.failover_notification_all_down', 
                                                            add_settings_button=True, policy_name=policy_name, 
                                                            primary_ip=primary_ip, backup_ips=", ".join(backup_ips))
                                    policy_status['critical_alert_sent'] = True
                    pass

            # === STAGE 3: PROCESS STANDALONE MONITORS AND SEND ALERTS ===
            logger.info("--- [HEALTH CHECK] Stage 3: Processing Standalone Monitors ---")
            if 'monitor_status' not in context.bot_data:
                context.bot_data['monitor_status'] = {}

            standalone_monitors = config.get("standalone_monitors", [])
            recipients_map = config.setdefault("notifications", {}).setdefault("recipients", {})
            recipients_map.setdefault("__default__", [])

            for monitor in standalone_monitors:
                if not monitor.get('enabled', True): continue
                
                monitor_name = monitor.get("monitor_name")
                ip = monitor.get("ip")
                port = monitor.get("check_port")
                
                if not all([monitor_name, ip, port]):
                    continue

                is_currently_online = health_results.get(ip, True)
                was_previously_online = context.bot_data['monitor_status'].get(monitor_name, True)

                if (is_currently_online and not was_previously_online) or (not is_currently_online and was_previously_online):
                    message_key = 'messages.monitor_up_alert' if is_currently_online else 'messages.monitor_down_alert'
                    recipients_to_notify = set(SUPER_ADMIN_IDS)
                    recipient_key = f"__monitor__{monitor_name}"

                    logger.info(f"--- Building recipients for monitor '{monitor_name}' ---")
                    logger.info(f"  - Looking for specific key: '{recipient_key}'")
                    
                    if recipient_key in recipients_map:
                        logger.info(f"  - SUCCESS: Found specific list. Members: {recipients_map[recipient_key]}")
                        recipients_to_notify.update(recipients_map[recipient_key])
                    else:
                        logger.info(f"  - FAILURE: Key not found. Falling back to default. Members: {recipients_map['__default__']}")
                        recipients_to_notify.update(recipients_map["__default__"])
                    
                    logger.info(f"  - FINAL LIST to notify for '{monitor_name}': {recipients_to_notify}")
                    await send_notification(context, recipients_to_notify, message_key, 
                                            monitor_name=monitor_name, ip=ip, port=port)
                
                context.bot_data['monitor_status'][monitor_name] = is_currently_online

        except Exception as e:
            logger.error(f"!!! [HEALTH CHECK] An unhandled exception in health_check_job !!!", exc_info=True)
        
        finally:
            logger.info("--- [HEALTH CHECK] Finalizing job and saving logs. ---")
            save_monitoring_log(monitoring_log)
            try:
                await context.application.persistence.flush()
            except Exception as e:
                logger.error(f"Failed to flush persistence at the end of health check job: {e}")
            logger.info("--- [HEALTH CHECK] Job Finished ---")

async def manual_health_check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Performs an on-demand health check for a specific policy or monitor."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    await query.edit_message_text(get_text('messages.manual_check_in_progress', lang))

    try:
        _, item_type, item_index_str = query.data.split('|')
        item_index = int(item_index_str)
        
        config = load_config()
        ips_to_check = []
        policy_name = "N/A"

        if item_type == 'monitor':
            policy = config['standalone_monitors'][item_index]
            policy_name = policy.get("monitor_name", "N/A")
            ip = policy.get('ip')
            if ip:
                ips_to_check.append({'ip': ip, 'group': policy.get('monitoring_group')})
        else:
            policy_list_key = "load_balancer_policies" if item_type == 'lb' else "failover_policies"
            policy = config[policy_list_key][item_index]
            policy_name = policy.get("policy_name", "N/A")
            
            if item_type == 'failover':
                primary_ip = policy.get('primary_ip')
                if primary_ip:
                    ips_to_check.append({'ip': primary_ip, 'group': policy.get('primary_monitoring_group')})
                for backup_ip in policy.get('backup_ips', []):
                    ips_to_check.append({'ip': backup_ip, 'group': policy.get('backup_monitoring_group')})
            else:
                for item in normalize_ip_list(policy.get('ips', [])):
                    ips_to_check.append({'ip': item['ip'], 'group': policy.get('monitoring_group')})

        if not ips_to_check:
            await query.edit_message_text(get_text('messages.manual_check_no_ips', lang))
            return

        monitoring_groups = config.get("monitoring_groups", {})
        results = []
        
        for ip_info in ips_to_check:
            ip = ip_info.get('ip')
            if not ip: continue

            group_name = ip_info.get('group')
            check_details = {"check_port": policy.get("check_port")}
            
            if group_name and group_name in monitoring_groups:
                group_data = monitoring_groups[group_name]
                check_details.update({"nodes": group_data.get("nodes", []), "threshold": group_data.get("threshold", 1)})
            elif group_name:
                logger.warning(f"Monitoring group '{group_name}' not found for '{policy_name}'. Performing a simple check.")

            is_online, _ = await get_ip_health_with_cache(context, ip, check_details)
            status_text = get_text('messages.manual_check_status_online', lang) if is_online else get_text('messages.manual_check_status_offline', lang)
            results.append(get_text('messages.manual_check_result_item', lang, ip=ip, status=status_text))

        result_text = get_text('messages.manual_check_results_header', lang, policy_name=escape_html(policy_name)) + "\n".join(results)
        
        buttons = []
        if item_type == 'monitor':
            back_to_source_callback = "monitors_menu"
            back_to_source_text_key = 'buttons.back_to_list'
        else:
            back_to_source_callback = f"{item_type}_policy_view|{item_index}"
            back_to_source_text_key = 'buttons.back_to_policy_details'
        
        buttons.append([InlineKeyboardButton(get_text(back_to_source_text_key, lang), callback_data=back_to_source_callback)])
        
        buttons.append([InlineKeyboardButton(get_text('buttons.back_to_status_list', lang), callback_data="status_refresh")])
        
        await query.edit_message_text(result_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

    except (IndexError, KeyError, ValueError) as e:
        logger.error(f"A session/key error in manual_health_check_callback: {e}", exc_info=True)
        await query.edit_message_text(get_text('messages.session_expired_error', lang))
    except Exception as e:
        logger.error(f"Error in manual_health_check_callback: {e}", exc_info=True)
        await query.edit_message_text(get_text('messages.manual_check_error', lang))

def clean_old_monitoring_logs(context: ContextTypes.DEFAULT_TYPE):
    """Removes old monitoring log entries based on the configured retention period."""
    config = load_config()
    retention_days = config.get("log_retention_days", 30)
    
    if retention_days <= 0:
        return
    
    log = load_monitoring_log()
    if not log:
        return
    
    cutoff_date = datetime.now() - timedelta(days=retention_days)

    recent_logs = [entry for entry in log if datetime.fromisoformat(entry['timestamp']) >= cutoff_date]
    
    if len(recent_logs) < len(log):
        save_monitoring_log(recent_logs)
        logger.info(f"Cleaned up {len(log) - len(recent_logs)} old monitoring log entries (older than {retention_days} days).")

async def send_daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    """Generates and sends the daily monitoring report to all admins."""
    logger.info("--- [JOB] Generating and sending daily report... ---")
    
    clean_old_monitoring_logs()
    log = load_monitoring_log()
    now = datetime.now()
    cutoff_date = now - timedelta(hours=24)
    recent_events = [e for e in log if datetime.fromisoformat(e['timestamp']) >= cutoff_date]
    
    failover_events = [e for e in recent_events if e['event_type'] == 'FAILOVER']
    failback_events = [e for e in recent_events if e['event_type'] == 'FAILBACK']
    lb_rotations = [e for e in recent_events if e['event_type'] == 'LB_ROTATION']
    
    lb_duration_stats = {}
    if lb_rotations:
        rotations_by_policy = {}
        for event in lb_rotations:
            policy_name = event.get('policy_name')
            if not policy_name: continue
            if policy_name not in rotations_by_policy:
                rotations_by_policy[policy_name] = []
            rotations_by_policy[policy_name].append(event)

        for policy_name, events in rotations_by_policy.items():
            if not events: continue
            events.sort(key=lambda e: e['timestamp'])
            
            ip_durations_seconds = {}
            
            involved_ips = set()
            for event in events:
                involved_ips.add(event.get('from_ip'))
                involved_ips.add(event.get('to_ip'))

            for ip in involved_ips:
                if not ip: continue
                total_active_time = timedelta()

                for i, event in enumerate(events):
                    if event.get('to_ip') == ip:
                        start_time = datetime.fromisoformat(event['timestamp'])
                        end_time = now
                        if i + 1 < len(events):
                            end_time = datetime.fromisoformat(events[i+1]['timestamp'])
                        
                        start_time = max(start_time, cutoff_date)
                        end_time = min(end_time, now)

                        if end_time > start_time:
                            total_active_time += (end_time - start_time)

                initial_events = [e for e in log if e.get('policy_name') == policy_name and e.get('event_type') == 'LB_ROTATION' and datetime.fromisoformat(e['timestamp']) < cutoff_date]
                if initial_events:
                    active_ip_at_start = initial_events[-1].get('to_ip')
                    if active_ip_at_start == ip:
                        first_event_time_in_window = events[0]['timestamp']
                        duration = datetime.fromisoformat(first_event_time_in_window) - cutoff_date
                        if duration.total_seconds() > 0:
                            total_active_time += duration
                
                if total_active_time.total_seconds() > 0:
                    ip_durations_seconds[ip] = total_active_time.total_seconds()

            total_duration_seconds = sum(ip_durations_seconds.values())
            if total_duration_seconds > 0:
                lb_duration_stats[policy_name] = {
                    ip: {
                        "hours": seconds / 3600,
                        "percent": (seconds / total_duration_seconds) * 100
                    } for ip, seconds in ip_durations_seconds.items()
                }

    uptime_stats = {}
    ip_status_events = [e for e in recent_events if e['event_type'] == 'IP_STATUS']

    if ip_status_events:
        unique_ips_in_log = sorted(list(set(e['ip'] for e in ip_status_events)))
        for ip in unique_ips_in_log:
            ip_specific_log = [e for e in ip_status_events if e['ip'] == ip]
            if ip_specific_log:
                up_checks = sum(1 for e in ip_specific_log if e['status'] == 'UP')
                uptime_percent = (up_checks / len(ip_specific_log)) * 100
                uptime_stats[ip] = f"{uptime_percent:.2f}"

    config = load_config()
    all_admin_ids = set(config.get("notifications", {}).get("chat_ids", [])) | SUPER_ADMIN_IDS
    user_data = await context.application.persistence.get_user_data()

    for chat_id in all_admin_ids:
        lang = user_data.get(chat_id, {}).get('language', 'fa')
        message_parts = [get_text('messages.daily_report_header', lang)]
        
        if not failover_events and not lb_rotations and not failback_events:
            message_parts.append(get_text('messages.daily_report_no_events', lang))
        else:
            message_parts.append(get_text('messages.daily_report_summary', lang, failovers=len(failover_events), failbacks=len(failback_events), rotations=len(lb_rotations)))
            if failover_events:
                message_parts.append(get_text('messages.daily_report_failover_header', lang))
                for event in failover_events:
                    safe_event = {k: escape_html(str(v)) for k, v in event.items()}
                    message_parts.append(get_text('messages.daily_report_failover_entry', lang, **safe_event))

        if lb_duration_stats:
            message_parts.append(get_text('messages.report_lb_duration_header', lang))
            for policy_name, stats in sorted(lb_duration_stats.items()):
                message_parts.append(get_text('messages.report_lb_duration_entry', lang, policy_name=escape_html(policy_name)))
                for ip, data in sorted(stats.items(), key=lambda item: item[1]['percent'], reverse=True):
                    message_parts.append(get_text('messages.report_lb_duration_ip_entry', lang, ip=ip, hours=data['hours'], percent=data['percent']))

        if uptime_stats:
            message_parts.append(get_text('messages.report_uptime_header', lang))
            for ip, uptime_percent in sorted(uptime_stats.items()):
                message_parts.append(get_text('messages.report_uptime_entry', lang, ip=ip, uptime_percent=uptime_percent))
        
        full_message = "\n".join(message_parts)
        try:
            await context.bot.send_message(chat_id=chat_id, text=full_message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to send daily report to {chat_id}: {e}")

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
    except error.BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error("A BadRequest error occurred in display_records_for_selection", exc_info=True)

# --- Node Selection Display Logic ---
NODES_PER_PAGE = 12

async def display_countries_for_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    query = update.callback_query
    lang = get_user_lang(context)
    
    now = datetime.now()
    nodes_last_updated = context.bot_data.get('nodes_last_updated')
    should_refresh_nodes = 'all_nodes' not in context.bot_data or \
                           not nodes_last_updated or \
                           (now - nodes_last_updated) > timedelta(hours=24)

    if should_refresh_nodes:
        await send_or_edit(update, context, get_text('messages.fetching_locations_message', lang))
        nodes_data = await check_host.get_nodes()
        if not nodes_data:
            await send_or_edit(update, context, get_text('messages.fetching_locations_error', lang))
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
    
    buttons = []
    
    selected_nodes = context.user_data.get('policy_selected_nodes', [])
    header_text = f"<b>Selected Nodes:</b>\n"
    if selected_nodes:
        selected_cities = []
        for node_id in sorted(selected_nodes)[:10]:
            node_info = context.bot_data.get('all_nodes', {}).get(node_id, {})
            city = escape_html(node_info.get('city', 'Unknown'))
            country = escape_html(node_info.get('country', 'Unknown'))
            selected_cities.append(f"<code>{city} ({country})</code>")
        header_text += ", ".join(selected_cities)
        if len(selected_nodes) > 10:
            header_text += f" ...and {len(selected_nodes) - 10} more."
    else:
        header_text += "<i>None</i>"
    
    message_text = f"{header_text}\n\n" + get_text('messages.select_country_message', lang)

    action_buttons = [
        InlineKeyboardButton(get_text('buttons.select_all_nodes', lang), callback_data="policy_nodes_select_all_global"),
        InlineKeyboardButton(get_text('buttons.clear_all_nodes', lang), callback_data="policy_nodes_clear_all_global")
    ]
    buttons.append(action_buttons)
    buttons.append([InlineKeyboardButton(get_text('buttons.force_update_nodes', lang), callback_data="policy_force_update_nodes")])

    start_index = page * NODES_PER_PAGE
    end_index = start_index + NODES_PER_PAGE
    page_countries = country_codes[start_index:end_index]

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

    if policy_type == 'group':
        back_button_callback = "groups_menu"
        back_button_text = get_text('buttons.back_to_list', lang)
    else:
        policy_index = context.user_data.get('edit_policy_index')
        if policy_index is None:
            await send_or_edit(update, context, get_text('messages.session_expired_error', lang))
            return
        back_button_callback = f"lb_policy_edit|{policy_index}" if policy_type == 'lb' else f"failover_policy_edit|{policy_index}"
        back_button_text = get_text('buttons.back_to_edit_menu_button', lang)
    
    buttons.append([InlineKeyboardButton(back_button_text, callback_data=back_button_callback)])
    
    await send_or_edit(update, context, message_text, InlineKeyboardMarkup(buttons))

NODES_PER_PAGE = 12

async def display_nodes_for_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, country_code: str, page: int = 0):
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    countries = context.bot_data.get('countries', {})
    country_info = countries.get(country_code)
    if not country_info:
        await send_or_edit(update, context, get_text('messages.internal_error', lang)); return

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

    header_text = f"<b>Selected Nodes:</b>\n"
    if selected_nodes:
        selected_cities = []
        for node_id in sorted(selected_nodes)[:10]:
            node_info = context.bot_data.get('all_nodes', {}).get(node_id, {})
            city = escape_html(node_info.get('city', 'Unknown'))
            country = escape_html(node_info.get('country', 'Unknown'))
            selected_cities.append(f"<code>{city} ({country})</code>")
        header_text += ", ".join(selected_cities)
        if len(selected_nodes) > 10:
            header_text += f" ...and {len(selected_nodes) - 10} more."
    else:
        header_text += "<i>None</i>"
    
    message_text = f"{header_text}\n\n" + get_text('messages.select_nodes_message', lang, country_name=f"<b>{escape_html(country_info['name'])}</b>")

    await send_or_edit(update, context, message_text, InlineKeyboardMarkup(buttons))

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

async def display_zones_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    query = update.callback_query
    lang = get_user_lang(context)
    token = get_current_token(context)

    if not token:
        await display_account_list(update, context)
        return

    if 'all_zones_cache' not in context.user_data:
        await send_or_edit(update, context, get_text('messages.fetching_zones', lang))
        zones = await get_all_zones(token)
        if not zones:
            msg = get_text('messages.no_zones_found', lang)
            buttons = [[InlineKeyboardButton(get_text('buttons.back_to_accounts', lang), callback_data="back_to_accounts")]]
            await send_or_edit(update, context, msg, reply_markup=InlineKeyboardMarkup(buttons))
            return
        context.user_data['all_zones_cache'] = zones
    
    all_zones = context.user_data['all_zones_cache']
    context.user_data['all_zones'] = {z['id']: z for z in all_zones}

    config = load_config()
    aliases = config.get('zone_aliases', {})
    
    sorted_zones = sorted(all_zones, key=lambda z: aliases.get(z['id'], z['name']))

    start_index = page * ZONES_PER_PAGE
    end_index = start_index + ZONES_PER_PAGE
    zones_on_page = sorted_zones[start_index:end_index]

    buttons = []
    for zone in zones_on_page:
        zone_id = zone['id']
        zone_name = zone['name']
        alias = aliases.get(zone_id)
        button_text = alias if alias else zone_name
        buttons.append([
            InlineKeyboardButton(button_text, callback_data=f"select_zone|{zone_id}"),
            InlineKeyboardButton(get_text('buttons.set_zone_alias', lang), callback_data=f"set_alias_start|{zone_id}")
        ])

    pagination_buttons = []
    if page > 0:
        pagination_buttons.append(InlineKeyboardButton(get_text('buttons.previous', lang), callback_data=f"zones_page|{page - 1}"))
    if end_index < len(sorted_zones):
        pagination_buttons.append(InlineKeyboardButton(get_text('buttons.next', lang), callback_data=f"zones_page|{page + 1}"))
    
    if pagination_buttons:
        buttons.append(pagination_buttons)

    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_accounts', lang), callback_data="back_to_accounts")])
    
    text = get_text('messages.choose_zone', lang)
    await send_or_edit(update, context, text, reply_markup=InlineKeyboardMarkup(buttons))

async def display_records_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    lang = get_user_lang(context)
    token = get_current_token(context)
    zone_id, zone_name = context.user_data.get('selected_zone_id'), context.user_data.get('selected_zone_name')
    if not token or not zone_id:
        await list_records_command(update, context); return

    context.user_data['current_page'] = page
    records_cache = context.user_data.get('records_list_cache', {})
    records = records_cache.get('data', [])
    cache_time = records_cache.get('timestamp')

    if not records or not cache_time or (datetime.now() - cache_time) > timedelta(minutes=5):
        await send_or_edit(update, context, get_text('messages.fetching_records', lang))
        records = await get_dns_records(token, zone_id)
        context.user_data['records_list_cache'] = {'data': records, 'timestamp': datetime.now()}
    context.user_data['all_records'] = records
    
    config = load_config()
    record_aliases = config.get('record_aliases', {}).get(zone_id, {})

    monitored_records_failover = set()
    for policy in config.get('failover_policies', []):
        if policy.get('zone_name') == zone_name:
            for record_name in policy.get('record_names', []):
                monitored_records_failover.add(record_name)

    monitored_records_lb = set()
    for policy in config.get('load_balancer_policies', []):
        if policy.get('zone_name') == zone_name:
            for record_name in policy.get('record_names', []):
                monitored_records_lb.add(record_name)

    records_in_view = context.user_data.get('records_in_view', context.user_data.get('all_records', []))
    search_query, search_ip_query = context.user_data.get('search_query'), context.user_data.get('search_ip_query')

    header_text = f"<i>{escape_html(context.user_data.get('selected_account_nickname', 'N/A'))}</i> / <code>{escape_html(zone_name or '')}</code>"
    message_text = get_text('messages.all_records_list', lang)
    if search_query: message_text = get_text('messages.search_results', lang, query=escape_html(search_query))
    elif search_ip_query: message_text = get_text('messages.search_results_ip', lang, query=f"<code>{escape_html(search_ip_query)}</code>")
    
    buttons = []
    if not records_in_view:
        buttons.extend([
            [InlineKeyboardButton(get_text('buttons.add_record', lang), callback_data="add")],
            [InlineKeyboardButton(get_text('buttons.back_to_zones', lang), callback_data="back_to_zones")]
        ])
        msg_text = get_text('messages.no_records_found_search' if (search_query or search_ip_query) else 'messages.no_records_found', lang)
        await send_or_edit(update, context, f"{header_text}\n\n{msg_text}", InlineKeyboardMarkup(buttons))
        return

    context.user_data["records"] = {r['id']: r for r in context.user_data.get('all_records', [])}
    records_on_page = records_in_view[page * RECORDS_PER_PAGE:(page + 1) * RECORDS_PER_PAGE]
    is_bulk_mode, selected_records = context.user_data.get('is_bulk_mode', False), context.user_data.get('selected_records', [])

    if is_bulk_mode:
        all_ids_in_view = {r['id'] for r in records_in_view}
        select_all_text = get_text('buttons.deselect_all' if all_ids_in_view and all_ids_in_view.issubset(set(selected_records)) else 'buttons.select_all', lang)
        buttons.append([InlineKeyboardButton(select_all_text, callback_data=f"bulk_select_all|{page}")])

    for r in records_on_page:
        short_name = get_short_name(r['name'], zone_name)
        
        status_icons = []
        if short_name in monitored_records_failover:
            status_icons.append("🛡️")
        if short_name in monitored_records_lb:
            status_icons.append("🚦")
        
        icons_str = "".join(status_icons)

        proxy_icon = "☁️" if r.get('proxied') else "⬜️"
        check_icon = "✅" if is_bulk_mode and r['id'] in selected_records else "▫️"
        
        alias_key = f"{r['type']}:{r['name']}"
        alias = record_aliases.get(alias_key)
        alias_display = f" ({escape_html(alias)})" if alias else ""
        
        button_text = f"{icons_str} {check_icon if is_bulk_mode else proxy_icon} {r['type']} {short_name}{alias_display}"
        callback_data = f"bulk_select|{r['id']}|{page}" if is_bulk_mode else f"select|{r['id']}"
        buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    pagination_buttons = []
    if page > 0: pagination_buttons.append(InlineKeyboardButton(get_text('buttons.previous', lang), callback_data=f"list_page|{page - 1}"))
    if (page + 1) * RECORDS_PER_PAGE < len(records_in_view): pagination_buttons.append(InlineKeyboardButton(get_text('buttons.next', lang), callback_data=f"list_page|{page + 1}"))
    if pagination_buttons: buttons.append(pagination_buttons)
    
    if is_bulk_mode:
        count = len(selected_records)
        buttons.append([
            InlineKeyboardButton(get_text('buttons.change_ip_selected', lang, count=count), callback_data="bulk_change_ip_start"), 
            InlineKeyboardButton(get_text('buttons.delete_selected', lang, count=count), callback_data="bulk_delete_confirm")
        ])
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
    await send_or_edit(update, context, f"{header_text}\n\n{message_text}", InlineKeyboardMarkup(buttons))

# --- Callback Handlers ---
async def clone_policy_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of cloning a policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        _, policy_type, policy_index_str = query.data.split('|')
        
        context.user_data['clone_info'] = {
            'type': policy_type,
            'index': int(policy_index_str)
        }
        
        context.user_data['state'] = 'awaiting_clone_name'
        
        text = get_text('prompts.enter_clone_name', lang)
        
        back_callback = f"{policy_type}_policy_view|{policy_index_str}"
        buttons = [[InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data=back_callback)]]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        context.user_data['clone_start_message_id'] = query.message.message_id

    except (IndexError, ValueError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))

async def toggle_maintenance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles the maintenance mode for a specific policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        _, policy_type, policy_index_str = query.data.split('|')
        policy_index = int(policy_index_str)
        
        config = load_config()
        policy_list_key = "load_balancer_policies" if policy_type == 'lb' else "failover_policies"
        
        policy = config[policy_list_key][policy_index]
        policy_name = policy.get('policy_name', 'N/A')
        
        current_status = policy.get('maintenance_mode', False)
        new_status = not current_status
        policy['maintenance_mode'] = new_status
        
        save_config(config)

        if not new_status:
            if 'health_status' in context.bot_data and policy_name in context.bot_data['health_status']:
                context.bot_data['health_status'][policy_name].pop('active_ip', None)
                logger.info(f"Cleared cached status for '{policy_name}' after exiting maintenance mode.")
        
        if new_status:
            await query.answer(get_text('messages.maintenance_mode_enabled', lang, policy_name=policy_name), show_alert=True)
        else:
            await query.answer(get_text('messages.maintenance_mode_disabled', lang, policy_name=policy_name), show_alert=True)
        
        if policy_type == 'lb':
            await lb_policy_view_callback(update, context)
        else:
            await failover_policy_view_callback(update, context)

    except (IndexError, KeyError, ValueError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))

async def send_test_alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text("Sending a test alert to default recipients...")
    await send_notification(context, 'messages.welcome', add_settings_button=True)

async def zones_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles pagination for the zones list."""
    query = update.callback_query
    await query.answer()
    page = int(query.data.split('|')[1])
    await display_zones_list(update, context, page=page)

async def get_chat_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Replies with the current chat's ID."""
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    
    message = (
        f"ℹ️ The ID for this chat is:\n\n"
        f"👉 <code>{chat_id}</code> 👈\n\n"
        f"Chat Type: {chat_type}"
    )
    
    if chat_type in ["group", "supergroup"]:
        message += "\n\nSince this is a group, you can add this negative ID to any notification group to receive alerts here."
        
    await update.message.reply_text(message, parse_mode="HTML")

async def set_notification_group_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Displays a list of available notification groups for the user to assign to a
    specific item (monitor, failover policy, or lb policy).
    """
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)

    try:
        _, item_type, item_index_str = query.data.split('|')
        item_index = int(item_index_str)
        
        context.user_data['set_notification_group_context'] = {'type': item_type, 'index': item_index}
    except (ValueError, IndexError):
        await query.edit_message_text(get_text('messages.internal_error', lang))
        return

    config = load_config()
    groups = config.get("notification_groups", {})
    
    buttons = [[InlineKeyboardButton(name, callback_data=f"set_notification_group_execute|{name}")] for name in sorted(groups.keys())]
    
    buttons.append([InlineKeyboardButton("🔕 None (Use Default)", callback_data="set_notification_group_execute|__NONE__")])
    
    if item_type == 'monitor':
        back_cb = f"monitor_edit|{item_index}"
    elif item_type == 'failover':
        back_cb = f"failover_policy_edit|{item_index}"
    else:
        back_cb = f"lb_policy_edit|{item_index}"
        
    buttons.append([InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data=back_cb)])
    
    await query.edit_message_text(
        get_text('messages.select_notification_group_prompt', lang),
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def set_notification_group_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Saves the selected notification group to the corresponding item's configuration
    and returns the user to the item's edit menu.
    """
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)

    try:
        group_name = query.data.split('|', 1)[1]
        
        ctx = context.user_data.pop('set_notification_group_context')
        item_type, item_index = ctx['type'], ctx['index']
    except (IndexError, KeyError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))
        return

    config = load_config()
    
    list_key_map = {
        'monitor': 'standalone_monitors',
        'failover': 'failover_policies',
        'lb': 'load_balancer_policies'
    }
    list_key = list_key_map.get(item_type)

    if not list_key:
        await query.edit_message_text(get_text('messages.internal_error', lang))
        return

    try:
        if group_name == '__NONE__':
            if 'notification_group' in config[list_key][item_index]:
                del config[list_key][item_index]['notification_group']
            await query.answer(get_text('messages.notification_group_cleared', lang), show_alert=True)
        else:
            config[list_key][item_index]['notification_group'] = group_name
            await query.answer(get_text('messages.notification_group_set_success', lang, group_name=escape_html(group_name)), show_alert=True)
        
        save_config(config)
    except (IndexError, KeyError):
        await query.edit_message_text(get_text('messages.error_generic_request', lang))
        return

    if item_type == 'monitor':
        await monitor_edit_menu_callback(update, context)
    elif item_type == 'failover':
        await failover_policy_edit_callback(update, context)
    else:
        await lb_policy_edit_callback(update, context)

async def monitor_purge_old_ip_logs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes all IP_STATUS events for a specific IP from the monitoring log."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        ip_to_purge = query.data.split('|', 1)[1]
    except IndexError:
        await query.edit_message_text(get_text('messages.internal_error', lang))
        return

    log = load_monitoring_log()
    
    new_log = [event for event in log if not (event.get('event_type') == 'IP_STATUS' and event.get('ip') == ip_to_purge)]
    
    if len(new_log) < len(log):
        save_monitoring_log(new_log)
        await query.answer(get_text('messages.monitor_logs_purged', lang, old_ip=escape_html(ip_to_purge)), show_alert=True)
    else:
        await query.answer("No logs found for the old IP.", show_alert=True)

    monitor_index = context.user_data.get('edit_monitor_index')
    if monitor_index is not None:
        await monitor_edit_menu_callback(update, context)
    else:
        await monitors_menu_callback(update, context)

async def policy_add_step_ask_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the list of monitoring groups for the user to choose when adding a new policy."""
    lang = get_user_lang(context)
    config = load_config()
    groups = config.get("monitoring_groups", {})

    if not groups:
        await send_or_edit(update, context, get_text('messages.no_groups_for_selection', lang))
        for key in ['add_policy_step', 'new_policy_data', 'add_policy_type']:
            context.user_data.pop(key, None)
        return

    buttons = [[InlineKeyboardButton(name, callback_data=f"policy_add_select_group|{name}")] for name in sorted(groups.keys())]
    buttons.append([InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="back_to_settings_main")])
    
    text = get_text('messages.policy_add_ask_group', lang)
    await send_or_edit(update, context, text, InlineKeyboardMarkup(buttons))

async def policy_add_select_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves the selected group and creates the new policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        group_name = query.data.split('|', 1)[1]
        policy_data = context.user_data['new_policy_data']
        policy_type = context.user_data['add_policy_type']
    except (IndexError, KeyError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))
        return
        
    config = load_config()

    if policy_type == 'failover':
        policy_data['primary_monitoring_group'] = group_name
        policy_data['backup_monitoring_group'] = group_name
        policy_data.setdefault('auto_failback', True)
        policy_data.setdefault('failback_minutes', 5.0)
        config.setdefault('failover_policies', []).append(policy_data)
        
    else:
        policy_data['monitoring_group'] = group_name
        config.setdefault('load_balancer_policies', []).append(policy_data)
            
    save_config(config)
    
    for key in ['add_policy_step', 'new_policy_data', 'add_policy_type', 'last_callback_query']:
        context.user_data.pop(key, None)
        
    await query.edit_message_text(get_text('messages.policy_added_successfully', lang, name=escape_html(policy_data['policy_name'])), parse_mode="HTML")
    await asyncio.sleep(2)
    
    if policy_type == 'failover':
        await settings_failover_policies_callback(update, context)
    else:
        await settings_lb_policies_callback(update, context)

async def policy_change_group_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of changing the monitoring group for any policy type."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    monitoring_type = query.data.split('|', 1)[1]
    context.user_data['monitoring_type'] = monitoring_type
    
    config = load_config()
    groups = config.get("monitoring_groups", {})

    if not groups:
        await query.answer(get_text('messages.no_groups_for_selection', lang), show_alert=True)
        return

    policy_type = context.user_data.get('editing_policy_type')
    policy_index = context.user_data.get('edit_policy_index')
    back_button_cb = f"{policy_type}_policy_edit|{policy_index}"

    buttons = [[InlineKeyboardButton(name, callback_data=f"policy_change_group_execute|{name}")] for name in sorted(groups.keys())]
    buttons.append([InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=back_button_cb)])
    
    text = get_text('messages.select_monitoring_group_prompt', lang, monitoring_type=monitoring_type)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def policy_change_group_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves the new monitoring group for the policy and removes old fields."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        new_group_name = query.data.split('|', 1)[1]
        policy_type = context.user_data['editing_policy_type']
        policy_index = context.user_data['edit_policy_index']
        monitoring_type = context.user_data['monitoring_type']
        
        config = load_config()
        
        if policy_type == 'failover':
            policy = config['failover_policies'][policy_index]
            group_field_name = f"{monitoring_type}_monitoring_group"
            nodes_field_name = f"{monitoring_type}_monitoring_nodes"
            threshold_field_name = f"{monitoring_type}_threshold"
            
            policy[group_field_name] = new_group_name
            policy.pop(nodes_field_name, None)
            policy.pop(threshold_field_name, None)

        else:
            policy = config['load_balancer_policies'][policy_index]
            policy['monitoring_group'] = new_group_name
            policy.pop('monitoring_nodes', None)
            policy.pop('threshold', None)
            
        save_config(config)
        
        await query.answer(get_text('messages.monitoring_group_updated', lang, monitoring_type=monitoring_type, group_name=new_group_name), show_alert=True)
    except (IndexError, KeyError):
        await query.answer(get_text('messages.session_expired_error', lang), show_alert=True)
        return

    if policy_type == 'failover':
        await failover_policy_edit_callback(update, context)
    else:
        await lb_policy_edit_callback(update, context)

async def policy_change_group_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves the new monitoring group for the policy and removes old fields."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        new_group_name = query.data.split('|', 1)[1]
        policy_type = context.user_data['editing_policy_type']
        policy_index = context.user_data['edit_policy_index']
        monitoring_type = context.user_data['monitoring_type']
        
        config = load_config()
        
        if policy_type == 'failover':
            policy = config['failover_policies'][policy_index]
            group_field_name = f"{monitoring_type}_monitoring_group"
            nodes_field_name = f"{monitoring_type}_monitoring_nodes"
            threshold_field_name = f"{monitoring_type}_threshold"
            
            policy[group_field_name] = new_group_name
            policy.pop(nodes_field_name, None)
            policy.pop(threshold_field_name, None)

        else:
            policy = config['load_balancer_policies'][policy_index]
            policy['monitoring_group'] = new_group_name
            policy.pop('monitoring_nodes', None)
            policy.pop('threshold', None)
            
        save_config(config)
        
        await query.answer(get_text('messages.monitoring_group_updated', lang, monitoring_type=monitoring_type, group_name=new_group_name), show_alert=True)
    except (IndexError, KeyError):
        await query.answer(get_text('messages.session_expired_error', lang), show_alert=True)
        return

    if policy_type == 'failover':
        await failover_policy_edit_callback(update, context)
    else:
        await lb_policy_edit_callback(update, context)

async def monitor_change_group_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of changing the monitoring group for a monitor."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        monitor_index = int(query.data.split('|')[1])
        context.user_data['edit_monitor_index'] = monitor_index
    except (IndexError, ValueError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))
        return

    config = load_config()
    groups = config.get("monitoring_groups", {})

    if not groups:
        await query.answer(get_text('messages.no_groups_for_selection', lang), show_alert=True)
        return

    buttons = [[InlineKeyboardButton(name, callback_data=f"monitor_change_group_execute|{name}")] for name in sorted(groups.keys())]
    buttons.append([InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"monitor_edit|{monitor_index}")])
    
    text = get_text('messages.monitor_select_new_group', lang)
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def monitor_change_group_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves the new monitoring group for the monitor."""
    query = update.callback_query
    await query.answer()
    
    try:
        new_group_name = query.data.split('|', 1)[1]
        monitor_index = context.user_data['edit_monitor_index']
        
        config = load_config()
        config['standalone_monitors'][monitor_index]['monitoring_group'] = new_group_name
        save_config(config)
        
        await query.answer("✅ Monitoring group updated successfully!", show_alert=True)
    except (IndexError, KeyError):
        await query.answer(get_text('messages.session_expired_error', get_user_lang(context)), show_alert=True)
        await monitors_menu_callback(update, context)
        return

    await monitor_edit_menu_callback(update, context)

async def monitor_step_ask_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the list of monitoring groups for the user to choose during monitor creation."""
    lang = get_user_lang(context)
    config = load_config()
    groups = config.get("monitoring_groups", {})

    if not groups:
        await send_or_edit(update, context, get_text('messages.no_groups_for_selection', lang))
        context.user_data.pop('monitor_add_step', None)
        context.user_data.pop('new_monitor_data', None)
        return

    buttons = [[InlineKeyboardButton(name, callback_data=f"monitor_select_group|{name}")] for name in sorted(groups.keys())]
    buttons.append([InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="monitors_menu")])
    
    text = get_text('messages.monitor_ask_group', lang)
    await send_or_edit(update, context, text, InlineKeyboardMarkup(buttons))


async def monitor_select_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves the selected group, creates the monitor, and finishes the process."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        group_name = query.data.split('|', 1)[1]
    except IndexError:
        await query.edit_message_text(get_text('messages.internal_error', lang))
        return

    monitor_data = context.user_data.get('new_monitor_data')
    if not monitor_data:
        await query.edit_message_text(get_text('messages.session_expired_error', lang))
        return
        
    monitor_data['monitoring_group'] = group_name
    monitor_data['check_type'] = 'tcp'
    monitor_data['enabled'] = True
    
    config = load_config()
    config.setdefault("standalone_monitors", []).append(monitor_data)
    save_config(config)
    
    for key in ['monitor_add_step', 'new_monitor_data', 'last_callback_query']:
        context.user_data.pop(key, None)
        
    await query.edit_message_text(get_text('messages.monitor_created_success', lang))
    await asyncio.sleep(2)
    
    await monitors_menu_callback(update, context)

async def group_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks for confirmation before deleting a monitoring group."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        group_name = query.data.split('|', 1)[1]
    except IndexError:
        await query.edit_message_text(get_text('messages.internal_error', lang))
        return
        
    buttons = [
        [InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"group_delete_execute|{group_name}")],
        [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="groups_menu")]
    ]
    text = get_text('messages.confirm_delete_group', lang, group_name=escape_html(group_name))
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def group_delete_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes a monitoring group from the config."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        group_name = query.data.split('|', 1)[1]
        config = load_config()
        if group_name in config.get("monitoring_groups", {}):
            del config["monitoring_groups"][group_name]
            save_config(config)
            await query.answer(get_text('messages.group_deleted_success', lang, group_name=escape_html(group_name)), show_alert=True)
        else:
            await query.answer("Group not found.", show_alert=True)
    except (IndexError, KeyError):
        await query.answer(get_text('messages.internal_error', lang), show_alert=True)

    await groups_menu_callback(update, context)

async def group_edit_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of editing an existing monitoring group."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)

    try:
        group_name = query.data.split('|', 1)[1]
        config = load_config()
        group_data = config['monitoring_groups'][group_name]
    except (IndexError, KeyError):
        await query.edit_message_text(get_text('messages.internal_error', lang))
        return

    context.user_data['editing_policy_type'] = 'group'
    context.user_data['new_group_name'] = group_name
    context.user_data['policy_selected_nodes'] = group_data.get('nodes', [])

    await query.edit_message_text(get_text('messages.group_edit_prompt_nodes', lang, group_name=escape_html(group_name)), parse_mode="HTML")
    await asyncio.sleep(2)
    
    await display_countries_for_selection(update, context, page=0)

async def group_add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of adding a new monitoring group."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    context.user_data['group_add_step'] = 'ask_name'
    
    buttons = [[InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="groups_menu")]]
    await query.edit_message_text(
        get_text('messages.group_add_prompt_name', lang),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML"
    )

async def groups_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main menu for managing monitoring groups."""
    query = update.callback_query
    if query and not getattr(query, 'is_dummy', False):
        await query.answer()
    lang = get_user_lang(context)
    
    config = load_config()
    groups = config.get("monitoring_groups", {})
    
    buttons = []
    groups_list_text = ""
    if not groups:
        groups_list_text = get_text('messages.no_groups_defined', lang)
    else:
        for group_name, group_data in sorted(groups.items()):
            groups_list_text += get_text('messages.group_list_entry', lang,
                                         group_name=escape_html(group_name),
                                         node_count=len(group_data.get('nodes', [])),
                                         threshold=group_data.get('threshold', 1))
            buttons.append([
                InlineKeyboardButton(f"» {group_name}", callback_data=f"group_edit_start|{group_name}"),
                InlineKeyboardButton(get_text('buttons.edit_group', lang), callback_data=f"group_edit_start|{group_name}"),
                InlineKeyboardButton(get_text('buttons.delete_group', lang), callback_data=f"group_delete_confirm|{group_name}")
            ])
            
    text = get_text('messages.groups_menu_header', lang, groups_list=groups_list_text)
    
    buttons.append([InlineKeyboardButton(get_text('buttons.add_new_group', lang), callback_data="group_add_start")])
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")])
    
    await send_or_edit(update, context, text, InlineKeyboardMarkup(buttons))

    
async def monitor_edit_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    """Displays the edit menu for a specific standalone monitor."""
    query = update.callback_query
    if query and not getattr(query, 'is_dummy', False):
        await query.answer()
    lang = get_user_lang(context)
    
    try:
        if query and "monitor_edit|" in query.data:
            monitor_index = int(query.data.split('|')[1])
        else:
            monitor_index = context.user_data.get('edit_monitor_index')

        if monitor_index is None:
             raise KeyError("Monitor index not found in context or query.")

        config = load_config()
        monitor = config['standalone_monitors'][monitor_index]
        monitor_name = monitor.get('monitor_name', 'N/A')
        
        context.user_data['edit_monitor_index'] = monitor_index

    except (IndexError, ValueError, KeyError):
        await send_or_edit(update, context, get_text('messages.internal_error', lang))
        return

    buttons = [
        [InlineKeyboardButton(get_text('buttons.edit_monitor_name', lang), callback_data=f"monitor_edit_field|monitor_name")],
        [InlineKeyboardButton(get_text('buttons.edit_monitor_address', lang), callback_data=f"monitor_edit_field|ip")],
        [InlineKeyboardButton(get_text('buttons.edit_monitor_port', lang), callback_data=f"monitor_edit_field|check_port")],
        [InlineKeyboardButton(get_text('buttons.edit_monitor_group', lang), callback_data=f"monitor_change_group_start|{monitor_index}")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="monitors_menu")]
    ]
    
    text = get_text('messages.monitor_edit_menu_header', lang, monitor_name=escape_html(monitor_name))
    
    await send_or_edit(update, context, text, InlineKeyboardMarkup(buttons), force_new_message=force_new_message)

async def monitor_edit_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of editing a specific field of a monitor."""
    query = update.callback_query
    context.user_data['last_callback_query'] = query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        _, field_to_edit = query.data.split('|')
        monitor_index = context.user_data['edit_monitor_index']
    except (ValueError, KeyError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))
        return
        
    context.user_data['monitor_edit_step'] = field_to_edit
    
    prompt_map = {
        'monitor_name': 'messages.monitor_ask_name_edit',
        'ip': 'messages.monitor_ask_ip_edit',
        'check_port': 'messages.monitor_ask_port_edit'
    }
    prompt_key = prompt_map.get(field_to_edit)
    
    if not prompt_key:
        await query.edit_message_text("Invalid field to edit.")
        return
        
    buttons = [[InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"monitor_edit|{monitor_index}")]]
    await query.edit_message_text(get_text(prompt_key, lang), reply_markup=InlineKeyboardMarkup(buttons))

async def monitor_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks for confirmation before deleting a standalone monitor."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        monitor_index = int(query.data.split('|')[1])
        config = load_config()
        monitor_name = config['standalone_monitors'][monitor_index]['monitor_name']
    except (IndexError, ValueError, KeyError):
        await query.edit_message_text(get_text('messages.internal_error', lang))
        return
        
    buttons = [
        [InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"monitor_delete_execute|{monitor_index}")],
        [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="monitors_menu")]
    ]
    text = get_text('messages.confirm_delete_monitor', lang, monitor_name=escape_html(monitor_name))
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def monitor_delete_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes a standalone monitor from the config."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        monitor_index = int(query.data.split('|')[1])
        config = load_config()
        monitor = config['standalone_monitors'].pop(monitor_index)
        save_config(config)
        await query.answer(get_text('messages.monitor_deleted_success', lang, monitor_name=escape_html(monitor['monitor_name'])), show_alert=True)
    except (IndexError, ValueError, KeyError):
        await query.answer(get_text('messages.internal_error', lang), show_alert=True)

    await monitors_menu_callback(update, context)

async def monitors_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main menu for standalone monitors with a two-row layout."""
    query = update.callback_query
    if query and not getattr(query, 'is_dummy', False):
        await query.answer()
    lang = get_user_lang(context)
    
    config = load_config()
    monitors = config.get("standalone_monitors", [])
    
    buttons = []
    monitors_list_text = ""

    if not monitors:
        monitors_list_text = get_text('messages.no_standalone_monitors', lang)
    else:
        for i, monitor in enumerate(monitors):
            status_icon = "✅" if monitor.get('enabled', True) else "⚪️"
            monitor_name = escape_html(monitor.get('monitor_name', 'N/A'))

            ip = escape_html(monitor.get('ip', 'N/A'))
            port = escape_html(str(monitor.get('check_port', 'N/A')))
            monitors_list_text += get_text('messages.monitor_list_entry_enabled' if monitor.get('enabled', True) else 'messages.monitor_list_entry', lang,
                                             monitor_name=monitor_name,
                                             ip=ip,
                                             check_port=port)
            
            buttons.append([
                InlineKeyboardButton(f"{status_icon} {monitor_name}", callback_data=f"monitor_view|{i}")
            ])
            
            buttons.append([
                InlineKeyboardButton(get_text('buttons.edit', lang), callback_data=f"monitor_edit|{i}"),
                InlineKeyboardButton(get_text('buttons.manual_check_short', lang), callback_data=f"manual_check|monitor|{i}"),
                InlineKeyboardButton(get_text('buttons.delete', lang), callback_data=f"monitor_delete_confirm|{i}")
            ])
            
    text = get_text('messages.standalone_monitors_menu_header', lang, monitors_list=monitors_list_text)
    
    buttons.append([InlineKeyboardButton(get_text('buttons.add_new_monitor', lang), callback_data="monitor_add_start")])
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")])
    
    await send_or_edit(update, context, text, InlineKeyboardMarkup(buttons))

async def monitor_add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of adding a new standalone monitor."""
    query = update.callback_query
    context.user_data['last_callback_query'] = query
    await query.answer()
    lang = get_user_lang(context)
    
    context.user_data['monitor_add_step'] = 'ask_name'
    context.user_data['new_monitor_data'] = {}
    
    buttons = [[InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="monitors_menu")]]
    await query.edit_message_text(
        get_text('messages.add_monitor_prompt_name', lang),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML"
    )

async def failover_start_backup_monitoring_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'No, configure separately' button to start backup monitoring setup."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = int(query.data.split('|')[1])
    except (IndexError, ValueError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))
        return

    context.user_data['edit_policy_index'] = policy_index
    context.user_data['editing_policy_type'] = 'failover'
    context.user_data['monitoring_type'] = 'backup'
    
    config = load_config()
    policy = config['failover_policies'][policy_index]
    context.user_data['policy_selected_nodes'] = policy.get('backup_monitoring_nodes', [])

    msg = get_text('messages.start_backup_monitoring_setup', lang)
    
    await query.edit_message_text(msg, parse_mode="HTML")
    
    await asyncio.sleep(2)
    
    await display_countries_for_selection(update, context, page=0)

async def set_record_alias_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of setting a record alias."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        rid = query.data.split('|')[1]
        record = context.user_data['records'][rid]
    except (IndexError, KeyError):
        await query.edit_message_text(get_text('messages.internal_error', lang))
        return
        
    prompt_message_id = query.message.message_id
    
    context.user_data['awaiting_record_alias'] = {
        'record_id': rid,
        'record_name': record['name'],
        'record_type': record['type'],
        'prompt_message_id': prompt_message_id 
    }
    
    text = get_text('prompts.set_record_alias_prompt', lang, record_name=escape_html(record['name']))
    await query.edit_message_text(text, parse_mode="HTML")

async def set_alias_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of setting a zone alias."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        zone_id = query.data.split('|')[1]
        zone_name = context.user_data['all_zones'][zone_id]['name']
    except (IndexError, KeyError):
        await query.edit_message_text(get_text('messages.internal_error', lang))
        return
        
    context.user_data['awaiting_zone_alias'] = {
        'zone_id': zone_id,
        'zone_name': zone_name
    }
    context.user_data['last_menu_message_id'] = query.message.message_id
    
    text = get_text('prompts.set_alias_prompt', lang, zone_name=escape_html(zone_name))
    await query.edit_message_text(text, parse_mode="HTML")

async def clear_logs_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks for confirmation before clearing logs."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    buttons = [
        [InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="clear_logs_execute")],
        [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="reporting_menu")]
    ]
    text = get_text('messages.confirm_clear_logs', lang)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def clear_logs_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clears all monitoring logs."""
    query = update.callback_query
    
    save_monitoring_log([])
    
    await query.answer(get_text('messages.logs_cleared_success', get_user_lang(context)), show_alert=True)
    
    await reporting_menu_callback(update, context)

async def log_retention_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays options for setting the log retention period."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    buttons = [
        [InlineKeyboardButton(get_text('messages.log_retention_7_days', lang), callback_data="set_log_retention|7")],
        [InlineKeyboardButton(get_text('messages.log_retention_30_days', lang), callback_data="set_log_retention|30")],
        [InlineKeyboardButton(get_text('messages.log_retention_90_days', lang), callback_data="set_log_retention|90")],
        [InlineKeyboardButton(get_text('messages.log_retention_never', lang), callback_data="set_log_retention|0")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="reporting_menu")]
    ]
    text = get_text('messages.log_retention_menu_header', lang)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def set_log_retention_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves the selected log retention period to the config."""
    query = update.callback_query
    lang = get_user_lang(context)
    
    days = int(query.data.split('|')[1])
    
    config = load_config()
    config['log_retention_days'] = days
    save_config(config)
    
    if days > 0:
        await query.answer(get_text('messages.log_retention_set_success', lang, days=days), show_alert=True)
    else:
        await query.answer(get_text('messages.log_retention_set_never', lang), show_alert=True)
        
    await reporting_menu_callback(update, context)

async def user_management_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if not getattr(query, 'is_dummy', False):
        await query.answer()
    
    if not is_super_admin(update):
        if not getattr(query, 'is_dummy', False):
            await query.answer(get_text('messages.not_a_super_admin', get_user_lang(context)), show_alert=True)
        return
        
    lang = get_user_lang(context)
    config = load_config()
    
    super_admins_str = ", ".join(map(str, sorted(list(SUPER_ADMIN_IDS))))
    
    admins = config.get("admins", [])
    admins_str = ", ".join(map(str, sorted(admins))) if admins else get_text('messages.no_admins_in_config', lang)
    
    text = get_text('messages.user_management_menu_header', lang, super_admins=super_admins_str, admins=admins_str)
    
    buttons = [
        [
            InlineKeyboardButton(get_text('buttons.add_admin_id', lang), callback_data="admin_add_start"),
            InlineKeyboardButton(get_text('buttons.remove_admin_id', lang), callback_data="admin_remove_start")
        ],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")]
    ]
    
    await send_or_edit(update, context, text, reply_markup=InlineKeyboardMarkup(buttons))

async def admin_add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of adding a new bot admin."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    if not is_super_admin(update):
        await query.answer(get_text('messages.not_a_super_admin', lang), show_alert=True)
        return

    context.user_data['awaiting_admin_id_to_add'] = True
    context.user_data['last_menu_message_id'] = query.message.message_id
    
    await query.edit_message_text(get_text('prompts.add_admin_prompt', lang))

async def admin_remove_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows a list of configurable admins to be removed."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)

    if not is_super_admin(update):
        await query.answer(get_text('messages.not_a_super_admin', lang), show_alert=True)
        return

    config = load_config()
    admins = config.get("admins", [])
    
    if not admins:
        await query.answer(get_text('messages.no_admins_in_config', lang), show_alert=True)
        return
        
    buttons = [[InlineKeyboardButton(str(admin_id), callback_data=f"admin_remove_confirm|{admin_id}")] for admin_id in sorted(admins)]
    buttons.append([InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data="user_management_menu")])
    
    await query.edit_message_text(get_text('prompts.remove_admin_prompt', lang), reply_markup=InlineKeyboardMarkup(buttons))

async def admin_remove_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = get_user_lang(context)
    
    if not is_super_admin(update):
        await query.answer(get_text('messages.not_a_super_admin', lang), show_alert=True)
        return
        
    admin_id_to_remove = int(query.data.split('|')[1])
    config = load_config()
    
    if admin_id_to_remove in config.get("admins", []):
        config["admins"].remove(admin_id_to_remove)
        save_config(config)
        await query.answer(get_text('messages.admin_removed_success', lang, user_id=admin_id_to_remove), show_alert=True)
    else:
        await query.answer()

    await user_management_menu_callback(update, context)

# --- Notification Recipient Callbacks ---
async def add_recipient_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of adding a new notification recipient."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    context.user_data['awaiting_recipient_id_to_add'] = True
    context.user_data['last_menu_message_id'] = query.message.message_id
    
    await query.edit_message_text(get_text('prompts.add_recipient_prompt', lang))

async def remove_recipient_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows a list of notification recipients to be removed."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    config = load_config()
    
    recipients = config.get("notifications", {}).get("chat_ids", [])
    
    if not recipients:
        await query.answer(get_text('messages.no_recipients_to_remove', lang), show_alert=True)
        return
        
    buttons = []
    for recipient_id in sorted(recipients):
        buttons.append([InlineKeyboardButton(str(recipient_id), callback_data=f"remove_recipient_confirm|{recipient_id}")])
    
    buttons.append([InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data="settings_notifications")])
    
    await query.edit_message_text(get_text('prompts.remove_recipient_prompt', lang), reply_markup=InlineKeyboardMarkup(buttons))

async def remove_recipient_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes a selected recipient from the config."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    recipient_id_to_remove = int(query.data.split('|')[1])
    config = load_config()
    
    if recipient_id_to_remove in config.get("notifications", {}).get("chat_ids", []):
        config['notifications']['chat_ids'].remove(recipient_id_to_remove)
        save_config(config)
        await query.answer(get_text('messages.recipient_removed_success', lang, user_id=recipient_id_to_remove), show_alert=True)
    
    await show_settings_notifications_menu(update, context)

async def user_management_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not is_super_admin(update):
        await query.answer(get_text('messages.not_a_super_admin', get_user_lang(context)), show_alert=True)
        return
        
    lang = get_user_lang(context)
    config = load_config()
    
    super_admins_str = ", ".join(map(str, sorted(list(SUPER_ADMIN_IDS))))
    
    admins = config.get("admins", [])
    admins_str = ", ".join(map(str, sorted(admins))) if admins else get_text('messages.no_admins_in_config', lang)
    
    text = get_text('messages.user_management_menu_header', lang, super_admins=super_admins_str, admins=admins_str)
    
    buttons = [
        [
            InlineKeyboardButton(get_text('buttons.add_admin_id', lang), callback_data="admin_add_start"),
            InlineKeyboardButton(get_text('buttons.remove_admin_id', lang), callback_data="admin_remove_start")
        ],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def admin_add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    if not is_super_admin(update):
        await query.answer(get_text('messages.not_a_super_admin', lang), show_alert=True)
        return

    context.user_data['awaiting_admin_id_to_add'] = True
    context.user_data['last_menu_message_id'] = query.message.message_id
    
    await query.edit_message_text(get_text('prompts.add_admin_prompt', lang))

async def reporting_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    config = load_config()
    retention_days = config.get("log_retention_days", 30)

    if retention_days > 0:
        retention_text = get_text('buttons.log_retention_period', lang, days=retention_days)
    else:
        retention_text = get_text('buttons.log_retention_never', lang)

    buttons = [
        [InlineKeyboardButton(get_text('messages.report_time_range_1', lang), callback_data="report_generate|1")],
        [InlineKeyboardButton(get_text('messages.report_time_range_7', lang), callback_data="report_generate|7")],
        [InlineKeyboardButton(get_text('messages.report_time_range_30', lang), callback_data="report_generate|30")],
        [InlineKeyboardButton(retention_text, callback_data="log_retention_menu")],
        [InlineKeyboardButton(get_text('buttons.clear_monitoring_logs', lang), callback_data="clear_logs_confirm")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")]
    ]
    
    text = get_text('messages.reporting_menu_header_with_options', lang)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def generate_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = get_user_lang(context)
    await query.answer()

    try:
        days = int(query.data.split('|')[1])
        
        await query.edit_message_text(get_text('messages.generating_report', lang), parse_mode="HTML")

        log = load_monitoring_log()
        
        now = datetime.now()
        cutoff_date = now - timedelta(days=days)

        recent_events = [e for e in log if datetime.fromisoformat(e['timestamp']) >= cutoff_date]

        if not recent_events:
            buttons = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="reporting_menu")]]
            await query.edit_message_text(get_text('messages.no_events_found', lang, days=days), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
            return

        failover_events = [e for e in recent_events if e['event_type'] == 'FAILOVER']
        failback_events = [e for e in recent_events if e['event_type'] == 'FAILBACK']
        lb_rotations = [e for e in recent_events if e['event_type'] == 'LB_ROTATION']
        
        uptime_stats = {}
        ip_status_events = [e for e in recent_events if e['event_type'] == 'IP_STATUS']
        if ip_status_events:
            unique_ips_in_log = sorted(list(set(e['ip'] for e in ip_status_events)))
            for ip in unique_ips_in_log:
                ip_specific_log = [e for e in ip_status_events if e['ip'] == ip]
                if ip_specific_log:
                    up_checks = sum(1 for e in ip_specific_log if e['status'] == 'UP')
                    total_checks = len(ip_specific_log)
                    uptime_percent = (up_checks / total_checks) * 100 if total_checks > 0 else 100
                    uptime_stats[ip] = f"{uptime_percent:.2f}"

        lb_duration_stats = {}
        if lb_rotations:
            rotations_by_policy = {}
            for event in lb_rotations:
                policy_name = event.get('policy_name')
                if not policy_name: continue
                if policy_name not in rotations_by_policy:
                    rotations_by_policy[policy_name] = []
                rotations_by_policy[policy_name].append(event)

            for policy_name, events in rotations_by_policy.items():
                try:
                    if not events: continue
                    events.sort(key=lambda e: e['timestamp'])
                    
                    ip_durations_seconds = {}
                    involved_ips = set()
                    for event in events:
                        involved_ips.add(event.get('from_ip'))
                        involved_ips.add(event.get('to_ip'))

                    for ip in involved_ips:
                        if not ip: continue
                        total_active_time = timedelta()
                        for i, event in enumerate(events):
                            if event.get('to_ip') == ip:
                                start_time = datetime.fromisoformat(event['timestamp'])
                                end_time = now
                                if i + 1 < len(events):
                                    end_time = datetime.fromisoformat(events[i+1]['timestamp'])
                                
                                start_time = max(start_time, cutoff_date)
                                end_time = min(end_time, now)
                                if end_time > start_time:
                                    total_active_time += (end_time - start_time)

                        initial_events = [e for e in log if e.get('policy_name') == policy_name and e.get('event_type') == 'LB_ROTATION' and datetime.fromisoformat(e['timestamp']) < cutoff_date]
                        if initial_events:
                            active_ip_at_start = initial_events[-1].get('to_ip')
                            if active_ip_at_start == ip and events:
                                first_event_time_in_window = events[0]['timestamp']
                                duration = datetime.fromisoformat(first_event_time_in_window) - cutoff_date
                                if duration.total_seconds() > 0:
                                    total_active_time += duration
                        
                        if total_active_time.total_seconds() > 0:
                            ip_durations_seconds[ip] = total_active_time.total_seconds()

                    total_duration_seconds = sum(ip_durations_seconds.values())
                    if total_duration_seconds > 0:
                        lb_duration_stats[policy_name] = {
                            ip: {
                                "hours": seconds / 3600,
                                "percent": (seconds / total_duration_seconds) * 100
                            } for ip, seconds in ip_durations_seconds.items()
                        }
                except Exception as e:
                    logger.error(f"Error calculating LB stats for policy '{policy_name}' in generate_report: {e}", exc_info=True)
                    continue

        message_parts = [get_text('messages.report_header', lang, days=days)]

        summary_text = get_text('messages.report_summary', lang, 
                                  failovers=len(failover_events),
                                  failbacks=len(failback_events),
                                  rotations=len(lb_rotations))
        message_parts.append(summary_text)

        if failover_events:
            message_parts.append(get_text('messages.report_failover_header', lang))
            for event in failover_events[:5]:
                utc_time = datetime.fromisoformat(event['timestamp'])
                local_time = utc_time.astimezone(USER_TIMEZONE)
                ts = local_time.strftime('%Y-%m-%d %H:%M')
                safe_event = {k: escape_html(str(v)) for k, v in event.items()}
                safe_event['ts'] = ts
                message_parts.append(get_text('messages.report_failover_entry', lang, **safe_event))

        if lb_duration_stats:
            message_parts.append(get_text('messages.report_lb_duration_header', lang))
            for policy_name, stats in sorted(lb_duration_stats.items()):
                message_parts.append(get_text('messages.report_lb_duration_entry', lang, policy_name=escape_html(policy_name)))
                for ip, data in sorted(stats.items(), key=lambda item: item[1]['percent'], reverse=True):
                    message_parts.append(get_text('messages.report_lb_duration_ip_entry', lang, ip=ip, hours=data['hours'], percent=data['percent']))

        if uptime_stats:
            message_parts.append(get_text('messages.report_uptime_header', lang))
            for ip, uptime_percent in sorted(uptime_stats.items()):
                message_parts.append(get_text('messages.report_uptime_entry', lang, ip=ip, uptime_percent=uptime_percent))
        
        full_message = "\n".join(message_parts)
        buttons = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="reporting_menu")]]

        await query.edit_message_text(full_message, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

    except Exception as e:
        logger.error(f"!!! An unhandled exception occurred in generate_report_callback: {e} !!!", exc_info=True)
        try:
            await send_or_edit(update, context, "An unexpected error occurred while generating the report.")
        except:
            pass

async def lb_policy_change_algo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = int(query.data.split('|')[1])
        config = load_config()
        policy = config['load_balancer_policies'][policy_index]
        
        current_algo = policy.get('rotation_algorithm', 'random')
        new_algo = 'round_robin' if current_algo == 'random' else 'random'
        
        config['load_balancer_policies'][policy_index]['rotation_algorithm'] = new_algo
        
        policy_name = policy.get('policy_name')
        if policy_name and 'health_status' in context.bot_data and policy_name in context.bot_data['health_status']:
            if 'wrr_state' in context.bot_data['health_status'][policy_name]:
                del context.bot_data['health_status'][policy_name]['wrr_state']
                logger.info(f"Cleared WRR state for policy '{policy_name}' due to algorithm change.")

        save_config(config)
        
        new_algo_name = "Weighted Random" if new_algo == 'random' else "Weighted Round-Robin"
        confirmation_message = get_text('messages.algorithm_changed', lang, algo_name=new_algo_name)
        await query.answer(confirmation_message, show_alert=True)
        
        await lb_policy_edit_callback(update, context)

    except (IndexError, ValueError):
        await query.edit_message_text(get_text('messages.error_policy_not_found', lang))
    except Exception as e:
        logger.error(f"Error in lb_policy_change_algo_callback: {e}", exc_info=True)
        await query.edit_message_text(get_text('messages.error_generic_request', lang))

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
    except error.BadRequest as e:
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

    if policy_type == 'group':
        if not selected_nodes:
            await query.answer("Please select at least one location.", show_alert=True)
            return
        
        context.user_data['awaiting_threshold'] = True
        group_name_text = get_text('messages.monitoring_type_group', lang, group_name=escape_html(context.user_data.get('new_group_name', '')))
        text_to_send = get_text('messages.nodes_updated_message', lang, 
                                  count=len(selected_nodes), 
                                  monitoring_type=group_name_text)
        await send_or_edit(update, context, text_to_send)
        return

    policy_index = context.user_data.get('edit_policy_index')
    monitoring_type = context.user_data.get('monitoring_type')

    if not all([policy_type, policy_index is not None, monitoring_type]):
        await send_or_edit(update, context, get_text('messages.session_expired_error', lang))
        return

    config = load_config()
    
    try:
        if policy_type == 'lb':
            config['load_balancer_policies'][policy_index]['monitoring_nodes'] = selected_nodes
        else:
            if monitoring_type == 'primary':
                config['failover_policies'][policy_index]['primary_monitoring_nodes'] = selected_nodes
            else:
                config['failover_policies'][policy_index]['backup_monitoring_nodes'] = selected_nodes
    except IndexError:
        await send_or_edit(update, context, get_text('messages.session_expired_error', lang))
        return
    
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
    
    type_text_key = f'messages.monitoring_type_{monitoring_type}'
    type_text = get_text(type_text_key, lang)
    
    text_to_send = get_text('messages.nodes_updated_message', lang, 
                              count=len(selected_nodes), 
                              monitoring_type=type_text)
    await send_or_edit(update, context, text_to_send)

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
    """Finalizes record selection for editing, wizard, and adding new policies."""
    query = update.callback_query
    context.user_data['last_callback_query'] = query
    await query.answer()
    lang = get_user_lang(context)

    if context.user_data.get('wizard_step') == 'select_records':
        selected_records = context.user_data.get('policy_selected_records', [])
        if not selected_records:
            await query.answer("Please select at least one record.", show_alert=True)
            return
        
        context.user_data['wizard_data']['record_names'] = selected_records
        
        for key in ['policy_all_records', 'policy_selected_records', 'current_selection_zone', 'add_policy_type', 'new_policy_data']:
            context.user_data.pop(key, None)
            
        await wizard_final_step_ask_monitoring(update, context)
        return
    
    selected_records = context.user_data.get('policy_selected_records', [])
    is_editing = context.user_data.get('is_editing_policy_records', False)
    
    if is_editing:
        policy_type = context.user_data.get('editing_policy_type')
        policy_index = context.user_data.get('edit_policy_index')

        if not all([policy_type, policy_index is not None]):
            await send_or_edit(update, context, get_text('messages.session_expired_error', lang)); return

        config = load_config()
        policy_list_key = 'load_balancer_policies' if policy_type == 'lb' else 'failover_policies'
        
        try:
            config[policy_list_key][policy_index]['record_names'] = selected_records
            save_config(config)
        except IndexError:
            await send_or_edit(update, context, get_text('messages.session_expired_error', lang)); return
        
        policy_type_display = "LB" if policy_type == 'lb' else "Failover"
        await send_or_edit(update, context, get_text('messages.policy_records_updated', lang, policy_type=policy_type_display))
        await asyncio.sleep(1)

        for key in ['is_editing_policy_records', 'policy_all_records', 'policy_selected_records', 'current_selection_zone']:
            context.user_data.pop(key, None)

        if policy_type == 'lb':
            await lb_policy_view_callback(update, context)
        else:
            await failover_policy_view_callback(update, context)
        return

    else:
        if not selected_records:
            await query.answer("Please select at least one record.", show_alert=True); return

        policy_type = context.user_data.get('add_policy_type')
        data = context.user_data['new_policy_data']
        data['record_names'] = selected_records
        
        for key in ['policy_all_records', 'policy_selected_records', 'current_selection_zone']:
            context.user_data.pop(key, None)

        if policy_type == 'lb' or policy_type == 'failover':
            context.user_data['add_policy_step'] = 'select_group'
            await policy_add_step_ask_group(update, context)
        else:
            await query.edit_message_text("Unsupported policy type for this flow.")
        return

async def policy_set_failback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    choice = query.data.split('|')[1]
    auto_failback_enabled = (choice == 'true')
    
    context.user_data['new_policy_data']['auto_failback'] = auto_failback_enabled
    
    if auto_failback_enabled:
        context.user_data['add_policy_step'] = 'failback_minutes'
        await send_or_edit(update, context, get_text('prompts.enter_failback_minutes', lang))
    else:
        context.user_data['new_policy_data']['failback_minutes'] = 5
        await start_node_selection_for_new_failover(update, context, 'primary')

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

async def start_node_selection_for_new_failover(update: Update, context: ContextTypes.DEFAULT_TYPE, monitoring_type: str):
    """Transitions a new failover policy creation to the node selection stage."""
    lang = get_user_lang(context)
    
    if 'edit_policy_index' not in context.user_data:
        config = load_config()
        config['failover_policies'].append(context.user_data['new_policy_data'])
        save_config(config)
        new_policy_index = len(config['failover_policies']) - 1
        context.user_data['edit_policy_index'] = new_policy_index
        
        for key in ['add_policy_step', 'new_policy_data', 'add_policy_type']:
            context.user_data.pop(key, None)

    context.user_data['editing_policy_type'] = 'failover'
    context.user_data['monitoring_type'] = monitoring_type
    context.user_data['policy_selected_nodes'] = []

    if monitoring_type == 'primary':
        msg = get_text('messages.start_primary_monitoring_setup', lang)
    else:
        msg = get_text('messages.start_backup_monitoring_setup', lang)
    
    await send_or_edit(update, context, msg)
    await asyncio.sleep(2)
    
    await display_countries_for_selection(update, context, page=0)

async def _handle_state_awaiting_clone_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles user input for a new policy clone name."""
    lang = get_user_lang(context)
    text = update.message.text.strip()

    try:
        if 'clone_start_message_id' in context.user_data:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data.pop('clone_start_message_id'))
        await update.message.delete()
    except Exception:
        pass

    try:
        new_name = text
        clone_info = context.user_data.pop('clone_info')
        policy_type = clone_info['type']
        policy_index = clone_info['index']
        
        config = load_config()
        
        all_names = [p.get('policy_name') for p in config.get('failover_policies', [])] + \
                    [p.get('policy_name') for p in config.get('load_balancer_policies', [])]
        
        if new_name in all_names:
            back_callback = f"{policy_type}_policy_view|{policy_index}"
            buttons = [[InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data=back_callback)]]
            error_text = f"{get_text('prompts.enter_clone_name', lang)}\n\n<b>{get_text('messages.clone_name_exists', lang)}</b>"
            error_msg = await update.effective_chat.send_message(error_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
            
            context.user_data['clone_info'] = clone_info
            context.user_data['state'] = 'awaiting_clone_name'
            context.user_data['clone_start_message_id'] = error_msg.message_id
            return

        policy_list_key = "load_balancer_policies" if policy_type == 'lb' else "failover_policies"
        original_policy = config[policy_list_key][policy_index]
        cloned_policy = copy.deepcopy(original_policy)
        
        original_name = cloned_policy.get('policy_name', 'N/A')
        cloned_policy['policy_name'] = new_name
        cloned_policy['enabled'] = False
        
        config[policy_list_key].append(cloned_policy)
        save_config(config)
        
        context.user_data.pop('state', None)
        
        success_text = get_text('messages.clone_success', lang, original_name=escape_html(original_name), new_name=escape_html(new_name))
        back_callback = "settings_lb_policies" if policy_type == 'lb' else "settings_failover_policies"
        buttons = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=back_callback)]]
        
        await update.effective_chat.send_message(success_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

    except (IndexError, KeyError):
        await update.message.reply_text(get_text('messages.session_expired_error', lang))

async def _handle_state_wizard_steps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all text inputs during the wizard setup process."""
    lang = get_user_lang(context)
    text = update.message.text.strip()
    wizard_step = context.user_data.get('wizard_step')
    
    start_time = context.user_data.get('wizard_start_time')
    if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
        for key in ['wizard_data', 'wizard_step', 'wizard_start_time', 'last_callback_query']:
            context.user_data.pop(key, None)
        await update.message.reply_text("فرآیند راه‌اندازی سریع به دلیل عدم فعالیت منقضی شد. لطفاً با /wizard دوباره شروع کنید.")
        return

    if wizard_step == 'ask_name':
        context.user_data['wizard_data']['policy_name'] = text
        await wizard_step3_ask_ips(update, context)
    
    elif wizard_step == 'ask_primary_ip':
        if not is_valid_ip(text):
            error_msg = await update.message.reply_text(f"❌ {get_text('messages.invalid_ip', lang)}")
            await asyncio.sleep(4)
            try: await error_msg.delete(); await update.message.delete()
            except Exception: pass
            return
        context.user_data['wizard_data']['primary_ip'] = text
        await wizard_step4_ask_backup_ips(update, context)

    elif wizard_step == 'ask_backup_ips':
        ips = [ip.strip() for ip in text.split(',') if ip.strip() and is_valid_ip(ip.strip())]
        if not ips:
            error_msg = await update.message.reply_text(f"❌ {get_text('messages.invalid_ip', lang)}")
            await asyncio.sleep(4)
            try: await error_msg.delete(); await update.message.delete()
            except Exception: pass
            return
        context.user_data['wizard_data']['backup_ips'] = ips
        await wizard_step5_ask_port(update, context)

    elif wizard_step == 'ask_lb_ips':
        new_ips_data = []
        ip_entries = [entry.strip() for entry in text.split(',') if entry.strip()]
        for entry in ip_entries:
            ip, weight = (entry.strip(), 1)
            if ':' in entry:
                parts = entry.split(':', 1)
                ip, weight_str = parts[0].strip(), parts[1].strip()
                if not (weight_str.isdigit() and int(weight_str) >= 1):
                    error_msg = await update.message.reply_text(f"❌ {get_text('messages.invalid_weight_positive', lang, ip=ip)}")
                    await asyncio.sleep(4); await error_msg.delete(); await update.message.delete()
                    return
                weight = int(weight_str)
            if not is_valid_ip(ip):
                error_msg = await update.message.reply_text(f"❌ {get_text('messages.invalid_ip', lang)}")
                await asyncio.sleep(4); await error_msg.delete(); await update.message.delete()
                return
            new_ips_data.append({"ip": ip, "weight": weight})
        if not new_ips_data: return
        context.user_data['wizard_data']['ips'] = new_ips_data
        await wizard_step5_ask_port(update, context)

    elif wizard_step == 'ask_port':
        if not (text.isdigit() and 1 <= int(text) <= 65535):
            error_msg = await update.message.reply_text(f"❌ {get_text('messages.invalid_port', lang)}")
            await asyncio.sleep(4)
            try: await error_msg.delete(); await update.message.delete()
            except Exception: pass
            return
        context.user_data['wizard_data']['check_port'] = int(text)
        await wizard_step6_ask_account(update, context)

async def _handle_state_aliases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text input for setting or removing zone and record aliases."""
    lang = get_user_lang(context)
    text = update.message.text.strip()
    
    try:
        await update.message.delete()
    except Exception:
        pass

    # --- Handle Record Alias ---
    if 'awaiting_record_alias' in context.user_data:
        alias_data = context.user_data.pop('awaiting_record_alias')
        try:
            if alias_data.get('prompt_message_id'):
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=alias_data['prompt_message_id'])
        except Exception:
            pass

        config = load_config()
        zone_id = context.user_data.get('selected_zone_id')
        config.setdefault('record_aliases', {}).setdefault(zone_id, {})
        alias_key = f"{alias_data['record_type']}:{alias_data['record_name']}"
        
        if text == '-':
            config['record_aliases'][zone_id].pop(alias_key, None)
            temp_msg_text = get_text('messages.record_alias_removed_success', lang, record_name=escape_html(alias_data['record_name']))
        else:
            config['record_aliases'][zone_id][alias_key] = text
            temp_msg_text = get_text('messages.record_alias_set_success', lang, record_name=escape_html(alias_data['record_name']), alias=escape_html(text))
        
        save_config(config)
        context.user_data.pop('all_records', None)
        await display_records_list(update, context)

    # --- Handle Zone Alias ---
    elif 'awaiting_zone_alias' in context.user_data:
        alias_data = context.user_data.pop('awaiting_zone_alias')
        zone_id, zone_name = alias_data['zone_id'], alias_data['zone_name']
        
        config = load_config()
        config.setdefault('zone_aliases', {})

        if text == '-':
            config['zone_aliases'].pop(zone_id, None)
            temp_msg_text = get_text('messages.alias_removed_success', lang, zone_name=escape_html(zone_name))
        else:
            config['zone_aliases'][zone_id] = text
            temp_msg_text = get_text('messages.alias_set_success', lang, zone_name=escape_html(zone_name), alias=escape_html(text))
            
        save_config(config)
        context.user_data.pop('all_zones_cache', None)
        await display_zones_list(update, context)

    if 'temp_msg_text' in locals():
        temp_msg = await context.bot.send_message(update.effective_chat.id, temp_msg_text, parse_mode="HTML")
        await asyncio.sleep(3)
        try: await temp_msg.delete()
        except Exception: pass

async def _create_dummy_update_from_text(update: Update, callback_data: str):
    """Creates a dummy Update object with a CallbackQuery from a text message."""
    async def dummy_answer(*args, **kwargs): return True
    message_obj = update.message if hasattr(update, 'message') and update.message else update.callback_query.message
    
    dummy_query = type('obj', (object,), {
        'data': callback_data, 'answer': dummy_answer,
        'message': message_obj, 'is_dummy': True
    })
    return type('obj', (object,), {
        'callback_query': dummy_query, 'effective_user': update.effective_user,
        'effective_chat': update.effective_chat
    })

async def _handle_state_lb_ip_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all text inputs for managing Load Balancer IP addresses and weights."""
    lang = get_user_lang(context)
    state = context.user_data.get('state')
    text = update.message.text.strip()
    try:
        await update.message.delete()
        if context.user_data.get('last_callback_query'):
            await context.user_data.pop('last_callback_query').message.delete()
    except Exception: pass

    try:
        policy_index = context.user_data['edit_policy_index']
        config = load_config()
        
        if state == 'awaiting_lb_ip_address':
            if not is_valid_ip(text): raise ValueError(f"Invalid IP: {text}")
            ip_index = context.user_data['lb_ip_action_index']
            config['load_balancer_policies'][policy_index]['ips'][ip_index]['ip'] = text
            save_config(config)

        elif state == 'awaiting_lb_ip_weight':
            if not text.isdigit() or int(text) < 1: raise ValueError(f"Invalid weight: {text}")
            ip_index = context.user_data['lb_ip_action_index']
            config['load_balancer_policies'][policy_index]['ips'][ip_index]['weight'] = int(text)
            save_config(config)

        elif state == 'awaiting_lb_new_ip':
            new_ips_data = []
            ip_entries = [entry.strip() for entry in text.split(',') if entry.strip()]
            for entry in ip_entries:
                ip, weight = (entry.strip(), 1)
                if ':' in entry:
                    parts = entry.split(':', 1)
                    ip, weight_str = parts[0].strip(), parts[1].strip()
                    if not (weight_str.isdigit() and int(weight_str) >= 1): raise ValueError(f"Invalid weight for {ip}")
                    weight = int(weight_str)
                if not is_valid_ip(ip): raise ValueError(f"Invalid IP: {ip}")
                new_ips_data.append({"ip": ip, "weight": weight})
            if new_ips_data:
                config['load_balancer_policies'][policy_index]['ips'].extend(new_ips_data)
                save_config(config)
    except (IndexError, KeyError) as e:
        logger.error(f"Session error in LB IP management: {e}", exc_info=True)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=get_text('messages.session_expired_error', lang))
    except ValueError as e:
        logger.warning(f"Invalid user input in LB IP management: {e}")
        error_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ {e}. Please try again.")
        await asyncio.sleep(4)
        try: await error_msg.delete() 
        except Exception: pass
    finally:
        context.user_data.pop('state', None)
        context.user_data.pop('lb_ip_action_index', None)
        
        await lb_ip_list_menu(update, context, force_new_message=True)

async def _handle_state_awaiting_clone_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles user input for a new policy clone name."""
    lang = get_user_lang(context)
    text = update.message.text.strip()

    try:
        if 'clone_start_message_id' in context.user_data:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data.pop('clone_start_message_id'))
        await update.message.delete()
    except Exception:
        pass

    try:
        new_name = text
        clone_info = context.user_data.pop('clone_info')
        policy_type = clone_info['type']
        policy_index = clone_info['index']
        
        config = load_config()
        
        all_names = [p.get('policy_name') for p in config.get('failover_policies', [])] + \
                    [p.get('policy_name') for p in config.get('load_balancer_policies', [])]
        
        if new_name in all_names:
            back_callback = f"{policy_type}_policy_view|{policy_index}"
            buttons = [[InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data=back_callback)]]
            error_text = f"{get_text('prompts.enter_clone_name', lang)}\n\n<b>{get_text('messages.clone_name_exists', lang)}</b>"
            error_msg = await update.effective_chat.send_message(error_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
            
            context.user_data['clone_info'] = clone_info
            context.user_data['state'] = 'awaiting_clone_name'
            context.user_data['clone_start_message_id'] = error_msg.message_id
            return

        policy_list_key = "load_balancer_policies" if policy_type == 'lb' else "failover_policies"
        original_policy = config[policy_list_key][policy_index]
        cloned_policy = copy.deepcopy(original_policy)
        
        original_name = cloned_policy.get('policy_name', 'N/A')
        cloned_policy['policy_name'] = new_name
        cloned_policy['enabled'] = False
        
        config[policy_list_key].append(cloned_policy)
        save_config(config)
        
        context.user_data.pop('state', None)
        
        success_text = get_text('messages.clone_success', lang, original_name=escape_html(original_name), new_name=escape_html(new_name))
        back_callback = "settings_lb_policies" if policy_type == 'lb' else "settings_failover_policies"
        buttons = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=back_callback)]]
        
        await update.effective_chat.send_message(success_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

    except (IndexError, KeyError):
        await update.message.reply_text(get_text('messages.session_expired_error', lang))

async def _handle_state_awaiting_notification_recipient(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles adding new recipient IDs to a notification group."""
    lang = get_user_lang(context)
    text = update.message.text.strip()
    recipient_key = context.user_data.get('current_recipient_key')

    if not recipient_key:
        await update.message.reply_text(get_text('messages.session_expired_try_again', lang))
        return

    try:
        await update.message.delete()
        if context.user_data.get('last_menu_message_id'):
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data.pop('last_menu_message_id'))
    except error.BadRequest:
        pass

    new_member_ids = set()
    parts = [p.strip() for p in text.split(',') if p.strip()]
    for part in parts:
        try:
            new_member_ids.add(int(part))
        except ValueError:
            await update.message.reply_text(get_text('messages.invalid_recipient_id', lang, part=escape_html(part)), parse_mode="HTML")
            return

    if not new_member_ids:
        await update.message.reply_text(get_text('messages.no_valid_ids_entered', lang))
        return
        
    config = load_config()
    recipients_map = config.setdefault("notifications", {}).setdefault("recipients", {})
    current_members = set(recipients_map.get(recipient_key, []))
    current_members.update(new_member_ids)
    recipients_map[recipient_key] = sorted(list(current_members))
    save_config(config)
    
    context.user_data.pop('state', None)
    
    dummy_update = await _create_dummy_update_from_text(update, f"notification_edit_recipients|{recipient_key}")
    await notification_edit_recipients_callback(dummy_update, context)

async def _create_dummy_update_from_text(update: Update, callback_data: str):
    """Creates a dummy Update object with a CallbackQuery from a text message."""
    async def dummy_answer(*args, **kwargs): return True
    message_obj = update.message if hasattr(update, 'message') and update.message else update.callback_query.message
    
    dummy_query = type('obj', (object,), {
        'data': callback_data, 'answer': dummy_answer,
        'message': message_obj, 'is_dummy': True
    })
    return type('obj', (object,), {
        'callback_query': dummy_query, 'effective_user': update.effective_user,
        'effective_chat': update.effective_chat
    })



async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles all non-command text messages using a clean, sequential if/elif/else chain
    to correctly dispatch to the appropriate state handler.
    """
    if not is_admin(update):
        return

    lang = get_user_lang(context)
    text = update.message.text.strip()
    state = context.user_data.get('state')
    
    def create_dummy_update(message_id, original_update, bot):
        async def dummy_answer(*args, **kwargs): return True
        dummy_query = type('obj', (object,), {'is_dummy': True, 'answer': dummy_answer, 'edit_message_text': lambda *args, **kwargs: bot.edit_message_text(chat_id=original_update.effective_chat.id, message_id=message_id, *args, **kwargs), 'message': type('obj', (object,), {'message_id': message_id, 'chat': original_update.effective_chat})})
        dummy_update = type('obj', (object,), {'callback_query': dummy_query, 'effective_user': original_update.effective_user, 'effective_chat': original_update.effective_chat})
        return dummy_update

    if state == 'awaiting_lb_ip_address' or state == 'awaiting_lb_ip_weight' or state == 'awaiting_lb_new_ip':
        try:
            await update.message.delete()
            if context.user_data.get('last_callback_query'):
                await context.user_data.pop('last_callback_query').message.delete()
        except Exception: pass
        try:
            policy_index = context.user_data['edit_policy_index']
            config = load_config()
            
            if state == 'awaiting_lb_ip_address':
                if not is_valid_ip(text): raise ValueError(f"Invalid IP: {text}")
                ip_index = context.user_data['lb_ip_action_index']
                config['load_balancer_policies'][policy_index]['ips'][ip_index]['ip'] = text
                save_config(config)

            elif state == 'awaiting_lb_ip_weight':
                if not text.isdigit() or int(text) < 1: raise ValueError(f"Invalid weight: {text}")
                ip_index = context.user_data['lb_ip_action_index']
                config['load_balancer_policies'][policy_index]['ips'][ip_index]['weight'] = int(text)
                save_config(config)

            elif state == 'awaiting_lb_new_ip':
                new_ips_data = []
                ip_entries = [entry.strip() for entry in text.split(',') if entry.strip()]
                for entry in ip_entries:
                    ip, weight = (entry.strip(), 1)
                    if ':' in entry:
                        parts = entry.split(':', 1)
                        ip, weight_str = parts[0].strip(), parts[1].strip()
                        if not (weight_str.isdigit() and int(weight_str) >= 1): raise ValueError(f"Invalid weight for {ip}")
                        weight = int(weight_str)
                    if not is_valid_ip(ip): raise ValueError(f"Invalid IP: {ip}")
                    new_ips_data.append({"ip": ip, "weight": weight})
                if new_ips_data:
                    config['load_balancer_policies'][policy_index]['ips'].extend(new_ips_data)
                    save_config(config)
        except (IndexError, KeyError) as e:
            logger.error(f"Session error in LB IP management: {e}", exc_info=True)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=get_text('messages.session_expired_error', lang))
        except ValueError as e:
            logger.warning(f"Invalid user input in LB IP management: {e}")
            error_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ {e}. Please try again.")
            await asyncio.sleep(4)
            try: await error_msg.delete() 
            except Exception: pass
        finally:
            context.user_data.pop('state', None)
            context.user_data.pop('lb_ip_action_index', None)
            dummy_update = await _create_dummy_update_from_text(update, "lb_ip_list_menu")
            await lb_ip_list_menu(dummy_update, context)

    elif state == 'awaiting_clone_name':
        try:
            if 'clone_start_message_id' in context.user_data:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data.pop('clone_start_message_id'))
            await update.message.delete()
            
            new_name = text
            clone_info = context.user_data.pop('clone_info')
            policy_type, policy_index = clone_info['type'], clone_info['index']
            
            config = load_config()
            all_names = [p.get('policy_name') for p in config.get('failover_policies', [])] + \
                        [p.get('policy_name') for p in config.get('load_balancer_policies', [])]
            if new_name in all_names:
                back_callback = f"{policy_type}_policy_view|{policy_index}"
                buttons = [[InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data=back_callback)]]
                error_text = f"{get_text('prompts.enter_clone_name', lang)}\n\n<b>{get_text('messages.clone_name_exists', lang)}</b>"
                error_msg = await update.effective_chat.send_message(error_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
                
                context.user_data.update({'clone_info': clone_info, 'state': 'awaiting_clone_name', 'clone_start_message_id': error_msg.message_id})
            else:
                policy_list_key = "load_balancer_policies" if policy_type == 'lb' else "failover_policies"
                original_policy = config[policy_list_key][policy_index]
                cloned_policy = copy.deepcopy(original_policy)
                original_name = cloned_policy.get('policy_name', 'N/A')
                cloned_policy.update({'policy_name': new_name, 'enabled': False})
                config[policy_list_key].append(cloned_policy)
                save_config(config)
                context.user_data.pop('state', None)
                success_text = get_text('messages.clone_success', lang, original_name=escape_html(original_name), new_name=escape_html(new_name))
                back_callback = "settings_lb_policies" if policy_type == 'lb' else "settings_failover_policies"
                buttons = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=back_callback)]]
                await update.effective_chat.send_message(success_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        except (IndexError, KeyError):
            await update.message.reply_text(get_text('messages.session_expired_error', lang))

    elif state == 'awaiting_notification_recipient':
        recipient_key = context.user_data.get('current_recipient_key')
        if not recipient_key:
            await update.message.reply_text(get_text('messages.session_expired_try_again', lang))
            return
        try:
            await update.message.delete()
            if context.user_data.get('last_menu_message_id'):
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data.pop('last_menu_message_id'))
        except Exception: pass
        
        new_member_ids = set()
        for part in [p.strip() for p in text.split(',') if p.strip()]:
            try: new_member_ids.add(int(part))
            except ValueError:
                await update.message.reply_text(get_text('messages.invalid_recipient_id', lang, part=escape_html(part)), parse_mode="HTML")
                return
        if not new_member_ids:
            await update.message.reply_text(get_text('messages.no_valid_ids_entered', lang))
            return
            
        config = load_config()
        recipients_map = config.setdefault("notifications", {}).setdefault("recipients", {})
        current_members = set(recipients_map.get(recipient_key, []))
        current_members.update(new_member_ids)
        recipients_map[recipient_key] = sorted(list(current_members))
        save_config(config)
        
        context.user_data.pop('state', None)
        await notification_edit_recipients_callback(update, context, force_new_message=True)

    elif 'wizard_step' in context.user_data:
        start_time = context.user_data.get('wizard_start_time')
        if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
            for key in ['wizard_data', 'wizard_step', 'wizard_start_time', 'last_callback_query']:
                context.user_data.pop(key, None)
            await update.message.reply_text("فرآیند راه‌اندازی سریع به دلیل عدم فعالیت منقضی شد. لطفاً با /wizard دوباره شروع کنید.")
            return

        wizard_step = context.user_data.get('wizard_step')
        if wizard_step == 'ask_name':
            context.user_data['wizard_data']['policy_name'] = text
            await wizard_step3_ask_ips(update, context)
        
        elif wizard_step == 'ask_primary_ip':
            if not is_valid_ip(text):
                error_msg = await update.message.reply_text(f"❌ {get_text('messages.invalid_ip', lang)}")
                await asyncio.sleep(4); await error_msg.delete(); await update.message.delete()
                return
            context.user_data['wizard_data']['primary_ip'] = text
            await wizard_step4_ask_backup_ips(update, context)

        elif wizard_step == 'ask_backup_ips':
            ips = [ip.strip() for ip in text.split(',') if ip.strip() and is_valid_ip(ip.strip())]
            if not ips:
                error_msg = await update.message.reply_text(f"❌ {get_text('messages.invalid_ip', lang)}")
                await asyncio.sleep(4); await error_msg.delete(); await update.message.delete()
                return
            context.user_data['wizard_data']['backup_ips'] = ips
            await wizard_step5_ask_port(update, context)

        elif wizard_step == 'ask_lb_ips':
            new_ips_data = []
            ip_entries = [entry.strip() for entry in text.split(',') if entry.strip()]
            for entry in ip_entries:
                ip, weight = (entry.strip(), 1)
                if ':' in entry:
                    parts = entry.split(':', 1)
                    ip, weight_str = parts[0].strip(), parts[1].strip()
                    if not (weight_str.isdigit() and int(weight_str) >= 1):
                        error_msg = await update.message.reply_text(f"❌ {get_text('messages.invalid_weight_positive', lang, ip=ip)}")
                        await asyncio.sleep(4); await error_msg.delete(); await update.message.delete()
                        return
                    weight = int(weight_str)
                if not is_valid_ip(ip):
                    error_msg = await update.message.reply_text(f"❌ {get_text('messages.invalid_ip', lang)}")
                    await asyncio.sleep(4); await error_msg.delete(); await update.message.delete()
                    return
                new_ips_data.append({"ip": ip, "weight": weight})
            if not new_ips_data: return
            context.user_data['wizard_data']['ips'] = new_ips_data
            await wizard_step5_ask_port(update, context)

        elif wizard_step == 'ask_port':
            if not (text.isdigit() and 1 <= int(text) <= 65535):
                error_msg = await update.message.reply_text(f"❌ {get_text('messages.invalid_port', lang)}")
                await asyncio.sleep(4); await error_msg.delete(); await update.message.delete()
                return
            context.user_data['wizard_data']['check_port'] = int(text)
            await wizard_step6_ask_account(update, context)

    elif 'awaiting_record_alias' in context.user_data:
        alias_data = context.user_data.pop('awaiting_record_alias')
        prompt_message_id = alias_data.get('prompt_message_id')
        try:
            await update.message.delete()
            if prompt_message_id: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=prompt_message_id)
        except Exception: pass
            
        config = load_config()
        zone_id = context.user_data.get('selected_zone_id')
        config.setdefault('record_aliases', {}).setdefault(zone_id, {})
        alias_key = f"{alias_data['record_type']}:{alias_data['record_name']}"
        
        if text == '-':
            config['record_aliases'][zone_id].pop(alias_key, None)
            temp_msg_text = get_text('messages.record_alias_removed_success', lang, record_name=escape_html(alias_data['record_name']))
        else:
            config['record_aliases'][zone_id][alias_key] = text
            temp_msg_text = get_text('messages.record_alias_set_success', lang, record_name=escape_html(alias_data['record_name']), alias=escape_html(text))
        
        save_config(config)
        temp_msg = await context.bot.send_message(update.effective_chat.id, temp_msg_text, parse_mode="HTML")
        await asyncio.sleep(2)
        try: await temp_msg.delete()
        except Exception: pass
        context.user_data.pop('all_records', None)
        await display_records_list(update, context)

    elif 'awaiting_zone_alias' in context.user_data:
        message_id_to_edit = context.user_data.pop('last_menu_message_id', None)
        alias_data = context.user_data.pop('awaiting_zone_alias')
        zone_id, zone_name = alias_data['zone_id'], alias_data['zone_name']
        try: await update.message.delete()
        except Exception: pass
            
        config = load_config()
        config.setdefault('zone_aliases', {})
        if text == '-':
            config['zone_aliases'].pop(zone_id, None)
            temp_msg_text = get_text('messages.alias_removed_success', lang, zone_name=escape_html(zone_name))
        else:
            config['zone_aliases'][zone_id] = text
            temp_msg_text = get_text('messages.alias_set_success', lang, zone_name=escape_html(zone_name), alias=escape_html(text))
        save_config(config)
        temp_msg = await context.bot.send_message(update.effective_chat.id, temp_msg_text, parse_mode="HTML")
        await asyncio.sleep(3)
        try: await temp_msg.delete()
        except Exception: pass
        if message_id_to_edit:
            dummy_update = create_dummy_update(message_id_to_edit, update, context.bot)
            await display_zones_list(dummy_update, context)

    elif context.user_data.get('awaiting_admin_id_to_add'):
        message_id_to_edit = context.user_data.pop('last_menu_message_id', None)
        context.user_data.pop('awaiting_admin_id_to_add')
        if not is_super_admin(update):
            await update.message.reply_text(get_text('messages.not_a_super_admin', lang))
            return
        try: await update.message.delete()
        except Exception: pass
        try:
            admin_id = int(text)
            config = load_config()
            config.setdefault("admins", [])
            if admin_id in config["admins"] or admin_id in SUPER_ADMIN_IDS:
                temp_msg_text = get_text('messages.admin_already_exists', lang)
            else:
                config["admins"].append(admin_id)
                save_config(config)
                temp_msg_text = get_text('messages.admin_added_success', lang, user_id=admin_id)
        except ValueError:
            if message_id_to_edit:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=message_id_to_edit, text=f"{get_text('prompts.add_admin_prompt', lang)}\n\n❌ {get_text('messages.invalid_id_numeric', lang)}")
                context.user_data.update({'awaiting_admin_id_to_add': True, 'last_menu_message_id': message_id_to_edit})
            else:
                await update.message.reply_text(get_text('messages.invalid_id_numeric', lang))
            return
        
        temp_msg = await context.bot.send_message(update.effective_chat.id, temp_msg_text, parse_mode="HTML")
        await asyncio.sleep(3)
        try: await temp_msg.delete()
        except Exception: pass

        if message_id_to_edit:
            dummy_update = create_dummy_update(message_id_to_edit, update, context.bot)
            await user_management_menu_callback(dummy_update, context)
    
    elif context.user_data.get('group_add_step') == 'ask_name':
        group_name = text
        config = load_config()
        if group_name in config.get("monitoring_groups", {}):
            error_text = get_text('messages.group_add_prompt_name', lang) + f"\n\n<b>❌ {get_text('messages.group_name_exists', lang)}</b>"
            if context.user_data.get('last_callback_query'):
                await context.user_data['last_callback_query'].message.edit_text(error_text, parse_mode="HTML")
            return
        context.user_data.update({'new_group_name': group_name, 'editing_policy_type': 'group', 'policy_selected_nodes': []})
        context.user_data.pop('group_add_step')
        try: await update.message.delete()
        except Exception: pass
        if context.user_data.get('last_callback_query'): await context.user_data.pop('last_callback_query').message.delete()
        await display_countries_for_selection(update, context, page=0)

    elif 'monitor_add_step' in context.user_data:
        monitor_add_step = context.user_data['monitor_add_step']
        try: await update.message.delete()
        except Exception: pass
        
        async def edit_previous_prompt(new_text, new_markup):
            if context.user_data.get('last_callback_query'):
                try:
                    await context.user_data['last_callback_query'].message.edit_text(new_text, reply_markup=new_markup, parse_mode="HTML")
                except Exception:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=new_text, reply_markup=new_markup, parse_mode="HTML")

        if monitor_add_step == 'ask_name':
            context.user_data['new_monitor_data']['monitor_name'] = text
            context.user_data['monitor_add_step'] = 'ask_ip'
            text_to_send = get_text('messages.wizard_name_saved', lang) + get_text('messages.monitor_ask_ip', lang)
            buttons = [[InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="monitors_menu")]]
            await edit_previous_prompt(text_to_send, InlineKeyboardMarkup(buttons))
        
        elif monitor_add_step == 'ask_ip':
            context.user_data['new_monitor_data']['ip'] = text
            context.user_data['monitor_add_step'] = 'ask_port'
            text_to_send = get_text('messages.monitor_ip_saved', lang) + get_text('messages.monitor_ask_port', lang)
            buttons = [[InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="monitors_menu")]]
            await edit_previous_prompt(text_to_send, InlineKeyboardMarkup(buttons))
            
        elif monitor_add_step == 'ask_port':
            if not text.isdigit() or not (1 <= int(text) <= 65535):
                error_text = get_text('messages.monitor_ask_port', lang) + f"\n\n<b>❌ {get_text('messages.monitor_invalid_port', lang)}</b>"
                buttons = [[InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="monitors_menu")]]
                await edit_previous_prompt(error_text, InlineKeyboardMarkup(buttons))
            else:
                context.user_data['new_monitor_data']['check_port'] = int(text)
                context.user_data['monitor_add_step'] = 'select_group'
                await monitor_step_ask_group(update, context)

    elif 'monitor_edit_step' in context.user_data:
        field = context.user_data.pop('monitor_edit_step')
        monitor_index = context.user_data.get('edit_monitor_index')
        if monitor_index is None:
            await update.message.reply_text(get_text('messages.session_expired_error', lang))
            return
        try: await update.message.delete()
        except Exception: pass

        new_value = text.strip()
        
        if field == 'check_port':
            if not new_value.isdigit() or not (1 <= int(new_value) <= 65535):
                error_text = get_text('messages.monitor_ask_port_edit', lang) + f"\n\n<b>❌ {get_text('messages.monitor_invalid_port', lang)}</b>"
                buttons = [[InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"monitor_edit|{monitor_index}")]]
                if context.user_data.get('last_callback_query'):
                    await context.user_data['last_callback_query'].message.edit_text(error_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
                context.user_data['monitor_edit_step'] = field
                return
            value_to_save = int(new_value)
        else:
            value_to_save = new_value
        
        config = load_config()
        old_ip = None
        try:
            if field == 'ip': old_ip = config['standalone_monitors'][monitor_index].get('ip')
            config['standalone_monitors'][monitor_index][field] = value_to_save
            save_config(config)
        except (IndexError, KeyError):
            await context.bot.send_message(chat_id=update.effective_chat.id, text=get_text('messages.internal_error', lang))
            return

        if field == 'ip' and old_ip and old_ip != value_to_save:
            buttons = [
                [InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"monitor_purge_logs|{old_ip}")],
                [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"monitor_edit|{monitor_index}")]
            ]
            text_to_send = get_text('messages.monitor_ask_purge_logs', lang, old_ip=escape_html(old_ip))
            if context.user_data.get('last_callback_query'):
                await context.user_data['last_callback_query'].message.edit_text(text_to_send, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        else:
            temp_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=get_text('messages.value_updated_success', lang))
            await asyncio.sleep(2)
            await temp_msg.delete()
            
            if context.user_data.get('last_callback_query'):
                await context.user_data.pop('last_callback_query').message.delete()
            dummy_update = await _create_dummy_update_from_text(update, f"monitor_edit|{monitor_index}")
            await monitor_edit_menu_callback(dummy_update, context)

    elif 'change_type_data' in context.user_data:
        data = context.user_data.pop('change_type_data')
        rid = data['rid']
        new_type = data['new_type']
        new_content = text

        record = context.user_data.get("records", {}).get(rid)
        if not record:
            await send_or_edit(update, context, get_text('messages.internal_error', lang))
            return
        
        proxied = record.get('proxied', False)
        if new_type not in ['A', 'AAAA', 'CNAME']:
            proxied = False

        token = get_current_token(context)
        zone_id = context.user_data.get('selected_zone_id')

        res = await update_record(token, zone_id, rid, new_type, record['name'], new_content, proxied)

        if res.get("success"):
            temp_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=get_text('messages.record_updated_successfully', lang, record_name=escape_html(record['name'])),
                parse_mode="HTML"
            )
            
            context.user_data.pop('all_records', None)
            context.user_data.pop('records_list_cache', None)
            
            await asyncio.sleep(2)
            try: await temp_msg.delete()
            except Exception: pass

            context.user_data['selected_record_id_for_view'] = rid

            all_records = await get_dns_records(token, zone_id)
            if all_records is not None:
                context.user_data['all_records'] = all_records
                context.user_data["records"] = {r['id']: r for r in all_records}
            
            await select_callback(update, context, force_new_message=True)
        else:
            error_msg = res.get('errors', [{}])[0].get('message', 'Unknown error')
            await send_or_edit(update, context, get_text('messages.error_updating_record', lang, error=error_msg))

    elif 'add_policy_step' in context.user_data:
        step = context.user_data['add_policy_step']
        data = context.user_data['new_policy_data']
        policy_type = context.user_data.get('add_policy_type')

        if policy_type == 'failover':
            if step == 'name':
                data['policy_name'] = text
                context.user_data['add_policy_step'] = 'primary_ip'
                await send_or_edit(update, context, get_text('prompts.enter_primary_ip', lang))
            elif step == 'primary_ip':
                if not is_valid_ip(text): await send_or_edit(update, context, get_text('messages.invalid_ip', lang)); return
                data['primary_ip'] = text
                context.user_data['add_policy_step'] = 'backup_ips'
                await send_or_edit(update, context, get_text('prompts.enter_backup_ip', lang))
            elif step == 'backup_ips':
                ips = [ip.strip() for ip in text.split(',') if ip.strip() and is_valid_ip(ip.strip())]
                if not ips: await send_or_edit(update, context, get_text('messages.invalid_ip', lang)); return
                data['backup_ips'] = ips
                context.user_data['add_policy_step'] = 'check_port'
                await send_or_edit(update, context, get_text('prompts.enter_check_port', lang))
            elif step == 'check_port':
                if not text.isdigit() or not (1 <= int(text) <= 65535): await send_or_edit(update, context, get_text('messages.invalid_port', lang)); return
                data['check_port'] = int(text)
                context.user_data['add_policy_step'] = 'failover_minutes'
                await send_or_edit(update, context, get_text('prompts.enter_failover_minutes', lang))
            elif step == 'failover_minutes':
                if not text.replace('.', '', 1).isdigit() or float(text) <= 0: await send_or_edit(update, context, get_text('messages.invalid_number', lang)); return
                data['failover_minutes'] = float(text)
                context.user_data['add_policy_step'] = 'account_nickname'
                buttons = [[InlineKeyboardButton(n, callback_data=f"failover_policy_set_account|{n}")] for n in CF_ACCOUNTS.keys()]
                await send_or_edit(update, context, get_text('prompts.choose_cf_account', lang), InlineKeyboardMarkup(buttons))
            elif step == 'failback_minutes':
                if not text.replace('.', '', 1).isdigit() or float(text) <= 0: await send_or_edit(update, context, get_text('messages.invalid_number', lang)); return
                data['failback_minutes'] = float(text)
                await start_node_selection_for_new_failover(update, context, 'primary')
        
        elif policy_type == 'lb':
            if step == 'name':
                data['policy_name'] = text
                context.user_data['add_policy_step'] = 'ips'
                await send_or_edit(update, context, get_text('prompts.enter_lb_ips', lang))
            elif step == 'ips':
                new_ips_data = []
                ip_entries = [entry.strip() for entry in text.split(',') if entry.strip()]
                for entry in ip_entries:
                    ip, weight = (entry.strip(), 1)
                    if ':' in entry:
                        parts = entry.split(':', 1)
                        ip, weight_str = parts[0].strip(), parts[1].strip()
                        if not weight_str.isdigit() or int(weight_str) < 1: await send_or_edit(update, context, get_text('messages.invalid_weight_positive', lang, ip=ip)); return
                        weight = int(weight_str)
                    if not is_valid_ip(ip): await send_or_edit(update, context, get_text('messages.invalid_ip', lang)); return
                    new_ips_data.append({"ip": ip, "weight": weight})
                if not new_ips_data: await send_or_edit(update, context, get_text('messages.bulk_no_selection', lang)); return
                data['ips'] = new_ips_data
                context.user_data['add_policy_step'] = 'rotation_interval_hours'
                await send_or_edit(update, context, get_text('prompts.enter_lb_interval', lang))
            elif step == 'rotation_interval_hours':
                parts = [p.strip() for p in text.split(',')]
                try:
                    if len(parts) == 1 and float(parts[0]) > 0: data.update({'rotation_min_hours': float(parts[0]), 'rotation_max_hours': float(parts[0])})
                    elif len(parts) == 2 and float(parts[0]) > 0 and float(parts[1]) >= float(parts[0]): data.update({'rotation_min_hours': float(parts[0]), 'rotation_max_hours': float(parts[1])})
                    else: raise ValueError()
                except (ValueError, IndexError): await send_or_edit(update, context, get_text('messages.invalid_number_range', lang)); return
                context.user_data['add_policy_step'] = 'check_port'
                await send_or_edit(update, context, get_text('prompts.enter_check_port', lang))
            elif step == 'check_port':
                if not text.isdigit() or not (1 <= int(text) <= 65535): await send_or_edit(update, context, get_text('messages.invalid_port', lang)); return
                data['check_port'] = int(text)
                context.user_data['add_policy_step'] = 'account_nickname'
                buttons = [[InlineKeyboardButton(n, callback_data=f"lb_policy_set_account|{n}")] for n in CF_ACCOUNTS.keys()]
                await send_or_edit(update, context, get_text('prompts.choose_cf_account', lang), InlineKeyboardMarkup(buttons))
        
    elif 'edit_policy_field' in context.user_data:
        field = context.user_data.pop('edit_policy_field')
        policy_type = context.user_data.get('editing_policy_type')
        policy_index = context.user_data.get('edit_policy_index')
        
        if policy_index is None or not policy_type:
            await send_or_edit(update, context, get_text('messages.session_expired_error', lang))
            return

        try:
            value_to_save = None
            
            if field == 'ips' and policy_type == 'lb':
                new_ips_data = []
                ip_entries = [entry.strip() for entry in text.split(',') if entry.strip()]
                for entry in ip_entries:
                    ip, weight = entry.strip(), 1
                    if ':' in entry:
                        parts = entry.split(':', 1)
                        ip, weight_str = parts[0].strip(), parts[1].strip()
                        if not weight_str.isdigit() or int(weight_str) < 1: 
                            raise ValueError(get_text('messages.invalid_weight_positive', lang, ip=ip))
                        weight = int(weight_str)
                    if not is_valid_ip(ip): 
                        raise ValueError(get_text('messages.invalid_ip', lang))
                    new_ips_data.append({"ip": ip, "weight": weight})
                if not new_ips_data: 
                    raise ValueError(get_text('messages.bulk_no_selection', lang))
                value_to_save = new_ips_data
            
            elif field == 'rotation_interval_range':
                parts = [p.strip() for p in text.split(',') if p.strip()]
                min_h, max_h = 0, 0
                if len(parts) == 1 and float(parts[0]) > 0: 
                    min_h = max_h = float(parts[0])
                elif len(parts) == 2 and float(parts[0]) > 0 and float(parts[1]) >= float(parts[0]): 
                    min_h, max_h = float(parts[0]), float(parts[1])
                else: 
                    raise ValueError(get_text('messages.invalid_number_range', lang))
                
                config = load_config()
                policy = config['load_balancer_policies'][policy_index]
                policy_name = policy.get('policy_name')
                policy.update({'rotation_min_hours': min_h, 'rotation_max_hours': max_h})
                save_config(config)

                if policy_name and 'health_status' in context.bot_data and policy_name in context.bot_data['health_status']:
                    context.bot_data['health_status'][policy_name].pop('lb_next_rotation_time', None)
                    logger.info(f"Reset 'lb_next_rotation_time' for policy '{policy_name}' due to interval change.")

                await send_or_edit(update, context, get_text('messages.policy_field_updated', lang, field=get_text('field_names.rotation_interval_hours', lang)))
                await lb_policy_view_callback(update, context, force_new_message=True)
                return
                
            elif field == 'backup_ips':
                ips = [ip.strip() for ip in text.split(',') if ip.strip()]
                if not ips or not all(is_valid_ip(ip) for ip in ips): 
                    raise ValueError(get_text('messages.invalid_ip', lang))
                value_to_save = ips
            
            elif field == 'primary_ip':
                 if not is_valid_ip(text): 
                     raise ValueError(get_text('messages.invalid_ip', lang))
                 value_to_save = text
            
            elif field == 'check_port':
                if not text.isdigit() or not (1 <= int(text) <= 65535): 
                    raise ValueError(get_text('messages.invalid_port', lang))
                value_to_save = int(text)
            
            elif field in ['failover_minutes', 'failback_minutes']:
                 if not text.replace('.', '', 1).isdigit() or float(text) <= 0: 
                     raise ValueError(get_text('messages.invalid_number', lang))
                 value_to_save = float(text)
            
            else:
                value_to_save = text

            config = load_config()
            policy_list_key = 'load_balancer_policies' if policy_type == 'lb' else 'failover_policies'
            config[policy_list_key][policy_index][field] = value_to_save
            save_config(config)
            
            field_name = get_text(f'field_names.{field}', lang)
            await send_or_edit(update, context, get_text('messages.policy_field_updated', lang, field=field_name))
            
            view_callback = lb_policy_view_callback if policy_type == 'lb' else failover_policy_view_callback
            await view_callback(update, context, force_new_message=True)

        except (ValueError, IndexError) as e:
            context.user_data['edit_policy_field'] = field
            await send_or_edit(update, context, f"❌ {e}")
            return
        
    elif context.user_data.get('awaiting_threshold'):
        if not text.isdigit() or int(text) < 1:
            await send_or_edit(update, context, get_text('messages.threshold_invalid_message', lang))
            return

        threshold = int(text)
        selected_nodes = context.user_data.get('policy_selected_nodes', [])
        
        if not selected_nodes:
            await send_or_edit(update, context, "No monitoring locations were selected. The process has been cancelled.")
            for key in ['awaiting_threshold', 'editing_policy_type', 'edit_policy_index', 'monitoring_type', 'policy_selected_nodes', 'is_wizard_manual_setup', 'new_group_name']:
                context.user_data.pop(key, None)
            return

        if threshold > len(selected_nodes):
            error_text = get_text('messages.nodes_updated_message', lang, count=len(selected_nodes), monitoring_type='selected') + \
                         f"\n\n<b>❌ {get_text('messages.threshold_too_high_message', lang, threshold=threshold, count=len(selected_nodes))}</b>"
            await send_or_edit(update, context, error_text, parse_mode="HTML")
            context.user_data['awaiting_threshold'] = True
            return

        context.user_data.pop('awaiting_threshold')
        config = load_config()
        
        if context.user_data.get('editing_policy_type') == 'group':
            group_name = context.user_data.pop('new_group_name')
            config.setdefault("monitoring_groups", {})[group_name] = {"nodes": selected_nodes, "threshold": threshold}
            save_config(config)
            
            for key in ['editing_policy_type', 'policy_selected_nodes', 'last_callback_query']:
                context.user_data.pop(key, None)
            
            success_text = get_text('messages.group_created_success', lang, group_name=escape_html(group_name))
            buttons = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="groups_menu")]]
            await send_or_edit(update, context, success_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

        else:
            is_wizard_flow = context.user_data.pop('is_wizard_manual_setup', False)
            policy_type = context.user_data.get('editing_policy_type')
            policy_index = context.user_data.get('edit_policy_index')
            monitoring_type = context.user_data.get('monitoring_type')

            if policy_type == 'lb':
                config['load_balancer_policies'][policy_index]['monitoring_nodes'] = selected_nodes
                config['load_balancer_policies'][policy_index]['threshold'] = threshold
            elif policy_type == 'failover':
                if is_wizard_flow or monitoring_type == 'primary':
                    config['failover_policies'][policy_index]['primary_monitoring_nodes'] = selected_nodes
                    config['failover_policies'][policy_index]['primary_threshold'] = threshold
                if is_wizard_flow:
                    config['failover_policies'][policy_index]['backup_monitoring_nodes'] = selected_nodes
                    config['failover_policies'][policy_index]['backup_threshold'] = threshold
                elif monitoring_type == 'backup':
                     config['failover_policies'][policy_index]['backup_monitoring_nodes'] = selected_nodes
                     config['failover_policies'][policy_index]['backup_threshold'] = threshold
            
            save_config(config)

            for key in ['editing_policy_type', 'edit_policy_index', 'monitoring_type', 'policy_selected_nodes']:
                context.user_data.pop(key, None)

            if is_wizard_flow:
                policy_data = context.user_data.pop('wizard_data', {})
                text_msg = get_text('messages.wizard_rule_created', lang, 
                                type_display="Failover" if policy_type == 'failover' else "Load Balancer",
                                policy_name=escape_html(policy_data.get('policy_name', 'N/A')))
                buttons = [[InlineKeyboardButton(get_text('buttons.settings', lang), callback_data="go_to_settings")]]
                await send_or_edit(update, context, text_msg, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
            else:
                type_text = get_text(f'messages.monitoring_type_{monitoring_type}', lang)
                success_text = get_text('messages.threshold_updated_message', lang, monitoring_type=type_text, threshold=threshold)
                back_callback = f"lb_policy_edit|{policy_index}" if policy_type == 'lb' else f"failover_policy_edit|{policy_index}"
                buttons = [[InlineKeyboardButton(get_text('buttons.back_to_edit_menu_button', lang), callback_data=back_callback)]]
                await send_or_edit(update, context, success_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

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

    elif "edit" in context.user_data:
        data = context.user_data.pop("edit")
        record_id = data["id"]
        context.user_data["confirm"] = {"id": record_id, "type": data["type"], "name": data["name"], "old": data["old"], "new": text}
        context.user_data['last_text_update'] = update
        kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="confirm_change")],
              [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"select|{record_id}")]]
        safe_old, safe_new = escape_html(data['old']), escape_html(text)
        try:
            await update.message.delete()
            if context.user_data.get('last_callback_query'):
                 await context.user_data.pop('last_callback_query').message.delete()
        except Exception: pass
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🔄 <code>{safe_old}</code> ➡️ <code>{safe_new}</code>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

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
                await send_or_edit(update, context, get_text('messages.subdomain_exists', lang), InlineKeyboardMarkup(buttons))
            else:
                context.user_data["new_name"] = new_name
                context.user_data["add_step"] = "content"
                prompt_text_key = 'prompts.enter_ip' if context.user_data.get("new_type") in ['A', 'AAAA'] else 'prompts.enter_content'
                await send_or_edit(update, context, get_text(prompt_text_key, lang, name=escape_html(text.strip())), parse_mode="HTML")
        elif step == "content":
            context.user_data["new_content"] = text
            context.user_data.pop("add_step")
            kb = [[InlineKeyboardButton("DNS Only", callback_data="add_proxied|false")], [InlineKeyboardButton("Proxied", callback_data="add_proxied|true")]]
            await send_or_edit(update, context, get_text('prompts.choose_proxy', lang), InlineKeyboardMarkup(kb))

    elif context.user_data.get('is_bulk_ip_change'):
        selected_ids = context.user_data.get('selected_records', [])
        context.user_data.pop('is_bulk_ip_change')
        context.user_data['bulk_ip_confirm_details'] = {'new_ip': text, 'record_ids': selected_ids}
        kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="bulk_change_ip_execute")],
              [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data="bulk_cancel")]]
        await send_or_edit(update, context, get_text('messages.bulk_confirm_change_ip', lang, count=len(selected_ids), new_ip=text), InlineKeyboardMarkup(kb))
            
    else:
        logger.warning(
            f"User {update.effective_user.id} sent text '{text}' but no active state was found to handle it."
        )

async def zones_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles pagination for the zones list."""
    query = update.callback_query
    await query.answer()
    page = int(query.data.split('|')[1])
    await display_zones_list(update, context, page=page)

async def select_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    nickname = query.data.split('|')[1]
    clear_state(context)
    context.user_data['selected_account_nickname'] = nickname
    await display_zones_list(update, context, page=0)

async def back_to_accounts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(context, preserve=[])
    await display_account_list(update, context)

async def refresh_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('records_list_cache', None)
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
    clear_state(context, preserve=['language', 'selected_account_nickname'])
    await display_zones_list(update, context, page=0)

async def back_to_records_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_page = context.user_data.get('current_page', 0)
    preserve_keys = ['language', 'selected_account_nickname', 'all_zones', 'selected_zone_id', 'selected_zone_name', 'all_records', 'records']
    clear_state(context, preserve=preserve_keys)
    await display_records_list(update, context, page=current_page)

async def list_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE): await display_records_list(update, context, page=int(update.callback_query.data.split('|')[1]))

    
async def select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    lang = get_user_lang(context)
    query = update.callback_query
    
    rid = None
    if query and not getattr(query, 'is_dummy', False):
        rid = query.data.split('|')[1]
    elif 'selected_record_id_for_view' in context.user_data:
        rid = context.user_data.pop('selected_record_id_for_view')
    
    if not rid:
        await send_or_edit(update, context, get_text('messages.session_expired_error', lang), force_new_message=force_new_message)
        return

    if "records" not in context.user_data:
        token = get_current_token(context)
        zone_id = context.user_data.get('selected_zone_id')
        if token and zone_id:
            all_records = await get_dns_records(token, zone_id)
            if all_records is not None:
                context.user_data['all_records'] = all_records
                context.user_data["records"] = {r['id']: r for r in all_records}

    record = context.user_data.get("records", {}).get(rid)
    if not record:
        await send_or_edit(update, context, get_text('messages.no_records_found', lang), force_new_message=force_new_message)
        return
        
    proxy_text = get_text('messages.proxy_status_active', lang) if record.get('proxied') else get_text('messages.proxy_status_inactive', lang)
    
    config = load_config()
    zone_id = context.user_data.get('selected_zone_id')
    alias_key = f"{record['type']}:{record['name']}"
    alias = config.get('record_aliases', {}).get(zone_id, {}).get(alias_key)
    
    if alias:
        text = get_text('messages.record_details_with_alias', lang, 
                        alias=escape_html(alias),
                        type=record['type'], 
                        name=escape_html(record['name']), 
                        content=escape_html(record['content']), 
                        proxy_status=proxy_text)
    else:
        text = get_text('messages.record_details', lang, 
                        type=record['type'], 
                        name=escape_html(record['name']), 
                        content=escape_html(record['content']), 
                        proxy_status=proxy_text)

    kb = [
        [InlineKeyboardButton(get_text('buttons.edit_value', lang), callback_data=f"edit|{rid}")],
        [InlineKeyboardButton(get_text('buttons.set_record_alias', lang), callback_data=f"set_record_alias_start|{rid}")],
        [InlineKeyboardButton(get_text('buttons.change_type', lang), callback_data=f"change_type|{rid}")],
        [InlineKeyboardButton(get_text('buttons.move_record', lang), callback_data=f"move_record_start|{rid}")],
        [InlineKeyboardButton(get_text('buttons.toggle_proxy', lang), callback_data=f"toggle_proxy|{rid}")],
        [InlineKeyboardButton(get_text('buttons.delete', lang), callback_data=f"delete|{rid}")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_records_list")]
    ]    
                    
    await send_or_edit(update, context, text, InlineKeyboardMarkup(kb), force_new_message=force_new_message)

async def move_record_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the record move/copy process."""
    query = update.callback_query
    await query.answer()
    
    rid = query.data.split('|')[1]
    context.user_data['move_record_rid'] = rid
    
    if len(CF_ACCOUNTS) > 1:
        lang = get_user_lang(context)
        buttons = [[InlineKeyboardButton(nickname, callback_data=f"move_select_dest_account|{nickname}")] for nickname in CF_ACCOUNTS.keys()]
        buttons.append([InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data=f"select|{rid}")])
        text = get_text('prompts.choose_destination_account', lang)
        await send_or_edit(update, context, text, InlineKeyboardMarkup(buttons))
    else:
        single_account_nickname = list(CF_ACCOUNTS.keys())[0]
        await display_destination_zones(update, context, single_account_nickname)

async def move_select_dest_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the selection of the destination account."""
    query = update.callback_query
    await query.answer()
    
    dest_account_nickname = query.data.split('|')[1]
    context.user_data['move_dest_account_nickname'] = dest_account_nickname
    await display_destination_zones(update, context, dest_account_nickname)

async def display_destination_zones(update: Update, context: ContextTypes.DEFAULT_TYPE, account_nickname: str):
    """Fetches and displays the list of destination zones for the user to choose from."""
    lang = get_user_lang(context)
    
    token = CF_ACCOUNTS.get(account_nickname)
    if not token:
        await send_or_edit(update, context, get_text('messages.error_no_token', lang)); return
        
    await send_or_edit(update, context, get_text('messages.fetching_zones', lang))
    
    all_zones = await get_all_zones(token)
    
    context.user_data['all_zones_for_move'] = all_zones
    
    source_zone_id = context.user_data.get('selected_zone_id')
    destination_zones = [zone for zone in all_zones if zone['id'] != source_zone_id]
    
    if not destination_zones:
        await send_or_edit(update, context, "No other zones found in this account to move the record to.")
        return

    buttons = [[InlineKeyboardButton(zone['name'], callback_data=f"move_select_dest_zone|{zone['id']}")] for zone in destination_zones]
    
    rid = context.user_data.get('move_record_rid')
    if rid:
        buttons.append([InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data=f"select|{rid}")])
        
    text = get_text('prompts.choose_destination_zone', lang)
    await send_or_edit(update, context, text, InlineKeyboardMarkup(buttons))

async def move_select_dest_zone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the selection of the destination zone and performs the copy action."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)

    try:
        dest_zone_id = query.data.split('|')[1]
        rid = context.user_data['move_record_rid']
        record = context.user_data.get("records", {}).get(rid)
        if not record: raise ValueError("Source record not found")
    except (IndexError, ValueError, KeyError):
        await send_or_edit(update, context, get_text('messages.internal_error', lang)); return

    await send_or_edit(update, context, get_text('messages.move_record_in_progress', lang))

    dest_token = None
    dest_account_nickname = context.user_data.get('move_dest_account_nickname')
    if dest_account_nickname:
        dest_token = CF_ACCOUNTS.get(dest_account_nickname)
    elif len(CF_ACCOUNTS) == 1:
        dest_token = list(CF_ACCOUNTS.values())[0]

    if not dest_token:
        await send_or_edit(update, context, get_text('messages.error_no_token', lang)); return
    
    source_zone_name = context.user_data.get('selected_zone_name')
    short_name = get_short_name(record['name'], source_zone_name)

    all_zones_in_dest_account = context.user_data.get('all_zones_for_move', [])
    dest_zone = next((z for z in all_zones_in_dest_account if z['id'] == dest_zone_id), None)
    if not dest_zone:
        await send_or_edit(update, context, "Error: Destination zone not found in cache."); return
    dest_zone_name = dest_zone['name']

    new_record_name = dest_zone_name if short_name == '@' else f"{short_name}.{dest_zone_name}"

    res = await create_record(
        token=dest_token,
        zone_id=dest_zone_id,
        rtype=record['type'],
        name=new_record_name,
        content=record['content'],
        proxied=record.get('proxied', False)
    )

    if res.get("success"):
        await send_or_edit(update, context, get_text('messages.move_record_success', lang, dest_zone_name=dest_zone_name))
        await asyncio.sleep(1)

        buttons = [
            [InlineKeyboardButton(get_text('buttons.action_delete_source', lang), callback_data=f"move_delete_source|{rid}")],
            [InlineKeyboardButton(get_text('buttons.action_copy_only', lang), callback_data="move_copy_complete")]
        ]
        text = get_text('messages.move_record_ask_delete', lang, source_zone_name=source_zone_name)
        await send_or_edit(update, context, text, InlineKeyboardMarkup(buttons))
    else:
        error_msg = res.get('errors', [{}])[0].get('message', 'Unknown error')
        await send_or_edit(update, context, get_text('messages.error_creating_record', lang, error=error_msg))

async def move_delete_source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes the original record after a successful copy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        rid = query.data.split('|')[1]
        token = get_current_token(context)
        zone_id = context.user_data.get('selected_zone_id')
    except (IndexError, KeyError):
        await send_or_edit(update, context, get_text('messages.internal_error', lang)); return
    
    res = await delete_record(token, zone_id, rid)

    if res.get("success"):
        await send_or_edit(update, context, get_text('messages.move_record_source_deleted', lang))
    else:
        await send_or_edit(update, context, get_text('messages.error_deleting_record', lang))
    
    for key in ['move_record_rid', 'move_dest_account_nickname']: context.user_data.pop(key, None)
    context.user_data.pop('all_records', None)
    await asyncio.sleep(2)
    await display_records_list(update, context)

async def move_copy_complete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finalizes the process when the user chooses to only copy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)

    await send_or_edit(update, context, get_text('messages.move_record_copy_complete', lang))
    
    for key in ['move_record_rid', 'move_dest_account_nickname']: context.user_data.pop(key, None)
    context.user_data.pop('all_records', None)
    await asyncio.sleep(2)
    await display_records_list(update, context)

async def change_record_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a list of all possible DNS record types for the user to choose from."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)

    try:
        rid = query.data.split('|')[1]
        record = context.user_data.get("records", {}).get(rid)
        if not record: raise ValueError("Record not found")
    except (IndexError, ValueError):
        await send_or_edit(update, context, get_text('messages.internal_error', lang)); return

    buttons = [InlineKeyboardButton(t, callback_data=f"change_type_select|{rid}|{t}") for t in DNS_RECORD_TYPES]
    buttons_in_rows = chunk_list(buttons, 3)
    buttons_in_rows.append([InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data=f"select|{rid}")])

    text = get_text('prompts.choose_new_record_type', lang, record_name=escape_html(record['name']))
    await send_or_edit(update, context, text, InlineKeyboardMarkup(buttons_in_rows))

async def change_record_type_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the user's selection of a new record type and prompts for the new content."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)

    try:
        _, rid, new_type = query.data.split('|')
        record = context.user_data.get("records", {}).get(rid)
        if not record: raise ValueError("Record not found")
    except (IndexError, ValueError):
        await send_or_edit(update, context, get_text('messages.internal_error', lang)); return

    context.user_data['change_type_data'] = {
        "rid": rid,
        "new_type": new_type
    }
    
    prompt_key = 'prompts.enter_new_content_for_record'
    if new_type in ['A', 'AAAA']:
        prompt_key = 'prompts.enter_new_ip_for_record'
    elif new_type == 'CNAME':
        prompt_key = 'prompts.enter_new_cname_for_record'
        
    text = get_text(prompt_key, lang, record_name=escape_html(record['name']))
    await send_or_edit(update, context, text)

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
    
    safe_short_name = escape_html(record_short_name)
    prompt_text = get_text(prompt_key, lang, name=f"<code>{safe_short_name}</code>")
    await update.callback_query.message.reply_text(prompt_text, parse_mode="HTML")

async def toggle_proxy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid)
    current_status = get_text('messages.proxy_status_active', lang) if record.get('proxied') else get_text('messages.proxy_status_inactive', lang)
    new_status = get_text('messages.proxy_status_inactive', lang) if record.get('proxied') else get_text('messages.proxy_status_active', lang)
    kb = [[InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data=f"toggle_proxy_confirm|{rid}")],
          [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"select|{rid}")]]          
    safe_record_name = escape_html(record['name'])
    text = get_text('messages.confirm_proxy_toggle', lang, record_name=f"<code>{safe_record_name}</code>", current_status=current_status, new_status=new_status)
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

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
    safe_record_name = escape_html(record['name'])
    await update.callback_query.edit_message_text(
        get_text('messages.confirm_delete_record', lang, record_name=f"<code>{safe_record_name}</code>"),
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML"
    )

async def delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    token = get_current_token(context)
    if not token: return
    rid = update.callback_query.data.split('|')[1]
    record = context.user_data.get("records", {}).get(rid, {})
    res = await delete_record(token, context.user_data['selected_zone_id'], rid)
    if res.get("success"):
        safe_name = escape_html(record.get('name', 'N/A'))
        await update.callback_query.edit_message_text(
            get_text('messages.record_deleted_successfully', lang, record_name=f"<code>{safe_name}</code>"),
            parse_mode="HTML"
        )
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
    
    record_id = info.get("id")
    if not record_id:
        await query.edit_message_text(get_text('messages.session_expired_error', lang))
        return

    original_record = context.user_data.get("records", {}).get(record_id, {})
    proxied_status = original_record.get("proxied", False)
    
    res = await update_record(token, context.user_data['selected_zone_id'], record_id, info["type"], info["name"], info["new"], proxied_status)
    
    if res.get("success"):
        safe_name = escape_html(info['name'])
        await query.edit_message_text(
            get_text('messages.record_updated_successfully', lang, record_name=f"<code>{safe_name}</code>"),
            parse_mode="HTML"
        )
        
        context.user_data.pop('all_records', None)
        context.user_data.pop('records_list_cache', None)
        context.user_data.pop('records', None)
        
        await asyncio.sleep(1)
        
        original_update = context.user_data.pop('last_text_update', update)
        context.user_data['selected_record_id_for_view'] = record_id
        
        await select_callback(original_update, context, force_new_message=True)
        
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
        safe_name = escape_html(name)
        await update.callback_query.edit_message_text(
            get_text('messages.record_added_successfully', lang, rtype=rtype, name=f"<code>{safe_name}</code>"),
            parse_mode="HTML"
        )
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
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
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
    safe_new_ip = escape_html(new_ip)
    await query.edit_message_text(get_text('messages.bulk_change_ip_progress', lang, count=len(record_ids), new_ip=f"<code>{safe_new_ip}</code>"),
                                  parse_mode="HTML")
                                  
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
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
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
        [InlineKeyboardButton(get_text('buttons.standalone_monitoring', lang), callback_data="monitors_menu")],
        [InlineKeyboardButton(get_text('buttons.manage_failover_policies', lang), callback_data="settings_failover_policies")],
        [InlineKeyboardButton(get_text('buttons.manage_lb_policies', lang), callback_data="settings_lb_policies")],
        [InlineKeyboardButton(get_text('buttons.monitoring_groups', lang), callback_data="groups_menu")],
        [
            InlineKeyboardButton(get_text('buttons.get_status', lang), callback_data="status_refresh"),
            InlineKeyboardButton(get_text('buttons.reporting_and_stats', lang), callback_data="reporting_menu")
        ],
        [InlineKeyboardButton(get_text('buttons.manage_notifications', lang), callback_data="settings_notifications")],
    ]

    if is_super_admin(update):
        buttons.insert(3, [InlineKeyboardButton(get_text('buttons.user_management', lang), callback_data="user_management_menu")])

    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_records_list")])
    
    reply_markup = InlineKeyboardMarkup(buttons)
    text = get_text('messages.settings_menu', lang)
    await send_or_edit(update, context, text, reply_markup)

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
    except error.BadRequest as e:
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
            safe_name = escape_html(policy.get('policy_name', 'N/A'))
            safe_ip = escape_html(policy.get('primary_ip', 'N/A'))
            policies_text += f"<b>{i+1}. {safe_name}</b>\n<code>{safe_ip}</code>\n\n"
            buttons.append([InlineKeyboardButton(f"{i+1}. {policy.get('policy_name', 'N/A')}", callback_data=f"failover_policy_view|{i}")])
            
    text = get_text('messages.policies_list_menu', lang, policies_text=policies_text)
    buttons.append([InlineKeyboardButton(get_text('buttons.add_policy', lang), callback_data="failover_policy_add_start")])
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")])
    
    try:
        if query: await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    except error.BadRequest as e:
        if "Message is not modified" not in str(e): raise e

async def lb_policy_add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process of adding a new Load Balancing policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    context.user_data['add_policy_type'] = 'lb'
    context.user_data['new_policy_data'] = {
        'rotation_algorithm': 'round_robin'
    }
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
        
    config = load_config()
    aliases = config.get('zone_aliases', {})

    sorted_zones = sorted(zones, key=lambda z: aliases.get(z['id'], z['name']))

    buttons = []
    for zone in sorted_zones:
        button_text = aliases.get(zone['id'], zone['name'])
        buttons.append([InlineKeyboardButton(button_text, callback_data=f"lb_policy_set_zone|{zone['name']}")])

    context.user_data['add_policy_step'] = 'zone_name'
    await query.edit_message_text(get_text('prompts.choose_zone_for_policy', lang), reply_markup=InlineKeyboardMarkup(buttons))

async def display_lb_ip_management_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays an interactive menu for managing IPs in a Load Balancer pool."""
    query = update.callback_query
    if query:
        await query.answer()
    
    lang = get_user_lang(context)
    
    try:
        policy_index = context.user_data.get('edit_policy_index')
        if policy_index is None:
            await send_or_edit(update, context, get_text('messages.session_expired_error', lang))
            return

        config = load_config()
        policy = config['load_balancer_policies'][policy_index]
        policy_name = policy.get('policy_name', 'N/A')
        ips_with_weights = normalize_ip_list(policy.get('ips', []))

    except (IndexError, KeyError):
        await send_or_edit(update, context, get_text('messages.error_policy_not_found', lang))
        return

    message_parts = [get_text('messages.lb_ip_management_header', lang, policy_name=escape_html(policy_name))]
    buttons = []

    if not ips_with_weights:
        message_parts.append(get_text('messages.lb_ip_management_no_ips', lang))
    else:
        sorted_ips = sorted(ips_with_weights, key=lambda x: x['ip'])
        for ip_info in sorted_ips:
            original_index = ips_with_weights.index(ip_info)
            ip = ip_info['ip']
            weight = ip_info['weight']
            
            message_parts.append(get_text('messages.lb_ip_management_list_item', lang, ip=ip, weight=weight))
            
            buttons.append([
                InlineKeyboardButton(get_text('buttons.edit_ip_address', lang), callback_data=f"lb_ip_edit_address_start|{original_index}"),
                InlineKeyboardButton(get_text('buttons.edit_ip_weight', lang), callback_data=f"lb_ip_edit_weight_start|{original_index}"),
                InlineKeyboardButton(get_text('buttons.delete_ip', lang, ip=''), callback_data=f"lb_ip_delete_start|{original_index}")
            ])
    
    buttons.append([InlineKeyboardButton(get_text('buttons.add_new_ip', lang), callback_data="lb_ip_add_start")])
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_edit_menu_button', lang), callback_data=f"lb_policy_edit|{policy_index}")])
    
    await send_or_edit(update, context, "\n".join(message_parts), reply_markup=InlineKeyboardMarkup(buttons))

async def lb_ip_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    """(Step 1) Displays a list of IPs in the pool, where each IP is a button."""
    query = update.callback_query
    if query and not getattr(query, 'is_dummy', False):
        await query.answer()
        
    lang = get_user_lang(context)
    try:
        policy_index = context.user_data['edit_policy_index']
        config = load_config()
        policy = config['load_balancer_policies'][policy_index]
        policy_name = policy.get('policy_name', 'N/A')
        ips_with_weights = normalize_ip_list(policy.get('ips', []))
    except (IndexError, KeyError):
        await send_or_edit(update, context, get_text('messages.error_policy_not_found', lang), force_new_message=force_new_message)
        return

    message = get_text('messages.lb_ip_management_header', lang, policy_name=escape_html(policy_name))
    buttons = []
    if not ips_with_weights:
        message += "\n" + get_text('messages.lb_ip_management_no_ips', lang)
    else:
        sorted_ips = sorted(ips_with_weights, key=lambda x: x['ip'])
        for ip_info in sorted_ips:
            original_index = -1
            for i, original_ip_info in enumerate(ips_with_weights):
                if original_ip_info == ip_info:
                    original_index = i
                    break
            
            if original_index != -1:
                buttons.append([InlineKeyboardButton(f"{ip_info['ip']} (Weight: {ip_info['weight']})", callback_data=f"lb_ip_select|{original_index}")])
    
    buttons.append([InlineKeyboardButton(get_text('buttons.add_new_ip', lang), callback_data="lb_ip_add_start")])
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_edit_menu_button', lang), callback_data=f"lb_policy_edit|{policy_index}")])
    
    await send_or_edit(update, context, message, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML", force_new_message=force_new_message)

async def lb_ip_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Step 2) Displays the edit menu for a specific selected IP."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    try:
        ip_index = int(query.data.split('|')[1])
        policy_index = context.user_data['edit_policy_index']
        config = load_config()
        ip_info = config['load_balancer_policies'][policy_index]['ips'][ip_index]
        context.user_data['lb_ip_action_index'] = ip_index
        text = get_text('messages.edit_ip_menu_header', lang, ip=ip_info['ip'], weight=ip_info['weight'])
        buttons = [
            [InlineKeyboardButton(get_text('buttons.edit_ip_address', lang), callback_data=f"lb_ip_edit_start|address")],
            [InlineKeyboardButton(get_text('buttons.edit_ip_weight', lang), callback_data=f"lb_ip_edit_start|weight")],
            [InlineKeyboardButton(get_text('buttons.delete_this_ip', lang), callback_data="lb_ip_delete_prompt")],
            [InlineKeyboardButton(get_text('buttons.back_to_ip_list', lang), callback_data="lb_ip_list_menu")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    except (IndexError, KeyError, ValueError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))

async def lb_ip_add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Step 2 - Add) Prompts the user to enter new IP(s) to add to the pool."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)

    try:
        for key in ['add_step', 'edit_policy_field', 'state']:
            context.user_data.pop(key, None)
        
        context.user_data['state'] = 'awaiting_lb_new_ip'
        
        text = get_text('prompts.enter_new_ips_to_add', lang)
        buttons = [[InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data="lb_ip_list_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    except KeyError:
        await query.edit_message_text(get_text('messages.session_expired_error', lang))

async def lb_ip_edit_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Step 3) Prompts the user for a new value (IP or weight)."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    edit_type = query.data.split('|')[1]
    try:
        ip_index = context.user_data['lb_ip_action_index']
        policy_index = context.user_data['edit_policy_index']
        config = load_config()
        ip_info = config['load_balancer_policies'][policy_index]['ips'][ip_index]
        
        for key in ['add_step', 'edit_policy_field', 'state']: context.user_data.pop(key, None)
        
        if edit_type == 'address':
            context.user_data['state'] = 'awaiting_lb_ip_address'
            prompt_key = 'prompts.enter_new_ip_for_entry'
            text = get_text(prompt_key, lang, old_ip=ip_info['ip'])
        else:
            context.user_data['state'] = 'awaiting_lb_ip_weight'
            prompt_key = 'prompts.enter_new_weight_for_ip'
            text = get_text(prompt_key, lang, ip=ip_info['ip'])
            
        buttons = [[InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data=f"lb_ip_select|{ip_index}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    except (IndexError, KeyError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))

async def lb_ip_delete_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Step 3 - Delete) Asks for confirmation before deleting an IP."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    try:
        ip_index = context.user_data['lb_ip_action_index']
        policy_index = context.user_data['edit_policy_index']
        config = load_config()
        ip_to_delete = config['load_balancer_policies'][policy_index]['ips'][ip_index]['ip']
        text = get_text('prompts.confirm_delete_ip', lang, ip=ip_to_delete)
        buttons = [
            [InlineKeyboardButton(get_text('buttons.confirm_action', lang), callback_data="lb_ip_delete_confirm")],
            [InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"lb_ip_select|{ip_index}")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    except (IndexError, KeyError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))

async def lb_ip_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Step 4 - Delete) Deletes the IP and refreshes the list."""
    query = update.callback_query
    lang = get_user_lang(context)
    try:
        ip_index = context.user_data.pop('lb_ip_action_index')
        policy_index = context.user_data['edit_policy_index']
        config = load_config()
        config['load_balancer_policies'][policy_index]['ips'].pop(ip_index)
        save_config(config)
        await query.answer(get_text('messages.ip_deleted_successfully', lang), show_alert=True)
        await lb_ip_list_menu(update, context)
    except (IndexError, KeyError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))

async def lb_policy_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    query = update.callback_query
    if query: await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = context.user_data.get('edit_policy_index')
        if policy_index is None and query and '|' in query.data:
            policy_index = int(query.data.split('|')[1])

        if policy_index is None:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=get_text('messages.session_expired_error', lang))
            return
        
        context.user_data['edit_policy_index'] = policy_index
        context.user_data['editing_policy_type'] = 'lb'
        config = load_config()
        policy = config['load_balancer_policies'][policy_index]
        current_algo = policy.get('rotation_algorithm', 'random')
        algo_name_display = "Random" if current_algo == 'random' else "Round-Robin"
        algo_text = get_text('buttons.change_algorithm', lang, algo_name=algo_name_display)

    except (IndexError, ValueError):
        await context.bot.send_message(chat_id=update.effective_chat.id, text=get_text('messages.error_policy_not_found', lang))
        return

    buttons = [
        [InlineKeyboardButton(get_text('buttons.edit_lb_policy_name', lang), callback_data=f"lb_policy_edit_field|policy_name"),
         InlineKeyboardButton(get_text('buttons.edit_lb_port', lang), callback_data=f"lb_policy_edit_field|check_port")],
        [InlineKeyboardButton(get_text('buttons.edit_lb_ips', lang), callback_data=f"lb_policy_edit_field|ips"),
         InlineKeyboardButton(get_text('buttons.edit_lb_interval', lang), callback_data=f"lb_policy_edit_field|rotation_interval_range")],
        [InlineKeyboardButton(get_text('buttons.edit_lb_records', lang), callback_data=f"lb_policy_edit_field|record_names")],
        [InlineKeyboardButton(get_text('buttons.edit_lb_group', lang), callback_data="policy_change_group_start|lb")],
        [InlineKeyboardButton(algo_text, callback_data=f"lb_policy_change_algo|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"lb_policy_view|{policy_index}")]
    ]
    
    text = get_text('messages.edit_lb_policy_menu', lang)
    reply_markup = InlineKeyboardMarkup(buttons)

    try:
        if query and not force_new_message:
            await query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)
    except error.BadRequest as e:
        if "Message is not modified" not in str(e): 
            logger.error(f"Error in lb_policy_edit_callback: {e}")

async def lb_policy_edit_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the selection of a field to edit in an LB policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    field_to_edit = query.data.split('|')[1]

    if field_to_edit == 'ips':
        await lb_ip_list_menu(update, context)
        return
    
    if field_to_edit == 'record_names':
        context.user_data['is_editing_policy_records'] = True
        policy_index = context.user_data['edit_policy_index']
        config = load_config()
        policy = config['load_balancer_policies'][policy_index]
        context.user_data['policy_selected_records'] = policy.get('record_names', [])
        await display_records_for_selection(update, context, page=0)
        return

    context.user_data['edit_policy_field'] = field_to_edit
    
    if field_to_edit == 'rotation_interval_range':
        prompt_key = "prompts.enter_new_lb_rotation_interval_hours"
    else:
        prompt_key = f"prompts.enter_new_lb_{field_to_edit}"
        
    text_to_send = get_text(prompt_key, lang)
    await send_or_edit(update, context, text_to_send)

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

async def settings_lb_policies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
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
            safe_name = escape_html(policy.get('policy_name', 'N/A'))
            normalized_ips = normalize_ip_list(policy.get('ips', []))
            ip_strings = [item['ip'] for item in normalized_ips]
            safe_ips = escape_html(", ".join(ip_strings))
            policies_text += f"<b>{i+1}. {safe_name}</b>\n<code>{safe_ips}</code>\n\n"
            buttons.append([InlineKeyboardButton(f"{i+1}. {policy.get('policy_name', 'N/A')}", callback_data=f"lb_policy_view|{i}")])
            
    text = get_text('messages.lb_policies_list_menu', lang, policies_text=policies_text)
    buttons.append([InlineKeyboardButton(get_text('buttons.add_lb_policy', lang), callback_data="lb_policy_add_start")])
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")])
    
    await send_or_edit(update, context, text, InlineKeyboardMarkup(buttons))

async def lb_policy_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    query = update.callback_query
    if query: await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_index = context.user_data.get('edit_policy_index')
        if policy_index is None and query and '|' in query.data:
            policy_index = int(query.data.split('|')[1])
        if policy_index is None:
            await send_or_edit(update, context, get_text('messages.session_expired_error', lang)); return
        
        config = load_config()
        policy = config['load_balancer_policies'][policy_index]
        context.user_data['edit_policy_index'] = policy_index
        context.user_data['editing_policy_type'] = 'lb'
    except (IndexError, ValueError):
        if query: await query.edit_message_text(get_text('messages.error_policy_not_found', lang)); return
        
    is_enabled = policy.get('enabled', True)
    status_text = get_text('messages.status_enabled', lang) if is_enabled else get_text('messages.status_disabled', lang)
    
    min_h, max_h = policy.get('rotation_min_hours'), policy.get('rotation_max_hours')
    if min_h and max_h:
        if min_h == max_h: interval_text = f"<code>{get_text('messages.interval_display_fixed', lang, hours=min_h)}</code>"
        else: interval_text = f"<code>{get_text('messages.interval_display_random', lang, min_hours=min_h, max_hours=max_h)}</code>"
    else: interval_text = f"<code>{policy.get('rotation_interval_hours', 'N/A')} hours</code>"

    ips_with_weights = normalize_ip_list(policy.get('ips', []))
    ips_str = "\n".join([f"<code>{escape_html(ip['ip'])}</code> (Weight: {ip['weight']})" for ip in ips_with_weights]) or f"<code>{get_text('messages.not_set', lang)}</code>"
    records_str = f"<code>{escape_html(', '.join(policy.get('record_names', [])))}</code>" if policy.get('record_names') else f"<code>{get_text('messages.not_set', lang)}</code>"
    
    current_algo = policy.get('rotation_algorithm', 'random')
    algo_display_name = "Weighted Random" if current_algo == 'random' else "Weighted Round-Robin"
    algo_field_name = get_text('policy_fields.rotation_algorithm', lang)

    monitoring_group = policy.get('monitoring_group', get_text('messages.not_set', lang))
    is_maintenance = policy.get('maintenance_mode', False)
    maintenance_status_text = get_text('messages.status_enabled', lang) if is_maintenance else get_text('messages.status_disabled', lang)

    details_parts = [
        f"<b>{get_text('policy_fields.name', lang)}:</b> <code>{escape_html(policy.get('policy_name', 'N/A'))}</code>",
        f"<b>{get_text('policy_fields.status', lang)}:</b> {status_text}",
        f"\n<b>{get_text('policy_fields.ip_pool', lang)}:</b>\n{ips_str}\n",
        f"<b>{get_text('policy_fields.rotation_interval', lang)}:</b> {interval_text}",
        f"<b>{algo_field_name}:</b> <code>{algo_display_name}</code>",
        f"<b>{get_text('policy_fields.health_check_port', lang)}:</b> <code>{escape_html(str(policy.get('check_port', 'N/A')))}</code>",
        f"<b>{get_text('policy_fields.monitoring_group', lang)}:</b> <code>{escape_html(monitoring_group)}</code>",
        f"<b>{get_text('policy_fields.account', lang)}:</b> <code>{escape_html(policy.get('account_nickname', 'N/A'))}</code>",
        f"<b>{get_text('policy_fields.zone', lang)}:</b> <code>{escape_html(policy.get('zone_name', 'N/A'))}</code>",
        f"<b>{get_text('policy_fields.monitored_records', lang)}:</b> {records_str}",
        f"<b>{get_text('policy_fields.maintenance_mode', lang)}:</b> {maintenance_status_text}"
    ]
    details_text = "\n".join(details_parts)
    
    toggle_btn_text = get_text('buttons.disable_policy', lang) if is_enabled else get_text('buttons.enable_policy', lang)
    toggle_maint_btn = get_text('buttons.exit_maintenance', lang) if is_maintenance else get_text('buttons.enter_maintenance', lang)
    buttons = [
        [InlineKeyboardButton(get_text('buttons.edit_policy', lang), callback_data=f"lb_policy_edit|{policy_index}"),
         InlineKeyboardButton(toggle_btn_text, callback_data=f"lb_policy_toggle|{policy_index}")],
        [InlineKeyboardButton(toggle_maint_btn, callback_data=f"toggle_maintenance|lb|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.clone_policy', lang), callback_data=f"clone_policy_start|lb|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.delete_policy', lang), callback_data=f"lb_policy_delete|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.manual_check', lang), callback_data=f"manual_check|lb|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="settings_lb_policies")]
    ]
    
    await send_or_edit(update, context, details_text, InlineKeyboardMarkup(buttons))

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
    except error.BadRequest as e:
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
    if query:
        await query.answer()
    await show_settings_notifications_menu(update, context)

async def show_settings_notifications_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main menu for notification settings, listing all configurable items."""
    query = update.callback_query
    if query:
        await query.answer()
    lang = get_user_lang(context)
    config = load_config()

    buttons = []
    buttons.append([InlineKeyboardButton(get_text('buttons.default_recipients', lang), callback_data="notification_edit_recipients|__default__")])

    for i, policy in enumerate(config.get("failover_policies", [])):
        policy_name = policy.get('policy_name')
        if policy_name:
            buttons.append([InlineKeyboardButton(get_text('buttons.item_recipients_policy', lang, item_name=policy_name), callback_data=f"notification_edit_recipients|failover|{i}")])

    for i, policy in enumerate(config.get("load_balancer_policies", [])):
        policy_name = policy.get('policy_name')
        if policy_name:
            buttons.append([InlineKeyboardButton(get_text('buttons.item_recipients_lb_policy', lang, item_name=policy_name), callback_data=f"notification_edit_recipients|lb|{i}")])

    for i, monitor in enumerate(config.get("standalone_monitors", [])):
        monitor_name = monitor.get('monitor_name')
        if monitor_name:
            buttons.append([InlineKeyboardButton(get_text('buttons.item_recipients_monitor', lang, item_name=monitor_name), callback_data=f"notification_edit_recipients|monitor|{i}")])
    
    buttons.append([InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")])
    
    text = get_text('messages.select_recipient_item_prompt', lang)
    await send_or_edit(update, context, text, InlineKeyboardMarkup(buttons))


    
async def notification_edit_recipients_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    """Displays the current recipients for a selected item and options to edit them."""
    query = update.callback_query
    if query:
        await query.answer()
    lang = get_user_lang(context)

    recipient_key = None
    item_name_display = ""
    config = load_config()

    data_parts = []
    if query and query.data and query.data.startswith("notification_edit_recipients|"):
        data_parts = query.data.split('|')
    
    if len(data_parts) > 1:
        if data_parts[1] == "__default__":
            recipient_key = "__default__"
            item_name_display = get_text('buttons.default_recipients', lang)
        elif len(data_parts) == 3:
            item_type, item_index_str = data_parts[1], data_parts[2]
            try:
                item_index = int(item_index_str)
                item_name = None
                
                if item_type == "failover":
                    item_name = config['failover_policies'][item_index]['policy_name']
                    recipient_key = f"__policy__{item_name}"
                elif item_type == "lb":
                    item_name = config['load_balancer_policies'][item_index]['policy_name']
                    recipient_key = f"__policy__{item_name}"
                elif item_type == "monitor":
                    item_name = config['standalone_monitors'][item_index]['monitor_name']
                    recipient_key = f"__monitor__{item_name}"
                
                if item_name:
                    item_name_display = item_name

            except (IndexError, ValueError, KeyError) as e:
                logger.error(f"Error parsing recipient callback data: {e}", exc_info=True)
                await send_or_edit(update, context, get_text('messages.session_expired_try_again', lang))
                return
    
    if not recipient_key and 'current_recipient_key' in context.user_data:
        recipient_key = context.user_data['current_recipient_key']
        if recipient_key == "__default__":
            item_name_display = get_text('buttons.default_recipients', lang)
        else:
            item_name_display = recipient_key.split('__', 2)[-1]

    if not recipient_key:
        await send_or_edit(update, context, get_text('messages.session_expired_try_again', lang))
        return

    context.user_data['current_recipient_key'] = recipient_key
    recipients_map = config.get("notifications", {}).get("recipients", {})
    members = recipients_map.get(recipient_key, [])
    members_str = ", ".join(f"<code>{member}</code>" for member in members) if members else get_text('messages.no_recipients_configured', lang)
    text = get_text('messages.recipient_edit_header', lang, item_name=escape_html(item_name_display), members_str=members_str)
    
    buttons = [
        [
            InlineKeyboardButton(get_text('buttons.add_member', lang), callback_data=f"notification_add_member_start"),
            InlineKeyboardButton(get_text('buttons.remove_member', lang), callback_data=f"notification_remove_member_start")
        ],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="settings_notifications")]
    ]
    await send_or_edit(update, context, text, InlineKeyboardMarkup(buttons), force_new_message=force_new_message)


async def notification_add_member_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts the user to enter new member IDs."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    context.user_data['state'] = 'awaiting_notification_recipient'
    
    recipient_key = context.user_data.get('current_recipient_key')
    if not recipient_key:
        await query.edit_message_text(get_text('messages.session_expired_try_again', lang))
        return

    buttons = [[InlineKeyboardButton(get_text('buttons.cancel_action', lang), callback_data=f"notification_edit_recipients|{recipient_key}")]]
    prompt_msg = await query.edit_message_text(
        get_text('prompts.enter_recipient_ids', lang),
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    context.user_data['last_menu_message_id'] = prompt_msg.message_id


async def notification_remove_member_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a list of current members to be removed."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    recipient_key = context.user_data.get('current_recipient_key')
    if not recipient_key:
        await query.edit_message_text(get_text('messages.session_expired_try_again', lang))
        return

    config = load_config()
    recipients_map = config.get("notifications", {}).get("recipients", {})
    members = recipients_map.get(recipient_key, [])
    
    if not members:
        await query.answer(get_text('messages.no_members_to_remove', lang), show_alert=True)
        return
        
    buttons = [[InlineKeyboardButton(str(member_id), callback_data=f"notification_remove_member_execute|{member_id}")] for member_id in members]
    buttons.append([InlineKeyboardButton(get_text('buttons.cancel', lang), callback_data=f"notification_edit_recipients|{recipient_key}")])
    
    await query.edit_message_text(get_text('prompts.select_member_to_remove', lang), reply_markup=InlineKeyboardMarkup(buttons))


async def notification_remove_member_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes the selected member from the list."""
    query = update.callback_query
    lang = get_user_lang(context)
    
    try:
        _, member_id_to_remove_str = query.data.split('|', 1)
        member_id_to_remove = int(member_id_to_remove_str)
        recipient_key = context.user_data.get('current_recipient_key')
    except (ValueError, KeyError):
        await query.edit_message_text(get_text('messages.session_expired_try_again', lang))
        return
    
    if not recipient_key:
        await query.edit_message_text(get_text('messages.session_expired_try_again', lang))
        return

    config = load_config()
    recipients_map = config.get("notifications", {}).get("recipients", {})
    
    if recipient_key in recipients_map and member_id_to_remove in recipients_map[recipient_key]:
        recipients_map[recipient_key].remove(member_id_to_remove)
        save_config(config)
        await query.answer(get_text('messages.member_removed_successfully', lang), show_alert=True)
    
    await notification_edit_recipients_callback(update, context)

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
        
    config = load_config()
    aliases = config.get('zone_aliases', {})

    sorted_zones = sorted(zones, key=lambda z: aliases.get(z['id'], z['name']))

    buttons = []
    for zone in sorted_zones:
        button_text = aliases.get(zone['id'], zone['name'])
        buttons.append([InlineKeyboardButton(button_text, callback_data=f"failover_policy_set_zone|{zone['name']}")])
    
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
            await send_or_edit(update, context, get_text('messages.session_expired_error', lang)); return
        
        config = load_config()
        policy = config['failover_policies'][policy_index]
        context.user_data['edit_policy_index'] = policy_index
        context.user_data['editing_policy_type'] = 'failover'
    except (IndexError, ValueError):
        await send_or_edit(update, context, get_text('messages.error_policy_not_found', lang)); return

    is_enabled = policy.get('enabled', True)
    status_text = get_text('messages.status_enabled', lang) if is_enabled else get_text('messages.status_disabled', lang)
    is_failback_enabled = policy.get('auto_failback', True)
    failback_status_text = get_text('messages.status_enabled', lang) if is_failback_enabled else get_text('messages.status_disabled', lang)
    is_maintenance = policy.get('maintenance_mode', False)
    maintenance_status_text = get_text('messages.status_enabled', lang) if is_maintenance else get_text('messages.status_disabled', lang)

    primary_group = policy.get('primary_monitoring_group', get_text('messages.not_set', lang))
    backup_group = policy.get('backup_monitoring_group', get_text('messages.not_set', lang))
    
    details_parts = [
        f"<b>{get_text('policy_fields.name', lang)}:</b> <code>{escape_html(policy.get('policy_name', 'N/A'))}</code>",
        f"<b>{get_text('policy_fields.primary_ip', lang)}:</b> <code>{escape_html(policy.get('primary_ip', 'N/A'))}</code> (Port: <code>{escape_html(str(policy.get('check_port', 'N/A')))}</code>)",
        f"<b>{get_text('policy_fields.backup_ips', lang)}:</b> <code>{escape_html(', '.join(policy.get('backup_ips', [])))}</code>",
        f"<b>{get_text('policy_fields.account', lang)}:</b> <code>{escape_html(policy.get('account_nickname', 'N/A'))}</code>",
        f"<b>{get_text('policy_fields.zone', lang)}:</b> <code>{escape_html(policy.get('zone_name', 'N/A'))}</code>",
        f"<b>{get_text('policy_fields.monitored_records', lang)}:</b> <code>{escape_html(', '.join(policy.get('record_names', [])))}</code>",
        f"<b>{get_text('policy_fields.primary_monitoring_group', lang)}:</b> <code>{escape_html(primary_group)}</code>",
        f"<b>{get_text('policy_fields.backup_monitoring_group', lang)}:</b> <code>{escape_html(backup_group)}</code>",
        f"<b>{get_text('policy_fields.status', lang)}:</b> {status_text}",
        f"<b>{get_text('policy_fields.auto_failback', lang)}:</b> {failback_status_text}",
        f"<b>{get_text('policy_fields.maintenance_mode', lang)}:</b> {maintenance_status_text}"
    ]
    details_text = "\n".join(details_parts)
    
    toggle_btn = get_text('buttons.disable_policy', lang) if is_enabled else get_text('buttons.enable_policy', lang)
    toggle_fb_btn = get_text('buttons.disable_failback', lang) if is_failback_enabled else get_text('buttons.enable_failback', lang)
    toggle_maint_btn = get_text('buttons.exit_maintenance', lang) if is_maintenance else get_text('buttons.enter_maintenance', lang)
    buttons = [
        [InlineKeyboardButton(get_text('buttons.edit_policy', lang), callback_data=f"failover_policy_edit|{policy_index}"),
         InlineKeyboardButton(toggle_btn, callback_data=f"failover_policy_toggle|{policy_index}")],
        [InlineKeyboardButton(toggle_fb_btn, callback_data=f"failover_policy_toggle_failback|{policy_index}")],
        [InlineKeyboardButton(toggle_maint_btn, callback_data=f"toggle_maintenance|failover|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.manual_check', lang), callback_data=f"manual_check|failover|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.clone_policy', lang), callback_data=f"clone_policy_start|failover|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.delete_policy', lang), callback_data=f"failover_policy_delete|{policy_index}")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="settings_failover_policies")]
    ]
    
    await send_or_edit(update, context, details_text, InlineKeyboardMarkup(buttons))

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
        [InlineKeyboardButton(get_text('buttons.edit_primary_group', lang), callback_data="policy_change_group_start|primary")],
        [InlineKeyboardButton(get_text('buttons.edit_backup_group', lang), callback_data="policy_change_group_start|backup")],
        [InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=f"failover_policy_view|{policy_index}")]
    ]
    
    text = get_text('messages.edit_policy_menu', lang)
    reply_markup = InlineKeyboardMarkup(buttons)
    
    try:
        if query and not force_new_message:
            await query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)
    except error.BadRequest as e:
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
    except error.BadRequest as e:
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

# --- Wizard Functions ---
async def wizard_final_step_ask_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wizard Final Step: Ask for monitoring preset."""
    start_time = context.user_data.get('wizard_start_time')
    if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        context.user_data.pop('wizard_start_time', None)
        query = update.callback_query
        await query.answer("فرآیند راه‌اندازی سریع منقضی شده است.", show_alert=True)
        await query.edit_message_text("فرآیند لغو شد. لطفاً با /wizard دوباره شروع کنید.")
        return
    lang = get_user_lang(context)
    context.user_data['wizard_step'] = 'select_monitoring'

    config = load_config()
    monitoring_groups = config.get("monitoring_groups", {})
    
    buttons = []
    
    if monitoring_groups:
        group_buttons = []
        for group_name in sorted(monitoring_groups.keys()):
            group_buttons.append(InlineKeyboardButton(f"📍 {group_name}", callback_data=f"wizard_set_monitoring|group_{group_name}"))
        
        for i in range(0, len(group_buttons), 2):
            buttons.append(group_buttons[i:i + 2])

    buttons.extend([
        [InlineKeyboardButton(get_text('buttons.wizard_monitoring_global', lang), callback_data="wizard_set_monitoring|global")],
        [InlineKeyboardButton(get_text('buttons.wizard_monitoring_regional', lang), callback_data="wizard_set_monitoring|regional")],
        [InlineKeyboardButton(get_text('buttons.wizard_monitoring_manual', lang), callback_data="wizard_set_monitoring|manual")]
    ])
    
    await send_or_edit(update, context, get_text('messages.wizard_final_step', lang), InlineKeyboardMarkup(buttons))

async def wizard_set_monitoring_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves the monitoring preset/group, creates the rule, and ends the wizard."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        selection = query.data.split('|', 1)[1]
    except IndexError:
        await query.edit_message_text("An error occurred. Wizard cancelled.")
        return

    policy_data = context.user_data['wizard_data']
    policy_type = policy_data['type']
    config = load_config()

    if selection.startswith("group_"):
        group_name = selection.replace("group_", "", 1)
        
        if policy_type == 'failover':
            policy_data['primary_monitoring_group'] = group_name
            policy_data['backup_monitoring_group'] = group_name
            policy_data.setdefault('failover_minutes', 2.0)
            policy_data.setdefault('auto_failback', True)
            policy_data.setdefault('failback_minutes', 5.0)
            config.setdefault('failover_policies', []).append(policy_data)
        else:
            policy_data['monitoring_group'] = group_name
            policy_data.setdefault('rotation_min_hours', 2.0)
            policy_data.setdefault('rotation_max_hours', 6.0)
            policy_data['rotation_algorithm'] = 'round_robin'
            config.setdefault('load_balancer_policies', []).append(policy_data)
            
        save_config(config)
        
        for key in ['wizard_data', 'wizard_step', 'last_callback_query', 'wizard_zones_cache']:
            context.user_data.pop(key, None)
        
        text = get_text('messages.wizard_rule_created', lang, 
                        type_display="Failover" if policy_type == 'failover' else "Load Balancer",
                        policy_name=escape_html(policy_data['policy_name']))
        
        buttons = [[InlineKeyboardButton(get_text('buttons.settings', lang), callback_data="go_to_settings")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    nodes = []
    threshold = 1
    
    if selection == 'global':
        nodes = ["fi1.node.check-host.net", "fr1.node.check-host.net", "fr2.node.check-host.net", "ru1.node.check-host.net", "ru2.node.check-host.net"]
        threshold = 3
    elif selection == 'regional':
        nodes = ["ru3.node.check-host.net", "ru4.node.check-host.net", "tr1.node.check-host.net", "us2.node.check-host.net"]
        threshold = 2
    
    if selection == 'manual':
        if policy_type == 'failover':
            policy_data.setdefault('failover_minutes', 2.0)
            policy_data.setdefault('auto_failback', True)
            policy_data.setdefault('failback_minutes', 10.0)
            config.setdefault('failover_policies', []).append(policy_data)
            policy_index = len(config['failover_policies']) - 1
            monitoring_type = 'primary'
        else:
            policy_data.setdefault('rotation_min_hours', 2.0)
            policy_data.setdefault('rotation_max_hours', 6.0)
            config.setdefault('load_balancer_policies', []).append(policy_data)
            policy_index = len(config['load_balancer_policies']) - 1
            monitoring_type = 'lb'
        save_config(config)

        context.user_data['edit_policy_index'] = policy_index
        context.user_data['editing_policy_type'] = policy_type
        context.user_data['monitoring_type'] = monitoring_type
        context.user_data['policy_selected_nodes'] = []

        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        
        await query.edit_message_text(get_text('messages.wizard_manual_monitoring_prompt', lang))
        await asyncio.sleep(2)
        await display_countries_for_selection(update, context, page=0)
        return

    if policy_type == 'failover':
        group_name = f"wizard_{selection}_{len(config.get('monitoring_groups', {})) + 1}"
        config.setdefault("monitoring_groups", {})[group_name] = {"nodes": nodes, "threshold": threshold}
        
        policy_data['primary_monitoring_group'] = group_name
        policy_data['backup_monitoring_group'] = group_name
        policy_data.setdefault('failover_minutes', 2.0)
        policy_data.setdefault('auto_failback', True)
        policy_data.setdefault('failback_minutes', 5.0)
        
        config.setdefault('failover_policies', []).append(policy_data)
        save_config(config)
        
    else:
        group_name = f"wizard_{selection}_{len(config.get('monitoring_groups', {})) + 1}"
        config.setdefault("monitoring_groups", {})[group_name] = {"nodes": nodes, "threshold": threshold}

        policy_data['monitoring_group'] = group_name
        policy_data.setdefault('rotation_min_hours', 2.0)
        policy_data.setdefault('rotation_max_hours', 6.0)
        policy_data['rotation_algorithm'] = 'round_robin'
        
        config.setdefault('load_balancer_policies', []).append(policy_data)
        save_config(config)

    for key in ['wizard_data', 'wizard_step', 'last_callback_query', 'wizard_zones_cache']:
        context.user_data.pop(key, None)
    
    text = get_text('messages.wizard_rule_created', lang, 
                    type_display="Failover" if policy_type == 'failover' else "Load Balancer",
                    policy_name=escape_html(policy_data['policy_name']))
    
    buttons = [[InlineKeyboardButton(get_text('buttons.settings', lang), callback_data="go_to_settings")]]
    reply_markup = InlineKeyboardMarkup(buttons)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")

async def wizard_step6_ask_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wizard Step 6: Display account list for the user to choose."""
    start_time = context.user_data.get('wizard_start_time')
    if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        context.user_data.pop('wizard_start_time', None)
        query = update.callback_query
        await query.answer("فرآیند راه‌اندازی سریع منقضی شده است.", show_alert=True)
        await query.edit_message_text("فرآیند لغو شد. لطفاً با /wizard دوباره شروع کنید.")
        return
    lang = get_user_lang(context)
    context.user_data['wizard_step'] = 'select_account'

    try:
        await update.message.delete()
    except Exception:
        pass

    buttons = [[InlineKeyboardButton(nickname, callback_data=f"wizard_select_account|{nickname}")] for nickname in CF_ACCOUNTS.keys()]
    buttons.append([InlineKeyboardButton(get_text('buttons.wizard_cancel', lang), callback_data="wizard_cancel")])
    reply_markup = InlineKeyboardMarkup(buttons)
    
    text = get_text('messages.wizard_port_saved', lang) + "\n\n" + get_text('prompts.choose_cf_account', lang)
    
    await update.effective_chat.send_message(
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def wizard_select_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wizard callback for when an account is selected."""
    start_time = context.user_data.get('wizard_start_time')
    if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        context.user_data.pop('wizard_start_time', None)
        query = update.callback_query
        await query.answer("فرآیند راه‌اندازی سریع منقضی شده است.", show_alert=True)
        await query.edit_message_text("فرآیند لغو شد. لطفاً با /wizard دوباره شروع کنید.")
        return
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        nickname = query.data.split('|')[1]
        context.user_data['wizard_data']['account_nickname'] = nickname
        token = CF_ACCOUNTS.get(nickname)
        logger.info(f"WIZARD: Account '{nickname}' selected. Fetching zones...")
        await query.edit_message_text(get_text('messages.fetching_zones', lang))
        zones = await get_all_zones(token)
        logger.info(f"WIZARD: Found {len(zones)} zones for account '{nickname}'.")
        if not zones:
            logger.warning("WIZARD: No zones found. Cancelling wizard.")
            await query.edit_message_text("No zones found for this account. Wizard cancelled.")
            context.user_data.pop('wizard_data', None)
            context.user_data.pop('wizard_step', None)
            return

        buttons = [[InlineKeyboardButton(zone['name'], callback_data=f"wizard_select_zone|{zone['id']}")] for zone in zones]
        buttons.append([InlineKeyboardButton(get_text('buttons.wizard_cancel', lang), callback_data="wizard_cancel")])
        
        context.user_data['wizard_step'] = 'select_zone'
        context.user_data['wizard_zones_cache'] = {z['id']: z for z in zones}
        
        logger.info("WIZARD: Displaying zone list to user.")
        await query.edit_message_text(get_text('prompts.choose_zone_for_policy', lang), reply_markup=InlineKeyboardMarkup(buttons))
        logger.info("WIZARD: Zone list displayed successfully.")

    except Exception as e:
        logger.error(f"!!! An error occurred in wizard_select_account_callback: {e} !!!", exc_info=True)
        await send_or_edit(update, context, "An unexpected error occurred. The wizard has been cancelled.")
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)

async def wizard_select_zone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wizard callback for when a zone is selected."""
    start_time = context.user_data.get('wizard_start_time')
    if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        context.user_data.pop('wizard_start_time', None)
        query = update.callback_query
        await query.answer("فرآیند راه‌اندازی سریع منقضی شده است.", show_alert=True)
        await query.edit_message_text("فرآیند لغو شد. لطفاً با /wizard دوباره شروع کنید.")
        return
    query = update.callback_query
    context.user_data['last_callback_query'] = query
    await query.answer()
    try:
        zone_id = query.data.split('|')[1]
        zone_name = context.user_data['wizard_zones_cache'][zone_id]['name']
        context.user_data['wizard_data']['zone_name'] = zone_name
    except (IndexError, KeyError):
        await query.edit_message_text("An error occurred (could not find zone data). Wizard cancelled.")
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        return
    
    context.user_data['add_policy_type'] = context.user_data['wizard_data']['type']
    context.user_data['new_policy_data'] = context.user_data['wizard_data']
    context.user_data['is_editing_policy_records'] = False
    context.user_data['policy_selected_records'] = []
    context.user_data['wizard_step'] = 'select_records'
    await display_records_for_selection(update, context, page=0)

async def wizard_edit_last_message(context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    """Helper function to edit the last message sent by the bot during the wizard."""
    start_time = context.user_data.get('wizard_start_time')
    if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        context.user_data.pop('wizard_start_time', None)
        query = update.callback_query
        await query.answer("فرآیند راه‌اندازی سریع منقضی شده است.", show_alert=True)
        await query.edit_message_text("فرآیند لغو شد. لطفاً با /wizard دوباره شروع کنید.")
        return
    if context.user_data.get('last_callback_query'):
        try:
            last_msg = context.user_data['last_callback_query'].message
            await last_msg.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
            return
        except Exception as e:
            logger.error(f"Wizard helper could not edit message: {e}")
    await context.bot.send_message(chat_id=context.user_data['last_callback_query'].message.chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML")


async def wizard_step3_ask_ips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wizard Step 3: Ask for the primary IP (Failover) or IP pool (LB)."""
    start_time = context.user_data.get('wizard_start_time')
    if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        context.user_data.pop('wizard_start_time', None)
        query = update.callback_query
        await query.answer("فرآیند راه‌اندازی سریع منقضی شده است.", show_alert=True)
        await query.edit_message_text("فرآیند لغو شد. لطفاً با /wizard دوباره شروع کنید.")
        return
    lang = get_user_lang(context)
    try:
        if context.user_data.get('last_callback_query'):
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['last_callback_query'].message.message_id
            )
    except Exception:
        pass
    policy_type = context.user_data['wizard_data']['type']
    if policy_type == 'failover':
        context.user_data['wizard_step'] = 'ask_primary_ip'
        text = get_text('messages.wizard_name_saved', lang) + get_text('messages.wizard_ask_primary_ip', lang)
    else:
        context.user_data['wizard_step'] = 'ask_lb_ips'
        text = get_text('messages.wizard_name_saved', lang) + get_text('messages.wizard_ask_lb_ips', lang)
    buttons = [[InlineKeyboardButton(get_text('buttons.wizard_cancel', lang), callback_data="wizard_cancel")]]
    reply_markup = InlineKeyboardMarkup(buttons)
    
    await context.bot.send_message(chat_id=update.effective_chat.id,text=text,reply_markup=reply_markup,parse_mode="HTML")

async def wizard_step4_ask_backup_ips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wizard Step 4 (Failover only): Ask for backup IPs."""
    start_time = context.user_data.get('wizard_start_time')
    if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        context.user_data.pop('wizard_start_time', None)
        query = update.callback_query
        await query.answer("فرآیند راه‌اندازی سریع منقضی شده است.", show_alert=True)
        await query.edit_message_text("فرآیند لغو شد. لطفاً با /wizard دوباره شروع کنید.")
        return
    lang = get_user_lang(context)
    context.user_data['wizard_step'] = 'ask_backup_ips'
    try:
        await update.message.delete()
    except Exception: pass
    text = get_text('messages.wizard_ip_saved', lang) + get_text('messages.wizard_ask_backup_ips', lang)
    buttons = [[InlineKeyboardButton(get_text('buttons.wizard_cancel', lang), callback_data="wizard_cancel")]]
    await update.effective_chat.send_message(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def wizard_step5_ask_port(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wizard Step 5: Ask for the health check port."""
    start_time = context.user_data.get('wizard_start_time')
    if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        context.user_data.pop('wizard_start_time', None)
        query = update.callback_query
        await query.answer("فرآیند راه‌اندازی سریع منقضی شده است.", show_alert=True)
        await query.edit_message_text("فرآیند لغو شد. لطفاً با /wizard دوباره شروع کنید.")
        return
    lang = get_user_lang(context)
    context.user_data['wizard_step'] = 'ask_port'

    try:
        await update.message.delete()
    except Exception: pass

    saved_text = get_text('messages.wizard_ips_saved', lang)
    text = saved_text + get_text('messages.wizard_ask_port', lang)
    buttons = [[InlineKeyboardButton(get_text('buttons.wizard_cancel', lang), callback_data="wizard_cancel")]]
    await update.effective_chat.send_message(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def wizard_step3_ask_ips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wizard Step 3: Asks for the IP after receiving the name."""
    lang = get_user_lang(context)
    try:
        await update.message.delete()
    except Exception:
        pass
    try:
        if 'last_callback_query' in context.user_data and context.user_data['last_callback_query']:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['last_callback_query'].message.message_id
            )
            context.user_data.pop('last_callback_query', None) 
    except Exception:
        pass

    policy_type = context.user_data['wizard_data']['type']
    if policy_type == 'failover':
        context.user_data['wizard_step'] = 'ask_primary_ip'
        text_to_send = get_text('messages.wizard_name_saved', lang) + get_text('messages.wizard_ask_primary_ip', lang)
    else:
        context.user_data['wizard_step'] = 'ask_lb_ips'
        text_to_send = get_text('messages.wizard_name_saved', lang) + get_text('messages.wizard_ask_lb_ips', lang)

    buttons = [[InlineKeyboardButton(get_text('buttons.wizard_cancel', lang), callback_data="wizard_cancel")]]
    reply_markup = InlineKeyboardMarkup(buttons)
    
    await update.effective_chat.send_message(
        text=text_to_send,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def wizard_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels the wizard and clears the state."""
    start_time = context.user_data.get('wizard_start_time')
    if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        context.user_data.pop('wizard_start_time', None)
        query = update.callback_query
        await query.answer("فرآیند راه‌اندازی سریع منقضی شده است.", show_alert=True)
        await query.edit_message_text("فرآیند لغو شد. لطفاً با /wizard دوباره شروع کنید.")
        return
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    context.user_data.pop('wizard_data', None)
    context.user_data.pop('wizard_step', None)
    
    text = get_text('messages.wizard_cancelled', lang)
    await query.edit_message_text(text)

async def wizard_set_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wizard Step 2: Save the policy type and ask for a name."""
    start_time = context.user_data.get('wizard_start_time')
    if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        context.user_data.pop('wizard_start_time', None)
        query = update.callback_query
        await query.answer("فرآیند راه‌اندازی سریع منقضی شده است.", show_alert=True)
        await query.edit_message_text("فرآیند لغو شد. لطفاً با /wizard دوباره شروع کنید.")
        return
    query = update.callback_query
    context.user_data['last_callback_query'] = query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_type = query.data.split('|')[1]
    except IndexError:
        await query.edit_message_text("An error occurred. Please try again.")
        return
        
    context.user_data['wizard_data']['type'] = policy_type
    context.user_data['wizard_step'] = 'ask_name'
    
    type_display = "Failover" if policy_type == 'failover' else "Load Balancer"
    
    text = get_text('messages.wizard_ask_name', lang, type_display=type_display)
    
    buttons = [[InlineKeyboardButton(get_text('buttons.wizard_cancel', lang), callback_data="wizard_cancel")]]
    reply_markup = InlineKeyboardMarkup(buttons)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")

async def wizard_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the setup wizard, either from /wizard command or button."""
    if not is_admin(update): return
    
    clear_state(context)
    context.user_data['wizard_start_time'] = datetime.now()
    await wizard_step1_ask_type(update, context)

async def wizard_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the 'Start Wizard' button."""
    if not is_admin(update): return
    query = update.callback_query
    context.user_data['last_callback_query'] = query
    await query.answer()
    
    clear_state(context)
    context.user_data['wizard_start_time'] = datetime.now()
    await wizard_step1_ask_type(update, context)

async def wizard_step1_ask_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wizard Step 1: Ask the user which type of policy they want to create."""
    start_time = context.user_data.get('wizard_start_time')
    if not start_time or (datetime.now() - start_time) > timedelta(minutes=10):
        context.user_data.pop('wizard_data', None)
        context.user_data.pop('wizard_step', None)
        context.user_data.pop('wizard_start_time', None)
        query = update.callback_query
        await query.answer("فرآیند راه‌اندازی سریع منقضی شده است.", show_alert=True)
        await query.edit_message_text("فرآیند لغو شد. لطفاً با /wizard دوباره شروع کنید.")
        return
    lang = get_user_lang(context)
    
    context.user_data['wizard_data'] = {}
    context.user_data['wizard_step'] = 'ask_type'
    
    buttons = [
        [
            InlineKeyboardButton(get_text('buttons.wizard_failover', lang), callback_data="wizard_set_type|failover"),
            InlineKeyboardButton(get_text('buttons.wizard_lb', lang), callback_data="wizard_set_type|lb")
        ],
        [InlineKeyboardButton(get_text('buttons.wizard_cancel', lang), callback_data="wizard_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    
    text = get_text('messages.wizard_welcome', lang)
    
    await send_or_edit(update, context, text, reply_markup, parse_mode="HTML")

# --- Command Handlers ---
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a real-time summary of all monitoring policies in a single message."""
    if not is_admin(update): return
    
    lang = get_user_lang(context)
    
    await send_or_edit(update, context, get_text('messages.status_fetching', lang))

    config = load_config()
    last_health_results = context.bot_data.get('last_health_results', {})
    status_data = context.bot_data.get('health_status', {})
    
    failover_policies = config.get("failover_policies", [])
    lb_policies = config.get("load_balancer_policies", [])
    all_policies = [('failover', p) for p in failover_policies] + [('lb', p) for p in lb_policies]

    if not all_policies:
        buttons = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")]]
        await send_or_edit(update, context, get_text('messages.status_no_policies', lang), reply_markup=InlineKeyboardMarkup(buttons))
        return

    direction_char = "\u200F" if lang == 'fa' else "\u200E"
    message_parts = [direction_char + get_text('messages.status_header', lang)]
    
    for i, (policy_type, policy) in enumerate(all_policies):
        index = i + 1
        policy_name = policy.get('policy_name', 'Unnamed')
        line = ""
        icon = "🛡️" if policy_type == 'failover' else "🚦"

        if policy.get('maintenance_mode', False):
            maintenance_text = get_text('messages.status_in_maintenance', lang)
            line = f"{escape_html(policy_name)}: {maintenance_text}"
        
        elif not policy.get('enabled', True):
            line = get_text('messages.status_policy_disabled', lang, policy_name=escape_html(policy_name))
        elif policy_type == 'failover':
            primary_ip = policy.get('primary_ip')
            backup_ips = policy.get('backup_ips', [])
            policy_status = status_data.get(policy_name, {})
            is_primary_online = last_health_results.get(primary_ip, True)
            if policy_status.get('downtime_start') and not is_primary_online:
                line = get_text('messages.status_failover_grace_period', lang, policy_name=escape_html(policy_name), ip=primary_ip)
            elif not is_primary_online:
                healthy_backup = next((ip for ip in backup_ips if last_health_results.get(ip, True)), None)
                line = get_text('messages.status_failover_active', lang, policy_name=escape_html(policy_name), ip=healthy_backup) if healthy_backup else get_text('messages.status_failover_all_down', lang, policy_name=escape_html(policy_name))
            else:
                line = get_text('messages.status_failover_ok', lang, policy_name=escape_html(policy_name), ip=primary_ip)
        else:
            ips = [item['ip'] for item in normalize_ip_list(policy.get('ips', []))]
            healthy_ips = [ip for ip in ips if last_health_results.get(ip, True)]
            if not healthy_ips:
                line = get_text('messages.status_lb_all_down', lang, policy_name=escape_html(policy_name))
            else:
                active_ip = status_data.get(policy_name, {}).get('active_ip', 'Unknown')
                line = get_text('messages.status_lb_ok', lang, policy_name=escape_html(policy_name), ip=active_ip)
        
        message_parts.append(f"{direction_char}<b>{index}.</b> {icon} {line}")

    last_check_time = context.bot_data.get('last_health_check_time')
    if last_check_time:
        time_diff = int((datetime.now() - last_check_time).total_seconds())
        footer = get_text('messages.status_last_updated', lang, time_diff=time_diff)
        message_parts.append(f"\n{direction_char}{footer}")
        
    buttons = []
    row = []
    for i, (policy_type, policy) in enumerate(all_policies):
        policy_name = policy.get('policy_name', 'Unnamed')
        short_name = policy_name[:20] + '...' if len(policy_name) > 20 else policy_name

        icon = "🛡️" if policy_type == 'failover' else "🚦"
        button_text = f"{icon} {i + 1}. {short_name}"
        
        row.append(InlineKeyboardButton(button_text, callback_data=f"status_select_policy|{i}"))
        
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(get_text('buttons.refresh_status', lang), callback_data="status_refresh"),
        InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data="back_to_settings_main")
    ])
    
    full_message = "\n".join(message_parts)
    await send_or_edit(update, context, full_message, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def status_select_policy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the action menu for a policy selected from the status message."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        policy_global_index = int(query.data.split('|')[1])
        
        config = load_config()
        failover_policies = config.get("failover_policies", [])
        lb_policies = config.get("load_balancer_policies", [])
        
        policy_type = 'failover'
        policy_index_in_type = policy_global_index
        
        if policy_global_index >= len(failover_policies):
            policy_type = 'lb'
            policy_index_in_type = policy_global_index - len(failover_policies)
            policy = lb_policies[policy_index_in_type]
        else:
            policy = failover_policies[policy_index_in_type]
            
        policy_name = policy.get("policy_name", "N/A")

        text = get_text('messages.status_action_menu_header', lang, policy_name=escape_html(policy_name))
        
        buttons = [[
            InlineKeyboardButton(get_text('buttons.show_logs', lang), callback_data=f"show_policy_log|{policy_type}|{policy_index_in_type}"),
            InlineKeyboardButton(get_text('buttons.go_to_settings_short', lang), callback_data=f"{policy_type}_policy_view|{policy_index_in_type}"),
            InlineKeyboardButton(get_text('buttons.manual_check_short', lang), callback_data=f"manual_check|{policy_type}|{policy_index_in_type}")
        ],[
            InlineKeyboardButton(get_text('buttons.back_to_status_list', lang), callback_data="status_refresh")
        ]]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

    except (IndexError, KeyError, ValueError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))

async def show_policy_log_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Filters and displays the last 10 events for a specific policy."""
    query = update.callback_query
    await query.answer()
    lang = get_user_lang(context)
    
    try:
        _, policy_type, policy_index_str = query.data.split('|')
        policy_index = int(policy_index_str)
        
        config = load_config()
        policy_list_key = "load_balancer_policies" if policy_type == 'lb' else "failover_policies"
        policy = config[policy_list_key][policy_index]
        policy_name = policy.get("policy_name", "N/A")

        all_logs = load_monitoring_log()
        
        policy_logs = [e for e in all_logs if e.get('policy_name') == policy_name and e.get('event_type') in ['FAILOVER', 'FAILBACK', 'LB_ROTATION']]
        
        message_parts = [get_text('messages.policy_log_header', lang, policy_name=escape_html(policy_name))]

        if not policy_logs:
            message_parts.append(get_text('messages.policy_log_no_events', lang))
        else:
            for event in policy_logs[-10:]:
                utc_time = datetime.fromisoformat(event['timestamp'])
                local_time = utc_time.astimezone(USER_TIMEZONE)
                ts = local_time.strftime('%Y-%m-%d %H:%M')
                event_type = event['event_type']
                line = ""
                
                if event_type == 'FAILOVER':
                    line = get_text('messages.log_event_failover', lang, from_ip=event.get('from_ip', '?'), to_ip=event.get('to_ip', '?'))
                elif event_type == 'FAILBACK':
                    line = get_text('messages.log_event_failback', lang, to_ip=event.get('to_ip', '?'))
                elif event_type == 'LB_ROTATION':
                    line = get_text('messages.log_event_rotation', lang, to_ip=event.get('to_ip', '?'))
                
                if line:
                    message_parts.append(f"`[{ts}]` {line}")

        failover_count = len(config.get("failover_policies", []))
        global_index = policy_index if policy_type == 'failover' else failover_count + policy_index
        back_callback = f"status_select_policy|{global_index}"

        buttons = [[InlineKeyboardButton(get_text('buttons.back_to_list', lang), callback_data=back_callback)]]
        
        await query.edit_message_text("\n".join(message_parts), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

    except (IndexError, KeyError, ValueError):
        await query.edit_message_text(get_text('messages.session_expired_error', lang))

async def status_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback to refresh the status message."""
    query = update.callback_query
    await query.answer()
    await status_command(update, context)

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
    
    buttons = [
        [InlineKeyboardButton(get_text('buttons.wizard_start', lang), callback_data="wizard_start")],
        [InlineKeyboardButton(get_text('buttons.settings', lang), callback_data="go_to_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    
    await update.message.reply_text(get_text('messages.welcome', lang), reply_markup=reply_markup)

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

    if not job_queue.get_jobs_by_name("daily_report_job"):
        report_time = datetime.strptime("04:30", "%H:%M").time()
        job_queue.run_daily(
            send_daily_report_job,
            time=report_time,
            name="daily_report_job"
        )
        logger.info(f"Daily report job scheduled to run every day at {report_time} UTC.")

async def debug_show_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A debug command to show a summary of the latest health check run."""
    if not is_admin(update): return
    
    lang = get_user_lang(context)
    monitoring_log = context.bot_data.get('monitoring_log', [])
    
    if not monitoring_log:
        await send_or_edit(update, context, get_text('messages.debug_log_empty', lang))
        return
        
    last_event = monitoring_log[-1]
    last_timestamp = last_event['timestamp']
    
    last_run_events = [e for e in monitoring_log if e['timestamp'] == last_timestamp]
    
    ip_status_events = [e for e in last_run_events if e['event_type'] == 'IP_STATUS']
    other_events = [e for e in last_run_events if e['event_type'] != 'IP_STATUS']

    ts_formatted = datetime.fromisoformat(last_timestamp).strftime('%Y-%m-%d %H:%M:%S')
    message_parts = [
        get_text('messages.debug_log_header_last_run', lang),
        get_text('messages.debug_log_timestamp', lang, timestamp=ts_formatted)
    ]

    if ip_status_events:
        message_parts.append(get_text('messages.debug_log_ip_statuses_header', lang))
        unique_ips = sorted(list(set(e['ip'] for e in ip_status_events)))
        for ip in unique_ips:
            status_entry = next((e for e in ip_status_events if e['ip'] == ip), None)
            if status_entry:
                message_parts.append(get_text('messages.debug_log_ip_status_entry', lang, ip=ip, status=status_entry['status']))
    
    if other_events:
        message_parts.append(get_text('messages.debug_log_actions_header', lang))
        for event in other_events:
            event_type = event['event_type']
            if event_type == "FAILOVER":
                message_parts.append(get_text('messages.debug_log_action_failover', lang, **event))
            elif event_type == "LB_ROTATION":
                message_parts.append(get_text('messages.debug_log_action_rotation', lang, **event))
            elif event_type == "FAILBACK":
                message_parts.append(get_text('messages.debug_log_action_failback', lang, **event))

    await send_or_edit(update, context, "\n".join(message_parts))

def main():
    """Starts the bot."""
    load_translations()
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
    
    persistence = PicklePersistence(filepath=persistence_file)
    
    application = Application.builder() \
        .token(TELEGRAM_BOT_TOKEN) \
        .persistence(persistence) \
        .post_init(post_startup_tasks) \
        .build()
       
    # Command Handlers
    application.add_handler(CommandHandler("clearcommands", clear_commands_command))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("wizard", wizard_start_command))
    application.add_handler(CommandHandler("language", language_command))
    application.add_handler(CommandHandler("list", list_records_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("bulk", bulk_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("backup", backup_command))
    application.add_handler(CommandHandler("restore", restore_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("debuglogs", debug_show_logs_command))
    
    # --- Main Navigation & Record Management ---
    application.add_handler(CallbackQueryHandler(lb_policy_change_algo_callback, pattern="^lb_policy_change_algo\|"))
    application.add_handler(CallbackQueryHandler(select_account_callback, pattern="^select_account\|"))
    application.add_handler(CallbackQueryHandler(back_to_accounts_callback, pattern="^back_to_accounts$"))
    application.add_handler(CallbackQueryHandler(zones_page_callback, pattern="^zones_page\|"))
    application.add_handler(CallbackQueryHandler(select_zone_callback, pattern="^select_zone\|"))
    application.add_handler(CallbackQueryHandler(back_to_zones_callback, pattern="^back_to_zones$"))
    application.add_handler(CallbackQueryHandler(list_page_callback, pattern="^list_page\|"))
    application.add_handler(CallbackQueryHandler(refresh_list_callback, pattern="^refresh_list$"))
    application.add_handler(CallbackQueryHandler(back_to_records_list_callback, pattern="^back_to_records_list$"))
    application.add_handler(CallbackQueryHandler(select_callback, pattern="^select\|"))
    application.add_handler(CallbackQueryHandler(change_record_type_callback, pattern="^change_type\|"))
    application.add_handler(CallbackQueryHandler(move_record_start_callback, pattern="^move_record_start\|"))
    application.add_handler(CallbackQueryHandler(move_select_dest_account_callback, pattern="^move_select_dest_account\|"))
    application.add_handler(CallbackQueryHandler(move_select_dest_zone_callback, pattern="^move_select_dest_zone\|"))
    application.add_handler(CallbackQueryHandler(move_delete_source_callback, pattern="^move_delete_source\|"))
    application.add_handler(CallbackQueryHandler(move_copy_complete_callback, pattern="^move_copy_complete$"))
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
    application.add_handler(CallbackQueryHandler(select_callback, pattern="^select\|"))
    application.add_handler(CallbackQueryHandler(change_record_type_select_callback, pattern="^change_type_select\|"))
    application.add_handler(CallbackQueryHandler(clear_logs_confirm_callback, pattern="^clear_logs_confirm$"))
    application.add_handler(CallbackQueryHandler(clear_logs_execute_callback, pattern="^clear_logs_execute$"))
    application.add_handler(CallbackQueryHandler(log_retention_menu_callback, pattern="^log_retention_menu$"))
    application.add_handler(CallbackQueryHandler(set_log_retention_callback, pattern="^set_log_retention\|"))
    application.add_handler(CallbackQueryHandler(set_alias_start_callback, pattern="^set_alias_start\|"))
    application.add_handler(CallbackQueryHandler(set_record_alias_start_callback, pattern="^set_record_alias_start\|"))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CallbackQueryHandler(status_refresh_callback, pattern="^status_refresh$"))
    application.add_handler(CallbackQueryHandler(status_select_policy_callback, pattern="^status_select_policy\|"))
    application.add_handler(CallbackQueryHandler(manual_health_check_callback, pattern="^manual_check\|"))
    application.add_handler(CallbackQueryHandler(show_policy_log_callback, pattern="^show_policy_log\|"))
    application.add_handler(CallbackQueryHandler(clone_policy_start_callback, pattern="^clone_policy_start\|"))

    # --- Monitor Actions ---
    application.add_handler(CallbackQueryHandler(monitors_menu_callback, pattern="^monitors_menu$"))
    application.add_handler(CallbackQueryHandler(monitor_add_start_callback, pattern="^monitor_add_start$"))
    application.add_handler(CallbackQueryHandler(monitor_delete_confirm_callback, pattern="^monitor_delete_confirm\|"))
    application.add_handler(CallbackQueryHandler(monitor_delete_execute_callback, pattern="^monitor_delete_execute\|"))
    application.add_handler(CallbackQueryHandler(monitor_edit_menu_callback, pattern="^monitor_edit\|"))
    application.add_handler(CallbackQueryHandler(monitor_edit_field_callback, pattern="^monitor_edit_field\|"))
    application.add_handler(CallbackQueryHandler(groups_menu_callback, pattern="^groups_menu$"))
    application.add_handler(CallbackQueryHandler(group_add_start_callback, pattern="^group_add_start$"))
    application.add_handler(CallbackQueryHandler(group_delete_confirm_callback, pattern="^group_delete_confirm\|"))
    application.add_handler(CallbackQueryHandler(group_delete_execute_callback, pattern="^group_delete_execute\|"))
    application.add_handler(CallbackQueryHandler(group_edit_start_callback, pattern="^group_edit_start\|"))
    application.add_handler(CallbackQueryHandler(monitor_select_group_callback, pattern="^monitor_select_group\|"))
    application.add_handler(CallbackQueryHandler(monitor_change_group_start_callback, pattern="^monitor_change_group_start\|"))
    application.add_handler(CallbackQueryHandler(monitor_change_group_execute_callback, pattern="^monitor_change_group_execute\|"))
    application.add_handler(CallbackQueryHandler(policy_change_group_start_callback, pattern="^policy_change_group_start\|"))
    application.add_handler(CallbackQueryHandler(policy_change_group_execute_callback, pattern="^policy_change_group_execute\|"))
    application.add_handler(CallbackQueryHandler(monitor_select_group_callback, pattern="^monitor_select_group\|"))
    application.add_handler(CallbackQueryHandler(monitor_purge_old_ip_logs_callback, pattern="^monitor_purge_logs\|"))

    # --- Maintenance  Actions ---
    application.add_handler(CallbackQueryHandler(toggle_maintenance_callback, pattern="^toggle_maintenance\|"))
    
    # --- Wizard Actions ---
    application.add_handler(CallbackQueryHandler(wizard_select_account_callback, pattern="^wizard_select_account\|"))
    application.add_handler(CallbackQueryHandler(wizard_select_zone_callback, pattern="^wizard_select_zone\|"))
    application.add_handler(CallbackQueryHandler(wizard_set_monitoring_callback, pattern="^wizard_set_monitoring\|"))
    application.add_handler(CallbackQueryHandler(wizard_start_callback, pattern="^wizard_start$"))
    application.add_handler(CallbackQueryHandler(wizard_set_type_callback, pattern="^wizard_set_type\|"))
    application.add_handler(CallbackQueryHandler(wizard_cancel_callback, pattern="^wizard_cancel$"))

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
    application.add_handler(CallbackQueryHandler(reporting_menu_callback, pattern="^reporting_menu$"))
    application.add_handler(CallbackQueryHandler(generate_report_callback, pattern="^report_generate\|"))
    
    # --- Notification Settings & User Management ---
    application.add_handler(CallbackQueryHandler(show_settings_notifications_menu, pattern="^settings_notifications$"))
    application.add_handler(CallbackQueryHandler(notification_edit_recipients_callback, pattern="^notification_edit_recipients\|"))
    application.add_handler(CallbackQueryHandler(notification_add_member_start_callback, pattern="^notification_add_member_start$"))
    application.add_handler(CallbackQueryHandler(notification_remove_member_start_callback, pattern="^notification_remove_member_start$"))
    application.add_handler(CallbackQueryHandler(notification_remove_member_execute_callback, pattern="^notification_remove_member_execute\|"))
    application.add_handler(CallbackQueryHandler(toggle_notifications_callback, pattern="^toggle_notifications$"))
    application.add_handler(CallbackQueryHandler(add_recipient_start_callback, pattern="^add_recipient_start$"))
    application.add_handler(CallbackQueryHandler(remove_recipient_start_callback, pattern="^remove_recipient_start$"))
    application.add_handler(CallbackQueryHandler(remove_recipient_confirm_callback, pattern="^remove_recipient_confirm\|"))
    application.add_handler(CallbackQueryHandler(user_management_menu_callback, pattern="^user_management_menu$"))
    application.add_handler(CallbackQueryHandler(admin_add_start_callback, pattern="^admin_add_start$"))
    application.add_handler(CallbackQueryHandler(admin_remove_start_callback, pattern="^admin_remove_start$"))
    application.add_handler(CallbackQueryHandler(admin_remove_confirm_callback, pattern="^admin_remove_confirm\|"))

    # --- Failover Policy Management ---
    application.add_handler(CallbackQueryHandler(copy_monitoring_confirm_callback, pattern="^copy_monitoring_confirm\|"))
    application.add_handler(CallbackQueryHandler(failover_start_backup_monitoring_callback, pattern="^failover_start_backup_monitoring\|"))
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
    application.add_handler(CallbackQueryHandler(lb_policy_change_algo_callback, pattern="^lb_policy_change_algo\|"))
    application.add_handler(CallbackQueryHandler(policy_add_select_group_callback, pattern="^policy_add_select_group\|"))
    application.add_handler(CallbackQueryHandler(lb_ip_list_menu, pattern="^lb_ip_list_menu$"))
    application.add_handler(CallbackQueryHandler(lb_ip_edit_menu, pattern="^lb_ip_select\|"))
    application.add_handler(CallbackQueryHandler(lb_ip_edit_start_callback, pattern="^lb_ip_edit_start\|"))
    application.add_handler(CallbackQueryHandler(lb_ip_delete_prompt_callback, pattern="^lb_ip_delete_prompt$"))
    application.add_handler(CallbackQueryHandler(lb_ip_delete_confirm_callback, pattern="^lb_ip_delete_confirm$"))
    application.add_handler(CallbackQueryHandler(lb_ip_add_start_callback, pattern="^lb_ip_add_start$"))

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
