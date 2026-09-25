import sqlite3
import hashlib
import os
import re
from datetime import datetime, timedelta
from contextlib import contextmanager

from newzyx.config import DB_PATH
from newzyx import utils

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id              TEXT PRIMARY KEY,
    url             TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    topic           TEXT,
    source          TEXT,
    state           TEXT DEFAULT 'collected',
    news_dt         TEXT,
    collect_dt      TEXT NOT NULL,
    extract_dt      TEXT,
    process_dt      TEXT,
    publish_dt      TEXT,
    article         TEXT,
    score           INTEGER,
    summary         TEXT,
    pod_script      TEXT,
    pod_question    TEXT,
    pod_answer      TEXT,
    invalid_reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_state ON articles(state);
CREATE INDEX IF NOT EXISTS idx_publish_dt ON articles(publish_dt);
CREATE INDEX IF NOT EXISTS idx_collect_dt ON articles(collect_dt);
"""


def _url_hash(url):
    return hashlib.sha256(url.encode()).hexdigest()[:16]


@contextmanager
def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with _connect() as conn:
        conn.executescript(SCHEMA)


def insert_article(url, title, topic, source, news_dt=None):
    aid = _url_hash(url)
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect() as conn:
        try:
            conn.execute(
                """INSERT INTO articles (id, url, title, topic, source, state, news_dt, collect_dt)
                   VALUES (?, ?, ?, ?, ?, 'collected', ?, ?)""",
                (aid, url, title, topic, source, news_dt, today),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def insert_articles_batch(rows):
    today = datetime.now().strftime("%Y-%m-%d")
    added = 0
    with _connect() as conn:
        for url, title, topic, source, news_dt in rows:
            aid = _url_hash(url)
            try:
                conn.execute(
                    """INSERT INTO articles (id, url, title, topic, source, state, news_dt, collect_dt)
                       VALUES (?, ?, ?, ?, ?, 'collected', ?, ?)""",
                    (aid, url, title, topic, source, news_dt, today),
                )
                added += 1
            except sqlite3.IntegrityError:
                if news_dt:
                    conn.execute(
                        "UPDATE articles SET news_dt=COALESCE(news_dt, ?) WHERE url=?",
                        (news_dt, url),
                    )
    return added


def get_collected(only_news_date=None):
    """
    If only_news_date is set (YYYY-MM-DD), return collected rows for that story date
    (known from URL) or with unknown date (NULL) so extract can set publication day.
    """
    with _connect() as conn:
        if only_news_date:
            return conn.execute(
                """SELECT id, url FROM articles WHERE state='collected' AND invalid_reason IS NULL
                   AND (news_dt IS NULL OR news_dt = ?) ORDER BY collect_dt""",
                (only_news_date,),
            ).fetchall()
        return conn.execute(
            "SELECT id, url FROM articles WHERE state='collected' AND invalid_reason IS NULL"
        ).fetchall()


def mark_extracted(article_id, article_text, news_dt=None):
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect() as conn:
        conn.execute(
            "UPDATE articles SET state='extracted', article=?, extract_dt=?, news_dt=COALESCE(?, news_dt) WHERE id=?",
            (article_text, today, news_dt, article_id),
        )


def mark_invalid(article_id, reason):
    with _connect() as conn:
        conn.execute(
            "UPDATE articles SET invalid_reason=? WHERE id=?",
            (reason, article_id),
        )


def get_extracted(limit_per_topic=12, limit_per_source=12, only_news_date=None):
    with _connect() as conn:
        if only_news_date:
            rows = conn.execute(
                """SELECT id, url, title, topic, source, article FROM articles
                   WHERE state='extracted' AND invalid_reason IS NULL
                     AND COALESCE(news_dt, collect_dt) = ?
                   ORDER BY collect_dt DESC""",
                (only_news_date,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, url, title, topic, source, article FROM articles
                   WHERE state='extracted' AND invalid_reason IS NULL
                   ORDER BY collect_dt DESC"""
            ).fetchall()
    result = []
    topic_counts = {}
    source_counts = {}
    for r in rows:
        t = r["topic"] or "general"
        src = r["source"] or "unknown"
        if topic_counts.get(t, 0) >= limit_per_topic:
            continue
        if source_counts.get(src, 0) >= limit_per_source:
            continue
        topic_counts[t] = topic_counts.get(t, 0) + 1
        source_counts[src] = source_counts.get(src, 0) + 1
        result.append(r)
    return result


def mark_scored(article_id, score, summary, pod_script, pod_question, pod_answer):
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect() as conn:
        conn.execute(
            """UPDATE articles SET state='scored', score=?, summary=?, pod_script=?,
               pod_question=?, pod_answer=?, process_dt=? WHERE id=?""",
            (int(score), summary, pod_script, pod_question, pod_answer, today, article_id),
        )


def get_publish_candidates(min_score=90, max_age_days=3):
    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    with _connect() as conn:
        return conn.execute(
            """SELECT id, url, title, topic, source, score, summary, pod_script,
                      pod_question, pod_answer, news_dt, collect_dt
               FROM articles
               WHERE state='scored' AND invalid_reason IS NULL AND score >= ?
                     AND collect_dt >= ?
               ORDER BY collect_dt DESC, score DESC""",
            (min_score, cutoff),
        ).fetchall()


def get_publish_candidates_for_date(news_date, min_score=90):
    """Scored stories whose article publication day (news_dt) matches a calendar day."""
    with _connect() as conn:
        return conn.execute(
            """SELECT id, url, title, topic, source, score, summary, pod_script,
                      pod_question, pod_answer, news_dt, collect_dt
               FROM articles
               WHERE state='scored' AND invalid_reason IS NULL AND score >= ?
                     AND COALESCE(news_dt, collect_dt) = ?
               ORDER BY score DESC, collect_dt DESC""",
            (min_score, news_date),
        ).fetchall()


# One story from each of these before any second science or nature piece.
FIRST_BUCKETS = ("sports", "technology", "world", "science", "health")
MAX_BUCKET = {
    "sports": 2,
    "technology": 2,
    "world": 2,
    "science": 1,
    "nature": 1,
    "health": 1,
    "history": 1,
}
MAX_SOURCE_COUNT = 1
MIN_SOURCES = 4
# Pull sports that the scorer capped below the normal 90 line.
POOL_MIN_SCORE = 60
OTHER_MIN_SCORE = 80
_NATURE_TITLE = re.compile(
    r"\b(bees?|trees?|wildlife|species|climate|typhoon|hurricane|equinox|"
    r"butterfl(?:y|ies)|whales?|forests?|coral|dinosaurs?|wildfires?|"
    r"animals?|plants?|birds?|bears?|weather)\b",
    re.I,
)


def story_bucket(topic, title):
    """Group section labels so plants, animals, and weather share one cap."""
    topic = (topic or "general").lower()
    title = title or ""
    if topic == "sports":
        return "sports"
    if topic in ("technology", "tech"):
        return "technology"
    if topic == "health":
        return "health"
    if topic == "history":
        return "history"
    if topic == "environment" or _NATURE_TITLE.search(title):
        return "nature"
    if topic in ("world", "general", "politics"):
        return "world"
    if topic == "science":
        return "science"
    return "world"


def choose_diverse(candidates, target=6):
    """
    Fill an episode with different categories and outlets.
    Sports, tech, world, science, and health are seated before a second nature story.
    """
    ranked = sorted(candidates, key=lambda c: c["score"] or 0, reverse=True)
    selected = []
    selected_ids = set()
    seen_sources = {}
    seen_buckets = {}

    def try_add(c, source_cap, bucket_caps):
        if len(selected) >= target:
            return False
        aid = c["id"]
        if aid in selected_ids:
            return False
        bucket = story_bucket(c["topic"], c["title"])
        cap = bucket_caps.get(bucket, 1)
        if seen_buckets.get(bucket, 0) >= cap:
            return False
        src = c["source"] or "unknown"
        if seen_sources.get(src, 0) >= source_cap:
            return False
        spoken = " ".join(
            str(c[key] or "")
            for key in ("title", "summary", "pod_script", "pod_question", "pod_answer")
        )
        blocked = utils.isBad(spoken, 1)
        if blocked:
            print(f"  Skip filtered ({blocked}): {(c['title'] or '')[:60]}")
            return False
        selected.append(c)
        selected_ids.add(aid)
        seen_sources[src] = seen_sources.get(src, 0) + 1
        seen_buckets[bucket] = seen_buckets.get(bucket, 0) + 1
        return True

    def best_in(bucket, source_cap, min_score, bucket_caps):
        for c in ranked:
            if story_bucket(c["topic"], c["title"]) != bucket:
                continue
            if (c["score"] or 0) < min_score:
                continue
            if try_add(c, source_cap, bucket_caps):
                return True
        return False

    def fill(source_cap, bucket_caps, min_score):
        for c in ranked:
            if len(selected) >= target:
                return
            if (c["score"] or 0) < min_score:
                continue
            try_add(c, source_cap, bucket_caps)

    for bucket in FIRST_BUCKETS:
        floor = POOL_MIN_SCORE if bucket == "sports" else OTHER_MIN_SCORE
        best_in(bucket, MAX_SOURCE_COUNT, floor, MAX_BUCKET)

    # One story per outlet until at least four sources are seated.
    fill(MAX_SOURCE_COUNT, MAX_BUCKET, OTHER_MIN_SCORE)
    if len({(c["source"] or "unknown") for c in selected}) < MIN_SOURCES:
        relaxed = {bucket: 2 for bucket in MAX_BUCKET}
        relaxed["sports"] = 2
        relaxed["nature"] = 1
        fill(MAX_SOURCE_COUNT, relaxed, POOL_MIN_SCORE)

    # Fill the remaining seats only after four outlets are in. Never a third story from one outlet.
    if len(selected) < target and len({(c["source"] or "unknown") for c in selected}) >= MIN_SOURCES:
        wider = dict(MAX_BUCKET)
        wider["science"] = 2
        wider["nature"] = 1
        wider["world"] = 2
        wider["technology"] = 2
        fill(2, wider, OTHER_MIN_SCORE)

    return selected[:target]


def select_episode(
    min_score=90,
    max_age_days=3,
    target=6,
    min_articles=4,
    news_date=None,
):
    """
    If news_date is a YYYY-MM-DD string, only articles with that news_dt (article date)
    are used — for backdated episodes. Otherwise uses recent collect window as before.
    """
    if news_date:
        candidates = get_publish_candidates_for_date(news_date, min_score=POOL_MIN_SCORE)
        if len(candidates) < min_articles:
            return []
    else:
        candidates = []
        for days in (max_age_days, 5, 7, 10):
            candidates = get_publish_candidates(POOL_MIN_SCORE, days)
            if len(candidates) >= min_articles:
                break
        if len(candidates) < min_articles:
            return []

    selected = choose_diverse(candidates, target=target)
    if len(selected) < min_articles:
        return []
    return selected


def mark_published(article_ids):
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect() as conn:
        for aid in article_ids:
            conn.execute(
                "UPDATE articles SET state='published', publish_dt=? WHERE id=?",
                (today, aid),
            )


def get_stats():
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        by_state = {}
        for row in conn.execute("SELECT state, COUNT(*) as cnt FROM articles GROUP BY state"):
            by_state[row["state"]] = row["cnt"]
        invalid = conn.execute("SELECT COUNT(*) FROM articles WHERE invalid_reason IS NOT NULL").fetchone()[0]
    return {"total": total, "by_state": by_state, "invalid": invalid}
