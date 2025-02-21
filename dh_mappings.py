import re
import requests
from bs4 import BeautifulSoup

# Mapping dictionary for translator names to their list of novel titles.
TRANSLATOR_NOVEL_MAP = {
    "Cannibal Turtle": [
        "Quick Transmigration: The Villain Is Too Pampered and Alluring"
    ],
    "Bluesky": [
        "People who eat melon are in 70",
        "Help others? It’s better to help yourself"
    ],
    "alara": [
        "Fever Break",
        "My Husband Became the Most Powerful Minister"
    ],
    "Snowbun": [
        "Rebirth of the Excellent Daughter of the Marquis Household (REDMH)",
        "The Eldest Legitimate Daughter is Both Beautiful and Valiant (ELDBBV)"
    ],
    "Kat": [
        "After Rebirth, I Married my Archenemy",
        "Giving Interstellar Players a Horror Ghost Game Shock",
        "Transmigrated into the Villain's Cannon Fodder Ex-Wife (Transmigrated into a Book)"
    ],
    "silverriver": [
        "To Those Who Regretted After I Died"
    ],
    "tscatthed": [
        "Phoenix Girl in the 1990s"
    ],
    "Eastwalk": [
        "I Am Being Mistaken for a Genius Strategist"
    ],
    "Bunnyyy": [
        "The Wind Heard Her Confession"
    ],
    "UchihaDavinchi": [
        "Disappointing Teleportation Magic ~ Even Though The Movement Distance Is Only 1 Millimeter, I Will Rise Through Ingenuity ~",
        "Reincarnated as a Farmer Gamer: Rising to the Top with the Evolution of the Weakest Job that Only I Know About!?",
        "The Great Sage Who Did not Remain in Legend",
        "After Retirement, Living a Stud Life in Another World",
        "Bondage and Marriage",
        "Clap",
        "Double Junk",
        "I Have been Reincarnated As a Lazy, Arrogant Noble, but When I Destroyed The Scenario Through Effort, I Became The Most Powerful With Extraordinary Magical Power",
        "My Bloody Valentine",
        "Reasonable Loss",
        "Red Dot",
        "The Goddess Granted Me the [Hatching] Skill and Somehow I Became the Strongest Tamer, Commanding Mythical and Divine Beasts",
        "The Second Son of The Marquis Runs Away from Home ~ Lacking Talent, He Abandons Everything and Becomes an Adventurer ~"
    ],
    "Athena": [
        "The Tyrant's Happy Ending",
        "When I started High School, My Childhood Friend, who had suddenly become distant and cold, was harassed by a stranger. I stepped in to help, and as a result, from the following day, My Childhood Friend's behavior became unusual.",
        "A Forest flowing with Milk and Honey",
        "The Sickly Villainess: No, I Wasn’t Poisoned! I'm Just Frail!",
        "Zion's Garden",
        "The Young Male Protagonist Who is Destined for Ruin Fell for Me",
        "The Final Task of the Forsaken Saint: A Command to Marry the Barbarian Count",
        "When the Mid-Boss Villainous Noble Recalls Memories of a Past Life and Gains Game Knowledge  I Will Never Accept a Future Where I'm Called the Jealous Earl"
    ],
    "Huangluan De Bai Huangshulang": [
        "Legend Of The Frost Blade"
    ],
    "Frian": [
        "Global Descent to Sky Islands: Getting a God-level Talent from the Start"
    ],
    "Luojiu": [
        "Diary of my Ex",
        "Gloria von Caldwell's Condemnation and Revenge"
    ],
    "An'er": [
        "After Transmigrating, I and the Female Lead Both Found It 'Really Fragrant' (GL)",
        "The Female Lead is Looking at Me Differently (GL)",
        "A Moment Too Late (GL)",
        "The Male Lead's Harem Belongs to Me (GL)",
        "After Transmigrating, I Married the Male Lead’s Sister (GL)",
        "Confession to You in Early Summer (GL)"
    ],
    "amee": [
        "Guide to the Fallen World"
    ],
    "Sunnie": [
        "Osratida",
        "The Three baby mining brothers"
    ],
    "Yumari": [
        "After Marrying the Disabled Prince (BG)"
    ],
    "silversoul": [
        "I Want to Avoid the Bad Ending"
    ],
    "ciasuraimu": [
        "If You Want To Frame Me As a Villainess, I Will Be The Villainess. However.",
        "Proof of the Demon Lord's Innocence"
    ],
    "Yijuan": [
        "Waiting for the Stars to Fall",
        "I Am Just Sad That I Can’t Grow Old With You"
    ],
    "emberblood": [
        "In this life, I will no longer be a scumbag to my childhood sweetheart",
    ],
    "Beryline": [
        "Little Blind Girl"
    ],
    "Sia": [
        "With Multiple Babies, Who Still Wants to Be the Marquess’s Wife?"
    ],
    "Thyllia": [
        "The Young Marquis Regrets Too Late"
    ],
    "kuro": [
        "The Unspoken Vow"
    ],
    "Niang'er": [
        "Mistakenly Treated The Princess As A Concubine"
    ],
    "Wolffy": [
        "Honkai: Star Rail, My Journey with Tom"
    ]
}

# Mapping for Discord role IDs.
DISCORD_ROLE_ID_MAP = {
    "Cannibal Turtle": "<@&1286581623848046662>",
    "Bluesky": "<@&1291243923238555749>",
    "alara": "<@&1291243990275985441>",
    "Snowbun": "<@&1291253678103330829>",
    "Kat": "<@&1295572430370377779>",
    "silverriver": "<@&1295572692853854228>",
    "tscatthed": "<@&1295572751775436911>",
    "Eastwalk": "<@&1295581200706179112>",
    "Bunnyyy": "<@&1302831622009126923>",
    "UchihaDavinchi": "<@&1309162305745064038>",
    "Athena": "<@&1314845173284343809>",
    "Huangluan De Bai Huangshulang": "<@&1315579651237613589>",
    "Frian": "<@&1315580646298750976>",
    "Luojiu": "<@&1315579753604055041>",
    "An'er": "<@&1315580006549815306>",
    "amee": "<@&1316342133698854944>",
    "Sunnie": "<@&1316342244676210729>",
    "Yumari": "<@&1318494118770638858>",
    "silversoul": "<@&1318494169471127552>",
    "ciasuraimu": "<@&1323168986044563561>",
    "Yijuan": "<@&1323169058706690106",
    "emberblood": "<@&1323169218262335519>",
    "Beryline": "<@&1323169289058127944>",
    "Sia": "<@&1323169355948752967>",
    "Thyllia": "<@&1341625811828215819>",
    "kuro": "<@&1341625880845221919>",
    "Niang'er": "<@&1328167570737594431>",
    "Wolffy": "<@&953602322045501460>"
}

def get_translator(title):
    """
    Determines the translator based on the title by iterating through
    TRANSLATOR_NOVEL_MAP. Returns the translator's name if a match is found,
    otherwise returns None.
    """
    for translator, novels in TRANSLATOR_NOVEL_MAP.items():
        for novel in novels:
            if novel in title:
                return translator
    return None

def get_discord_role_id(translator):
    """
    Returns the Discord role ID for the given translator.
    If no role ID is found, returns an empty string.
    """
    return DISCORD_ROLE_ID_MAP.get(translator, "")

def get_novel_url(title):
    """
    Returns the main page URL for the given novel title.
    First checks NOVEL_URL_OVERRIDES for a manual override.
    If none exists, constructs the URL using a slug.
    """
    NOVEL_URL_OVERRIDES = {
        "Help others? It’s better to help yourself": "https://dragonholic.com/novel/helping-others-its-better-to-help-yourself/",
        "Rebirth of the Excellent Daughter of the Marquis Household (REDMH)": "https://dragonholic.com/novel/redmh/",
        "The Eldest Legitimate Daughter is Both Beautiful and Valiant (ELDBBV)": "https://dragonholic.com/novel/eldbbv/",
        "Transmigrated into the Villain's Cannon Fodder Ex-Wife (Transmigrated into a Book)": "https://dragonholic.com/novel/transmigrated-as-the-villains-cannon-fodder-ex-wife/",
        "When I started High School, My Childhood Friend, who had suddenly become distant and cold, was harassed by a stranger. I stepped in to help, and as a result, from the following day, My Childhood Friend's behavior became unusual.":
            "https://dragonholic.com/novel/when-i-started-high-school-my-childhood-friend-who-had-suddenly-become-distant-and-cold/",
        "The Female Lead is Looking at Me Differently (GL)":
            "https://dragonholic.com/novel/the-female-lead-is-looking-at-me-differently/",
        "A Moment Too Late (GL)":
            "https://dragonholic.com/novel/gl-a-moment-too-late/",
        "After Marrying the Disabled Prince (BG)":
            "https://dragonholic.com/novel/after-marrying-the-disabled-prince/"
    }
    if title in NOVEL_URL_OVERRIDES and NOVEL_URL_OVERRIDES[title]:
        return NOVEL_URL_OVERRIDES[title]
    # Fallback: create URL from slug.
    def slugify(text):
        text = text.lower()
        text = re.sub(r'[^\w\s-]', '', text)
        return re.sub(r'[\s]+', '-', text)
    return f"https://dragonholic.com/novel/{slugify(title)}/"

def get_featured_image(title):
    """
    Attempts to scrape the novel’s main page for the cover image URL.
    It looks for a <div class="summary_image"> and an <img> tag inside it.
    Returns the URL from the image’s data-src attribute after removing any size suffix.
    If the image’s alt attribute contains "no image" or "no cover", returns an empty string.
    """
    novel_url = get_novel_url(title)
    try:
        response = requests.get(novel_url, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"Error fetching novel page for cover image: {novel_url}: {e}")
        return ""
    soup = BeautifulSoup(response.text, "html.parser")
    div = soup.find("div", class_="summary_image")
    if div:
        img = div.find("img")
        if img:
            alt_text = img.get("alt", "").lower()
            if "no image" in alt_text or "no cover" in alt_text:
                return ""
            cover_url = img.get("data-src", "").strip()
            if cover_url:
                # Remove size suffix (e.g. "-193x278") before the file extension.
                cover_url = re.sub(r'-\d+x\d+(?=\.\w+$)', '', cover_url)
                return cover_url
    return ""
