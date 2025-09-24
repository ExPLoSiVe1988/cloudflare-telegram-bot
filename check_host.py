import httpx
import asyncio
import logging
import json
import re
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

API_BASE_URL = "https://check-host.net"
REQUEST_HEADERS = {
    'Accept': 'application/json',
    'User-Agent': 'CloudflareMonitoringBot/1.1'
}
NODES_CACHE_FILE = "nodes_cache.json"
CACHE_EXPIRATION_HOURS = 24

STATIC_NODES_DATA = {
    "us1.node.check-host.net": {"country": "USA", "city": "New York", "location": "us"},
    "us2.node.check-host.net": {"country": "USA", "city": "Los Angeles", "location": "us"},
    "us3.node.check-host.net": {"country": "USA", "city": "Dallas", "location": "us"},
    "us4.node.check-host.net": {"country": "USA", "city": "Miami", "location": "us"},
    "us5.node.check-host.net": {"country": "USA", "city": "Chicago", "location": "us"},
    "us6.node.check-host.net": {"country": "USA", "city": "Seattle", "location": "us"},
    "us7.node.check-host.net": {"country": "USA", "city": "Atlanta", "location": "us"},
    "us8.node.check-host.net": {"country": "USA", "city": "Denver", "location": "us"},
    "ca1.node.check-host.net": {"country": "Canada", "city": "Beauharnois", "location": "ca"},
    "ca2.node.check-host.net": {"country": "Canada", "city": "Toronto", "location": "ca"},
    "ca3.node.check-host.net": {"country": "Canada", "city": "Vancouver", "location": "ca"},
    "de1.node.check-host.net": {"country": "Germany", "city": "Frankfurt", "location": "de"},
    "de2.node.check-host.net": {"country": "Germany", "city": "Nuremberg", "location": "de"},
    "de3.node.check-host.net": {"country": "Germany", "city": "Falkenstein", "location": "de"},
    "fr1.node.check-host.net": {"country": "France", "city": "Strasbourg", "location": "fr"},
    "fr2.node.check-host.net": {"country": "France", "city": "Paris", "location": "fr"},
    "gb1.node.check-host.net": {"country": "Great Britain", "city": "London", "location": "gb"},
    "gb2.node.check-host.net": {"country": "Great Britain", "city": "Manchester", "location": "gb"},
    "nl1.node.check-host.net": {"country": "Netherlands", "city": "Amsterdam", "location": "nl"},
    "pl1.node.check-host.net": {"country": "Poland", "city": "Warsaw", "location": "pl"},
    "es1.node.check-host.net": {"country": "Spain", "city": "Madrid", "location": "es"},
    "it1.node.check-host.net": {"country": "Italy", "city": "Milan", "location": "it"},
    "ru1.node.check-host.net": {"country": "Russia", "city": "Moscow", "location": "ru"},
    "ru2.node.check-host.net": {"country": "Russia", "city": "St. Petersburg", "location": "ru"},
    "ru3.node.check-host.net": {"country": "Russia", "city": "Novosibirsk", "location": "ru"},
    "ru4.node.check-host.net": {"country": "Russia", "city": "Khabarovsk", "location": "ru"},
    "ua1.node.check-host.net": {"country": "Ukraine", "city": "Kyiv", "location": "ua"},
    "kz1.node.check-host.net": {"country": "Kazakhstan", "city": "Almaty", "location": "kz"},
    "by1.node.check-host.net": {"country": "Belarus", "city": "Minsk", "location": "by"},
    "ir1.node.check-host.net": {"country": "Iran", "city": "Tehran", "location": "ir"},
    "ir3.node.check-host.net": {"country": "Iran", "city": "Shiraz", "location": "ir"},
    "tr1.node.check-host.net": {"country": "Turkey", "city": "Istanbul", "location": "tr"},
    "hk1.node.check-host.net": {"country": "Hong Kong", "city": "Hong Kong", "location": "hk"},
    "sg1.node.check-host.net": {"country": "Singapore", "city": "Singapore", "location": "sg"},
    "jp1.node.check-host.net": {"country": "Japan", "city": "Tokyo", "location": "jp"},
    "md1.node.check-host.net": {"country": "Moldova", "city": "Chisinau", "location": "md"},
    "br1.node.check-host.net": {"country": "Brazil", "city": "Sao Paulo", "location": "br"},
    "au1.node.check-host.net": {"country": "Australia", "city": "Sydney", "location": "au"},
    "ch1.node.check-host.net": {"country": "Switzerland", "city": "Zurich", "location": "ch"},
    "se1.node.check-host.net": {"country": "Sweden", "city": "Stockholm", "location": "se"},
    "fi1.node.check-host.net": {"country": "Finland", "city": "Helsinki", "location": "fi"},
    "cl1.node.check-host.net": {"country": "Chile", "city": "Santiago", "location": "cl"},
    "za1.node.check-host.net": {"country": "South Africa", "city": "Johannesburg", "location": "za"},
    "in1.node.check-host.net": {"country": "India", "city": "Mumbai", "location": "in"},
    "kr1.node.check-host.net": {"country": "South Korea", "city": "Seoul", "location": "kr"},
    "vn1.node.check-host.net": {"country": "Vietnam", "city": "Ho Chi Minh", "location": "vn"},
    "id1.node.check-host.net": {"country": "Indonesia", "city": "Jakarta", "location": "id"},
    "ae1.node.check-host.net": {"country": "United Arab Emirates", "city": "Dubai", "location": "ae"},
    "bg1.node.check-host.net": {"country": "Bulgaria", "city": "Sofia", "location": "bg"},
    "cz1.node.check-host.net": {"country": "Czech Republic", "city": "Prague", "location": "cz"},
    "at1.node.check-host.net": {"country": "Austria", "city": "Vienna", "location": "at"},
    "ro1.node.check-host.net": {"country": "Romania", "city": "Bucharest", "location": "ro"},
    "rs1.node.check-host.net": {"country": "Serbia", "city": "Belgrade", "location": "rs"},
    "lt1.node.check-host.net": {"country": "Lithuania", "city": "Vilnius", "location": "lt"},
    "lv1.node.check-host.net": {"country": "Latvia", "city": "Riga", "location": "lv"},
    "ee1.node.check-host.net": {"country": "Estonia", "city": "Tallinn", "location": "ee"},
    "ge1.node.check-host.net": {"country": "Georgia", "city": "Tbilisi", "location": "ge"},
    "uz1.node.check-host.net": {"country": "Uzbekistan", "city": "Tashkent", "location": "uz"},
    "kg1.node.check-host.net": {"country": "Kyrgyzstan", "city": "Bishkek", "location": "kg"},
    "th1.node.check-host.net": {"country": "Thailand", "city": "Bangkok", "location": "th"},
    "my1.node.check-host.net": {"country": "Malaysia", "city": "Kuala Lumpur", "location": "my"},
    "ph1.node.check-host.net": {"country": "Philippines", "city": "Manila", "location": "ph"},
    "mx1.node.check-host.net": {"country": "Mexico", "city": "Mexico City", "location": "mx"},
    "ar1.node.check-host.net": {"country": "Argentina", "city": "Buenos Aires", "location": "ar"},
    "co1.node.check-host.net": {"country": "Colombia", "city": "Bogota", "location": "co"},
    "pe1.node.check-host.net": {"country": "Peru", "city": "Lima", "location": "pe"},
    "ng1.node.check-host.net": {"country": "Nigeria", "city": "Lagos", "location": "ng"},
    "eg1.node.check-host.net": {"country": "Egypt", "city": "Cairo", "location": "eg"},
    "il1.node.check-host.net": {"country": "Israel", "city": "Tel Aviv", "location": "il"},
    "sa1.node.check-host.net": {"country": "Saudi Arabia", "city": "Riyadh", "location": "sa"},
    "pt1.node.check-host.net": {"country": "Portugal", "city": "Lisbon", "location": "pt"},
    "ie1.node.check-host.net": {"country": "Ireland", "city": "Dublin", "location": "ie"},
    "no1.node.check-host.net": {"country": "Norway", "city": "Oslo", "location": "no"},
    "dk1.node.check-host.net": {"country": "Denmark", "city": "Copenhagen", "location": "dk"},
    "gr1.node.check-host.net": {"country": "Greece", "city": "Athens", "location": "gr"},
    "hu1.node.check-host.net": {"country": "Hungary", "city": "Budapest", "location": "hu"},
    "be1.node.check-host.net": {"country": "Belgium", "city": "Brussels", "location": "be"},
    "cn1.node.check-host.net": {"country": "China", "city": "Shanghai", "location": "cn"},
    "cn2.node.check-host.net": {"country": "China", "city": "Beijing", "location": "cn"},
    "tw1.node.check-host.net": {"country": "Taiwan", "city": "Taipei", "location": "tw"},
    "nz1.node.check-host.net": {"country": "New Zealand", "city": "Auckland", "location": "nz"}
}

async def _fetch_nodes_dynamically() -> Optional[Dict[str, Any]]:
    """Tries to fetch nodes by parsing the website's JS, with a strict timeout."""
    url = f"{API_BASE_URL}/check-ping"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await asyncio.wait_for(client.get(url, headers=REQUEST_HEADERS), timeout=15.0)
            response.raise_for_status()
            
        match = re.search(r'var\s+permanent_nodes\s*=\s*({.*?});', response.text)
        if not match:
            logger.error("Could not find 'permanent_nodes' variable in page content.")
            return None

        nodes_data = json.loads(match.group(1))
        formatted_nodes = {}
        for node_key, info in nodes_data.items():
            if info and isinstance(info, dict) and all(k in info for k in ['country', 'city', 'location']):
                node_id = f"{node_key}.node.check-host.net"
                formatted_nodes[node_id] = {"country": info["country"], "city": info["city"], "location": info["location"]}
        
        logger.info(f"Successfully parsed {len(formatted_nodes)} nodes from website.")
        return formatted_nodes
    except asyncio.TimeoutError:
        logger.error("Dynamic node fetch timed out.")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch or parse nodes page dynamically: {e}")
        return None

async def get_nodes() -> Dict[str, Any]:
    """
    Gets the list of nodes, prioritizing a recent cache, then a dynamic fetch,
    and finally falling back to a static list.
    """
    if os.path.exists(NODES_CACHE_FILE):
        try:
            with open(NODES_CACHE_FILE, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            timestamp = datetime.fromisoformat(cache_data['timestamp'])
            if datetime.now() - timestamp < timedelta(hours=CACHE_EXPIRATION_HOURS):
                logger.info("Using recent cache of nodes.")
                return cache_data['nodes']
        except Exception:
            logger.warning("Cache file is corrupted or invalid. Will fetch fresh data.")

    logger.info("Cache is old or missing. Attempting to fetch fresh nodes list.")
    fresh_nodes = await _fetch_nodes_dynamically()
    
    nodes_to_use = fresh_nodes
    source = "dynamically fetched"

    if not fresh_nodes:
        logger.warning("Dynamic fetch failed. Falling back to the static nodes list.")
        nodes_to_use = STATIC_NODES_DATA
        source = "static fallback"

    try:
        with open(NODES_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'timestamp': datetime.now().isoformat(), 'nodes': nodes_to_use}, f, indent=2)
        logger.info(f"Updated cache file with {source} data.")
    except IOError as e:
        logger.error(f"Could not write to cache file: {e}")
    
    return nodes_to_use

async def perform_check(host: str, port: int, nodes: list) -> Optional[Dict[str, bool]]:
    """
    Performs a full TCP check with intelligent polling and result stability checks
    to prevent false positives from temporary network issues.
    """
    if not nodes:
        return {}
        
    target = f"{host}:{port}"
    unique_nodes = list(set(nodes))
    node_params = "&".join([f"node={node}" for node in unique_nodes])
    check_url = f"{API_BASE_URL}/check-tcp?host={target}&{node_params}"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(check_url, headers=REQUEST_HEADERS, timeout=15)
            response.raise_for_status()
            initial_data = response.json()

            request_id = initial_data.get('request_id')
            if not request_id:
                logger.error(f"No request_id returned from Check-Host for {target}.")
                return None

            result_url = f"{API_BASE_URL}/check-result/{request_id}"
            results = None
            max_wait_time = 20
            poll_interval = 5
            
            logger.info(f"Check-Host task created for {target} (ID: {request_id}). Polling for results up to {max_wait_time}s...")

            for attempt in range(max_wait_time // poll_interval):
                is_last_attempt = (attempt == (max_wait_time // poll_interval - 1))
                await asyncio.sleep(poll_interval)
                logger.info(f"Polling for results of {request_id} (Attempt {attempt + 1})...")
                
                result_response = await client.get(result_url, headers=REQUEST_HEADERS, timeout=20)
                if result_response.status_code != 200:
                    logger.warning(f"Polling attempt failed with status {result_response.status_code}. Retrying...")
                    continue
                
                current_results = result_response.json()
                
                all_nodes_reported = all(current_results.get(node) is not None for node in unique_nodes)
                
                if not all_nodes_reported and not is_last_attempt:
                    logger.info(f"Results for {request_id} are not yet complete. Retrying...")
                    continue

                temp_statuses = {}
                for node_id in unique_nodes:
                    result_data = current_results.get(node_id)
                    is_ok = (result_data and isinstance(result_data, list) and len(result_data) > 0 and 
                             isinstance(result_data[0], dict) and 'time' in result_data[0])
                    temp_statuses[node_id] = is_ok
                
                num_failures = sum(1 for status in temp_statuses.values() if not status)
                
                stability_threshold = len(unique_nodes) // 2 

                if num_failures < stability_threshold:
                    results = current_results
                    logger.info(f"Results for {request_id} are stable (failures: {num_failures}). Accepting result.")
                    break
                elif is_last_attempt:
                    results = current_results
                    logger.warning(f"High number of failures ({num_failures}) for {request_id}, but accepting result as it's the final attempt.")
                    break
                else:
                    logger.warning(f"High number of failures ({num_failures}) for {request_id}. Polling again for stability.")

            if not results:
                logger.warning(f"Could not get a final result for request {request_id} after {max_wait_time} seconds.")
                return None

            logger.info(f"FINAL RAW API RESULT for request_id {request_id} on host {target} -> {results}")

            final_statuses = {}
            for node_id in unique_nodes:
                result_data = results.get(node_id)
                is_ok = (result_data and isinstance(result_data, list) and len(result_data) > 0 and 
                         isinstance(result_data[0], dict) and 'time' in result_data[0])
                final_statuses[node_id] = is_ok
            
            return final_statuses

    except Exception as e:
        logger.error(f"An unexpected error occurred in perform_check for {target}: {e}", exc_info=True)
        return None
