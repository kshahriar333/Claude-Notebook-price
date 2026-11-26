"""Offline tests with fake pages that copy the real layouts of UCC, Ryans, Star Tech, Techland."""
import os, sys
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import monitor
from monitor import run, check_site, norm, parse_listing, discover_ucc, candidate_links, find_on_site, pack_messages

monitor.time.sleep = lambda *_: None
BLOCK = "BLOCK"

# ------------------------------------------------------------------ fake sites
def ucc_page(name, sku, price):
    return f"""<html><head><title>{name} UN.35MSI.01C Price in BD</title></head><body>
    <div>0 item(s) - ৳0</div><h1>{name}</h1>
    <ul><li><b>Stock:</b> In Stock</li><li><b>SKU:</b> {sku}</li></ul><h2>৳{price:,}</h2><p>EMI ৳2,674/month</p></body></html>"""

def ryans_page(sku, price):
    return f"""<html><head><meta property="product:price:amount" content="{price}"></head><body>
    <h1>Acer</h1><p>Special Price Tk {price:,} Regular Price Tk 99,999</p>
    <table><tr><td>Part No</td><td>{sku}</td></tr></table></body></html>"""

def startech_page(sku, price):
    return f"""<html><head><meta property="product:price:amount" content="{price}.0000"></head><body>
    <h1>Laptop</h1><ul><li>MPN: {sku}</li></ul></body></html>"""

def techland_page(sku, price):
    return f"""<html><body><h1>Laptop</h1><div>Stock : In Stock SKU : {sku} Brand : Acer</div>
    <div>Discount Price ৳ {price:,} ৳ {price + 6000:,}</div></body></html>"""

def card(url, name, price):
    return f"""<div class="product-layout"><div class="product-thumb"><a href="{url}"><img alt="x"></a>
    <a href="#">Quickview</a><div class="caption"><h4><a href="{url}">{name}</a></h4><p>Stock: In Stock</p>
    <p class="price">৳{price:,}</p></div><button onclick="cart.add(1)">Add to Cart</button>
    <a href="#">Add to Wish List</a></div></div>"""

NAV = '<nav><a href="/laptops">Laptops</a><a href="/desktop-pc">Desktop PC</a></nav>'
def listing_html(cards, total):
    return (f"<html><body>{NAV}<div>Show: 20 25 50</div>" + "".join(cards)
            + f"<div>Showing 1 to {len(cards)} of {total} (1 Pages)</div></body></html>")

def search_html(*links):
    return "<html><body>" + NAV + "".join(f'<div><a href="{u}">{t}</a></div>' for u, t in links) + "</body></html>"

P1 = dict(name="Acer Aspire 15 AS15-42-R5 Ryzen 5 7430U 16GB 512GB", sku="UN.35MSI.02B",
          url="https://www.ucc.com.bd/acer-aspire-15-as15-42-r5", price=77500)
P2 = dict(name="MSI Katana 15 HX B14WGK Intel Core i7 RTX 5070 16GB DDR5 Gaming Laptop", sku="9S7-158K11-4A2",
          url="https://www.ucc.com.bd/msi-katana-15-hx-b14wgk", price=233900)
P3 = dict(name="Acer Predator Helios Neo 16S AI Gaming Laptop RTX 5060 16GB", sku="NH.QSZSI.001",
          url="https://www.ucc.com.bd/laptops?product_id=5182", price=279900)
P3_CANON = "https://www.ucc.com.bd/index.php?route=product/product&product_id=5182"

class FakeSession:
    def __init__(self, pages): self.pages, self.calls = pages, []
    def get(self, url, headers=None, timeout=None):
        class R: pass
        r = R(); self.calls.append(url); page = self.pages.get(url)
        if page is None: r.status_code, r.text = 404, ""
        elif page == BLOCK: r.status_code, r.text = 403, "Just a moment..."
        else: r.status_code, r.text = 200, page
        return r

def world(listing=(P1, P2, P3), total=None, st_price=77500, ucc_price=None):
    pg = {}
    listed = [P1, P2, P3] if listing is None else list(listing)
    pg["https://www.ucc.com.bd/laptops?limit=100&page=1"] = listing_html(
        [card(p["url"], p["name"], p["price"]) for p in listed], total or len(listed))
    for p in (P1, P2):
        pg[p["url"]] = ucc_page(p["name"], p["sku"], (ucc_price or {}).get(p["sku"], p["price"]))
    pg[P3_CANON] = ucc_page(P3["name"], P3["sku"], P3["price"])
    # Ryans: P1 found by SKU search
    pg["https://www.ryans.com/search?q=UN.35MSI.02B"] = search_html(
        ("/acer-aspire-15-as15-42", "Acer Aspire 15 AS15-42 Ryzen 5 7430U 15.6 Inch FHD Laptop"))
    pg["https://www.ryans.com/acer-aspire-15-as15-42"] = ryans_page("UN.35MSI.02B", 77500)
    # Star Tech: P1 search shows a WRONG variant first (higher score), then the right one
    pg["https://www.startech.com.bd/product/search?search=UN.35MSI.02B"] = search_html(
        ("/acer-aspire-15-as15-42-r5-01c", "Acer Aspire 15 AS15-42-R5 Ryzen 5 7430U 15.6 Laptop 8GB"),
        ("/acer-aspire-15-as15-42-16gb", "Acer Aspire 15 AS15-42 Ryzen 5 7430U Laptop"))
    pg["https://www.startech.com.bd/acer-aspire-15-as15-42-r5-01c"] = startech_page("UN.35MSI.01C", 71000)
    pg["https://www.startech.com.bd/acer-aspire-15-as15-42-16gb"] = startech_page("UN.35MSI.02B", st_price)
    # Star Tech: P2
    pg["https://www.startech.com.bd/product/search?search=9S7-158K11-4A2"] = search_html(
        ("/msi-katana-15-hx-b14wgk", "MSI Katana 15 HX B14WGK Core i7 RTX 5070 Gaming Laptop"))
    pg["https://www.startech.com.bd/msi-katana-15-hx-b14wgk"] = startech_page("9S7-158K11-4A2", 235000)
    # Techland: first search URL gives nothing useful, second one works (P1 only)
    pg["https://www.techlandbd.com/index.php?route=product/search&search=UN.35MSI.02B"] = search_html()
    pg["https://www.techlandbd.com/search?search=UN.35MSI.02B"] = search_html(
        ("/acer-as15-42-ryzen-5", "Acer Aspire 15 AS15-42 Ryzen 5 7430U FHD IPS Laptop"))
    pg["https://www.techlandbd.com/acer-as15-42-ryzen-5"] = techland_page("UN.35MSI.02B", 77500)
    return pg

NOW = datetime(2026, 11, 25, 14, 0)
def make(pg):
    sess = FakeSession(pg)
    return (lambda: sess), sess

def listing_of(pg):
    return parse_listing(pg["https://www.ucc.com.bd/laptops?limit=100&page=1"], "https://www.ucc.com.bd/laptops")

# ------------------------------------------------------------------ 1. UCC list parsing
pg = world()
items, total = listing_of(pg)
assert total == 3 and len(items) == 3, (total, items)
assert items[2]["url"] == P3_CANON, items[2]          # laptops?product_id=5182 -> real product page
assert all(len(i["name"]) > 20 for i in items)
disc, tot = discover_ucc(FakeSession(pg))
assert tot == 3 and len(disc) == 3
print("1 UCC list parsing OK (3 of 3, product_id link fixed)")

# ------------------------------------------------------------------ 2. strict SKU matching
s = FakeSession(pg)
hit = find_on_site("Star Tech", {"name": P1["name"], "sku": P1["sku"]}, s)
assert hit and hit[0].endswith("as15-42-16gb") and hit[1]["price"] == 77500, hit   # wrong 01C variant rejected
assert find_on_site("Ryans", {"name": P2["name"], "sku": P2["sku"]}, s) is None
print("2 SKU match OK (wrong variant rejected, missing = None)")

# ------------------------------------------------------------------ 3. first run: everything auto-added and matched
mk, sess = make(pg)
msgs, st1, prods = run([], {}, mk, NOW, listing=listing_of(pg))
assert len(prods) == 3
by = {p["sku"]: p for p in prods}
assert set(by["UN.35MSI.02B"]["urls"]) == {"UCC", "Ryans", "Star Tech", "Techland"}
assert set(by["9S7-158K11-4A2"]["urls"]) == {"UCC", "Star Tech"}
assert set(by["NH.QSZSI.001"]["urls"]) == {"UCC"}
text = "\n".join(msgs)
assert "Monitoring started" in text and "Laptops: 3" in text and "matched on all 3 sites: 1" in text
assert "Star Tech 🔴 -" not in text.split("MSI Katana")[0]
assert "Star Tech 🟢 +1,100" in text           # Katana: Star Tech 235,000 vs UCC 233,900
print("3 first run OK\n", pack_messages(msgs)[0][:900], "\n")

# ------------------------------------------------------------------ 4. same day, nothing changed -> silence, no repeat searching
mk, sess = make(pg)
msgs, st2, prods2 = run(prods, st1, mk, NOW, listing=listing_of(pg))
assert msgs == [], msgs
assert not any("search" in c for c in sess.calls), "must not re-search the same day"
print("4 no change = silence OK")

# ------------------------------------------------------------------ 5. price change on a competitor
pg5 = world(st_price=74500)
mk, _ = make(pg5)
msgs, st3, _ = run(prods2, st2, mk, NOW, listing=listing_of(pg5))
assert len(msgs) == 1 and "Star Tech price DOWN" in msgs[0] and "৳3,000 cheaper than UCC" in msgs[0], msgs
print("5 competitor price change OK")

# ------------------------------------------------------------------ 6. UCC price change
pg6 = world(st_price=74500, ucc_price={P2["sku"]: 229900})
mk, _ = make(pg6)
msgs, st4, _ = run(prods2, st3, mk, NOW, listing=listing_of(pg6))
assert len(msgs) == 1 and "UCC price DOWN" in msgs[0] and "Katana" in msgs[0], msgs
print("6 UCC price change OK")

# ------------------------------------------------------------------ 7. next day: unmatched laptops are searched again
pg7 = world(st_price=74500, ucc_price={P2["sku"]: 229900})
pg7["https://www.ryans.com/search?q=9S7-158K11-4A2"] = search_html(
    ("/ryans-katana-b14wgk", "MSI Katana 15 HX B14WGK Intel Core i7 RTX 5070 Laptop"))
pg7["https://www.ryans.com/ryans-katana-b14wgk"] = ryans_page("9S7-158K11-4A2", 229900)
mk, sess = make(pg7)
tomorrow = NOW + timedelta(days=1)
msgs, st5, prods5 = run(prods2, st4, mk, tomorrow, listing=listing_of(pg7))
assert any("Now matched" in m and "Ryans" in m and "same as UCC" in m for m in msgs), msgs
print("7 daily retry finds a new match OK")

# ------------------------------------------------------------------ 8. brand-new laptop on UCC
P4 = dict(name="MSI Cyborg 15 B2RWEKG Intel Core 5 210H RTX 5050 16GB DDR5 Gaming Laptop",
          sku="9S7-15XXXX-1", url="https://www.ucc.com.bd/msi-cyborg-15", price=169900)
pg8 = world(listing=(P1, P2, P3, P4), st_price=74500, ucc_price={P2["sku"]: 229900})
pg8[P4["url"]] = ucc_page(P4["name"], P4["sku"], P4["price"])
mk, _ = make(pg8)
msgs, st6, prods6 = run(prods5, st5, mk, tomorrow, listing=listing_of(pg8))
assert any("🆕 New laptop on UCC" in m and "Cyborg" in m and "৳169,900" in m for m in msgs), msgs
assert len(prods6) == 4
print("8 new laptop alert OK")

# 8b. many new laptops -> one summary, not many messages
many = [dict(P1, name=f"Acer Test Laptop AB{i}-1{i}X Intel Core i5 8GB 512GB", sku=f"UN.TEST.0{i}",
             url=f"https://www.ucc.com.bd/test-{i}", price=60000 + i) for i in range(5)]
pg8b = world(listing=[P1, P2, P3] + many)
for m_ in many: pg8b[m_["url"]] = ucc_page(m_["name"], m_["sku"], m_["price"])
mk, _ = make(pg8b)
msgs, _, _ = run(prods5, st5, mk, tomorrow, listing=listing_of(pg8b))
assert sum("🆕" in m for m in msgs) == 1 and any("5 new laptops added" in m for m in msgs), msgs
print("8b many new laptops -> 1 summary OK")

# ------------------------------------------------------------------ 9. laptop removed from UCC list (needs complete list, 3 checks)
pg9 = world(listing=(P1, P2), st_price=74500, ucc_price={P2["sku"]: 229900})
st, pr = st5, prods5
for i in range(3):
    mk, _ = make(pg9)
    msgs, st, pr = run(pr, st, mk, tomorrow, listing=listing_of(pg9))
    if i < 2: assert not any("No longer" in m for m in msgs), (i, msgs)
assert any("No longer on the UCC laptop list" in m and "Predator" in m for m in msgs), msgs
print("9 removed laptop OK")

# 9b. partial list (page layout problem) -> never removes anything, warns once
pg9b = world(listing=(P1,), total=3, st_price=74500, ucc_price={P2["sku"]: 229900})
mk, _ = make(pg9b)
msgs, stw, prw = run(prods5, st5, mk, tomorrow, listing=listing_of(pg9b))
assert all(p["active"] for p in prw) and any("Read only 1 of 3" in m for m in msgs), msgs
msgs2, _, _ = run(prw, stw, mk, tomorrow, listing=listing_of(pg9b))
assert not any("Read only" in m for m in msgs2), "same warning must not repeat"
print("9b partial list safe + warning once OK")

# ------------------------------------------------------------------ 10. blocked search / blocked page
pg10 = world(); pg10["https://www.techlandbd.com/index.php?route=product/search&search=9S7-158K11-4A2"] = BLOCK
mk, _ = make(pg10)
msgs, _, _ = run([], {}, mk, NOW, listing=listing_of(pg10))
assert any("Techland blocked the search" in m for m in msgs), msgs
print("10 blocked search warning OK")

# ------------------------------------------------------------------ 11. manual product keeps working + pinned never removed
manual = [{"name": "Manual", "sku": "UN.35MSI.02B", "pinned": True, "urls": {
    "UCC": P1["url"], "Ryans": "https://www.ryans.com/acer-aspire-15-as15-42"}}]
mk, _ = make(world(listing=(P2,)))
m1, s1, p1 = run(manual, {}, mk, NOW, listing=listing_of(world(listing=(P2,))))
for _ in range(4):
    m1, s1, p1 = run(p1, s1, mk, NOW, listing=listing_of(world(listing=(P2,))))
assert next(p for p in p1 if p["name"] == "Manual")["active"]
print("11 manual/pinned OK")

# ------------------------------------------------------------------ 12. message packing
assert all(len(c) <= 3900 for c in pack_messages(["x" * 1000] * 9 + ["a\n\n" * 1500]))
print("12 packing OK")
print("ALL TESTS PASSED")

# ------------------------------------------------------------------ 13. price regex must not eat digits of an SKU
from monitor import price_from_text
assert price_from_text("Model X SKU: 9S7-158K11-4 ৳233,900 EMI ৳2,674/month", "") == 233900
assert price_from_text("SKU: UN.35MSI.02 ৳77,500", "") == 77500
assert price_from_text("Price 77,500৳ Regular Price 83,050৳", "") == 77500
assert price_from_text("Discount Price ৳ 57,900 ৳ 64,900", "") == 57900
assert price_from_text("৳83,000 ৳77,500", "") == 77500
print("13 price regex OK")
