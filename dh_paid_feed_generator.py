#!/usr/bin/env python3
import re
import datetime
import asyncio
import aiohttp
import feedparser
from bs4 import BeautifulSoup
import PyRSS2Gen
import xml.dom.minidom
from xml.sax.saxutils import escape
from urllib.parse import quote

from dh_mappings import (
    TRANSLATOR_NOVEL_MAP,
    NOVEL_URL_OVERRIDES,
    get_featured_image,
    get_translator,
    get_discord_role_id,
    get_nsfw_novels
)

semaphore = asyncio.Semaphore(100)

def get_novel_url(title: str) -> str:
    """
    Returns the main page URL for the given novel title.
    Uses NOVEL_URL_OVERRIDES if present, otherwise
    slugs via our unified slug() (keeps ☆④, collapses ASCII punctuation).
    """
    override = NOVEL_URL_OVERRIDES.get(title)
    if override:
        return override
    return f"https://dragonholic.com/novel/{slug(title)}/"

def slug(text: str) -> str:
    # 1) lowercase + trim
    s = text.lower().strip()

    # 2) remove ASCII punctuation but keep non‑ASCII, word chars, whitespace & hyphens
    s = re.sub(r"[^\w\s\u0080-\uFFFF-]", "", s)

    # 3) collapse whitespace/underscores into single hyphens
    s = re.sub(r"[\s_]+", "-", s)

    # 4) collapse any multiple hyphens into one
    s = re.sub(r"-{2,}", "-", s)

    return s

def split_title(full_title: str):
    parts = full_title.split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return full_title.strip(), ""

async def fetch_page(session, url: str) -> str:
    """Fetch a page; on non-200 or exception, log & return empty string."""
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                print(f"⚠️  Warning: {url} returned HTTP {resp.status}")
                return ""
            return await resp.text()
    except Exception as e:
        print(f"⚠️  Error fetching {url}: {e}")
        return ""

def clean_description(raw_desc: str) -> str:
    soup = BeautifulSoup(raw_desc, "html.parser")
    for div in soup.select("div.c-content-readmore"):
        div.decompose()
    cleaned = soup.decode_contents()
    return re.sub(r'\s+', ' ', cleaned).strip()

def extract_pubdate_from_soup(chap) -> datetime.datetime:
    span = chap.select_one("span.chapter-release-date i")
    if not span:
        return datetime.datetime.now(datetime.timezone.utc)
    date_str = span.get_text(strip=True)
    try:
        # absolute date
        return datetime.datetime.strptime(date_str, "%B %d, %Y")\
                .replace(tzinfo=datetime.timezone.utc)
    except:
        # relative
        now = datetime.datetime.now(datetime.timezone.utc)
        parts = date_str.lower().split()
        if parts and parts[0].isdigit():
            num = int(parts[0]); unit = parts[1]
            if "minute" in unit: return now - datetime.timedelta(minutes=num)
            if "hour"   in unit: return now - datetime.timedelta(hours=num)
            if "day"    in unit: return now - datetime.timedelta(days=num)
            if "week"   in unit: return now - datetime.timedelta(weeks=num)
    return now

def chapter_num(chaptername: str):
    nums = re.findall(r'\d+(?:\.\d+)?', chaptername)
    return tuple(float(n) if '.' in n else int(n) for n in nums) if nums else (0,)

def normalize_date(dt: datetime.datetime) -> datetime.datetime:
    return dt.replace(microsecond=0)

async def scrape_paid_chapters_async(session, base_url: str):
    """
    Fetch & parse the paid chapters from a novel page.
    Returns (list_of_dicts, main_description).
    """
    html = await fetch_page(session, base_url)
    if not html:
        return [], ""
    soup = BeautifulSoup(html, "html.parser")

    # description
    desc_div = soup.select_one("div.description-summary")
    main_desc = clean_description(desc_div.decode_contents()) if desc_div else ""

    paid = []
    now = datetime.datetime.now(datetime.timezone.utc)

    # volume‐based
    vol_ul = soup.select_one("ul.main.version-chap.volumns")
    if vol_ul:
        for vol_parent in vol_ul.select("li.parent.has-child"):
            # full dropdown text, e.g. "1 - Chalize is Dead"
            vol_label    = vol_parent.select_one("a.has-child").get_text(strip=True)
            vol_display  = vol_label
            # numeric part for URL fallback
            vol_id       = vol_label.split(" - ",1)[0].strip()

            for chap_li in vol_parent.select("ul.sub-chap-list li.wp-manga-chapter"):
                if "free-chap" in chap_li.get("class", []):
                    continue
                pub_dt = extract_pubdate_from_soup(chap_li)
                if pub_dt < now - datetime.timedelta(days=7):
                    continue

                a = chap_li.find("a")
                # simple split on the first " - " in the link text:
                raw_html = a.decode_contents()
                
                # 1) chaptername is everything before the first tag (<)
                m1 = re.match(r'\s*([^<]+)', raw_html)
                chap_name = m1.group(1).strip() if m1 else raw_html.strip()
                
                # 2) nameextend is whatever follows </i> – …  (drop only that first dash)
                m2 = re.search(r'</i>\s*[-–]\s*(.+)', raw_html)
                nameext = m2.group(1).strip() if m2 else ""
                num_m = re.search(r"(\d+(?:\.\d+)?)", chap_name)
                chap_id = num_m.group(1) if num_m else ""
                href = a.get("href","").strip()
                if href and href != "#":
                    link = href
                else:
                    # vol_display is e.g. "1 - Chalize is Dead", chap_name is e.g. "9 - The Puppet King, Henry"
                    link = f"{base_url}{slug(vol_display)}/{slug(chap_name)}/"

                guid = next((c.split("data-chapter-")[1]
                             for c in chap_li.get("class",[])
                             if c.startswith("data-chapter-")), chap_id)
                coin = chap_li.select_one("span.coin").get_text(strip=True) \
                       if chap_li.select_one("span.coin") else ""

                paid.append({
                    "volume":      vol_display,
                    "chaptername": chap_name,
                    "nameextend":  nameext,
                    "link":        link,
                    "description": main_desc,
                    "pubDate":     pub_dt,
                    "guid":        guid,
                    "coin":        coin
                })

    # no‐volume
    no_vol_ul = soup.select_one("ul.main.version-chap.no-volumn")
    if no_vol_ul:
        for chap_li in no_vol_ul.select("li.wp-manga-chapter"):
            if "free-chap" in chap_li.get("class", []):
                continue
            pub_dt = extract_pubdate_from_soup(chap_li)
            if pub_dt < now - datetime.timedelta(days=7):
                continue

            a = chap_li.find("a")
            # simple split on the first " - " in the link text:
            raw_html = a.decode_contents()
            
            # 1) chaptername is everything before the first tag (<)
            m1 = re.match(r'\s*([^<]+)', raw_html)
            chap_name = m1.group(1).strip() if m1 else raw_html.strip()
            
            # 2) nameextend is whatever follows </i> – …  (drop only that first dash)
            m2 = re.search(r'</i>\s*[-–]\s*(.+)', raw_html)
            nameext = m2.group(1).strip() if m2 else ""
            num_m = re.search(r"(\d+(?:\.\d+)?)", chap_name)
            chap_id = num_m.group(1) if num_m else ""
            href = a.get("href","").strip()
            if href and href != "#":
                link = href
            else:
                link = f"{base_url}{slug(chap_name)}/"

            guid = next((c.split("data-chapter-")[1]
                         for c in chap_li.get("class",[])
                         if c.startswith("data-chapter-")), chap_id)
            coin = chap_li.select_one("span.coin").get_text(strip=True) \
                   if chap_li.select_one("span.coin") else ""

            paid.append({
                "volume":      "",
                "chaptername": chap_name,
                "nameextend":  nameext,
                "link":        link,
                "description": main_desc,
                "pubDate":     pub_dt,
                "guid":        guid,
                "coin":        coin
            })

    return paid, main_desc

class MyRSSItem(PyRSS2Gen.RSSItem):
    def __init__(self, *args, volume="", chaptername="", nameextend="", coin="", **kwargs):
        self.volume      = volume
        self.chaptername = chaptername
        self.nameextend  = nameextend
        self.coin        = coin
        super().__init__(*args, **kwargs)

    def writexml(self, writer, indent="", addindent="", newl=""):
        w = writer.write
        w(f"{indent}  <item>{newl}")
        w(f"{indent}    <title>{escape(self.title)}</title>{newl}")
        w(f"{indent}    <volume>{escape(self.volume)}</volume>{newl}")
        w(f"{indent}    <chaptername>{escape(self.chaptername)}</chaptername>{newl}")
        ext = f"***{self.nameextend}***" if self.nameextend.strip() else ""
        w(f"{indent}    <nameextend>{escape(ext)}</nameextend>{newl}")
        w(f"{indent}    <link>{escape(self.link)}</link>{newl}")
        w(f"{indent}    <description><![CDATA[{self.description}]]></description>{newl}")
        cat = "NSFW" if self.title in get_nsfw_novels() else "SFW"
        w(f"{indent}    <category>{escape(cat)}</category>{newl}")
        trans = get_translator(self.title) or ""
        w(f"{indent}    <translator>{escape(trans)}</translator>{newl}")
        role = get_discord_role_id(trans)
        if cat=="NSFW": role += " <@&1304077473998442506>"
        w(f"{indent}    <discord_role_id><![CDATA[{role}]]></discord_role_id>{newl}")
        w(f"{indent}    <featuredImage url=\"{escape(get_featured_image(self.title))}\"/>{newl}")
        if self.coin:
            w(f"{indent}    <coin>{escape(self.coin)}</coin>{newl}")
        w(f"{indent}    <pubDate>{self.pubDate.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>{newl}")
        w(f"{indent}    <guid isPermaLink=\"false\">{escape(self.guid.guid)}</guid>{newl}")
        w(f"{indent}  </item>{newl}")

class CustomRSS2(PyRSS2Gen.RSS2):
    def writexml(self, writer, indent="", addindent="", newl=""):
        w = writer.write
        w(f'<?xml version="1.0" encoding="utf-8"?>{newl}')
        w('<rss xmlns:content="http://purl.org/rss/1.0/modules/content/" '
          'xmlns:wfw="http://wellformedweb.org/CommentAPI/" '
          'xmlns:dc="http://purl.org/dc/elements/1.1/" '
          'xmlns:atom="http://www.w3.org/2005/Atom" '
          'xmlns:sy="http://purl.org/rss/1.0/modules/syndication/" '
          'xmlns:slash="http://purl.org/rss/1.0/modules/slash/" '
          'xmlns:webfeeds="http://www.webfeeds.org/rss/1.0" '
          'xmlns:georss="http://www.georss.org/georss" '
          'xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#" '
          'version="2.0">' + newl)
        w(indent + "<channel>" + newl)
        for tag in ("title","link","description","language","lastBuildDate","docs","generator","ttl"):
            val = getattr(self, tag, None)
            if val:
                if tag=="lastBuildDate":
                    val = val.strftime("%a, %d %b %Y %H:%M:%S +0000")
                w(f"{indent}{addindent}<{tag}>{escape(str(val))}</{tag}>{newl}")
        for item in self.items:
            item.writexml(writer, indent+addindent, addindent, newl)
        w(indent + "</channel>" + newl)
        w("</rss>" + newl)

async def process_novel(session, title: str):
    try:
        override = NOVEL_URL_OVERRIDES.get(title)
        base    = get_novel_url(title)
        to_try  = [base]

        base_url = None
        for url in to_try:
            html = await fetch_page(session, url)
            if html:
                base_url = url
                break
        if not base_url:
            print(f"❌  Could not fetch ANY page for '{title}', skipping.")
            return []

        paid_list, _ = await scrape_paid_chapters_async(session, base_url)
        if not paid_list:
            return []

        chapters, _ = await scrape_paid_chapters_async(session, base_url)
        items = []
        for chap in chapters:
            pd = chap["pubDate"]
            if pd.tzinfo is None:
                pd = pd.replace(tzinfo=datetime.timezone.utc)
            if pd.minute >= 30:
                pd += datetime.timedelta(hours=1)
            pd = pd.replace(minute=0, second=0, microsecond=0)

            items.append(MyRSSItem(
                title=title,
                volume=chap["volume"],
                chaptername=chap["chaptername"],
                nameextend=chap["nameextend"],
                link=chap["link"],
                description=chap["description"],
                guid=PyRSS2Gen.Guid(chap["guid"], isPermaLink=False),
                pubDate=pd,
                coin=chap.get("coin","")
            ))
        return items

    except Exception as e:
        # catch anything unexpected, log it, and keep going
        print(f"❌ Error processing {title}: {e}")
        return []

async def main_async():
    all_items = []
    async with aiohttp.ClientSession() as session:
        tasks = [process_novel(session, t) for novels in TRANSLATOR_NOVEL_MAP.values() for t in novels]
        for result in await asyncio.gather(*tasks):
            all_items.extend(result)

    # sort descending
    all_items.sort(key=lambda it:(normalize_date(it.pubDate), chapter_num(it.chaptername)), reverse=True)

    feed = CustomRSS2(
        title="Dragonholic Paid Chapters",
        link="https://dragonholic.com",
        description="Aggregated RSS feed for paid chapters across mapped novels.",
        lastBuildDate=datetime.datetime.now(datetime.timezone.utc),
        items=all_items
    )
    # write & prettify
    xml_path = "dh_paid_feed.xml"
    with open(xml_path, "w", encoding="utf-8") as f:
        feed.writexml(f, indent="  ", addindent="  ", newl="\n")
    pretty = xml.dom.minidom.parseString(open(xml_path, "r", encoding="utf-8").read())\
              .toprettyxml(indent="  ")
    with open(xml_path, "w", encoding="utf-8") as f:
        # strip blank lines
        f.write("\n".join(l for l in pretty.splitlines() if l.strip()))
        
    # ---------------------------------------------------
    # sanity‑check: make sure every mapped novel actually appeared

    with open(xml_path, "r", encoding="utf-8") as f:
        feed = feedparser.parse(f.read())
    titles_in_feed = {entry.title for entry in feed.entries}

    for translator, novels in TRANSLATOR_NOVEL_MAP.items():
        for novel in novels:
            if novel not in titles_in_feed:
                print(f"❌ No feed entries for: {novel}")
    # ---------------------------------------------------
    
    print(f"✅  Feed generated with {len(all_items)} items.")

if __name__ == "__main__":
    asyncio.run(main_async())
