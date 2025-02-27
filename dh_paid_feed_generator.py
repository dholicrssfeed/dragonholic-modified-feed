import re
import datetime
import asyncio
import aiohttp
from bs4 import BeautifulSoup
import PyRSS2Gen
import xml.dom.minidom
from xml.sax.saxutils import escape
from collections import defaultdict

# Import mapping functions and data from your mappings file (dh_mappings.py)
from dh_mappings import (
    TRANSLATOR_NOVEL_MAP,
    get_novel_url,
    get_featured_image,
    get_translator,
    get_discord_role_id,
    get_nsfw_novels
)

# Set up a semaphore to limit concurrency to 100 tasks.
semaphore = asyncio.Semaphore(100)

# ---------------- Helper Functions (Synchronous) ----------------

def clean_description(raw_desc):
    """Cleans the raw HTML description by removing extra whitespace."""
    soup = BeautifulSoup(raw_desc, "html.parser")
    for div in soup.find_all("div", class_="c-content-readmore"):
        div.decompose()
    cleaned = soup.decode_contents()
    return re.sub(r'\s+', ' ', cleaned).strip()

def split_title(full_title):
    """Splits a chapter title into chapter number and extra text."""
    parts = full_title.split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return full_title.strip(), ""

def extract_pubdate_from_soup(chap):
    """
    Extracts the publication date from a chapter element.
    Tries an absolute date (e.g. "February 16, 2025") or a relative date.
    """
    release_span = chap.find("span", class_="chapter-release-date")
    if release_span:
        i_tag = release_span.find("i")
        if i_tag:
            date_str = i_tag.get_text(strip=True)
            try:
                pub_dt = datetime.datetime.strptime(date_str, "%B %d, %Y")
                return pub_dt.replace(tzinfo=datetime.timezone.utc)
            except Exception:
                if "ago" in date_str.lower():
                    now = datetime.datetime.now(datetime.timezone.utc)
                    parts = date_str.lower().split()
                    try:
                        number = int(parts[0])
                        unit = parts[1]
                        if "minute" in unit:
                            return now - datetime.timedelta(minutes=number)
                        elif "hour" in unit:
                            return now - datetime.timedelta(hours=number)
                        elif "day" in unit:
                            return now - datetime.timedelta(days=number)
                        elif "week" in unit:
                            return now - datetime.timedelta(weeks=number)
                    except Exception as e:
                        print(f"Error parsing relative date '{date_str}': {e}")
    return datetime.datetime.now(datetime.timezone.utc)

import re

def chapter_num(chaptername):
    """
    Extracts all numeric sequences from the chaptername and returns them as a tuple.
    Each number is converted to an int (or float if a decimal is present).
    Any non-numeric words are ignored.
    
    Examples:
      "Volume 1 Chapter 15" -> (1, 15)
      "Volume 2 Chapter 1"  -> (2, 1)
      "Episode 2"           -> (2,)
      "1.1"                 -> (1.1,)
    """
    numbers = re.findall(r'\d+(?:\.\d+)?', chaptername)
    if not numbers:
        return (0,)
    return tuple(float(n) if '.' in n else int(n) for n in numbers)

def normalize_date(dt):
    """Normalizes a datetime by removing microseconds."""
    return dt.replace(microsecond=0)

# ---------------- Asynchronous Fetch Functions ----------------

async def fetch_page(session, url):
    """Fetches a URL using aiohttp and returns the response text."""
    async with session.get(url) as response:
        return await response.text()

async def novel_has_paid_update_async(session, novel_url):
    """
    Quickly checks if the novel page has a recent premium (paid/locked) update.
    Loads the page, finds the first chapter element, and if it has the 'premium'
    class (and not 'free-chap') with a release date within the last 7 days, returns True.
    """
    try:
        html = await fetch_page(session, novel_url)
    except Exception as e:
        print(f"Error fetching {novel_url} for quick check: {e}")
        return False

    soup = BeautifulSoup(html, "html.parser")
    chapter_li = soup.find("li", class_="wp-manga-chapter")
    if chapter_li:
        classes = chapter_li.get("class", [])
        if "premium" in classes and "free-chap" not in classes:
            pub_span = chapter_li.find("span", class_="chapter-release-date")
            if pub_span:
                i_tag = pub_span.find("i")
                if i_tag:
                    date_str = i_tag.get_text(strip=True)
                    try:
                        pub_dt = datetime.datetime.strptime(date_str, "%B %d, %Y").replace(tzinfo=datetime.timezone.utc)
                    except Exception:
                        pub_dt = datetime.datetime.now(datetime.timezone.utc)
                    now = datetime.datetime.now(datetime.timezone.utc)
                    if pub_dt >= now - datetime.timedelta(days=7):
                        return True
            else:
                return True
    return False

async def scrape_paid_chapters_async(session, novel_url):
    """
    Asynchronously fetches the novel page and extracts:
      - The main description.
      - Paid chapters (excluding free chapters) from <li class="wp-manga-chapter"> elements.
    Stops processing once a chapter older than 7 days is encountered.
    Returns a tuple: (list_of_chapters, main_description)
    """
    try:
        html = await fetch_page(session, novel_url)
    except Exception as e:
        print(f"Error fetching {novel_url}: {e}")
        return [], ""
    
    soup = BeautifulSoup(html, "html.parser")
    desc_div = soup.find("div", class_="description-summary")
    if desc_div:
        main_desc = clean_description(desc_div.decode_contents())
        print("Main description fetched.")
    else:
        main_desc = ""
        print("No main description found.")
    
    chapters = soup.find_all("li", class_="wp-manga-chapter")
    paid_chapters = []
    now = datetime.datetime.now(datetime.timezone.utc)
    print(f"Found {len(chapters)} chapter elements on {novel_url}")
    for chap in chapters:
        # Skip free chapters.
        if "free-chap" in chap.get("class", []):
            continue
        pub_dt = extract_pubdate_from_soup(chap)
        if pub_dt < now - datetime.timedelta(days=7):
            break  # Assumes chapters are sorted newest first.
        a_tag = chap.find("a")
        if not a_tag:
            continue
        raw_title = a_tag.get_text(" ", strip=True)
        print(f"Processing chapter: {raw_title}")
        chap_number, chap_title = split_title(raw_title)
        href = a_tag.get("href")
        if href and href.strip() != "#":
            chapter_link = href.strip()
        else:
            parts = chap_number.split()
            chapter_num_str = parts[-1] if parts else "unknown"
            chapter_link = f"{novel_url}chapter-{chapter_num_str}/"
        guid = None
        for cls in chap.get("class", []):
            if cls.startswith("data-chapter-"):
                guid = cls.replace("data-chapter-", "")
                break
        if not guid:
            parts = chap_number.split()
            guid = parts[-1] if parts else "unknown"
        coin_span = chap.find("span", class_="coin")
        coin_value = coin_span.get_text(strip=True) if coin_span else ""
        paid_chapters.append({
            "chaptername": chap_number,
            "nameextend": chap_title,
            "link": chapter_link,
            "description": main_desc,
            "pubDate": pub_dt,
            "guid": guid,
            "coin": coin_value
        })
    print(f"Total paid chapters processed from {novel_url}: {len(paid_chapters)}")
    return paid_chapters, main_desc

# ---------------- RSS Generation Classes (Synchronous) ----------------

class MyRSSItem(PyRSS2Gen.RSSItem):
    def __init__(self, *args, chaptername="", nameextend="", coin="", **kwargs):
        self.chaptername = chaptername
        self.nameextend = nameextend
        self.coin = coin
        super().__init__(*args, **kwargs)
    
    def writexml(self, writer, indent="", addindent="", newl=""):
        writer.write(indent + "  <item>" + newl)
        writer.write(indent + "    <title>%s</title>" % escape(self.title) + newl)
        writer.write(indent + "    <chaptername>%s</chaptername>" % escape(self.chaptername) + newl)
        formatted_nameextend = f"***{self.nameextend}***" if self.nameextend.strip() else ""
        writer.write(indent + "    <nameextend>%s</nameextend>" % escape(formatted_nameextend) + newl)
        writer.write(indent + "    <link>%s</link>" % escape(self.link) + newl)
        writer.write(indent + "    <description><![CDATA[%s]]></description>" % self.description + newl)
        nsfw_list = get_nsfw_novels()
        category_value = "NSFW" if self.title in nsfw_list else "SFW"
        writer.write(indent + "    <category>%s</category>" % escape(category_value) + newl)
        translator = get_translator(self.title)
        writer.write(indent + "    <translator>%s</translator>" % (translator if translator else "") + newl)
        discord_role = get_discord_role_id(translator)
        if category_value == "NSFW":
            discord_role += " <@&1304077473998442506>"
        writer.write(indent + "    <discord_role_id><![CDATA[%s]]></discord_role_id>" % discord_role + newl)
        writer.write(indent + '    <featuredImage url="%s"/>' % escape(get_featured_image(self.title)) + newl)
        if self.coin:
            writer.write(indent + "    <coin>%s</coin>" % escape(self.coin) + newl)
        writer.write(indent + "    <pubDate>%s</pubDate>" % self.pubDate.strftime("%a, %d %b %Y %H:%M:%S +0000") + newl)
        writer.write(indent + "    <guid isPermaLink=\"%s\">%s</guid>" % (str(self.guid.isPermaLink).lower(), self.guid.guid) + newl)
        writer.write(indent + "  </item>" + newl)

class CustomRSS2(PyRSS2Gen.RSS2):
    def writexml(self, writer, indent="", addindent="", newl=""):
        writer.write('<?xml version="1.0" encoding="utf-8"?>' + newl)
        writer.write(
            '<rss xmlns:content="http://purl.org/rss/1.0/modules/content/" '
            'xmlns:wfw="http://wellformedweb.org/CommentAPI/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:atom="http://www.w3.org/2005/Atom" '
            'xmlns:sy="http://purl.org/rss/1.0/modules/syndication/" '
            'xmlns:slash="http://purl.org/rss/1.0/modules/slash/" '
            'xmlns:webfeeds="http://www.webfeeds.org/rss/1.0" '
            'xmlns:georss="http://www.georss.org/georss" '
            'xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#" '
            'version="2.0">' + newl
        )
        writer.write(indent + "<channel>" + newl)
        writer.write(indent + addindent + "<title>%s</title>" % escape(self.title) + newl)
        writer.write(indent + addindent + "<link>%s</link>" % escape(self.link) + newl)
        writer.write(indent + addindent + "<description>%s</description>" % escape(self.description) + newl)
        if hasattr(self, 'language') and self.language:
            writer.write(indent + addindent + "<language>%s</language>" % escape(self.language) + newl)
        if hasattr(self, 'lastBuildDate') and self.lastBuildDate:
            writer.write(indent + addindent + "<lastBuildDate>%s</lastBuildDate>" %
                         self.lastBuildDate.strftime("%a, %d %b %Y %H:%M:%S +0000") + newl)
        if hasattr(self, 'docs') and self.docs:
            writer.write(indent + addindent + "<docs>%s</docs>" % escape(self.docs) + newl)
        if hasattr(self, 'generator') and self.generator:
            writer.write(indent + addindent + "<generator>%s</generator>" % escape(self.generator) + newl)
        if hasattr(self, 'ttl') and self.ttl is not None:
            writer.write(indent + addindent + "<ttl>%s</ttl>" % escape(str(self.ttl)) + newl)
        for item in self.items:
            item.writexml(writer, indent + addindent, addindent, newl)
        writer.write(indent + "</channel>" + newl)
        writer.write("</rss>" + newl)

# ---------------- Main Asynchronous Function ----------------

async def process_novel(session, novel_title):
    """Processes a single novel under the semaphore limit."""
    async with semaphore:  # Limit concurrent processing to 100 novels.
        title = novel_title  # title is a string
        novel_url = get_novel_url(title)
        print(f"Scraping: {novel_url}")
        if not await novel_has_paid_update_async(session, novel_url):
            print(f"Skipping {title}: no recent premium update found.")
            return []
        paid_chapters, main_desc = await scrape_paid_chapters_async(session, novel_url)
        items = []
        if paid_chapters:
            for chap in paid_chapters:
                pub_date = chap["pubDate"]
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=datetime.timezone.utc)
                # Create the RSS item using the exact pubDate.
                item = MyRSSItem(
                    title=title,
                    link=chap["link"],
                    description=chap["description"],
                    guid=PyRSS2Gen.Guid(chap["guid"], isPermaLink=False),
                    pubDate=pub_date,
                    chaptername=chap["chaptername"],
                    nameextend=chap["nameextend"],
                    coin=chap.get("coin", "")
                )
                items.append(item)
        return items

async def main_async():
    rss_items = []
    async with aiohttp.ClientSession() as session:
        tasks = []
        # Create a task for each novel.
        for translator, novel_titles in TRANSLATOR_NOVEL_MAP.items():
            for novel_title in novel_titles:
                tasks.append(asyncio.create_task(process_novel(session, novel_title)))
        # Wait for all novel tasks to complete.
        results = await asyncio.gather(*tasks)
        for items in results:
            rss_items.extend(items)
    
    # Sort by (normalized pubDate, then chapter number) in descending order.
    rss_items.sort(key=lambda item: (normalize_date(item.pubDate), chapter_num(item.chaptername)), reverse=True)
    
    # Debug: print chapter numbers and pubDates for verification.
    for item in rss_items:
        print(f"{item.title} - {item.chaptername} ({chapter_num(item.chaptername)}) : {item.pubDate}")
    
    new_feed = CustomRSS2(
        title="Dragonholic Paid Chapters",
        link="https://dragonholic.com",
        description="Aggregated RSS feed for paid chapters across mapped novels.",
        lastBuildDate=datetime.datetime.now(datetime.timezone.utc),
        items=rss_items
    )
    
    output_file = "dh_paid_feed.xml"
    with open(output_file, "w", encoding="utf-8") as f:
        new_feed.writexml(f, indent="  ", addindent="  ", newl="\n")
    
    with open(output_file, "r", encoding="utf-8") as f:
        xml_content = f.read()
    dom = xml.dom.minidom.parseString(xml_content)
    pretty_xml = "\n".join([line for line in dom.toprettyxml(indent="  ").splitlines() if line.strip()])
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(pretty_xml)
    
    # Debug: print chapter numbers and pubDates again after feed generation.
    for item in rss_items:
        print(f"{item.title} - {item.chaptername} ({chapter_num(item.chaptername)}) : {item.pubDate}")
    
    print(f"Modified feed generated with {len(rss_items)} items.")
    print(f"Output written to {output_file}")

if __name__ == "__main__":
    asyncio.run(main_async())
