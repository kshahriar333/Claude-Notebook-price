#!/usr/bin/env python3
"""
UCC Laptop Price Monitor
------------------------
1. Reads ALL laptops from the UCC laptops page (new laptops are picked up automatically).
2. Reads the SKU / part number of each laptop from its UCC page.
3. Searches that SKU on Ryans, Star Tech and Techland, opens the result pages and
   keeps a match ONLY if the page shows the same SKU.
4. Every run: reads all prices, and sends a Telegram message when a price changes.

Run:  python monitor.py            (sends Telegram messages, saves files)
      python monitor.py --dry-run  (prints only, saves nothing)
"""
import copy
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote_plus, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.getenv("PRODUCTS_FILE", os.path.join(BASE, "products.json"))
STATE_FILE = os.getenv("STATE_FILE", os.path.join(BASE, "state.json"))
UCC_LIST_URL = os.getenv("UCC_LIST_URL", "https://www.ucc.com.bd/laptops")
MATCH_BUDGET = int(os.getenv("MATCH_BUDGET_SECONDS", "600"))

COMPETITORS = ["Ryans", "Star Tech", "Techland"]
SITE_ORDER = ["UCC"] + COMPETITORS
SEARCH_URLS = {   # first working one is used
    "Ryans": ["https://www.ryans.com/search?q={q}"],
    "Star Tech": ["https://www.startech.com.bd/product/search?search={q}"],
    "Techland": ["https://www.techlandbd.com/index.php?route=product/search&search={q}",
                 "https://www.techlandbd.com/search?search={q}"],
}
MIN_PRICE, MAX_PRICE = 5_000, 2_000_000
BIG_JUMP = 0.30
MISSING_LIMIT = 3          # runs a laptop can be missing from UCC list before we stop tracking
MAX_NEW_INDIVIDUAL = 3     # more new laptops than this -> one summary message

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
BLOCK_MARKERS = ("just a moment", "attention required", "cf-chl", "captcha",
                 "access denied", "enable javascript and cookies", "bot detection")
PROBLEM_TEXT = {
    "BLOCKED": "website blocked the bot (bot check)",
    "ERROR": "cannot open the page",
    "NO_PRICE": "price not found on the page",
    "SKU_MISMATCH": "page shows a DIFFERENT SKU",
}


# ------------------------------------------------------------------ helpers
def norm(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def money(n):
    return f"৳{int(n):,}"


def short(text, n=70):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n - 1] + "…"


def to_int(value):
    try:
        v = int(round(float(str(value).replace(",", "").strip())))
    except (ValueError, TypeError):
        return None
    return v if MIN_PRICE <= v <= MAX_PRICE else None


class FetchError(Exception):
    def __init__(self, kind, msg):
        super().__init__(msg)
        self.kind, self.msg = kind, msg


def looks_blocked(text):
    head = (text or "")[:6000].lower()
    return any(m in head for m in BLOCK_MARKERS)


def fetch(url, session):
    last = FetchError("ERROR", "unknown error")
    for attempt in range(3):
        try:
            r = session.get(url, headers=HEADERS, timeout=(10, 25))
        except requests.RequestException as exc:
            last = FetchError("ERROR", str(exc)[:120])
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (403, 429) or (r.status_code == 503 and looks_blocked(r.text)):
            raise FetchError("BLOCKED", f"HTTP {r.status_code}")
        if r.status_code == 404:
            raise FetchError("ERROR", "HTTP 404 (page removed or link changed)")
        if r.status_code >= 400:
            last = FetchError("ERROR", f"HTTP {r.status_code}")
            time.sleep(2 * (attempt + 1))
            continue
        if len(r.text) < 30_000 and looks_blocked(r.text):
            raise FetchError("BLOCKED", "bot check page")
        return r.text
    raise last


def run_parallel(chunks, fn):
    chunks = [c for c in chunks if c]
    out = {}
    if not chunks:
        return out
    with ThreadPoolExecutor(max_workers=len(chunks)) as ex:
        for fut in [ex.submit(fn, c) for c in chunks]:
            out.update(fut.result())
    return out


# ------------------------------------------------------------------ page parsing
AMT_RE = re.compile(
    r"(?<![A-Za-z])(?:৳|Tk\.?|BDT)\s*(\d[\d,]*)"
    r"|(?<![\w.,\-])(\d{1,3}(?:,\d{3})+|\d{4,})\s*(?:৳|Tk\b|BDT)", re.I)
SKU_RE = re.compile(
    r"\b(?:SKU|MPN|Part\s*(?:No\.?|Number|#))\s*[:#\-]?\s*"
    r"([A-Za-z0-9](?=[A-Za-z0-9.\-/_]*\d)[A-Za-z0-9.\-/_]{3,})", re.I)
PRICE_SELECTORS = [".price-new", ".special-price", "[itemprop=price]",
                   ".product-price", ".price"]


def visible_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["head", "script", "style", "noscript", "template"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def price_from_meta(soup):
    for tag in soup.select('meta[property="product:price:amount"], '
                           'meta[property="og:price:amount"], meta[itemprop="price"]'):
        v = to_int(tag.get("content"))
        if v:
            return v
    return None


def price_from_jsonld(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except ValueError:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                for key in ("price", "lowPrice"):
                    v = to_int(d.get(key))
                    if v:
                        return v
                stack.extend(x for x in d.values() if isinstance(x, (dict, list)))
    return None


def price_from_text(text, anchor):
    """First price after the heading. If an old price is followed right away by a
    lower price, the lower one is the selling price."""
    start = max(text.find(anchor) if anchor else -1, 0)
    found = []
    for m in AMT_RE.finditer(text, start):
        v = to_int(m.group(1) or m.group(2))
        if v:
            found.append((v, m.start(), m.end()))
        if len(found) == 2:
            break
    if not found:
        return None
    if len(found) == 2:
        (v1, _, e1), (v2, s2, _) = found
        if s2 - e1 < 25 and 0.6 * v1 <= v2 < v1:
            return v2
    return found[0][0]


def price_from_selectors(soup):
    for sel in PRICE_SELECTORS:
        for tag in soup.select(sel):
            v = to_int(tag.get("content")) or to_int(re.sub(r"[^\d.]", "", tag.get_text(" ")))
            if v:
                return v
    return None


def check_site(site, url, expected_norm, session):
    res = {"site": site, "url": url, "status": "OK", "price": None, "title": "",
           "sku_found": None, "sku_status": "UNVERIFIED", "error": ""}
    try:
        html = fetch(url, session)
    except FetchError as exc:
        res.update(status=exc.kind, error=exc.msg)
        return res

    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    h1text = re.sub(r"\s+", " ", h1.get_text(" ", strip=True)) if h1 else ""
    res["title"] = h1text
    text = visible_text(html)

    price = (price_from_meta(soup) or price_from_jsonld(soup)
             or price_from_text(text, h1text) or price_from_selectors(soup))
    labeled = SKU_RE.findall(text)
    first = labeled[0].strip(".") if labeled else None
    res["sku_found"] = first

    if expected_norm:
        if first:
            res["sku_status"] = "VERIFIED" if norm(first) == expected_norm else "MISMATCH"
        else:
            res["sku_status"] = "PRESENT" if expected_norm in norm(text) else "UNVERIFIED"

    if res["sku_status"] == "MISMATCH":
        res["status"] = "SKU_MISMATCH"
        res["error"] = f"page SKU is {first}"
    elif price is None:
        res["status"] = "NO_PRICE"
    res["price"] = price
    return res


# ------------------------------------------------------------------ UCC list discovery
ADD_RE = re.compile(r"add to cart", re.I)
TOTAL_RE = re.compile(r"of\s+(\d+)\s*\(\s*\d+\s*Pages?\s*\)", re.I)


def canonical_ucc_url(href, base):
    url = urljoin(base, href.strip()).split("#")[0].rstrip("/")
    m = re.search(r"product_id=(\d+)", url)
    if m:
        return f"https://www.ucc.com.bd/index.php?route=product/product&product_id={m.group(1)}"
    return url.replace("http://", "https://")


def parse_listing(html, base_url):
    """Find product cards: the smallest block that has exactly one 'Add to Cart'."""
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for node in soup.find_all(string=ADD_RE):
        card = node.parent
        while card.parent is not None and len(card.parent.find_all(string=ADD_RE)) == 1:
            card = card.parent
        if len(card.get_text(" ", strip=True)) > 3000:
            continue
        link = None
        for a in card.find_all("a", href=True):
            href = a["href"].strip().lower()
            name = a.get_text(" ", strip=True)
            if len(name) < 20 or href.startswith(("#", "javascript")):
                continue
            if any(x in href for x in ("compare", "wishlist", "cart")):
                continue
            link = (canonical_ucc_url(a["href"], base_url), re.sub(r"\s+", " ", name))
            break
        if link and link[0] not in seen:
            seen.add(link[0])
            items.append({"url": link[0], "name": link[1]})
    m = TOTAL_RE.search(soup.get_text(" ", strip=True))
    return items, int(m.group(1)) if m else None


def discover_ucc(session):
    found, total = {}, None
    for page in range(1, 9):
        sep = "&" if "?" in UCC_LIST_URL else "?"
        html = fetch(f"{UCC_LIST_URL}{sep}limit=100&page={page}", session)
        items, t = parse_listing(html, UCC_LIST_URL)
        total = total or t
        new = [i for i in items if i["url"] not in found]
        if not new:
            break
        for i in new:
            found[i["url"]] = i
        if total and len(found) >= total:
            break
    return list(found.values()), total


# ------------------------------------------------------------------ competitor matching
TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)*")
GENERIC = re.compile(
    r"^(?:\d+(?:GB|TB|MB|HZ|MHZ|GHZ|INCH|WHR|MM|NM|W|MS|ST|ND|RD|TH)|(?:LP)?DDR\d\w*|GEN\d+|"
    r"RTX\d+|GTX\d+|WINDOWS11|WIN11|WQXGA|NVME|PCIE|GDDR\d+|\d+X\d+)$", re.I)
BAD_PATH = ("search", "compare", "wishlist", "cart", "login", "account", "checkout")


def model_tokens(text):
    out = set()
    for t in TOKEN_RE.findall(text or ""):
        parts = [t] + (re.split(r"[-.]", t) if re.search(r"[-.]", t) else [])
        for q in parts:
            q = q.upper()
            if (len(q) >= 4 and re.search(r"\d", q) and re.search(r"[A-Z]", q)
                    and not GENERIC.match(q)):
                out.add(q)
    return out


def model_query(name, sku):
    for t in TOKEN_RE.findall(name or ""):
        q = t.upper()
        if (len(q) >= 5 and re.search(r"\d", q) and re.search(r"[A-Z]", q)
                and not GENERIC.match(q) and norm(q) != norm(sku)):
            return t
    return None


def candidate_links(html, base_url, ucc_name, sku):
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(base_url).netloc.replace("www.", "")
    want = model_tokens(ucc_name) | model_tokens(sku)
    sku_n = norm(sku)
    scored = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript", "mailto", "tel")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        pu = urlparse(url)
        if pu.netloc.replace("www.", "") != host:
            continue
        if any(x in pu.path.lower() for x in BAD_PATH):
            continue
        text = " ".join(filter(None, [a.get_text(" ", strip=True), a.get("title", ""),
                                      " ".join(i.get("alt", "") for i in a.find_all("img"))]))
        if len(text) < 12:
            continue
        score = len(want & model_tokens(text)) + (10 if sku_n and sku_n in norm(text) else 0)
        if score >= 1:
            scored[url] = max(scored.get(url, 0), score)
    return [u for u, _ in sorted(scored.items(), key=lambda kv: -kv[1])]


def find_on_site(site, product, session, sleep=0.5):
    """Returns (url, check_result) when a page with the SAME SKU is found, else None.
    Raises FetchError(BLOCKED) if the site blocks the search."""
    expected = norm(product["sku"])
    queries = [product["sku"]]
    alt = model_query(product.get("name"), product["sku"])
    if alt:
        queries.append(alt)
    tried = set()
    for q in queries:
        cands = []
        for tmpl in SEARCH_URLS[site]:
            url = tmpl.format(q=quote_plus(q))
            try:
                html = fetch(url, session)
            except FetchError as exc:
                if exc.kind == "BLOCKED":
                    raise
                continue
            time.sleep(sleep)
            cands = candidate_links(html, url, product.get("name"), product["sku"])
            if cands:
                break
        for link in cands[:4]:
            if link in tried:
                continue
            tried.add(link)
            res = check_site(site, link, expected, session)
            time.sleep(sleep)
            if res["status"] == "OK" and res["sku_status"] in ("VERIFIED", "PRESENT"):
                return link, res
    return None


# ------------------------------------------------------------------ messages
def relation(price, ucc_price):
    if ucc_price is None or price is None:
        return ""
    diff = price - ucc_price
    if diff == 0:
        return "✅ same as UCC"
    if diff < 0:
        return f"🔴 {money(-diff)} cheaper than UCC"
    return f"🟢 {money(diff)} higher than UCC"


def now_lines(urls, entries):
    ucc_price = (entries.get("UCC") or {}).get("price")
    lines = []
    for site in SITE_ORDER:
        if site not in urls:
            lines.append(f"{site}: — not matched")
            continue
        e = entries.get(site) or {}
        price, status = e.get("price"), e.get("status", "ERROR")
        if status == "OK" and price:
            tag = "(our price)" if site == "UCC" else relation(price, ucc_price)
            warn = " ⚠️SKU not confirmed" if e.get("sku_status") == "UNVERIFIED" else ""
            lines.append(f"{site}: {money(price)} {tag}{warn}".rstrip())
        else:
            last = f" | last known {money(price)}" if price else ""
            lines.append(f"{site}: ⚠️ {PROBLEM_TEXT.get(status, status)}{last}")
    return "\n".join(lines)


def compact_block(p, entries):
    up = (entries.get("UCC") or {}).get("price")
    head = (f"{short(p.get('name') or p['id'])}\n"
            f"SKU: {p.get('sku') or 'not found'} | UCC {money(up) if up else '—'}")
    parts = []
    for site in COMPETITORS:
        e = entries.get(site) or {}
        if site not in p["urls"]:
            parts.append(f"{site} —")
        elif e.get("status") != "OK" or not e.get("price"):
            parts.append(f"{site} ⚠️")
        elif up is None:
            parts.append(f"{site} {money(e['price'])}")
        elif e["price"] == up:
            parts.append(f"{site} ✅")
        elif e["price"] < up:
            parts.append(f"{site} 🔴 -{up - e['price']:,}")
        else:
            parts.append(f"{site} 🟢 +{e['price'] - up:,}")
    return head + "\n" + " | ".join(parts)


LEGEND = "✅ same as UCC · 🔴 cheaper than UCC · 🟢 higher than UCC · — not found · ⚠️ cannot read"


def summary_message(title, plist, state_map, stamp):
    full = sum(1 for p in plist if all(s in p["urls"] for s in COMPETITORS))
    head = f"{title}\nLaptops: {len(plist)} | matched on all 3 sites: {full}\n{LEGEND}"
    blocks = [compact_block(p, state_map.get(p["id"], {})) for p in plist]
    return head + "\n\n" + "\n\n".join(blocks) + f"\n\n🕒 {stamp}"


def pack_messages(messages, limit=3800):
    """Join short messages and split long ones at blank lines. Whole alerts stay together."""
    sep = "\n\n━━━━━━━━\n\n"
    units = []
    for m in messages:
        if len(m) <= limit:
            units.append(m)
        else:
            units.extend(m.split("\n\n"))
    chunks, cur = [], ""
    for u in units:
        if cur and len(cur) + len(sep) + len(u) > limit:
            chunks.append(cur)
            cur = u
        else:
            cur = u if not cur else cur + sep + u
    if cur:
        chunks.append(cur)
    return chunks


# ------------------------------------------------------------------ core
def run(products, state, make_session, now, listing=None, listing_error=None,
        force_report=False, sleep=0.5, budget=MATCH_BUDGET):
    """Returns (messages, new_state, products). No file or Telegram access here."""
    products = copy.deepcopy(products)
    today, stamp = now.strftime("%Y-%m-%d"), now.strftime("%d %b %Y, %I:%M %p")
    deadline = time.time() + budget
    old_meta = state.get("__meta__", {})
    first_run = not any(k for k in state if k != "__meta__")
    warnings = {}

    for p in products:
        p.setdefault("id", p["urls"]["UCC"])
        p.setdefault("active", True)
        p.setdefault("missing_runs", 0)
        p.setdefault("searched", {})
        p.setdefault("added", today)
    by_id = {p["id"]: p for p in products}

    # ---- 1. sync with the UCC laptop list
    added, removed, reactivated = [], [], []
    if listing_error:
        warnings["listing"] = f"⚠️ Could not read the UCC laptop list: {listing_error}"
    if listing:
        items, total = listing
        listed = set()
        for it in items:
            pid = it["url"]
            listed.add(pid)
            p = by_id.get(pid)
            if p is None:
                p = {"id": pid, "name": it["name"], "sku": None, "urls": {"UCC": pid},
                     "searched": {}, "active": True, "missing_runs": 0, "added": today}
                products.append(p)
                by_id[pid] = p
                added.append(p)
            else:
                p["missing_runs"] = 0
                if not p["active"]:
                    p["active"] = True
                    reactivated.append(p)
        if total and len(items) >= total:
            for p in products:
                if p["id"] in listed or not p["active"] or p.get("pinned"):
                    continue
                p["missing_runs"] += 1
                if p["missing_runs"] >= MISSING_LIMIT:
                    p["active"] = False
                    removed.append(p)
        elif total:
            warnings["listing_short"] = (f"⚠️ Read only {len(items)} of {total} laptops from the "
                                         f"UCC list. Check the page layout.")
        elif not items:
            warnings["listing_empty"] = "⚠️ No laptops found on the UCC list page."

    active = [p for p in products if p["active"]]

    # ---- 2. UCC pages (price + SKU)
    def ucc_worker(chunk):
        s, out = make_session(), {}
        for p in chunk:
            out[p["id"]] = check_site("UCC", p["urls"]["UCC"], norm(p.get("sku")) or None, s)
        return out

    ucc_res = run_parallel([active[i::6] for i in range(6)], ucc_worker)
    for p in active:
        res = ucc_res.get(p["id"], {})
        if not p.get("sku") and res.get("sku_found"):
            p["sku"] = res["sku_found"]
        if not p.get("name") and res.get("title"):
            p["name"] = res["title"]

    # ---- 3. find the same laptop on competitor sites (once per day for missing ones)
    def due(p, site):
        return bool(p.get("sku")) and site not in p["urls"] and p["searched"].get(site) != today

    def match_worker(chunk):
        site, s, out = chunk[0], make_session(), {}
        todo = [p for p in active if due(p, site)]
        for p in todo:
            if time.time() > deadline:
                break
            try:
                hit = find_on_site(site, p, s, sleep)
            except FetchError as exc:
                out[("__blocked__", site)] = exc.msg
                break
            out[(p["id"], site)] = hit or "none"
        return out

    match_out = run_parallel([[site] for site in COMPETITORS], match_worker)
    pre, newly_matched = {}, []
    for site in COMPETITORS:
        tried = [k for k in match_out if k[1] == site and k[0] != "__blocked__"]
        found = 0
        for k in tried:
            pid = k[0]
            if match_out[k] == "none":
                by_id[pid]["searched"][site] = today
            else:
                url, res = match_out[k]
                by_id[pid]["urls"][site] = url
                by_id[pid]["searched"].pop(site, None)
                pre[(pid, site)] = res
                newly_matched.append((by_id[pid], site, res["price"]))
                found += 1
        if ("__blocked__", site) in match_out:
            warnings[f"blocked_{site}"] = (f"⚠️ {site} blocked the search "
                                           f"({match_out[('__blocked__', site)]}). Matching paused.")
        elif len(tried) >= 5 and found == 0 and not any(site in p["urls"] for p in active):
            warnings[f"zero_{site}"] = (f"⚠️ {site}: 0 of {len(tried)} laptops matched. The search "
                                        f"page may work differently. Add links by hand in products.json.")

    # ---- 4. read competitor prices (parallel, one worker per site)
    def comp_worker(chunk):
        site, s, out = chunk[0], make_session(), {}
        for p in active:
            if site not in p["urls"]:
                continue
            key = (p["id"], site)
            if key in pre:
                out[key] = pre[key]
                continue
            out[key] = check_site(site, p["urls"][site], norm(p.get("sku")) or None, s)
            time.sleep(sleep)
        return out

    comp_res = run_parallel([[site] for site in COMPETITORS], comp_worker)

    # ---- 5. compare with saved prices
    new_state = {"__meta__": {}}
    alerts, current_entries = [], {}
    for p in active:
        pid, urls = p["id"], p["urls"]
        results = {}
        if pid in ucc_res:
            results["UCC"] = ucc_res[pid]
        for site in COMPETITORS:
            if (pid, site) in comp_res:
                results[site] = comp_res[(pid, site)]

        prev_all = state.get(pid, {})
        entries, changes, problems, recovered = {}, [], [], []
        for site, res in results.items():
            prev = prev_all.get(site, {})
            e = dict(prev)
            if res["status"] == "OK":
                old, new = prev.get("price"), res["price"]
                if old is not None and old != new:
                    changes.append((site, old, new))
                    e["last_change"] = today
                if prev.get("alerted"):
                    recovered.append(site)
                e.update(price=new, status="OK", fails=0, alerted=False,
                         sku_status=res["sku_status"])
            else:
                fails = prev.get("fails", 0) + 1
                e.update(status=res["status"], fails=fails, sku_status=res["sku_status"])
                limit = 1 if res["status"] == "SKU_MISMATCH" else 2
                if fails >= limit and not prev.get("alerted"):
                    problems.append((site, res))
                    e["alerted"] = True
            entries[site] = e
        new_state[pid] = entries
        current_entries[pid] = entries

        parts, link_sites = [], []
        for site, old, new in changes:
            diff = new - old
            arrow, word = ("🔻", "DOWN") if diff < 0 else ("🔺", "UP")
            flag = "  ⚠️ big change, please verify" if abs(diff) / old > BIG_JUMP else ""
            parts.append(f"{arrow} {site} price {word}: {money(old)} → {money(new)} "
                         f"({'-' if diff < 0 else '+'}{money(abs(diff))}){flag}")
            link_sites.append(site)
        for site, res in problems:
            extra = f" ({res['error']})" if res["error"] else ""
            parts.append(f"⚠️ {site}: {PROBLEM_TEXT.get(res['status'], res['status'])}{extra}")
            link_sites.append(site)
        for site in recovered:
            parts.append(f"✅ {site} can be read again")
        if parts:
            links = "\n".join(f"{s}: {urls[s]}" for s in link_sites if s in urls)
            alerts.append(f"{short(p.get('name') or pid, 90)}\nSKU: {p.get('sku') or 'unknown'}\n\n"
                          + "\n".join(parts) + "\n\nNow:\n" + now_lines(urls, entries)
                          + (f"\n\n{links}" if links else "") + f"\n\n🕒 {stamp}")

    # ---- 6. messages
    messages = []
    old_warned = set(old_meta.get("warned", []))
    for key, text in warnings.items():
        if key not in old_warned:
            messages.append(text)
    new_state["__meta__"] = {"warned": sorted(warnings)}

    if first_run:
        messages.append(summary_message("✅ Monitoring started", active, current_entries, stamp))
    else:
        if force_report:
            messages.append(summary_message("📊 Price report", active, current_entries, stamp))
        if added:
            if len(added) <= MAX_NEW_INDIVIDUAL:
                for p in added:
                    messages.append(f"🆕 New laptop on UCC\n{short(p.get('name') or p['id'], 90)}\n"
                                    f"SKU: {p.get('sku') or 'unknown'}\n\nNow:\n"
                                    + now_lines(p["urls"], current_entries.get(p["id"], {}))
                                    + f"\n\n🕒 {stamp}")
            else:
                messages.append(summary_message(f"🆕 {len(added)} new laptops added", added,
                                                current_entries, stamp))
        added_ids = {p["id"] for p in added}
        lines = []
        for p, site, price in newly_matched:
            if p["id"] in added_ids:
                continue
            up = (current_entries.get(p["id"], {}).get("UCC") or {}).get("price")
            lines.append(f"• {short(p.get('name') or p['id'], 60)}\n  {site}: {money(price)} "
                         f"{relation(price, up)}".rstrip())
        if lines:
            messages.append("🔗 Now matched on a competitor site:\n" + "\n".join(lines))
        messages.extend(alerts)
    if first_run:
        messages.extend(alerts)
    for p in removed:
        messages.append(f"🗑️ No longer on the UCC laptop list (missing {MISSING_LIMIT} checks), "
                        f"tracking stopped:\n{short(p.get('name') or p['id'], 90)}")
    for p in reactivated:
        messages.append(f"♻️ Back on the UCC laptop list, tracking again:\n"
                        f"{short(p.get('name') or p['id'], 90)}")
    nosku = [p for p in active if not p.get("sku")]
    if nosku and (first_run or added):
        messages.append(f"⚠️ {len(nosku)} laptop(s) have no SKU on the UCC page, so they can't be "
                        f"matched:\n" + "\n".join(f"• {short(p.get('name') or p['id'], 80)}"
                                                   for p in nosku[:15]))
    return messages, new_state, products


# ------------------------------------------------------------------ io
def send_telegram(text, token, chat_id):
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": "true"},
        timeout=20)
    r.raise_for_status()
    if not r.json().get("ok"):
        raise RuntimeError(f"Telegram rejected the message: {r.text[:200]}")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def main():
    dry = "--dry-run" in sys.argv
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not dry and not (token and chat_id):
        sys.exit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID. "
                 "Add them as GitHub Secrets, or use --dry-run.")

    products = load_json(PRODUCTS_FILE, [])
    state = load_json(STATE_FILE, {})
    force = os.getenv("FORCE_REPORT", "").lower() in ("1", "true", "yes")
    now = datetime.now(ZoneInfo("Asia/Dhaka"))

    listing, listing_error = None, None
    try:
        with requests.Session() as s:
            listing = discover_ucc(s)
        print(f"UCC list: {len(listing[0])} laptops (page says {listing[1]})")
    except FetchError as exc:
        listing_error = f"{exc.kind} {exc.msg}"
    if not products and not listing:
        sys.exit(f"No products and cannot read UCC list: {listing_error}")

    messages, new_state, products = run(products, state, requests.Session, now,
                                        listing=listing, listing_error=listing_error,
                                        force_report=force)
    chunks = pack_messages(messages)
    for c in chunks:
        if dry:
            print(c, "\n" + "=" * 40)
        else:
            send_telegram(c, token, chat_id)     # if this fails, nothing is saved
            time.sleep(1)
    print(f"Done. Telegram messages: {len(chunks)}")

    if not dry:
        save_json(PRODUCTS_FILE, products)
        if new_state != state:
            save_json(STATE_FILE, new_state)


if __name__ == "__main__":
    main()
