import re
import datetime
import feedparser
import PyRSS2Gen
import xml.dom.minidom
from xml.sax.saxutils import escape
from urllib.parse import urlparse, unquote

# Import mapping functions from your mappings file (named dh_mappings.py)
from dh_mappings import get_translator, get_featured_image, get_discord_role_id, get_nsfw_novels

def split_title(full_title):
    """
    Splits the full title into three parts:
      - main_title: before the first " - "
      - chaptername: after the first " - " (or if there are only two parts)
      - nameextend: the third part if present (or fourth part if the third is empty)
    """
    parts = full_title.split(" - ")
    if len(parts) == 2:
        main_title = parts[0].strip()
        chaptername = parts[1].strip()
        nameextend = ""
    elif len(parts) >= 3:
        main_title = parts[0].strip()
        chaptername = parts[1].strip()
        nameextend = parts[2].strip() if parts[2].strip() else (parts[3].strip() if len(parts) > 3 else "")
    else:
        main_title = full_title
        chaptername = ""
        nameextend = ""
    return main_title, chaptername, nameextend

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

from urllib.parse import urlparse, unquote

def smart_title(parts: list[str]) -> str:
    small = {"a","an","the","and","but","or","nor","for","so","yet",
             "at","by","in","of","on","to","up","via"}
    out = []
    last = len(parts) - 1
    for i, w in enumerate(parts):
        wl = w.lower()
        if i == 0 or i == last or wl not in small:
            out.append(w.capitalize())
        else:
            out.append(wl)
    return " ".join(out)

def format_volume_from_url(url: str) -> str:
    segs = [s for s in urlparse(url).path.split("/") if s]
    if len(segs) >= 4 and segs[0] == "novel":
        raw   = unquote(segs[2]).replace("_","-").strip("-")
        parts = raw.split("-")
        if not parts:
            return ""

        colon_keywords = {"volume","chapter","vol","chap","arc","world","plane","story","v"}
        lead = parts[0].lower()

        if lead in colon_keywords and len(parts) >= 2 and parts[1].isdigit():
            num  = parts[1]
            rest = parts[2:]
            if lead == "v":
                return f"V{num}: {smart_title(rest)}" if rest else f"V{num}"
            label = lead.capitalize()
            return f"{label} {num}: {smart_title(rest)}"

        # fallback: smart‑title *all* parts
        return smart_title(parts)

    return ""

class MyRSSItem(PyRSS2Gen.RSSItem):
    def __init__(self, *args, volume="", chaptername="", nameextend="", **kwargs):
        self.volume      = volume
        self.chaptername = chaptername
        self.nameextend  = nameextend
        super().__init__(*args, **kwargs)
    
    def writexml(self, writer, indent="", addindent="", newl=""):
        writer.write(indent + "  <item>" + newl)
        writer.write(indent + "    <title>%s</title>" % escape(self.title) + newl)
        writer.write(indent + "    <volume>%s</volume>"      % escape(self.volume)      + newl)
        writer.write(indent + "    <chaptername>%s</chaptername>" % escape(self.chaptername) + newl)
        formatted_nameextend = f"***{self.nameextend}***" if self.nameextend.strip() else ""
        writer.write(indent + "    <nameextend>%s</nameextend>" % escape(formatted_nameextend) + newl)
        writer.write(indent + "    <link>%s</link>" % escape(self.link) + newl)
        writer.write(indent + "    <description><![CDATA[%s]]></description>" % self.description + newl)
        
        # New <category> element (placed below description, above translator)
        nsfw_list = get_nsfw_novels()
        category_value = "NSFW" if self.title in nsfw_list else "SFW"
        writer.write(indent + "    <category>%s</category>" % escape(category_value) + newl)
        
        translator = get_translator(self.title)
        writer.write(indent + "    <translator>%s</translator>" % (translator if translator else "") + newl)
        
        # Get the original discord role id
        discord_role = get_discord_role_id(translator)
        # Append additional role if NSFW
        if category_value == "NSFW":
            discord_role += " <@&1304077473998442506>"
        writer.write(indent + "    <discord_role_id><![CDATA[%s]]></discord_role_id>" % discord_role + newl)
        
        writer.write(indent + '    <featuredImage url="%s"/>' % escape(get_featured_image(self.title)) + newl)
        writer.write(indent + "    <pubDate>%s</pubDate>" % self.pubDate.strftime("%a, %d %b %Y %H:%M:%S +0000") + newl)
        writer.write(indent + "    <guid isPermaLink=\"%s\">%s</guid>" % (str(self.guid.isPermaLink).lower(), self.guid.guid) + newl)
        writer.write(indent + "  </item>" + newl)

class CustomRSS2(PyRSS2Gen.RSS2):
    """
    Subclass of PyRSS2Gen.RSS2 that overrides the writexml() method so that the 
    opening <rss> tag contains the desired namespace declarations.
    """
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
    feed_url = "https://dragonholic.com/feed/manga-chapters/"
    parsed_feed = feedparser.parse(feed_url)
    for entry in parsed_feed.entries:
        main_title, chaptername, nameextend = split_title(entry.title)
        volume = format_volume_from_url(entry.link)
        translator = get_translator(main_title)
        if not translator:
            print("Skipping item (no translator found):", main_title)
            continue
        pub_date = datetime.datetime(*entry.published_parsed[:6])
        item = MyRSSItem(
            title=main_title,
            link=entry.link,
            description=entry.description,
            guid=PyRSS2Gen.Guid(entry.id, isPermaLink=False),
            pubDate=pub_date,
            volume=volume,    
            chaptername=chaptername,
            nameextend=nameextend
        )
        rss_items.append(item)
    
    # Sort items primarily by publication date (newest first).
    # For items with the same title, sort by the chapter number (highest first).
    rss_items.sort(key=lambda item: (
        item.pubDate,
        item.title,
        chapter_num(item.chaptername)
    ), reverse=True)
    
    new_feed = CustomRSS2(
        title=parsed_feed.feed.title,
        link=parsed_feed.feed.link,
        description=(parsed_feed.feed.subtitle if hasattr(parsed_feed.feed, 'subtitle') else "Modified feed"),
        lastBuildDate=datetime.datetime.now(),
        items=rss_items
    )
    
    output_file = "dh_modified_feed.xml"
    with open(output_file, "w", encoding="utf-8") as f:
        new_feed.writexml(f, indent="  ", addindent="  ", newl="\n")
    
    with open(output_file, "r", encoding="utf-8") as f:
        xml_content = f.read()
    dom = xml.dom.minidom.parseString(xml_content)
    pretty_xml = "\n".join([line for line in dom.toprettyxml(indent="  ").splitlines() if line.strip()])
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(pretty_xml)
    
    print("Modified feed generated with", len(rss_items), "items.")
    print("Output written to", output_file)

if __name__ == "__main__":
    main()
