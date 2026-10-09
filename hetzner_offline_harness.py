"""Load real Hetzner functions without importing Telegram, .env or making API calls."""
import ast
import asyncio
import hashlib
import html
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import warnings
from hcloud_features import CostHistory, valid_number, search_resources, rescale_issue, image_issue

warnings.filterwarnings('ignore', category=SyntaxWarning)
ROOT = Path(__file__).resolve().parent

class Button:
    def __init__(self, text, callback_data):
        self.text, self.callback_data = text, callback_data

class Markup(list):
    @property
    def inline_keyboard(self):
        return self

class TelegramError(Exception):
    pass

def load_functions():
    tree = ast.parse((ROOT / 'bot.py').read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and
             (node.name.startswith(('hcloud_', '_handle_state_hcloud_')) or node.name == 'create_hcloud_handler')]
    ns = dict(globals())
    ns.update({'HCLOUD_LIST_PAGE_SIZE': 5, 'HCLOUD_COST_LOCK': asyncio.Lock(), 'HCLOUD_ACTION_LOCK': asyncio.Lock(),
               'HCLOUD_PENDING_ACTIONS': {}, 'HETZNER_CLOUD_ACCOUNTS': {},
               'HCLOUD_COST_FILE': str(ROOT / 'DO_NOT_USE_REAL_COST_FILE.json'),
               'logger': logging.getLogger('offline-hetzner'), 'error': SimpleNamespace(TelegramError=TelegramError),
               'InlineKeyboardButton': Button, 'InlineKeyboardMarkup': Markup})
    module=ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]+nodes,type_ignores=[]))
    exec(compile(module, '<real Hetzner functions>', 'exec'), ns)
    return ns
