#!/usr/bin/env python3
import re
import asyncio
import aiohttp
from datetime import datetime, timezone
from bs4 import BeautifulSoup

# import your mappings & utils
from dh_mappings import TRANSLATOR_NOVEL_MAP, NOVEL_URL_OVERRIDES
from dh_paid_feed_generator import slugify_title, extract_pubdate_from_soup

async def fetch_page(session, url):
    async with session.get(url) as r:
        return await r.text() if r.status == 200 else ""

async def scrape_all_paid(session, base_url):
    """Grab _all_ paid <li> entries, regardless of date."""
    html = await fetch_page(session, base_url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    paid = []

    def collect(chap_li, vol_label=""):
        pub_dt = extract_pubdate_from_soup(chap_li)
        a = chap_li.find("a")
        title = a.get_text(" ", strip=True)
        paid.append((title, pub_dt))

    # volume‐based
    for vol in soup.select("ul.main.version-chap.volumns li.parent.has-child"):
        vol_label = vol.select_one("a.has-child").get_text(strip=True)
        for chap_li in vol.select("ul.sub-chap-list li.wp-manga-chapter"):
            if "free-chap" not in chap_li.get("class", []):
                collect(chap_li, vol_label)

    # no‐volume
    for chap_li in soup.select("ul.main.version-chap.no-volumn li.wp-manga-chapter"):
        if "free-chap" not in chap_li.get("class", []):
            collect(chap_li)

    return paid

async def check_all():
    async with aiohttp.ClientSession() as session:
        for translator, novels in TRANSLATOR_NOVEL_MAP.items():
            for novel in novels:
                # build URL override → slug
                url = NOVEL_URL_OVERRIDES.get(novel) or slugify_title(novel)
                chaps = await scrape_all_paid(session, url)
                if not chaps:
                    print(f"❌  {novel!r}: page found but no paid‑chapters at all")
                else:
                    dates = [dt for _, dt in chaps]
                    latest = max(dates).astimezone(timezone.utc).strftime("%Y‑%m‑%d")
                    print(f"✅  {novel!r}: {len(chaps)} total paid chapters, latest on {latest}")

if __name__ == "__main__":
    asyncio.run(check_all())
