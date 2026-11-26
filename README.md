# UCC Laptop Price Monitor

Watches ALL laptops on https://www.ucc.com.bd/laptops and the same laptops on
Ryans, Star Tech and Techland. Sends a Telegram message when any price changes.

## How it works
1. Reads every laptop on the UCC laptops page (all pages). A new laptop on UCC is added automatically.
2. Reads the SKU / part number from each UCC laptop page.
3. Searches that SKU on Ryans, Star Tech and Techland. A match is kept ONLY if the
   product page shows the same SKU. Different variants (RAM / colour) are never mixed.
4. Every hour it reads all prices and messages you only when something changes.
5. Laptops not found on a site are searched again once a day.

## Telegram messages you get
- 🔻 / 🔺  a price went down / up (UCC or competitor), with "cheaper / higher than UCC"
- 🆕  a new laptop was added on UCC (more than 3 new -> one summary)
- 🔗  a laptop was found on a competitor site later
- 🗑️  a laptop is gone from the UCC list (after 3 checks)
- ⚠️  a page is blocked / cannot be read / shows a different SKU (sent once)
- 📊  full report: run the workflow by hand (Actions -> Run workflow)

## Setup
1. Telegram: @BotFather -> /newbot -> copy token. Open
   https://api.telegram.org/bot<TOKEN>/getUpdates after messaging your bot, copy chat id.
2. Private GitHub repo. Upload ALL files (including the `.github` folder).
3. Settings -> Secrets and variables -> Actions: add `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
4. Settings -> Actions -> General -> Workflow permissions -> Read and write.
5. Actions tab -> "UCC Price Monitor" -> Run workflow.
   The first run sends the full report. Matching all laptops can take about 10 minutes;
   anything not finished is continued by the next hourly run.

## If a laptop is not matched
Open `products.json` (the bot writes it). Add the link by hand under that laptop's `urls`:
    "Ryans": "https://www.ryans.com/...."
Manual links are kept. Add `"pinned": true` to stop the bot from ever dropping a laptop.

## If a whole site shows "0 matched"
The search page of that site may work differently. Send the warning message and one
search page link to the developer, or add links by hand as above.

## Test on your PC
    pip install -r requirements.txt
    python monitor.py --dry-run     # prints messages, sends nothing, saves nothing
    python tests/test_monitor.py    # offline tests

## Notes
- `state.json` = last prices, `products.json` = laptop list and links. Do not delete them.
- GitHub private repos have 2,000 free Actions minutes per month. Hourly use is about
  1,000-1,500. If you run out, change `cron` in `.github/workflows/monitor.yml` to
  `"0 */2 * * *"` (every 2 hours).
- Apple laptops: part numbers often differ between shops (e.g. ZP/A), so they may stay "not matched".
