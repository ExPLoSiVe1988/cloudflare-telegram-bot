import httpx
import asyncio
import logging
import json
import os
import random
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

CHECK_HOST_MODULE_VERSION = "2026-07-16-fast-parallel-forced-v2"
logger.warning(f"Loaded check_host.py version: {CHECK_HOST_MODULE_VERSION}")

API_BASE_URL = "https://check-host.net"
REQUEST_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "CloudflareMonitoringBot/1.1"
}
NODES_CACHE_FILE = "nodes_cache.json"
CACHE_EXPIRATION_HOURS = int(os.getenv("CHECK_HOST_NODES_CACHE_HOURS", "6"))

CHECK_HOST_CONCURRENCY = max(4, int(os.getenv("CHECK_HOST_CONCURRENCY", "8")))
CHECK_HOST_MAX_WAIT = max(15, int(os.getenv("CHECK_HOST_MAX_WAIT", "35")))
CHECK_HOST_POLL_INTERVAL = max(2, int(os.getenv("CHECK_HOST_POLL_INTERVAL", "2")))
CHECK_HOST_MAX_RETRIES = max(1, int(os.getenv("CHECK_HOST_MAX_RETRIES", "1")))
CHECK_HOST_AUTO_FALLBACK = os.getenv("CHECK_HOST_AUTO_FALLBACK", "1").lower() not in {"0", "false", "no"}
CHECK_HOST_AUTO_MAX_NODES = max(1, int(os.getenv("CHECK_HOST_AUTO_MAX_NODES", "3")))
CHECK_HOST_EARLY_RETURN = os.getenv("CHECK_HOST_EARLY_RETURN", "1").lower() not in {"0", "false", "no"}
CHECK_HOST_MIN_COMPLETED = max(1, int(os.getenv("CHECK_HOST_MIN_COMPLETED", "2")))
CHECK_HOST_EARLY_RATIO = min(1.0, max(0.1, float(os.getenv("CHECK_HOST_EARLY_RATIO", "0.6"))))

_CHECK_HOST_SEMAPHORE = asyncio.Semaphore(CHECK_HOST_CONCURRENCY)
logger.warning(f"Check-Host runtime config: concurrency={CHECK_HOST_CONCURRENCY}, max_wait={CHECK_HOST_MAX_WAIT}s, poll_interval={CHECK_HOST_POLL_INTERVAL}s, retries={CHECK_HOST_MAX_RETRIES}, early_return={CHECK_HOST_EARLY_RETURN}")

async def _fetch_nodes_from_api() -> Optional[Dict[str, Any]]:
    """Fetch current Check-Host nodes from the official API endpoint only.

    This function intentionally does not scrape any website JavaScript. If this fails,
    the caller should keep using the user-configured node list rather than falling back
    to an old hard-coded list.
    """
    url = f"{API_BASE_URL}/nodes/hosts"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url, headers=REQUEST_HEADERS)
            response.raise_for_status()
            payload = response.json()

        raw_nodes = payload.get("nodes", {})
        if not isinstance(raw_nodes, dict) or not raw_nodes:
            logger.warning(f"Official Check-Host nodes API returned no usable nodes: {payload}")
            return None

        formatted: Dict[str, Any] = {}
        for node_id, info in raw_nodes.items():
            if not isinstance(info, dict):
                continue
            location = info.get("location")
            if not isinstance(location, list) or len(location) < 3:
                continue
            formatted[node_id] = {
                "location": location[0],
                "country": location[1],
                "city": location[2],
                "ip": info.get("ip"),
                "asn": info.get("asn"),
            }

        logger.info(f"Fetched {len(formatted)} Check-Host nodes from official API.")
        return formatted or None

    except Exception as exc:
        logger.warning(f"Could not fetch Check-Host nodes from official API: {exc}")
        return None

async def get_nodes() -> Dict[str, Any]:
    """Return Check-Host nodes from cache or official API.

    No scraping and no stale built-in static node table are used in this version.
    """
    if os.path.exists(NODES_CACHE_FILE):
        try:
            with open(NODES_CACHE_FILE, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            timestamp = datetime.fromisoformat(cache_data["timestamp"])
            nodes = cache_data.get("nodes", {})
            if isinstance(nodes, dict) and nodes and datetime.now() - timestamp < timedelta(hours=CACHE_EXPIRATION_HOURS):
                logger.info(f"Using cached Check-Host nodes: {len(nodes)} node(s).")
                return nodes
        except Exception:
            logger.warning("Check-Host nodes cache is invalid. Fetching fresh nodes.")

    fresh = await _fetch_nodes_from_api()
    if fresh:
        try:
            with open(NODES_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"timestamp": datetime.now().isoformat(), "nodes": fresh}, f, indent=2)
            logger.info("Updated Check-Host nodes cache from official API.")
        except IOError as exc:
            logger.warning(f"Could not write Check-Host nodes cache: {exc}")
        return fresh

    logger.warning("No fresh Check-Host nodes list is available. Continuing with configured nodes only.")
    return {}

def _dedupe(items: List[str]) -> List[str]:
    return list(dict.fromkeys([x for x in items if x]))

def _tcp_node_result_finished(result_data: Any) -> bool:
    """Return True if a TCP node has produced a final success/failure result.

    Check-Host TCP examples:
      success: [{"time": 0.03, "address": "1.2.3.4"}]
      failure: [{"error": "Connection timed out"}]
      pending: null
    """
    if result_data is None:
        return False
    if result_data in ([], [None], [[None]]):
        return False
    if isinstance(result_data, list) and result_data:
        first = result_data[0]
        if first is None or first == [None]:
            return False
        if isinstance(first, dict):
            return "time" in first or "error" in first
    return True

def _tcp_node_result_ok(result_data: Any) -> bool:
    if not _tcp_node_result_finished(result_data):
        return False
    if isinstance(result_data, list) and result_data and isinstance(result_data[0], dict):
        first = result_data[0]
        return "time" in first and "error" not in first
    return False

def _summarize_snapshot(snapshot: Optional[Dict[str, Any]], limit: int = 1200) -> str:
    try:
        text = json.dumps(snapshot, ensure_ascii=False)
    except Exception:
        text = repr(snapshot)
    return text[:limit] + ("..." if len(text) > limit else "")

async def perform_check(host: str, port: int, nodes: list) -> Optional[Dict[str, bool]]:
    """Perform a TCP check with Check-Host.

    This version:
      - never scrapes the website page;
      - validates nodes using the official nodes API when available;
      - polls only nodes accepted by Check-Host in the initial response;
      - accepts partial final results;
      - throttles concurrent Check-Host jobs;
      - falls back to Check-Host auto-selected nodes if configured nodes stay pending.
    """
    async with _CHECK_HOST_SEMAPHORE:
        return await _perform_check_limited(host, port, nodes)

async def _perform_check_limited(host: str, port: int, nodes: list) -> Optional[Dict[str, bool]]:
    target = f"{host}:{port}"
    requested_nodes = _dedupe(list(nodes or []))

    try:
        live_nodes = await get_nodes()
        if live_nodes and requested_nodes:
            valid_nodes = [node for node in requested_nodes if node in live_nodes]
            stale_nodes = [node for node in requested_nodes if node not in live_nodes]
            if stale_nodes:
                logger.warning(f"Ignoring stale/unknown Check-Host nodes for {target}: {stale_nodes}")
            requested_nodes = valid_nodes
    except Exception as exc:
        logger.warning(f"Could not validate Check-Host nodes for {target}: {exc}")

    modes: List[Tuple[str, Optional[List[str]]]] = []
    if requested_nodes:
        modes.append(("configured_nodes", requested_nodes))
    if CHECK_HOST_AUTO_FALLBACK:
        modes.append(("auto_nodes", None))

    if not modes:
        logger.error(f"No Check-Host nodes available for {target}.")
        return None

    for mode_name, mode_nodes in modes:
        result = await _attempt_check_mode(target, mode_name, mode_nodes)
        if result is not None:
            return result
        if mode_name == "configured_nodes" and CHECK_HOST_AUTO_FALLBACK:
            logger.warning(f"Configured Check-Host nodes did not produce usable results for {target}; trying auto-selected nodes.")

    return None

async def _attempt_check_mode(target: str, mode_name: str, mode_nodes: Optional[List[str]]) -> Optional[Dict[str, bool]]:
    retry_delay = 8

    for attempt in range(CHECK_HOST_MAX_RETRIES):
        try:
            await asyncio.sleep(random.uniform(0.05, 0.25))

            async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as client:
                if mode_nodes:
                    params = [("host", target)] + [("node", node) for node in mode_nodes]
                else:
                    params = [("host", target), ("max_nodes", str(CHECK_HOST_AUTO_MAX_NODES))]

                logger.info(
                    f"Starting Check-Host TCP check for {target} using {mode_name} "
                    f"(attempt {attempt + 1}/{CHECK_HOST_MAX_RETRIES}, params={params})."
                )

                response = await client.get(f"{API_BASE_URL}/check-tcp", headers=REQUEST_HEADERS, params=params)
                response.raise_for_status()
                initial_data = response.json()

                if not initial_data.get("ok"):
                    logger.warning(f"Check-Host initial response not ok for {target}: {initial_data}")

                request_id = initial_data.get("request_id")
                if not request_id:
                    logger.error(f"No request_id from Check-Host for {target}. Response: {initial_data}")
                    return None

                initial_nodes = initial_data.get("nodes") or {}
                actual_nodes = list(initial_nodes.keys()) if isinstance(initial_nodes, dict) else []
                if not actual_nodes and mode_nodes:
                    actual_nodes = mode_nodes

                if not actual_nodes:
                    logger.warning(f"Check-Host accepted no nodes for {target}. Initial response: {initial_data}")
                    return None

                logger.info(
                    f"Check-Host task for {target} created: request_id={request_id}, "
                    f"mode={mode_name}, accepted_nodes={actual_nodes}"
                )

                result_url = f"{API_BASE_URL}/check-result/{request_id}"
                results: Optional[Dict[str, Any]] = None
                last_snapshot: Optional[Dict[str, Any]] = None
                best_completed_count = 0

                for _ in range(max(1, CHECK_HOST_MAX_WAIT // CHECK_HOST_POLL_INTERVAL)):
                    await asyncio.sleep(CHECK_HOST_POLL_INTERVAL)

                    result_response = await client.get(result_url, headers=REQUEST_HEADERS, timeout=40.0)
                    if result_response.status_code != 200:
                        logger.warning(f"Polling {request_id} returned HTTP {result_response.status_code}.")
                        continue

                    current_results = result_response.json()
                    last_snapshot = current_results
                    completed_count = sum(
                        1 for node in actual_nodes
                        if _tcp_node_result_finished(current_results.get(node))
                    )

                    if completed_count > best_completed_count:
                        best_completed_count = completed_count
                        results = current_results
                        logger.info(
                            f"Check-Host progress for {target} request_id={request_id}: "
                            f"{completed_count}/{len(actual_nodes)} node(s) finished."
                        )

                    if completed_count == len(actual_nodes):
                        break

                    early_needed = max(
                        CHECK_HOST_MIN_COMPLETED,
                        int((len(actual_nodes) * CHECK_HOST_EARLY_RATIO) + 0.999999)
                    )
                    if CHECK_HOST_EARLY_RETURN and completed_count >= min(len(actual_nodes), early_needed):
                        logger.info(
                            f"Check-Host early return for {target} request_id={request_id}: "
                            f"{completed_count}/{len(actual_nodes)} node(s) finished; not waiting for pending nodes."
                        )
                        break

                if not results:
                    logger.warning(
                        f"Check-Host request_id={request_id} for {target} produced no usable TCP result after "
                        f"{CHECK_HOST_MAX_WAIT}s in mode={mode_name}. Last raw snapshot: {_summarize_snapshot(last_snapshot)}"
                    )
                    if attempt < CHECK_HOST_MAX_RETRIES - 1:
                        logger.info(f"Retrying {target} in {retry_delay}s.")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    return None

                logger.info(
                    f"FINAL Check-Host TCP result for {target} request_id={request_id}: "
                    f"{_summarize_snapshot(results, limit=2000)}"
                )

                final_statuses: Dict[str, bool] = {}
                for node_id in actual_nodes:

                    node_result = results.get(node_id)
                    if _tcp_node_result_finished(node_result):
                        final_statuses[node_id] = _tcp_node_result_ok(node_result)

                if final_statuses:
                    return final_statuses

                logger.warning(f"Check-Host result for {target} had no completed nodes after filtering pending values.")
                return None

        except httpx.ReadTimeout:
            logger.warning(f"Attempt {attempt + 1}/{CHECK_HOST_MAX_RETRIES} for {target} failed with ReadTimeout.")
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            logger.warning(f"Attempt {attempt + 1}/{CHECK_HOST_MAX_RETRIES} for {target} failed with HTTP {status_code}.")
        except Exception as exc:
            logger.error(f"Unexpected error during Check-Host check for {target}: {exc}", exc_info=True)
            return None

        if attempt < CHECK_HOST_MAX_RETRIES - 1:
            logger.info(f"Retrying {target} in {retry_delay}s.")
            await asyncio.sleep(retry_delay)
            retry_delay *= 2

    return None
