"""
Scrapes Business-class prices for TLV -> exotic long-haul destinations.
Searches each destination individually on Google Flights to get the
cheapest available price over the next ~3 months.
"""

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright, Page

from storage import init_db, save_flights

ORIGIN_CODE = "TLV"

# Curated exotic long-haul destinations from TLV
DESTINATIONS = [
    ("New York",        "JFK",  "USA"),
    ("Los Angeles",     "LAX",  "USA"),
    ("Miami",           "MIA",  "USA"),
    ("Chicago",         "ORD",  "USA"),
    ("Toronto",         "YYZ",  "Canada"),
    ("Bangkok",         "BKK",  "Thailand"),
    ("Phuket",          "HKT",  "Thailand"),
    ("Tokyo",           "NRT",  "Japan"),
    ("Singapore",       "SIN",  "Singapore"),
    ("Hong Kong",       "HKG",  "Hong Kong"),
    ("Seoul",           "ICN",  "South Korea"),
    ("Bali",            "DPS",  "Indonesia"),
    ("Mumbai",          "BOM",  "India"),
    ("Delhi",           "DEL",  "India"),
    ("Sydney",          "SYD",  "Australia"),
    ("Melbourne",       "MEL",  "Australia"),
    ("Cape Town",       "CPT",  "South Africa"),
    ("Johannesburg",    "JNB",  "South Africa"),
    ("Maldives",        "MLE",  "Maldives"),
    ("Nairobi",         "NBO",  "Kenya"),
    ("Sao Paulo",       "GRU",  "Brazil"),
    ("Buenos Aires",    "EZE",  "Argentina"),
    ("Shanghai",        "PVG",  "China"),
    ("Beijing",         "PEK",  "China"),
    ("Kuala Lumpur",    "KUL",  "Malaysia"),
]

SEARCH_BASE = "https://www.google.com/travel/flights?hl=en&curr=USD"


async def dismiss_overlays(page: Page):
    for text in ["Accept all", "I agree", "Accept"]:
        try:
            btn = page.locator(f"button:has-text('{text}')").first
            if await btn.is_visible(timeout=1000):
                await btn.click()
                await asyncio.sleep(0.5)
                return
        except Exception:
            pass


async def set_business_class(page: Page) -> bool:
    economy = page.locator(':text-is("Economy")').first
    try:
        await economy.click(timeout=4_000, force=True)
    except Exception:
        try:
            box = await economy.bounding_box(timeout=2_000)
            if box:
                await page.mouse.click(
                    box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2,
                )
            else:
                return False
        except Exception:
            return False

    await asyncio.sleep(1.2)
    business = page.locator(':text-is("Business")').first
    try:
        await business.click(timeout=3_000, force=True)
        await asyncio.sleep(0.5)
        return True
    except Exception:
        return False


async def extract_cheapest_price(page: Page) -> float | None:
    """Pull the lowest $-price visible on the current search results page."""
    prices = await page.evaluate(r"""
        () => {
            const rx = /\$([\d,]+)/;
            const found = [];
            document.querySelectorAll('span, div').forEach(el => {
                if (el.children.length > 0) return;  // leaf nodes only
                const m = el.textContent.match(rx);
                if (!m) return;
                const v = parseFloat(m[1].replace(/,/g, ''));
                if (v >= 500 && v <= 30000) found.push(v);
            });
            return found.sort((a, b) => a - b);
        }
    """)
    return prices[0] if prices else None


async def search_route(
    page: Page,
    dest_city: str,
    dest_iata: str,
    dest_country: str,
    data_dir: Path,
) -> dict | None:
    query = dest_city.lower().replace(" ", "+")
    url = f"{SEARCH_BASE}&q=flights+from+tel+aviv+to+{query}"

    print(f"  Searching TLV -> {dest_city} ({dest_iata})...")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)

        # Close any consent / sign-in overlays
        await dismiss_overlays(page)
        for _ in range(2):
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.2)

        # Switch to Business class (only needed once, but Google sometimes resets it)
        await set_business_class(page)
        await asyncio.sleep(3)

        price = await extract_cheapest_price(page)

        if price:
            print(f"    -> ${price:,.0f}")
            return {
                "destination": dest_city,
                "country": dest_country,
                "iata_code": dest_iata,
                "price_usd": price,
                "cabin": "Business",
                "origin": ORIGIN_CODE,
            }
        else:
            print(f"    -> no price found")
            slug = dest_iata.lower()
            await page.screenshot(path=str(data_dir / f"debug_{slug}.png"))
            return None

    except Exception as exc:
        print(f"    -> error: {exc}")
        return None


async def scrape() -> list[dict]:
    init_db()
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        headless = os.environ.get("CI", "") == "true"
        browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="Asia/Jerusalem",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()

        flights = []

        for dest_city, dest_iata, dest_country in DESTINATIONS:
            result = await search_route(page, dest_city, dest_iata, dest_country, data_dir)
            if result:
                flights.append(result)
            # Small pause between searches to avoid rate limiting
            await asyncio.sleep(2)

        await browser.close()

    if flights:
        # Flag deals: 30% below the average price across all results
        prices = [f["price_usd"] for f in flights]
        avg = sum(prices) / len(prices)
        threshold = avg * 0.70
        for f in flights:
            f["is_deal"] = f["price_usd"] <= threshold
            f["avg_price_usd"] = round(avg, 2)

        saved = save_flights(flights)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        (data_dir / "flights.json").write_text(
            json.dumps(flights, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Append to history for trend tracking
        history_path = data_dir / "history.json"
        history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
        # Remove any existing entries for today to avoid duplicates on re-run
        history = [h for h in history if h.get("date") != today]
        for f in flights:
            history.append({
                "destination": f["destination"],
                "country": f["country"],
                "price_usd": f["price_usd"],
                "date": today,
            })
        history_path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nSaved {saved} flights to DB + flights.json + history.json")

    return flights


if __name__ == "__main__":
    print(f"Searching Business class prices for {len(DESTINATIONS)} exotic destinations from TLV\n")
    results = asyncio.run(scrape())

    print("\n--- Results ---")
    if results:
        avg = results[0].get("avg_price_usd", 0)
        print(f"Average price: ${avg:,.0f}  |  Deal threshold: ${avg * 0.7:,.0f}\n")
        for r in sorted(results, key=lambda x: x["price_usd"]):
            flag = "  *** DEAL ***" if r.get("is_deal") else ""
            print(f"  {r['destination']:<20} {r['country']:<15}  ${r['price_usd']:>6,.0f}{flag}")
    else:
        print("No results. Check data/debug_*.png screenshots.")
