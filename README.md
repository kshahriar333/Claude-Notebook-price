# UCC Price Monitor

Watches the SAME product on UCC, Ryans, Star Tech and Techland.
Sends a Telegram message when any price goes up or down.

## How matching works
- You give the exact product link for each website in `products.json`.
- The bot opens each page, reads the SKU / part number, and checks it equals the UCC SKU.
- If a page shows a different SKU, the bot does NOT compare the price. It warns you.
- If nothing changes, the bot sends nothing.

## Setup (10 minutes)
1. Telegram: message @BotFather -> /newbot -> copy the token.
   Send any message to your new bot, then open
   https://api.telegram.org/bot<TOKEN>/getUpdates and copy `"chat":{"id": ...}`.
2. Create a PRIVATE GitHub repo. Upload ALL files from this zip (including the `.github` folder).
3. Repo -> Settings -> Secrets and variables -> Actions -> New secret:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Repo -> Settings -> Actions -> General -> Workflow permissions -> "Read and write permissions" -> Save.
5. Actions tab -> "UCC Price Monitor" -> Run workflow. You should get a Telegram report.
   After that it runs every hour.

## Add more products
Copy one block in `products.json`, paste the 4 links. `sku` is optional
(if missing, it is read from the UCC page).

## Test on your own PC
    pip install -r requirements.txt
    python monitor.py --dry-run      # prints messages, sends nothing
    python tests/test_monitor.py     # offline logic tests

## If a website shows "blocked"
Some shops block GitHub's servers. The bot tells you once (after 2 failed checks).
Fix: run the same bot on your own PC or a small VPS (same code), because home
internet is usually not blocked.

## Notes
- `state.json` stores the last prices. Do not delete it.
- GitHub pauses scheduled runs if a repo has no activity for 60 days. Price changes
  commit to `state.json`, which counts as activity.
