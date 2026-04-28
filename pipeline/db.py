import sqlite3
import hashlib
import os
from datetime import datetime, timedelta
from contextlib import contextmanager

from newzyx.config import DB_PATH

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
                pass
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


def get_extracted(limit_per_topic=12, only_news_date=None):
    with _connect() as conn:
        if only_news_date:
            rows = conn.execute(
                """SELECT id, url, title, topic, source, article FROM articles
                   WHERE state='extracted' AND invalid_reason IS NULL AND news_dt = ?
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
    for r in rows:
        t = r["topic"] or "general"
        topic_counts[t] = topic_counts.get(t, 0) + 1
        if topic_counts[t] <= limit_per_topic:
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
                     AND news_dt = ?
               ORDER BY score DESC, collect_dt DESC""",
            (min_score, news_date),
        ).fetchall()


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
        candidates = []
        for score_floor in (min_score, 85, 80):
            candidates = get_publish_candidates_for_date(news_date, min_score=score_floor)
            if len(candidates) >= min_articles:
                break
        if len(candidates) < min_articles:
            return []
    else:
        candidates = []
        for days in (max_age_days, 5, 7, 10):
            candidates = get_publish_candidates(min_score, days)
            if len(candidates) >= min_articles:
                break
        if len(candidates) < min_articles:
            return []

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    scored = []
    for c in candidates:
        recency = 1.0
        if not news_date:
            cdt = c["collect_dt"] or today
            if cdt < yesterday:
                recency = 0.7
            elif cdt < today:
                recency = 0.9
        final = c["score"] * recency
        scored.append((final, c))

    scored.sort(key=lambda x: x[0], reverse=True)

    selected = []
    seen_sources = {}
    seen_topics = {}

    for final_score, c in scored:
        if len(selected) >= target:
            break
        src = c["source"] or "unknown"
        topic = c["topic"] or "general"

        src_count = seen_sources.get(src, 0)
        topic_count = seen_topics.get(topic, 0)

        penalty = 1.0
        if src_count == 1:
            penalty *= 0.5
        elif src_count >= 2:
            penalty *= 0.15
        if topic_count == 1 and len(selected) >= 2:
            penalty *= 0.6
        elif topic_count >= 2:
            penalty *= 0.2

        adj = final_score * penalty
        if adj > 0:
            selected.append((adj, c))
            seen_sources[src] = src_count + 1
            seen_topics[topic] = topic_count + 1

    selected.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in selected[:target]]


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
