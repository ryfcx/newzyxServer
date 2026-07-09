"""
Single daily pipeline run — server edition (exit after one pass; schedule externally).
"""
from __future__ import annotations

import os
from datetime import datetime

from newzyx import workspace
from pipeline import db, collect, extract, process, episode, tts, upload, rss

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


def run_daily_pipeline(t: int = 0) -> int:
    """Run the full pipeline once. Returns 0 on success (including skipped episode)."""
    started = datetime.now()
    workspace.init_workspace_from_env()
    try:
        db.init_db()
        ep = None
        audio_files = []
        site_files = []
        script_path = os.path.join(workspace.get_workspace(), "script.txt")

        _step(1, collect.collect_urls)
        _step(2, extract.process_urls)
        _step(3, process.process_content)

        def pick_ep():
            nonlocal ep
            ep = episode.select_articles()

        _step(4, pick_ep)

        if not ep:
            print("[newzyx] Skipped: not enough quality articles.", flush=True)
            return 0

        _step(5, lambda: episode.create_script(script_path, ep, t=t))

        def do_tts():
            nonlocal audio_files
            audio_files = tts.tts(script_path, t=t)

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
            paths = [os.path.join(web_root, f) for f in site_files]
            paths.extend(audio_files)
            paths.append(os.path.join(web_root, "feed.xml"))
            paths.append(os.path.join(web_root, "today.mp3"))
            paths.append(
                os.path.join(workspace.project_website_dir(), rss.PODCAST_ARTWORK_BASENAME)
            )
            upload.upload_files(paths)

        _step(9, do_upload)
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
