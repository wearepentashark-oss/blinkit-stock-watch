#!/usr/bin/env python3
"""
Blinkit stock watcher - REAL BROWSER edition.

Blinkit's API is behind Cloudflare bot-protection that rejects plain HTTP
clients (403 challenge). A real browser executes the challenge JS and passes,
so this drives a headless Chromium (via Playwright) and runs the stock API
call *inside* the page - exactly what a logged-in tab does.

Same config.json, same Telegram alerts, same state/heartbeat as watch.py.
Requires: pip install playwright && playwright install chromium
"""

import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
META_KEY = "__meta__"

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state.json")

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# Runs inside the page: calls the product API and extracts the inventory that
# belongs to our exact product_id (walking the layout tree).
FETCH_JS = r"""
async ({pid, lat, lon}) => {
  try {
    const r = await fetch('https://blinkit.com/v1/layout/product/' + pid, {
      method: 'POST',
      headers: {'content-type':'application/json','app_client':'consumer_web',
                'lat': String(lat), 'lon': String(lon)},
      body: '{}', credentials: 'include'
    });
    if (r.status !== 200) return {status: r.status, inv: null};
    const j = await r.json();
    const t = String(pid); const invs = [];
    (function walk(o){
      if (o && typeof o === 'object') {
        if ('inventory' in o && String(o.product_id) === t) {
          const n = parseInt(o.inventory); if (!isNaN(n)) invs.push(n);
        }
        for (const k in o) walk(o[k]);
      }
    })(j);
    return {status: 200, inv: invs.length ? Math.max(...invs) : null};
  } catch (e) { return {status: 0, inv: null, err: String(e)}; }
}
"""


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def tg_send(text):
    if not TG_TOKEN or not TG_CHAT:
        print("!! TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set - skipping Telegram send")
        return
    url = "https://api.telegram.org/bot{}/sendMessage".format(TG_TOKEN)
    payload = urllib.parse.urlencode({
        "chat_id": TG_CHAT, "text": text, "disable_web_page_preview": "false",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=payload), timeout=25) as r:
            r.read()
    except Exception as e:  # noqa: BLE001
        print("!! Telegram send failed:", e)


def fetch_inventory(page, pid, lat, lon):
    try:
        res = page.evaluate(FETCH_JS, {"pid": str(pid), "lat": lat, "lon": lon})
    except Exception as e:  # noqa: BLE001
        return None, 0, "eval error: {}".format(str(e)[:60])
    status = res.get("status")
    inv = res.get("inv")
    if status == 400:
        return None, 400, "no store / bad request"
    if status != 200:
        return None, status, "http {}".format(status)
    if inv is None:
        return None, 200, "product not found for location"
    return inv, 200, "ok"


def maybe_heartbeat(cfg, state, products, locations):
    if not bool(cfg.get("heartbeat", True)):
        return
    hour = int(cfg.get("heartbeat_hour_ist", 9))
    now = datetime.datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    meta = state.setdefault(META_KEY, {})
    if now.hour < hour or meta.get("last_heartbeat_date") == today:
        return
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
            "Watching {} products x {} locations ({} checks/run).\n".format(
                now.strftime("%d %b %Y, %H:%M IST"), len(products), len(locations), total))
    body += ("In stock right now:\n" + "\n".join(in_stock_now)) if in_stock_now else \
        "Everything is out of stock right now. You'll be pinged the moment that changes."
    print("--- HEARTBEAT ---\n" + body)
    tg_send(body)
    meta["last_heartbeat_date"] = today


def run_once(page, cfg, state):
    products = cfg.get("products", [])
    locations = cfg.get("locations", [])
    alert_first = bool(cfg.get("alert_on_first_seen", True))
    notify_out = bool(cfg.get("notify_when_back_out_of_stock", False))
    alerts = []

    for prod in products:
        pid = str(prod.get("id", "")).strip()
        plabel = prod.get("label", pid)
        if not pid:
            continue
        for loc in locations:
            llabel = loc.get("label", "")
            lat, lon = loc.get("lat"), loc.get("lon")
            key = "{}@{}".format(pid, llabel)

            inv, status, note = fetch_inventory(page, pid, lat, lon)
            in_stock = inv is not None and inv > 0
            prev_stock = state.get(key, {}).get("in_stock")

            print("[{}] {} | {} -> inv={} status={} ({})".format(
                "IN " if in_stock else "out", plabel, llabel,
                inv if inv is not None else "-", status, note))

            newly_in = in_stock and prev_stock is not True
            first_time = prev_stock is None
            if in_stock and newly_in and (alert_first or not first_time):
                link = "https://blinkit.com/prn/x/prid/{}".format(pid)
                alerts.append("\U0001F7E2 IN STOCK on Blinkit\n{}\nLocation: {}\n"
                              "Qty available: {}\n{}".format(plabel, llabel, inv, link))
            elif notify_out and prev_stock is True and not in_stock:
                alerts.append("\U0001F534 Sold out again\n{}\nLocation: {}".format(plabel, llabel))

            state[key] = {"in_stock": in_stock, "inv": inv, "ts": int(time.time())}
            time.sleep(0.4)

    for msg in alerts:
        print("--- ALERT ---\n" + msg)
        tg_send(msg)
    maybe_heartbeat(cfg, state, products, locations)
    return len(alerts)


def make_page(pw):
    browser = pw.chromium.launch(headless=True, args=[
        "--no-sandbox", "--disable-blink-features=AutomationControlled",
    ])
    ctx = browser.new_context(user_agent=UA, locale="en-IN",
                              timezone_id="Asia/Kolkata",
                              viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    # Load the site so Cloudflare's challenge runs and the browser gets cleared.
    page.goto("https://blinkit.com", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    return browser, page


def main():
    cfg = load_json(CONFIG_PATH, {})
    if not cfg.get("products") or not cfg.get("locations"):
        print("config.json has no products or locations - nothing to do.")
        return
    state = load_json(STATE_PATH, {})

    loop_min = float(os.environ.get("LOOP_MINUTES", "0") or 0)
    total_min = float(os.environ.get("LOOP_TOTAL_MINUTES", "350") or 350)

    with sync_playwright() as pw:
        browser, page = make_page(pw)
        try:
            deadline = time.time() + total_min * 60
            while True:
                try:
                    n = run_once(page, cfg, state)
                    save_json(STATE_PATH, state)
                    print("Pass done. {} alert(s).".format(n))
                except Exception as e:  # noqa: BLE001
                    print("!! run_once error:", e)
                if loop_min <= 0 or time.time() + loop_min * 60 >= deadline:
                    break
                time.sleep(loop_min * 60)
        finally:
            browser.close()


if __name__ == "__main__":
    if "--test" in sys.argv:
        tg_send("✅ Blinkit browser watcher: Telegram is wired up.")
        print("Sent test message (if creds were set).")
        sys.exit(0)
    main()
