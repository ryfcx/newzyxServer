import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from email.utils import parsedate_to_datetime
from collections import defaultdict
from xml.etree import ElementTree as ET
import ftfy
from newzyx import utils
from pipeline import db

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# Per (source, topic) cap before DB insert — keeps each run bounded while scraping more sections.
MAX_LINKS_PER_SOURCE_TOPIC = 48

SOURCES = [
    ("guardian", "science", "https://www.theguardian.com/science", "https://www.theguardian.com"),
    ("guardian", "technology", "https://www.theguardian.com/technology", "https://www.theguardian.com"),
    ("guardian", "world", "https://www.theguardian.com/world", "https://www.theguardian.com"),
    ("guardian", "environment", "https://www.theguardian.com/environment", "https://www.theguardian.com"),
    ("popsci", "science", "https://www.popsci.com/category/science/", ""),
    ("popsci", "technology", "https://www.popsci.com/category/technology/", ""),
    ("bbc", "sports", "https://www.bbc.com/sport/", "https://www.bbc.com"),
    ("bbc", "technology", "https://www.bbc.com/news/technology", "https://www.bbc.com"),
    ("bbc", "science", "https://www.bbc.com/news/science_and_environment", "https://www.bbc.com"),
    ("national-geographic", "history", "https://www.nationalgeographic.com/history/", ""),
    ("national-geographic", "science", "https://www.nationalgeographic.com/science/", ""),
    ("abc-news", "general", "https://abcnews.go.com/", ""),
    ("nbc-news", "world", "https://www.nbcnews.com/world", ""),
    ("nbc-news", "politics", "https://www.nbcnews.com/politics", ""),
    ("nbc-news", "sports", "https://www.nbcnews.com/sports", ""),
]

# RSS first so non-Guardian stories get a pubDate (HTML scrapers usually do not).
RSS_SOURCES = [
    ("bbc", "sports", "https://feeds.bbci.co.uk/sport/rss.xml"),
    ("bbc", "technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("bbc", "science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
    ("bbc", "world", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("bbc", "health", "https://feeds.bbci.co.uk/news/health/rss.xml"),
    ("popsci", "science", "https://www.popsci.com/feed/"),
    ("nbc-news", "world", "https://feeds.nbcnews.com/nbcnews/public/world"),
    ("nbc-news", "technology", "https://feeds.nbcnews.com/nbcnews/public/tech"),
    ("nbc-news", "science", "https://feeds.nbcnews.com/nbcnews/public/science"),
    ("abc-news", "general", "https://abcnews.go.com/abcnews/topstories"),
    ("abc-news", "technology", "https://abcnews.go.com/abcnews/technologyheadlines"),
]

_BBC_TIME_PREFIX = re.compile(
    r"^(?:\d+\s+(?:min|mins|hr|hrs|hour|hours|day|days)\s+ago|\d{1,2}\s+[A-Za-z]{3,}\s+\d{4})\s+",
    re.I,
)


def _fetch(url):
    return utils.retry_request(
        lambda: requests.get(url, headers=HEADERS, timeout=15),
        retries=3, backoff=1.0,
    )


def _abs_url(href, prefix):
    return href if href.startswith("http") else prefix + href


def _parse_date_from_url(url_parts, fmt_indices, fmt_str=None):
    try:
        parts = [url_parts[i] for i in fmt_indices]
        raw = "".join(parts)
        if fmt_str:
            return datetime.strptime(raw, fmt_str).strftime("%Y-%m-%d")
        return datetime(int(parts[0]), int(parts[1]), int(parts[2])).strftime("%Y-%m-%d")
    except (ValueError, IndexError):
        return None


def _local_tag(el):
    return (el.tag or "").split("}")[-1]


def _rss_child_text(item, names):
    names = set(names)
    for child in list(item):
        if _local_tag(child) not in names:
            continue
        text = (child.text or "").strip()
        if text:
            return text
        href = child.get("href") or child.get("url")
        if href:
            return href.strip()
    return ""


def _parse_rss_date(raw):
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _collect_rss():
    candidates = []
    for source, topic, feed_url in RSS_SOURCES:
        try:
            resp = _fetch(feed_url)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            items = [
                el for el in root.iter()
                if _local_tag(el) in ("item", "entry")
            ]
            for item in items:
                title = ftfy.fix_text(_rss_child_text(item, ("title",)))
                url = _rss_child_text(item, ("link", "id", "guid"))
                if not title or not url or not url.startswith("http"):
                    continue
                news_dt = _parse_rss_date(
                    _rss_child_text(item, ("pubDate", "published", "updated", "date"))
                )
                item_topic = topic
                if source == "popsci" and "/technology/" in url:
                    item_topic = "technology"
                candidates.append((url, title, item_topic, source, news_dt))
                print(f"  rss: {source}/{item_topic} {title[:60]}")
        except Exception as e:
            print(f"  Failed to fetch RSS {feed_url}: {e}")
    return candidates


def _bbc_article_links(soup, prefix):
    seen = set()
    rows = []
    for tag in soup.find_all("a", href=True):
        href = str(tag.get("href") or "")
        url = _abs_url(href, prefix)
        if url in seen:
            continue
        if "/articles/" not in url and "/news/" not in url and "/sport/" not in url:
            continue
        if any(skip in url for skip in ("/videos/", "/live/", "/sounds/", "/iplayer/")):
            continue
        title = ftfy.fix_text(tag.get_text(" ", strip=True) or "")
        title = _BBC_TIME_PREFIX.sub("", title).strip()
        if len(title) < 20:
            continue
        seen.add(url)
        rows.append((url, title, None))
    return rows


def collect_urls(only_news_date=None):
    """
    If only_news_date is YYYY-MM-DD, only enqueue URLs with that story date
    (when the date is present in the link) so backfill aligns with a calendar day.
    """
    db.init_db()
    candidates = _collect_rss()

    for source, topic, base_url, prefix in SOURCES:
        try:
            resp = _fetch(base_url)
            resp.encoding = "utf-8"
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            if "guardian" in base_url:
                for tag in soup.find_all("a", attrs={"aria-label": True}):
                    title = ftfy.fix_text(str(tag["aria-label"]))
                    href = str(tag["href"])
                    url = _abs_url(href, prefix)
                    news_dt = _parse_date_from_url(url.split("/"), [4, 5, 6], "%Y%b%d")
                    candidates.append((url, title, topic, source, news_dt))
                    print(f"  fetch: {source}/{topic} {title[:60]}")

            elif "popsci" in base_url:
                for h3 in soup.find_all("h3"):
                    ahref = h3.find_previous("a")
                    if ahref:
                        span = h3.find("span")
                        if span:
                            title = ftfy.fix_text(span.text.strip())
                            href = ahref.get("href", "")
                            url = _abs_url(href, prefix)
                            candidates.append((url, title, topic, source, None))
                            print(f"  fetch: {source}/{topic} {title[:60]}")

            elif "nationalgeographic" in base_url:
                for h2 in soup.find_all("h2"):
                    ahref = h2.find("a")
                    if ahref:
                        span = h2.find("span")
                        if span:
                            title = ftfy.fix_text(span.text.strip())
                            href = ahref.get("href", "")
                            url = _abs_url(href, prefix)
                            candidates.append((url, title, topic, source, None))
                            print(f"  fetch: {source}/{topic} {title[:60]}")

            elif "nbcnews" in base_url:
                for h2 in soup.find_all("h2"):
                    ahref = h2.find("a")
                    if ahref:
                        title = ftfy.fix_text(ahref.text.strip())
                        href = ahref.get("href", "")
                        url = _abs_url(href, prefix)
                        if url.count("/") > 4:
                            candidates.append((url, title, topic, source, None))
                            print(f"  fetch: {source}/{topic} {title[:60]}")

            elif "abcnews" in base_url:
                for tag in soup.find_all("a", attrs={"aria-label": True, "data-testid": True}):
                    title = ftfy.fix_text(str(tag["aria-label"]))
                    href = str(tag["href"])
                    url = _abs_url(href, prefix)
                    if url.count("/") > 4:
                        candidates.append((url, title, topic, source, None))
                        print(f"  fetch: {source}/{topic} {title[:60]}")

            elif "bbc" in base_url:
                for url, title, news_dt in _bbc_article_links(soup, prefix):
                    candidates.append((url, title, topic, source, news_dt))
                    print(f"  fetch: {source}/{topic} {title[:60]}")

            else:
                for h3 in soup.find_all("h3"):
                    ahref = h3.find("a")
                    if ahref:
                        span = h3.find("span")
                        if span:
                            title = ftfy.fix_text(span.text.strip())
                            href = ahref.get("href", "")
                            url = _abs_url(href, prefix)
                            candidates.append((url, title, topic, source, None))
                            print(f"  fetch: {source}/{topic} {title[:60]}")

        except Exception as e:
            print(f"  Failed to fetch {base_url}: {e}")

    seen_urls = set()
    filtered = []
    for url, title, topic, source, news_dt in candidates:
        if url in seen_urls or utils.is_ad_url(url):
            continue
        seen_urls.add(url)
        bad = utils.isBad(url + " " + title, 0)
        if bad:
            print(f"  Filtered: {title[:40]} ({bad})")
            continue
        if only_news_date and news_dt and news_dt != only_news_date:
            continue
        filtered.append((url, title, topic, source, news_dt))

    by_source_topic = defaultdict(list)
    capped = []
    for row in filtered:
        key = (row[3], row[2])
        if len(by_source_topic[key]) >= MAX_LINKS_PER_SOURCE_TOPIC:
            continue
        by_source_topic[key].append(row)
        capped.append(row)
    filtered = capped

    added = db.insert_articles_batch(filtered)
    print(f"Collected {len(candidates)} URLs, filtered to {len(filtered)}, added {added} new")
    return added
