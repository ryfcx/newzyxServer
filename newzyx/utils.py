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


def isBad(txt, mode=0):
    filter1 = [
        "shit", "bitch", "asshole", "porn", "xxx", "sex", " rape ",
        "menstruation", "cocaine", "alcohol", "casino", "violence", "racist",
        " nazi ", "suicide", "erotic", "homicide", "terrorist", "airstrike",
        "missile", "assault", "abuse", "overdose", "hostage", "arrested",
        "sentenced", "lawsuit", "indicted", "pedophile", "deadly",
        "manslaughter", "deals", "iphone",
    ]
    filter2 = [
        "kill", "hate", "cutting", "flame", "bully", "drugs", "weed",
        "bomb", "gun", "troll", "charges", "trial", "review", "stab", "gamble",
    ]
    filter3 = [
        "election", "campaign", "gop", "democrat", "republican", "senate",
        "parliament", "tax", "stocks", "markets", "interest rate", "inflation",
        "fed", "bond", "attorneys",
    ]
    filter4 = [
        "gallery/", "interactive/", "video/", "/video", "audio/", "/audio",
        "commentisfree", "thefilter", "you-solve-it", "/videos/", "/live/",
        "/extra/", "/cricket/", "/sounds/", "radio-and-tv",
        "/football/european", "/football/premier-league", "BBC Sport app",
        "/photos/", "/photo/", "shop/", "diy/", "gma/", "entertainment/",
        "watch/",
    ]
    filter5 = [
        "kardashian", "bachelor", "bachelorette", "reality tv", "coupon",
        "dating", "celebrity", "paparazzi", "red carpet", "fashion week",
        "skincare", "makeup tutorial", "horoscope", "astrology", "weight loss",
        "diet pill", "crypto", "nft", "forex", "obituar", "funeral",
        "wedding", "divorce", "affair", "pregnant", "baby bump",
    ]
    filter6 = [
        "% off", "$ off", "discount", "promo code", "best deals",
        "price drop", "buy now", "on sale", "cheapest", "best buy",
        "shop now", "checkout", "airdrop", "upgrade your", "get one free",
        "limited time", "flash sale", "unboxing", "hands-on review", "vs.",
        "which should you buy", "sponsored", "affiliate", "paid partnership",
        "ad:", "advertisement",
    ]
    bad_words = filter1 + filter2 + filter3 + filter4 + filter5 + filter6 if mode == 0 else filter1
    low = txt.lower()
    return next((w for w in bad_words if w in low), "")


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
