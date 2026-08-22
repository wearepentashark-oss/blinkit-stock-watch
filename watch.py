#!/usr/bin/env python3
"""
Blinkit stock watcher.

For every product x every location in config.json, ask Blinkit's product API
whether that item is in stock at the dark store serving that location, and send
a Telegram message the moment something flips from out-of-stock to in-stock.

Pure standard library - no pip installs. Runs on GitHub Actions (or anywhere
with Python 3). State is kept in state.json so you only get pinged on changes.
"""

import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

# Blinkit's edge blocks bare Python HTTP clients (HTTP 403) via TLS/bot
# fingerprinting. curl_cffi impersonates a real Chrome TLS handshake, which
# gets past it. If it isn't installed we fall back to urllib (works locally
# from a residential IP, but will likely be 403'd from a datacenter).
try:
    from curl_cffi import requests as cffi_requests  # type: ignore
    _HAVE_CFFI = True
except Exception:  # noqa: BLE001
    cffi_requests = None
    _HAVE_CFFI = False

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
META_KEY = "__meta__"

API = "https://blinkit.com/v1/layout/product/{pid}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state.json")

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def fetch_inventory(pid, lat, lon):
    """Return (inventory:int|None, http_status:int, note:str).

    inventory is None when we couldn't determine stock (e.g. no store serves
    this location, or the product wasn't in the response).
    """
    url = API.format(pid=urllib.parse.quote(str(pid)))
    headers = {
        "content-type": "application/json",
        "app_client": "consumer_web",
        "lat": str(lat),
        "lon": str(lon),
        "user-agent": UA,
        "accept": "*/*",
        "web_app_version": "1000.0.0",
        "origin": "https://blinkit.com",
        "referer": "https://blinkit.com/",
    }
    if _HAVE_CFFI:
        try:
            r = cffi_requests.post(url, data=b"{}", headers=headers,
                                   impersonate="chrome", timeout=25)
            status = r.status_code
            body = r.text
            if status == 400:
                return None, status, "no store / bad request"
            if status != 200:
                return None, status, f"http {status}"
        except Exception as e:  # noqa: BLE001
            return None, 0, f"error: {e}"
    else:
        req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                status = resp.getcode()
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            # 400 usually means no Blinkit store serves this lat/lon.
            return None, e.code, "no store / bad request" if e.code == 400 else f"http {e.code}"
        except Exception as e:  # noqa: BLE001
            return None, 0, f"error: {e}"

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None, status, "non-json response"

    # Walk the layout tree and collect every inventory value that belongs to
    # our exact product_id. Take the max (variants can appear more than once).
    target = str(pid)
    invs = []

    def walk(o):
        if isinstance(o, dict):
            if "inventory" in o and str(o.get("product_id")) == target:
                try:
                    invs.append(int(o["inventory"]))
                except (TypeError, ValueError):
                    pass
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    if not invs:
        return None, status, "product not found for location"
    return max(invs), status, "ok"


def tg_send(text):
    if not TG_TOKEN or not TG_CHAT:
        print("!! TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set - skipping Telegram send")
        return
    url = "https://api.telegram.org/bot{}/sendMessage".format(TG_TOKEN)
    payload = urllib.parse.urlencode({
        "chat_id": TG_CHAT,
        "text": text,
        "disable_web_page_preview": "false",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=payload), timeout=25) as r:
            r.read()
    except Exception as e:  # noqa: BLE001
        print("!! Telegram send failed:", e)


def maybe_heartbeat(cfg, state, products, locations):
    """Send one 'still alive' summary per day, at/after the configured IST hour."""
    if not bool(cfg.get("heartbeat", True)):
        return
    hour = int(cfg.get("heartbeat_hour_ist", 9))
    now = datetime.datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    meta = state.setdefault(META_KEY, {})
    if now.hour < hour or meta.get("last_heartbeat_date") == today:
        return

    # Build a quick current-stock summary from the freshly-updated state.
    in_stock_now = []
    for prod in products:
        pid = str(prod.get("id", "")).strip()
        for loc in locations:
            st = state.get("{}@{}".format(pid, loc.get("label", "")), {})
            if st.get("in_stock"):
                in_stock_now.append("- {} @ {} (qty {})".format(
                    prod.get("label", pid), loc.get("label", ""), st.get("inv")))

    total = len(products) * len(locations)
    body = ("\U0001F49A Blinkit watcher is alive - {}\n"
            "Watching {} products x {} locations ({} checks/run).\n"
            .format(now.strftime("%d %b %Y, %H:%M IST"),
                    len(products), len(locations), total))
    if in_stock_now:
        body += "In stock right now:\n" + "\n".join(in_stock_now)
    else:
        body += "Everything is out of stock right now. You'll be pinged the moment that changes."

    print("--- HEARTBEAT ---\n" + body)
    tg_send(body)
    meta["last_heartbeat_date"] = today


def run_once():
    cfg = load_json(CONFIG_PATH, {})
    products = cfg.get("products", [])
    locations = cfg.get("locations", [])
    alert_first = bool(cfg.get("alert_on_first_seen", True))
    notify_out = bool(cfg.get("notify_when_back_out_of_stock", False))

    if not products or not locations:
        print("config.json has no products or no locations - nothing to do.")
        return

    state = load_json(STATE_PATH, {})
    alerts = []
    changed = False

    for prod in products:
        pid = str(prod.get("id", "")).strip()
        plabel = prod.get("label", pid)
        if not pid:
            continue
        for loc in locations:
            llabel = loc.get("label", "")
            lat, lon = loc.get("lat"), loc.get("lon")
            key = "{}@{}".format(pid, llabel)

            inv, status, note = fetch_inventory(pid, lat, lon)
            in_stock = inv is not None and inv > 0
            prev = state.get(key, {})
            prev_stock = prev.get("in_stock")  # True / False / None (never seen)

            print("[{}] {} | {} -> inv={} status={} ({})".format(
                "IN " if in_stock else "out", plabel, llabel,
                inv if inv is not None else "-", status, note))

            # Decide whether to alert.
            newly_in = in_stock and prev_stock is not True
            first_time = prev_stock is None
            if in_stock and (newly_in and (alert_first or not first_time)):
                link = "https://blinkit.com/prn/x/prid/{}".format(pid)
                alerts.append(
                    "\U0001F7E2 IN STOCK on Blinkit\n"
                    "{}\n"
                    "Location: {}\n"
                    "Qty available: {}\n"
                    "{}".format(plabel, llabel, inv, link)
                )
            elif notify_out and prev_stock is True and not in_stock:
                alerts.append(
                    "\U0001F534 Sold out again\n{}\nLocation: {}".format(plabel, llabel))

            if prev_stock != in_stock:
                changed = True
            state[key] = {"in_stock": in_stock, "inv": inv, "ts": int(time.time())}
            time.sleep(0.7)  # be polite between requests

    for msg in alerts:
        print("--- ALERT ---\n" + msg)
        tg_send(msg)

    maybe_heartbeat(cfg, state, products, locations)

    save_json(STATE_PATH, state)
    print("Done. {} product-location checks, {} alert(s), state {}.".format(
        len(products) * len(locations), len(alerts),
        "updated" if changed else "unchanged"))


def main():
    """Run one pass, or - if LOOP_MINUTES is set - keep checking on that interval
    for up to LOOP_TOTAL_MINUTES (used by the frequent/near-real-time workflow).
    """
    loop_min = float(os.environ.get("LOOP_MINUTES", "0") or 0)
    total_min = float(os.environ.get("LOOP_TOTAL_MINUTES", "55") or 55)
    if loop_min <= 0:
        run_once()
        return

    deadline = time.time() + total_min * 60
    while True:
        try:
            run_once()
        except Exception as e:  # noqa: BLE001  keep the loop alive on transient errors
            print("!! run_once error:", e)
        if time.time() + loop_min * 60 >= deadline:
            break
        time.sleep(loop_min * 60)


if __name__ == "__main__":
    # `python watch.py --test` sends a test Telegram message and exits.
    if "--test" in sys.argv:
        tg_send("✅ Blinkit stock watcher: Telegram is wired up correctly.")
        print("Sent test message (if creds were set).")
        sys.exit(0)
    main()
