#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# --- Helper functions ---

def get_page_source(url):
    response = requests.get(url)
    response.raise_for_status()
    return response.text

def extract_volume_info(soup):
    """
    Locate the volume container in the page source.
    For example, the volumes are contained in a <ul class="main version-chap volumns">.
    """
    volume_container = soup.find("ul", class_="main version-chap volumns")
    volumes = {}
    if volume_container:
        # Find list items that are volume "parent" items
        volume_parents = volume_container.find_all(
            "li",
            class_=lambda x: x and "parent" in x and "has-child" in x
        )
        for parent in volume_parents:
            # The volume title is in the <a> tag inside the parent
            a_tag = parent.find("a", class_="has-child")
            if a_tag:
                vol_title = a_tag.get_text(strip=True)
                # The chapters for this volume are inside the nested <ul> of sub-chapters.
                chapter_list = parent.find("ul", class_="sub-chap list-chap")
                chapters = []
                if chapter_list:
                    # Depending on how chapters are structured, you may loop over each <li>
                    for li in chapter_list.find_all("li"):
                        # You might want to extract the chapter title, link, etc.
                        chapter_link = li.find("a")
                        if chapter_link:
                            chapter_title = chapter_link.get_text(strip=True)
                            chapter_url = chapter_link.get("href")
                            chapters.append({
                                "title": chapter_title,
                                "url": chapter_url
                            })
                volumes[vol_title] = chapters
    return volumes

def extract_chapters(soup):
    """
    In case the novel does not have volumes, extract all chapters.
    """
    chapters = []
    # Example: look for all chapter <li> elements inside the chapter list container.
    chapter_container = soup.find("div", class_="listing-chapters_wrap")
    if chapter_container:
        for li in chapter_container.find_all("li", class_="wp-manga-chapter"):
            a_tag = li.find("a")
            if a_tag:
                chapter_title = a_tag.get_text(strip=True)
                chapter_url = a_tag.get("href")
                chapters.append({
                    "title": chapter_title,
                    "url": chapter_url
                })
    return chapters

def generate_rss_item(novel_title, chapter, volume=None):
    """
    Create an RSS item XML string.
    """
    volume_text = f"Volume: {volume}" if volume else ""
    pub_date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    item_xml = f"""  <item>
      <title>{novel_title}: {chapter['title']}</title>
      <link>{chapter['url']}</link>
      <description><![CDATA[{volume_text}]]></description>
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">{chapter['url']}</guid>
    </item>
"""
    return item_xml

def build_rss_feed(novel_title, volumes, standalone_chapters):
    rss_items = ""
    if volumes:
        # For each volume, create an item per chapter with volume info.
        for vol, chapters in volumes.items():
            for chapter in chapters:
                rss_items += generate_rss_item(novel_title, chapter, volume=vol)
    elif standalone_chapters:
        for chapter in standalone_chapters:
            rss_items += generate_rss_item(novel_title, chapter)
    else:
        print("No chapters found!")
    rss_feed = f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
  <channel>
    <title>{novel_title} - Dragonholic RSS Feed</title>
    <link>https://dragonholic.com/</link>
    <description>RSS feed for {novel_title}</description>
{rss_items}
  </channel>
</rss>
"""
    return rss_feed

# --- Main script ---

if __name__ == "__main__":
    # Example URL for the novel "After Rebirth, I Married my Archenemy"
    novel_url = "https://dragonholic.com/novel/after-rebirth-i-married-my-archenemy/"
    novel_title = "After Rebirth, I Married my Archenemy"
    
    # Fetch page source
    html = get_page_source(novel_url)
    soup = BeautifulSoup(html, "html.parser")
    
    # Try to extract volume info first:
    volumes = extract_volume_info(soup)
    
    if volumes:
        print("Volumes detected:")
        for vol in volumes:
            print("  ", vol, "with", len(volumes[vol]), "chapters")
    else:
        print("No volumes detected; extracting standalone chapters")
        volumes = None
    
    # If no volumes, fall back on a general chapter extraction:
    standalone_chapters = extract_chapters(soup) if not volumes else None
    
    # Build the RSS feed XML
    rss_feed_xml = build_rss_feed(novel_title, volumes, standalone_chapters)
    
    # Write to file
    with open("dh_paid_feed.xml", "w", encoding="utf-8") as f:
        f.write(rss_feed_xml)
    
    print("Modified feed generated and written to dh_paid_feed.xml")
