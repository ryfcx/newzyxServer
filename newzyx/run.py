"""
Single daily pipeline run — server edition (exit after one pass; schedule externally).
"""
from __future__ import annotations

import atexit
import os
import re
import signal
import tempfile
from datetime import datetime, timedelta

from newzyx import utils, workspace
from pipeline import db, collect, extract, process, episode, tts, upload, rss


def _install_cleanup_handlers():
    """Archive ephemeral /tmp/newzyx_* dirs on exit or Ctrl-C/SIGTERM."""
    atexit.register(workspace.cleanup_workspace)

    def _on_signal(signum, _frame):
        workspace.cleanup_workspace()
        raise SystemExit(128 + signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass

STEP_LABELS = (
    "Collect URLs",
    "Extract articles",
    "AI scoring",
    "Select episode",
    "Write script",
    "TTS audio",
    "Build site",
    "Update RSS",
    "Upload S3",
)


def _step(num: int, fn):
    print(f"[newzyx {num}/{len(STEP_LABELS)}] {STEP_LABELS[num - 1]}", flush=True)
    return fn()


# After today's episode, fill holes from the last three weeks. Older archive gaps stay put.
CATCHUP_LOOKBACK_DAYS = 21


def published_episode_dates() -> set[str]:
    """Dates that already have an episode in the live RSS feed."""
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        if not upload.download_object_if_exists("feed.xml", path):
            return set()
        with open(path, encoding="utf-8") as f:
            text = f.read()
        return set(re.findall(r"episodes/(\d{4}-\d{2}-\d{2})/", text))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def missing_days_ago(lookback: int = CATCHUP_LOOKBACK_DAYS) -> list[int]:
    """Days-ago values for dates in the lookback window that are not in the feed. Oldest first."""
    have = published_episode_dates()
    missing = []
    today = datetime.now()
    for n in range(1, lookback + 1):
        day = (today - timedelta(days=n)).strftime("%Y-%m-%d")
        if day not in have:
            missing.append(n)
    missing.sort(reverse=True)
    return missing


def run_daily_pipeline(t: int = 0, fill_gaps: bool = True) -> int:
    """Run one day, then catch up any missing days in the recent window.

    Catch-up runs do not look for further gaps, so a backfill cannot loop.
    """
    code = _run_one_day(t)
    if fill_gaps and t == 0:
        _fill_recent_gaps()
    return code


def _fill_recent_gaps() -> None:
    try:
        gaps = missing_days_ago()
    except Exception as e:
        print(f"[newzyx] Could not check for missing episodes: {e}", flush=True)
        return
    if not gaps:
        print(
            f"[newzyx] No missing episodes in the last {CATCHUP_LOOKBACK_DAYS} days.",
            flush=True,
        )
        return
    dates = ", ".join(utils.ymd(n) for n in gaps)
    print(f"[newzyx] Missing episodes: {dates}", flush=True)
    for n in gaps:
        print(f"[newzyx] Catch-up {utils.ymd(n)}", flush=True)
        _run_one_day(n)


def _run_one_day(t: int = 0) -> int:
    """Run the full pipeline once. Returns 0 on success (including skipped episode).

    ``t`` is days ago. The episode date and article news_dt both use that calendar
    day so a catch-up for yesterday cannot pick today's stories.
    """
    started = datetime.now()
    news_date = utils.ymd(t)
    _install_cleanup_handlers()
    workspace.init_workspace_from_env()
    try:
        db.init_db()
        ep = None
        audio_files = []
        site_files = []
        script_parts = {}
        script_path = os.path.join(workspace.get_workspace(), "script.txt")
        print(f"[newzyx] Episode news date {news_date} (t={t})", flush=True)

        _step(1, collect.collect_urls)
        _step(2, extract.process_urls)
        _step(3, process.process_content)

        def pick_ep():
            nonlocal ep
            ep = episode.select_articles(news_date=news_date)

        _step(4, pick_ep)

        if not ep:
            print("[newzyx] Skipped: not enough quality articles.", flush=True)
            return 0

        def do_script():
            nonlocal script_parts
            script_parts = episode.create_script(script_path, ep, t=t)

        _step(5, do_script)

        def do_tts():
            nonlocal audio_files
            audio_files = tts.tts(script_parts, t=t)

        _step(6, do_tts)

        def do_site():
            nonlocal site_files
            site_files = episode.create_site(ep, t=t)

        _step(7, do_site)

        feed_path = os.path.join(workspace.generated_website_dir(), "feed.xml")

        _step(
            8,
            lambda: rss.incremental_append_current_episode(
                feed_path,
                audio_files[0],
                t=t,
                articles=ep,
            ),
        )

        def do_upload():
            web_root = workspace.generated_website_dir()
            proj_web = workspace.project_website_dir()
            paths = [os.path.join(web_root, f) for f in site_files]
            paths.extend(audio_files)
            paths.append(os.path.join(web_root, "feed.xml"))
            if t == 0:
                today_mp3 = os.path.join(web_root, "today.mp3")
                if os.path.isfile(today_mp3):
                    paths.append(today_mp3)
            for extra_name in (
                rss.PODCAST_ARTWORK_BASENAME,
                "NewzyxV2-removebg.png",
                "NewzyxV2Favicon.ico",
                "404.html",
                "about.html",
                "terms.html",
                "company.html",
            ):
                extra_path = os.path.join(proj_web, extra_name)
                if os.path.isfile(extra_path):
                    paths.append(extra_path)
            return upload.upload_files(paths)

        uploaded = _step(9, do_upload)
        date_str = utils.ymd(t)
        required = [
            f"episodes/{date_str}/{date_str}.html",
            f"episodes/{date_str}/{date_str}.mp3",
            "feed.xml",
        ]
        if t == 0:
            required.extend(["index.html", "latest.json"])
        missing = [key for key in required if key not in (uploaded or [])]
        if missing:
            print(
                f"[newzyx] Upload incomplete, not marking published: {missing}",
                flush=True,
            )
            return 1

        db.mark_published([a["id"] for a in ep])

        elapsed = datetime.now() - started
        stats = db.get_stats()
        print(
            f"[newzyx] Done in {elapsed.total_seconds():.0f}s | articles={stats['total']} {stats['by_state']}",
            flush=True,
        )
        return 0
    finally:
        workspace.cleanup_workspace()
