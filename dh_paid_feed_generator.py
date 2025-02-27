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
    Extract volume information from the page source.
    Looks for a <ul class="main version-chap volumns"> element.
    Each volume is a <li> with classes "parent" and "has-child".
    Within that, the volume title is in the <a class="has-child">.
    The nested chapters are found within a nested <ul class="sub-chap list-chap">,
    and then inside a <ul class="sub-chap-list">.
    """
    volumes = {}
    volume_container = soup.find("ul", class_="main version-chap volumns")
    if volume_container:
        # Find parent <li> elements that have both "parent" and "has-child" in their class list.
        volume_parents = volume_container.find_all("li", class_=lambda x: x and "parent" in x and "has-child" in x)
        print(f"Found {len(volume_parents)} volume parent elements.")
        for parent in volume_parents:
            # Get the volume title from the <a class="has-child">
            a_tag = parent.find("a", class_="has-child")
            if not a_tag:
                continue
            vol_title = a_tag.get_text(strip=True)
            print(f"Volume title found: {vol_title}")
            # Now, look for the nested chapter list. In your snippet, the structure is:
            # <ul class="sub-chap list-chap" ...>
            #   <li>
            #       <ul class="sub-chap-list"> ... chapter items ... </ul>
            #   </li>
            chapter_list = parent.find("ul", class_="sub-chap list-chap")
            chapters = []
            if chapter_list:
                # Look for the inner <ul class="sub-chap-list">
                inner_ul = chapter_list.find("ul", class_="sub-chap-list")
                if inner_ul:
                    # Each chapter is an <li> inside inner_ul.
                    chapter_lis = inner_ul.find_all("li", class_=lambda x: x and "wp-manga-chapter" in x)
                    print(f"Found {len(chapter_lis)} chapters for volume '{vol_title}'.")
                    for li in chapter_lis:
                        chapter_link = li.find("a")
                        if chapter_link:
                            chapter_title = chapter_link.get_text(strip=True)
                            chapter_url = chapter_link.get("href")
                            chapters.append({
                                "title": chapter_title,
                                "url": chapter_url
                            })
                else:
                    print(f"No inner sub-chap-list found for volume '{vol_title}'.")
            else:
                print(f"No chapter list found for volume '{vol_title}'.")
            volumes[vol_title] = chapters
    else:
        print("No volume container found.")
    return volumes

def extract_chapters(soup):
    """
    In case the novel does not have volumes,
    extract standalone chapters from the <div class="listing-chapters_wrap">
    that contain <li class="wp-manga-chapter"> elements.
    """
    chapters = []
    chapter_container = soup.find("div", class_="listing-chapters_wrap")
    if chapter_container:
        chapter_lis = chapter_container.find_all("li", class_=lambda x: x and "wp-manga-chapter" in x)
        print(f"Found {len(chapter_lis)} standalone chapter items.")
        for li in chapter_lis:
            a_tag = li.find("a")
            if a_tag:
                chapter_title = a_tag.get_text(strip=True)
                chapter_url = a_tag.get("href")
                chapters.append({
                    "title": chapter_title,
                    "url": chapter_url
                })
    else:
        print("No chapter container found.")
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
        for vol, chapters in volumes.items():
            if chapters:
                for chapter in chapters:
                    rss_items += generate_rss_item(novel_title, chapter, volume=vol)
            else:
                print(f"Warning: Volume '{vol}' has no chapters.")
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
    # Set the novel URL and title
    novel_url = "https://dragonholic.com/novel/after-rebirth-i-married-my-archenemy/"
    novel_title = "After Rebirth, I Married my Archenemy"
    
    print(f"Fetching page source from: {novel_url}")
    html = get_page_source(novel_url)
    soup = BeautifulSoup(html, "html.parser")
    
    # First, try to extract volumes.
    volumes = extract_volume_info(soup)
    
    if volumes and any(volumes.values()):
        print("Volumes detected:")
        for vol, chapters in volumes.items():
            print(f"  {vol}: {len(chapters)} chapters")
    else:
        print("No volumes detected; attempting to extract standalone chapters.")
        volumes = None

    standalone_chapters = extract_chapters(soup) if not volumes else None
    
    # Build the RSS feed XML
    rss_feed_xml = build_rss_feed(novel_title, volumes, standalone_chapters)
    
    # Write the RSS feed to file
    output_filename = "dh_paid_feed.xml"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(rss_feed_xml)
    
    print(f"RSS feed generated and written to {output_filename}")
