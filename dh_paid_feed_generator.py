import re
import datetime
import requests
from bs4 import BeautifulSoup
import PyRSS2Gen
import xml.dom.minidom
from xml.sax.saxutils import escape
from itertools import groupby

# Import mapping functions and data from your mappings file (dh_paid_mappings.py)
from dh_paid_mappings import (
    TRANSLATOR_NOVEL_MAP,
    get_novel_url,
    get_featured_image,
    get_translator,
    get_discord_role_id,
    get_nsfw_novels
)

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

def extract_pubdate(chap):
    """
    Extracts the publication date from a chapter element.
    Tries to parse an absolute date (e.g. "February 16, 2025");
    if the text is relative (e.g. "2 hours ago"), subtracts that delta from current UTC.
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

def extract_chapter_number(chaptername):
    """
    Extracts the numeric portion from a chapter string.
    For example, "Chapter 605" returns 605.0 (as a float to handle decimals).
    Returns 0 if no numeric value is found.
    """
    match = re.search(r'\bChapter\s*([\d\.]+)', chaptername, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except Exception:
            return 0
    return 0

def scrape_paid_chapters(novel_url):
    """
    Fetches the novel page and extracts:
      - Main description from <div class="description-summary">.
      - All paid chapters (excluding free chapters) from <li class="wp-manga-chapter"> elements.
    Returns a tuple: (list_of_chapters, main_description)
    Each chapter dict contains: "chaptername", "nameextend", "link", "description",
    "pubDate", "guid", and "coin".
    """
    try:
        response = requests.get(novel_url, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"Error fetching {novel_url}: {e}")
        return [], ""
    
    soup = BeautifulSoup(response.text, "html.parser")
    desc_div = soup.find("div", class_="description-summary")
    if desc_div:
        main_desc = clean_description(desc_div.decode_contents())
        print("Main description fetched.")
    else:
        main_desc = ""
        print("No main description found.")
    
    chapters = soup.find_all("li", class_="wp-manga-chapter")
    paid_chapters = []
    print(f"Found {len(chapters)} chapter elements on {novel_url}")
    for chap in chapters:
        if "free-chap" in chap.get("class", []):
            continue
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
            chapter_num = parts[-1] if parts else "unknown"
            chapter_link = f"{novel_url}chapter-{chapter_num}/"
        guid = None
        for cls in chap.get("class", []):
            if cls.startswith("data-chapter-"):
                guid = cls.replace("data-chapter-", "")
                break
        if not guid:
            parts = chap_number.split()
            guid = parts[-1] if parts else "unknown"
        pub_dt = extract_pubdate(chap)
        # Extract coin value from the <span class="coin"> element.
        coin_value = ""
        coin_span = chap.find("span", class_="coin")
        if coin_span:
            coin_value = coin_span.get_text(strip=True)
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
        
        # New <category> element (below description)
        nsfw_list = get_nsfw_novels()
        category_value = "NSFW" if self.title in nsfw_list else "SFW"
        writer.write(indent + "    <category>%s</category>" % escape(category_value) + newl)
        
        translator = get_translator(self.title)
        writer.write(indent + "    <translator>%s</translator>" % (translator if translator else "") + newl)
        
        # Get Discord role ID and add an extra role if NSFW
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

def main():
    rss_items = []
    # Iterate over each translator and their novel titles
    for translator, novel_titles in TRANSLATOR_NOVEL_MAP.items():
        for novel_title in novel_titles:
            title = novel_title  # title is a string
            novel_url = get_novel_url(title)
            print(f"Scraping: {novel_url}")
            paid_chapters, main_desc = scrape_paid_chapters(novel_url)
            if not paid_chapters:
                print(f"No chapters found for {title} at {novel_url}")
                continue
            for chap in paid_chapters:
                pub_date = chap["pubDate"]
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=datetime.timezone.utc)
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
                rss_items.append(item)

    # First, sort all items by pubDate (newest first)
    rss_items.sort(key=lambda item: item.pubDate, reverse=True)

    # Group items with the same pubDate and then, for those with the same novel title, sort by chapter number (descending)
    new_rss_items = []
    for pub_date, group in groupby(rss_items, key=lambda item: item.pubDate):
        group_list = list(group)
        # Group by novel title within the same pubDate
        grouped_by_title = {}
        for item in group_list:
            grouped_by_title.setdefault(item.title, []).append(item)
        # For each novel title, if there are multiple chapters, sort by chapter number descending
        for title, items in grouped_by_title.items():
            if len(items) > 1:
                items.sort(key=lambda item: extract_chapter_number(item.chaptername), reverse=True)
            new_rss_items.extend(items)

    # Finally, sort the overall list by pubDate (newest first)
    new_rss_items.sort(key=lambda item: item.pubDate, reverse=True)
    rss_items = new_rss_items

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
    main()
