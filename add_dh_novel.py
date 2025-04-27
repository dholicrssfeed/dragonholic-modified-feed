#!/usr/bin/env python3
"""
Interactive CLI tool to add a new novel entry to dh_mappings.py:
  - Slugifies the title to guess its URL
  - If the guessed URL fails, prompts for an override
  - Fetches the page to extract cover image URL and NSFW badge
  - Prompts for translator username (and Discord role ID if the translator is new)
  - Wraps numeric role IDs as <@&ID>
  - Updates TRANSLATOR_NOVEL_MAP, DISCORD_ROLE_ID_MAP, NOVEL_URL_OVERRIDES,
    featured_image_map, and NSFW list in place
  - Writes all five mappings back into dh_mappings.py via regex replacement,
    using JSON-style double quotes and four-space indents
Usage:
  python add_novel.py "Novel Title"
Optional flags:
  --url URL           # override guessed URL
  --translator NAME   # override translator username
  --role-id ID        # override Discord role ID (numeric)
Requires:
  pip install requests beautifulsoup4
"""
import argparse
import json
import re
import requests
from bs4 import BeautifulSoup
import dh_mappings

def slugify(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^\w\s\u0080-\uFFFF-]", "", t)
    t = re.sub(r"[\s_]+", "-", t)
    return re.sub(r"-{2,}", "-", t)

def get_valid_url(title: str, override: str=None) -> str:
    if override:
        return override.strip()
    guess = f"https://dragonholic.com/novel/{slugify(title)}/"
    print(f"🔍 Trying guessed URL: {guess}")
    try:
        r = requests.get(guess, timeout=5)
        code = r.status_code
    except requests.RequestException:
        code = None
    if code != 200:
        return input("❌ Guess failed. Enter the correct URL: ").strip()
    print("✅ Found page.")
    return guess

def parse_page(url: str):
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    img = soup.find(lambda tag: tag.has_attr("data-src") and "wp-content/uploads" in tag["data-src"])
    featured = None
    if img:
        raw = img["data-src"]
        featured = re.sub(r"-\d+x\d+(?=\.\w+)", "", raw)
    nsfw = bool(soup.find("span", class_=lambda c: c and "adult" in c))
    return featured, nsfw

def update_mappings(title, url, featured, nsfw, tran_arg, role_arg):
    # 1) URL
    dh_mappings.NOVEL_URL_OVERRIDES[title] = url
    # 2) Cover image
    dh_mappings.featured_image_map[title] = featured or ""
    # 3) NSFW
    if nsfw and title not in dh_mappings.get_nsfw_novels():
        dh_mappings.get_nsfw_novels().append(title)
    # 4) Translator & role
    tmap = dh_mappings.TRANSLATOR_NOVEL_MAP
    translator = tran_arg or input(f"Translator for '{title}': ").strip()
    if translator in tmap:
        if title not in tmap[translator]:
            tmap[translator].append(title)
    else:
        tmap[translator] = [title]
        raw = role_arg or input(f"New translator '{translator}'. Role ID (numeric): ").strip()
        rid = raw if raw.startswith("<@&") and raw.endswith(">") else f"<@&{raw}>"
        dh_mappings.DISCORD_ROLE_ID_MAP[translator] = rid

def extract_literal(source: str, name: str, kind: str):
    if kind == 'dict':
        pattern = rf"({name}\s*=\s*)\{{[\s\S]*?\}}"
        m = re.search(pattern, source, flags=re.MULTILINE)
        if not m:
            raise RuntimeError(f"Could not find dict '{name}'")
        return m.span(0)
    else:  # kind == 'list'
        pattern = r"(def get_nsfw_novels\(\):\s*return\s*)(\[[\s\S]*?\])"
        m = re.search(pattern, source)
        if not m:
            raise RuntimeError("Could not find NSFW list")
        return m.span(2)

def replace_literal(source: str, name: str, obj, span):
    before, after = source[:span[0]], source[span[1]:]
    # JSON dump for double-quotes, 4-space indent
    block = json.dumps(obj, ensure_ascii=False, indent=4)
    if name == 'get_nsfw_novels':
        # want: def get_nsfw_novels(): return <block>
        prefix = source[span[0] - len("return "): span[0]]  # include "return "
        return source[:span[0] - len("return ")] + prefix + block + after
    else:
        return before + f"{name} = " + block + after

def write_back():
    path = dh_mappings.__file__
    src = open(path, 'r', encoding='utf-8').read()

    span_t = extract_literal(src, 'TRANSLATOR_NOVEL_MAP', 'dict')
    span_d = extract_literal(src, 'DISCORD_ROLE_ID_MAP',  'dict')
    span_o = extract_literal(src, 'NOVEL_URL_OVERRIDES',  'dict')
    span_f = extract_literal(src, 'featured_image_map',   'dict')
    span_n = extract_literal(src, 'get_nsfw_novels',      'list')

    updated = src
    updated = replace_literal(updated, 'TRANSLATOR_NOVEL_MAP',      dh_mappings.TRANSLATOR_NOVEL_MAP,   span_t)
    updated = replace_literal(updated, 'DISCORD_ROLE_ID_MAP',       dh_mappings.DISCORD_ROLE_ID_MAP,    span_d)
    updated = replace_literal(updated, 'NOVEL_URL_OVERRIDES',       dh_mappings.NOVEL_URL_OVERRIDES,    span_o)
    updated = replace_literal(updated, 'featured_image_map',        dh_mappings.featured_image_map,     span_f)
    updated = replace_literal(updated, 'get_nsfw_novels',           dh_mappings.get_nsfw_novels(),      span_n)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(updated)
    print(f"✅ Wrote updated mappings to {path}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('title', help="Novel title")
    parser.add_argument('--url', help="Override guessed URL")
    parser.add_argument('--translator', help="Override translator")
    parser.add_argument('--role-id', help="Override Discord role ID (numeric)")
    args = parser.parse_args()

    url = get_valid_url(args.title, args.url)
    featured, nsfw = parse_page(url)
    update_mappings(args.title, url, featured, nsfw, args.translator, args.role_id)
    write_back()

if __name__ == '__main__':
    main()
