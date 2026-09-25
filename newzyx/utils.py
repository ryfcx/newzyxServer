from datetime import datetime, timedelta
import re
import ftfy


def cleanupTxt(txt):
    txt = txt.encode("ascii", "ignore").decode("utf-8")
    txt = txt.replace('\u2014', '-').replace('\u2013', '-')
    txt = txt.replace('\u2019', "'").replace('\u201c', '"').replace('\u201d', '"')
    txt = txt.replace('\u2026', '...').replace('\xa0', ' ')
    txt = ftfy.fix_text(txt)
    txt = re.sub(r'\[.*?\]', ' ', txt)
    txt = re.sub(r'\(.*?\)', ' ', txt)
    return txt


def ymd(n=0, fmt="%Y-%m-%d"):
    return (datetime.now() - timedelta(days=n)).strftime(fmt)


# Whole words only, so names and ordinary words (Dickinson, cocktail, Scunthorpe) stay.
_PROFANITY = re.compile(
    r"\b("
    r"fuck\w*|fck|fuq|fuk|motherf\w*|"
    r"f[\W_]*u[\W_]*c[\W_]*k|f\*+[c]?k|"
    r"bullshit\w*|shit\w*|shite|"
    r"s[\W_]*h[\W_]*i[\W_]*t|sh\*+t|b\*+tch|"
    r"bitch\w*|bastard\w*|bollocks?|wanker\w*|twat\w*|"
    r"assholes?|asshats?|dumbass\w*|jackass\w*|badass\w*|smartass\w*|\bass\b|"
    r"damn\w*|goddamn\w*|\bhell\b|"
    r"craps?|crappy|piss\w*|"
    r"dicks?|dickhead\w*|\bcocks?\b|"
    r"puss(?:y|ies)|"
    r"sluts?|whores?|"
    r"pricks?|\btits?\b|"
    r"cunts?|"
    r"porn\w*|\bxxx\b|"
    r"\bsex\w*|"
    r"blowjobs?|handjobs?|"
    r"rapists?|\brape\w*|"
    r"faggot\w*|\bfags?\b|"
    r"\bretard(?:ed|s)?\b|"
    r"niggers?|niggaz?|"
    r"chinks?|\bspics?\b|\bkikes?\b"
    r")\b",
    re.I,
)


def inappropriate(txt):
    """Return the matched crude word, or '' if the text is fine for the kids show."""
    if not txt:
        return ""
    match = _PROFANITY.search(txt)
    return match.group(0) if match else ""


# Whole words, so "skill", "classic", and "preview" are not treated as "kill" or "review".
_CONTENT_BLOCK = re.compile(
    r"\b("
    r"menstruation|cocaine|alcohol|casino|violence|racist|nazis?|suicide|erotic|"
    r"homicide|terrorists?|airstrike|missile|assault\w*|abuse|overdose|hostage|"
    r"arrested|sentenced|lawsuit|indicted|pedophile|deadly|manslaughter|"
    r"murder\w*|massacre|genocide|torture|"
    r"kill\w*|drugs|weed|bombs?|guns?|gambl\w*|"
    r"elections?|campaigns?|\bgop\b|democrats?|republicans?|senate|parliament|"
    r"stocks|inflation|"
    r"kardashian|bachelorette|paparazzi|horoscope|astrology|"
    r"obituaries|obituary|funerals?|"
    r"divorces?|affairs?"
    r")\b",
    re.I,
)
_COMMERCE_BLOCK = (
    "gallery/", "interactive/", "video/", "/video", "audio/", "/audio",
    "commentisfree", "thefilter", "you-solve-it", "/videos/", "/live/",
    "/extra/", "/sounds/", "radio-and-tv",
    "/photos/", "/photo/", "shop/", "diy/", "gma/", "entertainment/",
    "watch/", "% off", "$ off", "discount", "promo code", "best deals",
    "price drop", "buy now", "on sale", "cheapest", "best buy",
    "shop now", "checkout", "sponsored", "affiliate", "paid partnership",
    "advertisement", "iphone", "deals",
)


def isBad(txt, mode=0):
    crude = inappropriate(txt)
    if crude:
        return crude
    blocked = _CONTENT_BLOCK.search(txt or "")
    if blocked:
        return blocked.group(0)
    if mode == 0:
        low = (txt or "").lower()
        return next((w for w in _COMMERCE_BLOCK if w in low), "")
    return ""


AD_DOMAINS = [
    "doubleclick.net", "googlesyndication", "facebook.com/tr", "undefined",
    "siemens-energy.com", "iberdrola.com", "amazon-adsystem",
]


def is_ad_url(url):
    return any(ad in url for ad in AD_DOMAINS)


def retry_request(fn, retries=3, backoff=2.0):
    import time
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = backoff * (2 ** attempt)
            print(f"  Retry {attempt + 1}/{retries} after {wait:.1f}s: {e}")
            time.sleep(wait)
