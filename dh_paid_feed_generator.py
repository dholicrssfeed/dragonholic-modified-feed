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
# These are placeholders; replace with your real imports & data.

from dh_mappings import (
    TRANSLATOR_NOVEL_MAP,
    get_novel_url,
    get_featured_image,
    get_translator,
    get_discord_role_id,
    get_nsfw_novels
)

# ---------------- Semaphore Setup ----------------
semaphore = asyncio.Semaphore(100)

# ---------------- Helper Functions (Synchronous) ----------------

def split_paid_chapter_dragonholic(raw_title):
    """
    Handles raw paid feed titles like:
      "Chapter 640 <i class=\"fas fa-lock\"></i> - The Abandoned Supporting Female Role 022"
    Steps:
      1) Remove any <i ...>...</i> tags (such as lock icons).
      2) Split once on " - " to separate the primary part (e.g. "Chapter 640") from the trailing text.
      3) Return (chaptername, nameextend).
    """
    # Remove <i ...>...</i>
    cleaned = re.sub(r'<i[^>]*>.*?</i>', '', raw_title).strip()
    # Split once on " - "
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
    Given a chapter link like:
      https://dragonholic.com/novel/some-slug/1/37-2
    we parse out:
      volume -> '1'
      chapter -> '37.2'  (if we see "37-2", we convert the dash to a dot).
    If no numeric volume or chapter found, return empty strings.
    """
    parsed = urlparse(chapter_link)
    # path might be "/novel/some-slug/1/37-2"
    segments = parsed.path.strip('/').split('/')
    # Typically, the last two segments are volume & chapter if the second-last is numeric
    # But not all novels have a volume segment. We'll do a quick check.
    # Start with empty
    volume_str = ""
    chapter_str = ""

    if len(segments) >= 2:
        possible_volume = segments[-2]
        possible_chapter = segments[-1]

        # Check if second last is numeric => volume
        try:
            _ = float(possible_volume)
            # If parse is OK => we treat it as volume
            volume_str = possible_volume
        except ValueError:
            # Not numeric, so maybe there's no volume
            pass

        # Attempt to parse the last segment as a numeric or numeric-with-dash
        # e.g. "37-2" => "37.2"
        if '-' in possible_chapter and possible_chapter.count('-') == 1:
            # Convert single dash to a decimal point
            maybe_float = possible_chapter.replace('-', '.')
            # Check if numeric
            try:
                float(maybe_float)  # Just to confirm
                possible_chapter = maybe_float
            except ValueError:
                pass

        # Now see if it's numeric
        # We'll store it as-is (like "37.2") if float parsing succeeds
        # or keep it as a string if not numeric
        chapter_str = possible_chapter
        # If user wants to remove "Chapter " from the final, handle that in the split_paid_chapter_dragonholic
        # or do it here if needed.

    return volume_str, chapter_str

def clean_description(raw_desc):
    """Cleans the raw HTML description by removing extra whitespace and certain readmore divs."""
    soup = BeautifulSoup(raw_desc, "html.parser")
    for div in soup.find_all("div", class_="c-content-readmore"):
        div.decompose()
    cleaned = soup.decode_contents()
    return re.sub(r'\s+', ' ', cleaned).strip()

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
                # Attempt a relative "X days ago" parse
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
    Extracts numeric sequences from chaptername for sorting (tuple).
    Examples:
      "Chapter 65" -> (65,)
      "37.2"       -> (37.2,)
      "1.5"        -> (1.5,)
      "Volume 1 Chapter 20" -> (1, 20)
    If no numbers, returns (0,).
    """
    numbers = re.findall(r'\d+(?:\.\d+)?', chaptername)
    if not numbers:
        return (0,)
    # Convert each to int or float
    result = []
    for n in numbers:
        if '.' in n:
            result.append(float(n))
        else:
            result.append(int(n))
    return tuple(result)

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
    class (and not 'free-chap') with a release date within the last 7 days => True.
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
                # No date found, but it's premium => consider True
                return True
    return False

async def scrape_paid_chapters_async(session, novel_url):
    """
    Asynchronously fetches the novel page and extracts:
      - The main description.
      - Paid chapters (excluding free chapters) from <li class="wp-manga-chapter"> elements,
        stopping once older than 7 days.
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
        # Skip free chapters
        if "free-chap" in chap.get("class", []):
            continue

        pub_dt = extract_pubdate_from_soup(chap)
        if pub_dt < now - datetime.timedelta(days=7):
            break  # assumes newest to oldest order

        a_tag = chap.find("a")
        if not a_tag:
            continue

        raw_title = a_tag.get_text(" ", strip=True)
        print(f"Processing chapter: {raw_title}")

        # 1) Clean & separate primary vs. extended name
        cleaned_chapname, nameextend = split_paid_chapter_dragonholic(raw_title)

        # 2) The anchor's href is the real full chapter link
        href = a_tag.get("href")
        if href and href.strip() != "#":
            chapter_link = href.strip()
        else:
            # No real link found => skip or fallback to novel_url
            chapter_link = novel_url

        # 3) Try to parse volume & numeric chapter from the link
        volume_val, chapter_val = parse_volume_chapter_from_url(chapter_link)

        # 4) Extract or build a GUID
        guid = None
        for cls in chap.get("class", []):
            if cls.startswith("data-chapter-"):
                guid = cls.replace("data-chapter-", "")
                break
        if not guid:
            guid = f"{volume_val}-{chapter_val}" if volume_val or chapter_val else "unknown"

        # 5) Extract coin (if any)
        coin_span = chap.find("span", class_="coin")
        coin_value = coin_span.get_text(strip=True) if coin_span else ""

        paid_chapters.append({
            "volume": volume_val,
            "chaptername": chapter_val if chapter_val else cleaned_chapname,
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
        # <title> is the novel title, as provided
        writer.write(indent + f"    <title>{escape(self.title)}</title>{newl}")

        # <volume> tag
        writer.write(indent + f"    <volume>{escape(self.volume)}</volume>{newl}")

        # <chaptername> (we assume user wants just '37.2' not "Chapter 37.2")
        writer.write(indent + f"    <chaptername>{escape(self.chaptername)}</chaptername>{newl}")

        # <nameextend>, e.g. ***Side Story 1***
        formatted_nameextend = self.nameextend.strip()
        if formatted_nameextend:
            formatted_nameextend = f"***{formatted_nameextend}***"
        writer.write(indent + f"    <nameextend>{escape(formatted_nameextend)}</nameextend>{newl}")

        # link
        writer.write(indent + f"    <link>{escape(self.link)}</link>{newl}")

        # description in CDATA
        writer.write(indent + f"    <description><![CDATA[{self.description}]]></description>{newl}")

        # category -> SFW or NSFW
        nsfw_list = get_nsfw_novels()
        category_value = "NSFW" if self.title in nsfw_list else "SFW"
        writer.write(indent + f"    <category>{escape(category_value)}</category>{newl}")

        # translator
        translator = get_translator(self.title)
        writer.write(indent + f"    <translator>{translator if translator else ''}</translator>{newl}")

        # discord_role_id with optional NSFW mention
        discord_role = get_discord_role_id(translator)
        if category_value == "NSFW":
            discord_role += " <@&1304077473998442506>"
        writer.write(indent + f"    <discord_role_id><![CDATA[{discord_role}]]></discord_role_id>{newl}")

        # featuredImage
        featured_img = get_featured_image(self.title)
        writer.write(indent + f'    <featuredImage url="{escape(featured_img)}"/>{newl}')

        # coin
        if self.coin:
            writer.write(indent + f"    <coin>{escape(self.coin)}</coin>{newl}")

        # pubDate
        writer.write(indent + f"    <pubDate>{self.pubDate.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>{newl}")

        # guid
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

        # Quick check for a recent premium update
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

    # Flatten all results into one list
    for items in results:
        rss_items.extend(items)

    # Sort by (date, numeric chapter) descending
    rss_items.sort(key=lambda item: (normalize_date(item.pubDate), chapter_num(item.chaptername)), reverse=True)

    # Debug listing
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

    # (Optional) Pretty-print the XML
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
