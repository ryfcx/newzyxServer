import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import time
import random
from newzyx import utils
from pipeline import db

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

BAD_LINES = [
    "daily newsletter", "DIY tips sent", "Terms of Service", "must-have deals",
    "Sign Up", "affiliate programs", "This video can not be played",
    "Click above", "Breakthroughs, discoveries, and DIY tips",
]


def _extract_single(url, timeout=15, max_chars=7000):
    def do_fetch():
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r

    r = utils.retry_request(do_fetch, retries=3, backoff=2.0)
    soup = BeautifulSoup(r.text, "html.parser")
    news_dt = None

    def _as_day(raw):
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return None

    for meta_name in ["article:published_time", "datePublished", "date"]:
        meta = soup.find("meta", attrs={"name": meta_name}) or soup.find("meta", attrs={"property": meta_name})
        if meta and meta.get("content"):
            news_dt = _as_day(meta["content"])
            if news_dt:
                break

    if not news_dt:
        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag:
            news_dt = _as_day(time_tag["datetime"])

    if not news_dt:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text() or ""
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            objs = data if isinstance(data, list) else [data]
            extra = []
            for obj in objs:
                if isinstance(obj, dict) and "@graph" in obj:
                    extra.extend(obj.get("@graph") or [])
            for obj in objs + extra:
                if not isinstance(obj, dict):
                    continue
                news_dt = _as_day(obj.get("datePublished") or obj.get("dateCreated"))
                if news_dt:
                    break
            if news_dt:
                break

    node = soup.find("article")
    paragraphs = node.find_all("p") if node else soup.find_all("p")

    clean = [p for p in paragraphs if not any(bad in p.get_text() for bad in BAD_LINES)]

    text = " ".join(p.get_text(" ", strip=True) for p in clean)
    text = " ".join(text.split())[:max_chars]
    return text, news_dt


def process_urls(only_news_date=None):
    rows = db.get_collected(only_news_date=only_news_date)
    print(f"  {len(rows)} articles to extract")
    extracted = 0

    for row in rows:
        aid, url = row["id"], row["url"]
        try:
            text, news_dt = _extract_single(url)
            text = utils.cleanupTxt(text)

            if not text.strip():
                db.mark_invalid(aid, "empty_content")
                continue

            bad = utils.isBad(text, 1)
            if bad:
                db.mark_invalid(aid, bad)
                print(f"  Invalid content: {aid[:8]} ({bad})")
                continue

            if only_news_date and (not news_dt or news_dt != only_news_date):
                db.mark_invalid(
                    aid,
                    f"story_date_mismatch: want {only_news_date!r} got {news_dt!r}",
                )
                print(
                    f"  Skip: date for {aid[:8]} is {news_dt!r} (backfill for {only_news_date})"
                )
                continue

            db.mark_extracted(aid, text, news_dt)
            extracted += 1
            print(f"  Extracted: {aid[:8]} {news_dt or ''} {text[:80]}")

            time.sleep(random.uniform(0.3, 0.7))
        except Exception as e:
            print(f"  Failed {url[:60]}: {e}")

    print(f"Extracted {extracted} of {len(rows)} articles")
    return extracted
