import re
import datetime
import asyncio
import aiohttp
from bs4 import BeautifulSoup
import PyRSS2Gen
import xml.dom.minidom
from xml.sax.saxutils import escape
from collections import defaultdict
from urllib.parse import urlparse

# ---------------- Mappings & Data (from your dh_mappings.py) ----------------
# Replace these with your actual imports.
from dh_mappings import (
    TRANSLATOR_NOVEL_MAP,
    get_novel_url,
    get_featured_image,
    get_translator,
    get_discord_role_id,
    get_nsfw_novels
)

# ---------------- Concurrency Setup ----------------
semaphore = asyncio.Semaphore(100)

# ---------------- Helper Functions (Synchronous) ----------------

def split_paid_chapter_dragonholic(raw_title):
    """
    Handles raw paid feed titles like:
      "Chapter 640 <i class=\"fas fa-lock\"></i> - The Abandoned Supporting Female Role 027"
    Steps:
      1) Remove any <i ...>...</i> tags.
      2) Split on " - " once to separate the primary text from any trailing extended title.
    Returns (chaptername, nameextend).
    """
    cleaned = re.sub(r'<i[^>]*>.*?</i>', '', raw_title).strip()
    parts = cleaned.split(' - ', 1)
    if len(parts) == 2:
        chaptername = parts[0].strip()
        nameextend = parts[1].strip()
    else:
        chaptername = cleaned
        nameextend = ""
    return chaptername, nameextend

def parse_volume_chapter_from_url(chapter_link):
    """
    Given a chapter URL, tries to parse out the volume and chapter number.
    For example, for:
      https://dragonholic.com/novel/the-goddess-granted-me-the-hatching-skill-and-somehow-i-became-the-strongest-tamer-commanding-mythical-and-divine-beasts/1/37/
    it returns ("1", "37").  
    If the URL has only three segments (i.e. no volume) then volume is returned as an empty string.
    If the chapter portion contains a dash (e.g. "37-2"), it converts it to "37.2".
    """
    parsed = urlparse(chapter_link)
    segments = parsed.path.strip('/').split('/')
    volume_str = ""
    chapter_str = ""
    if len(segments) >= 4:
        # Expected format: "novel", "slug", "volume", "chapter"
        possible_volume = segments[-2]
        possible_chapter = segments[-1]
        try:
            # If this segment is numeric, treat it as volume.
            float(possible_volume)
            volume_str = possible_volume
        except ValueError:
            volume_str = ""
        # If the chapter string has a single dash (e.g. "37-2"), convert it to a decimal.
        if '-' in possible_chapter and possible_chapter.count('-') == 1:
            maybe_float = possible_chapter.replace('-', '.')
            try:
                float(maybe_float)
                possible_chapter = maybe_float
            except ValueError:
                pass
        chapter_str = possible_chapter
    elif len(segments) >= 3:
        # No volume segment present.
        chapter_str = segments[-1]
        # Optionally, you could try to extract a number from chapter_str.
    return volume_str, chapter_str

def clean_description(raw_desc):
    """Cleans the raw HTML description by removing extra whitespace and unwanted readmore divs."""
    soup = BeautifulSoup(raw_desc, "html.parser")
    for div in soup.find_all("div", class_="c-content-readmore"):
        div.decompose()
    cleaned = soup.decode_contents()
    return re.sub(r'\s+', ' ', cleaned).strip()

def extract_pubdate_from_soup(chap):
    """
    Extracts the publication date from a chapter element.
    Tries to parse an absolute date (e.g. "February 16, 2025") or a relative date.
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

def chapter_num(chaptername):
    """
    Extracts numeric sequences from chaptername for sorting purposes.
    For example:
      "Chapter 65" -> (65,)
      "37.2"       -> (37.2,)
    If no number is found, returns (0,).
    """
    numbers = re.findall(r'\d+(?:\.\d+)?', chaptername)
    if not numbers:
        return (0,)
    result = []
    for n in numbers:
        if '.' in n:
            result.append(float(n))
        else:
            result.append(int(n))
    return tuple(result)

def normalize_date(dt):
    """Normalizes a datetime object by zeroing microseconds."""
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
    Returns a tuple: (list_of_chapters, main_description).
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
        if "free-chap" in chap.get("class", []):
            continue

        pub_dt = extract_pubdate_from_soup(chap)
        if pub_dt < now - datetime.timedelta(days=7):
            break  # Assumes chapters are ordered newest first

        a_tag = chap.find("a")
        if not a_tag:
            continue

        raw_title = a_tag.get_text(" ", strip=True)
        print(f"Processing chapter: {raw_title}")
        cleaned_chapname, nameextend = split_paid_chapter_dragonholic(raw_title)
        href = a_tag.get("href")
        chapter_link = href.strip() if href and href.strip() != "#" else novel_url

        # Parse volume and chapter from the chapter link.
        volume_val, chapter_val = parse_volume_chapter_from_url(chapter_link)
        # If no chapter value was extracted, fall back to cleaned_chapname.
        if not chapter_val:
            chapter_val = cleaned_chapname

        # Extract a GUID from class names (if available)
        guid = None
        for cls in chap.get("class", []):
            if cls.startswith("data-chapter-"):
                guid = cls.replace("data-chapter-", "")
                break
        if not guid:
            guid = f"{volume_val}-{chapter_val}" if volume_val or chapter_val else "unknown"

        coin_span = chap.find("span", class_="coin")
        coin_value = coin_span.get_text(strip=True) if coin_span else ""

        paid_chapters.append({
            "volume": volume_val,
            "chaptername": chapter_val,
            "nameextend": nameextend,
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
    def __init__(self, *args, chaptername="", nameextend="", coin="", volume="", **kwargs):
        self.chaptername = chaptername
        self.nameextend = nameextend
        self.coin = coin
        self.volume = volume
        super().__init__(*args, **kwargs)
    
    def writexml(self, writer, indent="", addindent="", newl=""):
        writer.write(indent + "  <item>" + newl)
        writer.write(indent + f"    <title>{escape(self.title)}</title>{newl}")
        writer.write(indent + f"    <volume>{escape(self.volume)}</volume>{newl}")
        writer.write(indent + f"    <chaptername>{escape(self.chaptername)}</chaptername>{newl}")
        formatted_nameextend = self.nameextend.strip()
        if formatted_nameextend:
            formatted_nameextend = f"***{formatted_nameextend}***"
        writer.write(indent + f"    <nameextend>{escape(formatted_nameextend)}</nameextend>{newl}")
        writer.write(indent + f"    <link>{escape(self.link)}</link>{newl}")
        writer.write(indent + f"    <description><![CDATA[{self.description}]]></description>{newl}")
        nsfw_list = get_nsfw_novels()
        category_value = "NSFW" if self.title in nsfw_list else "SFW"
        writer.write(indent + f"    <category>{escape(category_value)}</category>{newl}")
        translator = get_translator(self.title)
        writer.write(indent + f"    <translator>{translator if translator else ''}</translator>{newl}")
        discord_role = get_discord_role_id(translator)
        if category_value == "NSFW":
            discord_role += " <@&1304077473998442506>"
        writer.write(indent + f"    <discord_role_id><![CDATA[{discord_role}]]></discord_role_id>{newl}")
        writer.write(indent + f'    <featuredImage url="{escape(get_featured_image(self.title))}"/>{newl}')
        if self.coin:
            writer.write(indent + f"    <coin>{escape(self.coin)}</coin>{newl}")
        writer.write(indent + f"    <pubDate>{self.pubDate.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>{newl}")
        writer.write(indent + f'    <guid isPermaLink="{str(self.guid.isPermaLink).lower()}">{self.guid.guid}</guid>{newl}')
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
        writer.write(indent + addindent + f"<title>{escape(self.title)}</title>{newl}")
        writer.write(indent + addindent + f"<link>{escape(self.link)}</link>{newl}")
        writer.write(indent + addindent + f"<description>{escape(self.description)}</description>{newl}")
        if hasattr(self, 'language') and self.language:
            writer.write(indent + addindent + f"<language>{escape(self.language)}</language>{newl}")
        if hasattr(self, 'lastBuildDate') and self.lastBuildDate:
            writer.write(indent + addindent + "<lastBuildDate>%s</lastBuildDate>" %
                         self.lastBuildDate.strftime("%a, %d %b %Y %H:%M:%S +0000") + newl)
        if hasattr(self, 'docs') and self.docs:
            writer.write(indent + addindent + f"<docs>{escape(self.docs)}</docs>{newl}")
        if hasattr(self, 'generator') and self.generator:
            writer.write(indent + addindent + f"<generator>{escape(self.generator)}</generator>{newl}")
        if hasattr(self, 'ttl') and self.ttl is not None:
            writer.write(indent + addindent + f"<ttl>{escape(str(self.ttl))}</ttl>{newl}")

        for item in self.items:
            item.writexml(writer, indent + addindent, addindent, newl)

        writer.write(indent + "</channel>" + newl)
        writer.write("</rss>" + newl)

# ---------------- Main Asynchronous Logic ----------------

async def process_novel(session, novel_title):
    """Processes a single novel under the semaphore limit, extracting recent paid chapters."""
    async with semaphore:
        print(f"Scraping: {novel_title}")
        novel_url = get_novel_url(novel_title)
        if not novel_url:
            print(f"No URL found for novel: {novel_title}")
            return []

        if not await novel_has_paid_update_async(session, novel_url):
            print(f"Skipping {novel_title}: no recent premium update found.")
            return []

        paid_chapters, main_desc = await scrape_paid_chapters_async(session, novel_url)
        items = []
        if paid_chapters:
            for chap in paid_chapters:
                pub_date = chap["pubDate"]
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=datetime.timezone.utc)
                item = MyRSSItem(
                    title=novel_title,
                    link=chap["link"],
                    description=chap["description"],
                    guid=PyRSS2Gen.Guid(chap["guid"], isPermaLink=False),
                    pubDate=pub_date,
                    volume=chap["volume"],
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
        for translator, novel_titles in TRANSLATOR_NOVEL_MAP.items():
            for novel_title in novel_titles:
                tasks.append(asyncio.create_task(process_novel(session, novel_title)))
        results = await asyncio.gather(*tasks)
    for items in results:
        rss_items.extend(items)

    rss_items.sort(key=lambda item: (normalize_date(item.pubDate), chapter_num(item.chaptername)), reverse=True)
    for it in rss_items:
        print(f"{it.title} - Vol={it.volume} Chap={it.chaptername} => {it.pubDate}")

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

    print(f"Modified feed generated with {len(rss_items)} items.")
    print(f"Output written to {output_file}")

if __name__ == "__main__":
    asyncio.run(main_async())
