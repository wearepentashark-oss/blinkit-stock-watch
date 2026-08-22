# Blinkit Stock Watcher

Watches specific products on Blinkit at specific locations and sends a **Telegram
message the moment an item comes in stock**. Runs for free on GitHub Actions every
~5 minutes, 24/7, even when your computer is off.

How it works: for each product x location it calls Blinkit's own product API with
that location's coordinates and reads the `inventory` value. No login, no scraping
of pages, no browser. When something flips from out-of-stock to in-stock, you get a
ping. You're only notified on *changes*, so no spam while an item sits in stock.

```
GitHub Actions (cron, free) --> Blinkit product API (per location) --> Telegram --> your phone
```

---

## One-time setup (about 10 minutes)

### 1. Create a Telegram bot
1. In Telegram, open a chat with **@BotFather**.
2. Send `/newbot`, follow the prompts, and copy the **bot token** it gives you
   (looks like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxx`).
3. Open a chat with **your new bot** and send it any message (e.g. "hi"). This is
   required so the bot is allowed to message you.

### 2. Get your chat ID (no software needed)
Open this URL in any browser, replacing `<TOKEN>` with your bot token:

```
https://api.telegram.org/bot<TOKEN>/getUpdates
```

Find `"chat":{"id":123456789,...}` in the page. That number is your **chat ID**.
(If it's empty, message your bot again and refresh.)

### 3. Put this folder on GitHub
1. Create a new **public** GitHub repo (public = free 2-3 min checks; your secrets
   stay encrypted regardless — see the cost section below).
2. Upload these files (`watch.py`, `config.json`,
   `.github/workflows/monitor-frequent.yml`, `README.md`) to it — via the web
   "Add file > Upload files" (you can drag the whole folder in), or Git.
   `state.json` is created automatically on the first run — don't add it yourself.

### 4. Add your secrets
In the repo: **Settings > Secrets and variables > Actions > New repository secret**.
Add two:
- `TELEGRAM_TOKEN` = your bot token
- `TELEGRAM_CHAT_ID` = your chat ID

### 5. Fill in `config.json`
List the products and locations you want watched (see next section for how to get
the IDs and coordinates). Every product is checked at every location.

```json
{
  "alert_on_first_seen": true,
  "notify_when_back_out_of_stock": false,
  "products": [
    { "id": "444563", "label": "Hot Wheels 1:64 Basic Car" }
  ],
  "locations": [
    { "label": "Koramangala", "lat": 12.9352403, "lon": 77.624532 }
  ]
}
```

### 6. Turn it on and test
1. Go to the **Actions** tab, enable workflows if prompted.
2. Open **Blinkit stock watch > Run workflow** to run it once by hand.
3. Check the run log — you'll see an `IN`/`out` line per product-location.
4. To confirm Telegram works, you can temporarily change the run command to
   `python watch.py --test` (or run it once with that) to get a test ping.

After that it runs itself every ~5 minutes.

---

## How to get the values for config.json

### Product `id`
It's the number Blinkit uses internally for the item. Easiest options:
- **Ask me** — give me the product names and I'll look up the IDs and pre-fill
  `config.json` for you.
- Or self-serve: on `blinkit.com` search the product, open your browser's DevTools
  (F12) > Network, click the item, and look at the `.../v1/layout/product/<ID>`
  request — the `<ID>` is what you want.

### Location `lat` / `lon`
These are the GPS coordinates of the area (this is what actually decides which dark
store, and therefore which stock, you see).
- **Ask me** — give me the area names or pincodes you've saved in your Blinkit app
  and I'll resolve the coordinates.
- Or self-serve: on `blinkit.com`, set your delivery location, then in DevTools >
  Application > Cookies read `gr_1_lat` and `gr_1_lon`.

---

## Settings in config.json
- `alert_on_first_seen` (default `true`) — if an item is already in stock the first
  time it's checked, notify immediately. Set `false` to only notify on later
  out->in transitions.
- `notify_when_back_out_of_stock` (default `false`) — also ping when an item sells
  out again.
- `heartbeat` (default `true`) — send one "watcher is alive" summary per day so you
  know it's still running. The message lists anything currently in stock, or
  confirms everything's out of stock.
- `heartbeat_hour_ist` (default `9`) — the hour (India time, 0-23) at/after which
  the daily heartbeat is sent.

---

## How often it checks, and what it costs
GitHub bills each Actions run rounded **up to the whole minute**, and the free
tiers differ by repo type. This matters once you poll frequently:

| Repo type | Free Actions minutes | Good for |
| --- | --- | --- |
| **Public** | **Unlimited (free)** | Any interval, including 2-3 min. Recommended. |
| **Private** | 2,000 min/month (Free plan) | ~Every 30 min, no faster, to stay free. |

- **This repo ships one workflow: `monitor-frequent.yml` = near-real-time (~3 min).**
  It triggers hourly and loops internally every `LOOP_MINUTES` (GitHub's cron won't
  reliably fire under 5 min on its own). Set `LOOP_MINUTES` to `2` or `3` inside it.
  - On a **public repo: $0** — unlimited minutes, true ~2-3 min cadence.
  - On a **private repo: not worth it** — it runs almost continuously, roughly
    35,000-40,000 billed min/month ≈ **~$250-300/month**. Keep the repo public.
- **Bottom line:** put the repo on **public** and you get 2-3 minute checks for
  **free**. Your Telegram token/chat ID live in encrypted **Secrets** — they are
  *not* exposed by a public repo. The only thing visible is the code and your
  config (toy product IDs + area pincodes), which isn't sensitive.
- **Timing caveat:** GitHub can still delay scheduled runs a few minutes under load;
  it's near-real-time, not second-accurate.
- **Keep it alive:** GitHub pauses scheduled workflows after 60 days with no repo
  activity. The bot commits `state.json` on changes, which counts as activity; if it
  ever pauses, just re-enable it from the Actions tab.
- **If Blinkit ever blocks the cloud IP:** the same `watch.py` runs anywhere with
  Python 3 (a home PC, a Raspberry Pi, a free VPS) with the two env vars set.
- Watching many products x many locations = more requests per run. A few dozen
  combinations is fine; keep it reasonable.
