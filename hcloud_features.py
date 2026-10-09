"""Pure Hetzner helpers and an atomic, local cost history. No API credentials stored."""
import copy
import json
import math
import os
from datetime import datetime, timezone


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime):
        raise ValueError('Invalid UTC timestamp')
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def month_bounds(now):
    now = utc(now)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    end = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)
    return start, end


def valid_number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError):
        return None


def search_resources(items, term):
    term = (term or '').strip().casefold()
    if not term:
        return items
    def values(item):
        yield str(item.get('id', ''))
        for key in ('name', 'description', 'ip', 'type'):
            yield str(item.get(key, ''))
        for version in ('ipv4', 'ipv6'):
            yield str(((item.get('public_net') or {}).get(version) or {}).get('ip', ''))
    return [item for item in items if any(term in value.casefold() for value in values(item))]


def rescale_issue(server, target, available):
    if not server or not target or not available:
        return 'hcloud_plan_unavailable'
    source = server.get('server_type') or {}
    if str(source.get('id')) == str(target.get('id')) or source.get('name') == target.get('name'):
        return 'hcloud_plan_same'
    if not source.get('architecture') or source.get('architecture') != target.get('architecture'):
        return 'hcloud_plan_architecture'
    disk = valid_number(server.get('disk'))
    target_disk = valid_number(target.get('disk'))
    if disk is None or target_disk is None or target_disk < disk:
        return 'hcloud_plan_disk'
    return None


def image_issue(image, server_type, disk=None):
    if not image or image.get('status', 'available') != 'available' or image.get('deprecated'):
        return 'hcloud_image_unavailable'
    if image.get('architecture') and image.get('architecture') != server_type.get('architecture'):
        return 'hcloud_plan_architecture'
    minimum = valid_number(image.get('disk_size'))
    capacity = valid_number(disk if disk is not None else server_type.get('disk'))
    if minimum is not None and (capacity is None or minimum > capacity):
        return 'hcloud_plan_disk'
    return None


class CostHistory:
    """Observation history, not an invoice. Changes outside the bot are timestamped at observation."""
    def __init__(self, path):
        self.path = os.fspath(path)
        self.data = {'version': 1, 'accounts': {}}
        if os.path.exists(self.path):
            with open(self.path, encoding='utf-8') as stream:
                self.data = json.load(stream)
            if self.data.get('version') != 1 or not isinstance(self.data.get('accounts'), dict):
                raise ValueError('Invalid Hetzner cost history')

    def save(self):
        temporary = self.path + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(self.data, stream, ensure_ascii=False, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def observe(self, account, identity, currency, resources, now):
        now = utc(now)
        stamp = now.isoformat()
        start, _ = month_bounds(now)
        accounts = self.data['accounts']
        entry = accounts.setdefault(account, {'identity': identity, 'currency': currency,
                                              'since': stamp, 'resources': {}, 'last': stamp})
        if entry['identity'] != identity or entry['currency'] != currency:
            raise ValueError('Hetzner project/currency changed; cost history cannot be mixed')
        if now < utc(entry['last']):
            raise ValueError('Out-of-order Hetzner cost observation')
        previous = entry['resources']
        known_resources = set(previous)
        seen = set()
        for resource in resources:
            key = resource['key']
            seen.add(key)
            record = previous.get(key)
            rate = copy.deepcopy(resource.get('rate'))
            if record is None:
                unknown_created = False
                try:
                    began = max(start, utc(resource['created']))
                except (KeyError, TypeError, ValueError):
                    began = now
                    unknown_created = True
                if resource.get('start_at_observation'):
                    began = now
                record = previous[key] = {'category': resource['category'], 'name': resource.get('name', key),
                                          'segments': [{'start': min(began, now).isoformat(), 'end': None, 'rate': rate}],
                                          'traffic': {}, 'deleted': None, 'unknown_created': unknown_created,
                                          'backup_history_unknown': (resource['category'] == 'backups' and
                                              'servers:' + key.split(':', 1)[1] not in known_resources)}
            elif record['deleted'] is not None:
                record['segments'].append({'start': stamp, 'end': None, 'rate': rate})
                record['deleted'] = None
            elif record['segments'][-1]['rate'] != rate:
                record['segments'][-1]['end'] = stamp
                record['segments'].append({'start': stamp, 'end': None, 'rate': rate})
            record['name'] = resource.get('name', key)
            traffic = resource.get('traffic')
            if traffic is not None:
                month = now.strftime('%Y-%m')
                old = record['traffic'].get(month)
                if old is None or traffic.get('out', 0) >= old.get('out', 0):
                    record['traffic'][month] = copy.deepcopy(traffic)
        for key, record in previous.items():
            if key not in seen and record['deleted'] is None:
                record['segments'][-1]['end'] = stamp
                record['deleted'] = stamp
        entry['last'] = stamp
        self.save()
        return entry

    def totals(self, account, now):
        now = utc(now)
        start, end = month_bounds(now)
        entry = self.data['accounts'][account]
        result = {'costs': {}, 'missing': 0, 'since': entry['since'], 'last': entry['last'],
                  'currency': entry['currency'], 'deleted': 0}
        for record in entry['resources'].values():
            if record.get('unknown_created'):
                rates = [segment.get('rate') for segment in record['segments']]
                if any(rate and any(value for value in rate.values()) for rate in rates):
                    result['missing'] += 1
            if record.get('backup_history_unknown') and start <= utc(entry['since']) < end:
                result['missing'] += 1
            costs = []
            caps = []
            hours = 0.0
            for segment in record['segments']:
                left = max(start, utc(segment['start']))
                right = min(now, utc(segment['end']) if segment['end'] else now, end)
                if right <= left:
                    continue
                seconds = (right - left).total_seconds()
                rate = segment.get('rate')
                if not rate or (rate.get('hourly') is None and rate.get('monthly') is None):
                    result['missing'] += 1
                    continue
                monthly, hourly = rate.get('monthly'), rate.get('hourly')
                if hourly is not None:
                    costs.append(seconds / 3600 * hourly)
                    hours += seconds / 3600
                    if monthly is not None:
                        caps.append((seconds, monthly))
                else:
                    costs.append(monthly * seconds / (end - start).total_seconds())
            amount = sum(costs)
            if hours:
                weighted_hour = sum(costs) / hours
                amount += (math.ceil(round(hours, 9)) - hours) * weighted_hour
                if caps:
                    cap = sum(seconds * monthly for seconds, monthly in caps) / sum(seconds for seconds, _ in caps)
                    amount = min(amount, cap)
            category = record['category']
            result['costs'][category] = result['costs'].get(category, 0) + amount
            if record['deleted'] and utc(record['deleted']) > start:
                result['deleted'] += 1
            traffic = record['traffic'].get(now.strftime('%Y-%m'))
            if traffic:
                outgoing, included, price = traffic.get('out'), traffic.get('included'), traffic.get('price')
                if outgoing is None or included is None or price is None:
                    result['missing'] += 1
                else:
                    excess = max(outgoing - included, 0)
                    overage = math.ceil(excess / 100_000_000) * 100_000_000 / 1_000_000_000_000 * price
                    result['costs']['traffic'] = result['costs'].get('traffic', 0) + overage
        result['total'] = sum(result['costs'].values())
        return result
