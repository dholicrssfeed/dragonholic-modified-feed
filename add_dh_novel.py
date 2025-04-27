#!/usr/bin/env python3
"""
Interactive CLI tool to add a new novel entry to dh_mappings.py:
  - Slugifies the title to guess its URL
  - If the guessed URL fails, prompts for the correct URL
  - Fetches the page to extract cover image URL and NSFW badge
  - Prompts for translator username (and Discord role ID if the translator is new)
  - Wraps numeric role IDs as <@&ID>
  - Updates TRANSLATOR_NOVEL_MAP, DISCORD_ROLE_ID_MAP, NOVEL_URL_OVERRIDES,
    featured_image_map, and NSFW list in place
  - Writes all mapping dicts back into dh_mappings.py via regex replacement,
    preserving insertion order in dicts

Usage:
  python add_novel.py "Novel Title"

Optional flags (primarily for testing, but interactive prompts will override):
  --url URL           # provide URL override
  --translator NAME   # provide translator override
  --role-id ID        # provide raw numeric Discord role ID override

Requires:
  pip install requests beautifulsoup4
"""
import sys
import re
import pprint
import requests
from bs4 import BeautifulSoup
import argparse
import dh_mappings


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\u0080-\uFFFF-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-{2,}", "-", text)


def get_valid_url(title: str, override: str = None) -> str:
    if override:
        return override.strip()
    guess = f"https://dragonholic.com/novel/{slugify(title)}/"
    print(f"Trying guessed URL: {guess}")
    try:
        resp = requests.get(guess, timeout=5)
    except requests.RequestException:
        resp = None
    if not resp or resp.status_code != 200:
        return input("Could not fetch guessed URL. Enter the correct URL: ").strip()
    return guess


def parse_page(url: str):
    resp = requests.get(url)
    soup = BeautifulSoup(resp.text, "html.parser")
    img = soup.find(lambda tag: tag.has_attr("data-src") and "wp-content/uploads" in tag["data-src"])
    featured = None
    if img:
        raw = img["data-src"]
        featured = re.sub(r"-\d+x\d+(?=\.\w+)", "", raw)
    nsfw = bool(soup.find("span", class_=lambda c: c and "adult" in c))
    return featured, nsfw


def update_mappings(title: str, url: str, featured: str, nsfw: bool,
                    translator_arg: str = None, role_id_arg: str = None):
    # URL override
    dh_mappings.NOVEL_URL_OVERRIDES[title] = url
    # featured image
    if not hasattr(dh_mappings, 'featured_image_map'):
        dh_mappings.featured_image_map = {}
    if featured:
        dh_mappings.featured_image_map[title] = featured
    # NSFW list (in-place modification of mapping list)
    if nsfw:
        nsfw_list = dh_mappings.get_nsfw_novels()
        if title not in nsfw_list:
            nsfw_list.append(title)
    # translator and Discord role
    translator = (translator_arg or input(f"Enter translator username for '{title}': ")).strip()
    if translator in dh_mappings.TRANSLATOR_NOVEL_MAP:
        if title not in dh_mappings.TRANSLATOR_NOVEL_MAP[translator]:
            dh_mappings.TRANSLATOR_NOVEL_MAP[translator].append(title)
    else:
        dh_mappings.TRANSLATOR_NOVEL_MAP[translator] = [title]
        raw_role = (role_id_arg or input(f"New translator '{translator}' – enter Discord role ID (numeric): ")).strip()
        role_id = raw_role if raw_role.startswith("<@&") and raw_role.endswith(">") else f"<@&{raw_role}>"
        dh_mappings.DISCORD_ROLE_ID_MAP[translator] = role_id


def write_back():
    path = dh_mappings.__file__
    text = open(path, 'r', encoding='utf-8').read()
    mappings = {
        'TRANSLATOR_NOVEL_MAP': dh_mappings.TRANSLATOR_NOVEL_MAP,
        'DISCORD_ROLE_ID_MAP': dh_mappings.DISCORD_ROLE_ID_MAP,
        'NOVEL_URL_OVERRIDES': dh_mappings.NOVEL_URL_OVERRIDES,
        'featured_image_map': dh_mappings.featured_image_map
    }
    for name, obj in mappings.items():
        pattern = rf"{name}\s*=\s*\{{[\s\S]*?^\}}"
        replacement = f"{name} = " + pprint.pformat(obj, width=100, sort_dicts=False)
        text, _ = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print(f"Updated mappings in {path}.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('title', help="The novel title to add")
    parser.add_argument('--url', help="Override the guessed URL")
    parser.add_argument('--translator', help="Override translator username")
    parser.add_argument('--role-id', help="Override Discord role ID (numeric)")
    args = parser.parse_args()

    title = args.title
    url = get_valid_url(title, args.url)
    featured, nsfw = parse_page(url)
    update_mappings(
        title, url, featured, nsfw,
        translator_arg=args.translator,
        role_id_arg=args.role_id
    )
    write_back()


if __name__ == '__main__':
    main()
