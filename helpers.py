import os
import json
import html
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, error
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
CONFIG_FILE = "config.json"
translations = {}

def load_translations():
    global translations
    for lang in ['en', 'fa']:
        try:
            with open(f'{lang}.json', 'r', encoding='utf-8') as f:
                translations[lang] = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.fatal(f"FATAL: Could not load or decode {lang}.json: {e}")
            exit(1)
    logger.info("Translation files have been loaded successfully.")

def get_text(key: str, lang: str, **kwargs):
    try:
        keys = key.split('.')
        text_template = translations.get(lang, translations.get('en', {}))
        for k in keys:
            text_template = text_template[k]
        return text_template.format(**kwargs)
    except (KeyError, AttributeError):
        logger.warning(f"Untranslated key found: '{key}' for language: '{lang}'.")
        return f"Untranslated key: {key}"

def get_user_lang(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.get('language', 'fa')

def load_config():
    try:
        if not os.path.exists(CONFIG_FILE):
            default_config = {"notifications": {"enabled": True, "chat_ids": []}, "failover_policies": [], "load_balancer_policies": [], "admins": [], "zone_aliases": {}, "record_aliases": {}, "log_retention_days": 30, "monitoring_groups": {}, "standalone_monitors": [], "notification_groups": {}}
            save_config(default_config)
            return default_config
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        config.setdefault("notification_groups", {})
        config.setdefault("notifications", {}).setdefault("chat_ids", [])
        return config
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.fatal(f"FATAL: Could not read or decode {CONFIG_FILE}. Error: {e}")
        return None

def save_config(config_data):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)

async def send_or_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None, parse_mode="HTML"):
    query = update.callback_query
    try:
        if query and query.message:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except error.BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"Failed to edit message, falling back to send: {e}")
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"An unexpected error in send_or_edit: {e}", exc_info=True)

def escape_html(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    return html.escape(text)

async def send_notification(context: ContextTypes.DEFAULT_TYPE, chat_ids_to_notify: set, message_key: str, add_settings_button: bool = False, **kwargs):
    """
    (DUMB SENDER VERSION)
    Sends a message to a specific set of chat_ids provided to it. It has no logic of its own.
    """
    if not chat_ids_to_notify:
        logger.warning(f"Notification for '{message_key}' aborted: recipient list was empty.")
        return

    logger.info(f"DUMB SENDER: Preparing to send '{message_key}' to: {chat_ids_to_notify}")

    safe_kwargs = {k: escape_html(str(v)) for k, v in kwargs.items()}
    user_data = await context.application.persistence.get_user_data()

    for chat_id in chat_ids_to_notify:
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
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"DUMB SENDER: Failed to send notification to {chat_id}: {e}", exc_info=True)

load_translations()
