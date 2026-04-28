# Newzyx V2 — **Server**

GitHub: **[ryfcx/newzyxServer](https://github.com/ryfcx/newzyxServer)**

This repository is the **server deployment** build: ephemeral workspace, S3-centric RSS, SQLite configurable path, and **no** in-process daily scheduler—use systemd or cron.

Daily news podcast pipeline for kids (about ages 10–16): scrape headlines, score and summarize with an LLM, pick stories, generate script + ElevenLabs audio, build a static episode page, update the podcast RSS feed, and upload everything to **Amazon S3** (optional **CloudFront** invalidation).

Designed to run **once per day** on a small Linux box or **Raspberry Pi**: one process per run, no built-in scheduler—use **systemd timer** or **cron** (see [`docs/DEPLOY.md`](docs/DEPLOY.md)).

---

## Pipeline (9 steps)

| Step | Module | What it does |
|------|--------|----------------|
| 1 | [`pipeline/collect.py`](pipeline/collect.py) | Scrape headline URLs (Guardian, PopSci, BBC, NatGeo, ABC, NBC) |
| 2 | [`pipeline/extract.py`](pipeline/extract.py) | Fetch pages; extract article text |
| 3 | [`pipeline/process.py`](pipeline/process.py) | LLM scoring (0–100), summaries, podcast snippets |
| 4 | [`pipeline/episode.py`](pipeline/episode.py) | Select top stories (weighted diversity + recency) |
| 5 | [`pipeline/episode.py`](pipeline/episode.py) | Assemble script; optional polish pass |
| 6 | [`pipeline/tts.py`](pipeline/tts.py) | ElevenLabs text-to-speech → MP3 |
| 7 | [`pipeline/episode.py`](pipeline/episode.py) | Render dated HTML from `website/template.html` |
| 8 | [`pipeline/rss.py`](pipeline/rss.py) | Merge **`feed.xml`**: download canonical feed from S3, append this episode (no full local episode history required) |
| 9 | [`pipeline/upload.py`](pipeline/upload.py) | Upload assets to S3; invalidate CloudFront if configured |

---

## Data

Article state is stored in **SQLite** (default path: `data/newzyx.db`). Override with **`NEWZYX_DB_PATH`** (for example a path on USB storage to reduce wear on an SD card).

States:

```
collected → extracted → scored → published
```

---

## Project layout

```
repo/
├── main.py                 # Wrapper → scripts/run_once.py
├── run_once.py             # Same
├── scripts/
│   └── run_once.py         # Sets cwd + PYTHONPATH; runs newzyx.run.run_daily_pipeline()
├── newzyx/
│   ├── config.py           # Loads .env from repo root
│   ├── workspace.py        # Ephemeral or fixed build directory
│   ├── run.py              # Full pipeline implementation
│   └── utils.py
├── pipeline/               # Steps 1–9 modules
├── website/                # Template + static files required at build/upload time
│   ├── template.html
│   ├── NewzyxV2-Podcast.jpg   # Podcast artwork (name must match pipeline/rss.py)
│   └── 404.html            # Optional static page if you host it on S3
├── data/                   # SQLite (gitignored when present)
├── docs/
│   └── DEPLOY.md           # Production: systemd timer, Pi notes
├── .env                    # Secrets (not committed) — copy from .env.example
├── requirements.txt
└── README.md
```

Generated episode HTML/MP3/RSS for each run are written to a **workspace** (temp dir by default), uploaded, then removed—see **Hybrid deploy** below. You do **not** need a large `website/episodes/` tree on disk.

---

## Quick start (local)

```bash
cd /path/to/repo
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # Edit: API keys, bucket, region
python scripts/run_once.py
# Equivalent: python main.py   or   python run_once.py
```

---

## Hybrid deploy (server / Raspberry Pi)

| Variable | Default | Meaning |
|----------|---------|---------|
| `NEWZYX_EPHEMERAL` | `1` | `1` = build in a temp directory, delete after upload. `0` = write under `./website/` (debug / legacy). |
| `NEWZYX_WORKSPACE` | *(unset)* | If set, use this directory as the workspace instead of a temp dir. **Not** auto-deleted after a run. |
| `NEWZYX_DB_PATH` | `data/newzyx.db` | Absolute or repo-relative path to the SQLite file. |

RSS: step 8 pulls **`feed.xml`** from your bucket when present, then appends the new episode so the Pi does not need old MP3s locally.

**Scheduling:** run `scripts/run_once.py` daily via **systemd timer** or **cron**—see [`docs/DEPLOY.md`](docs/DEPLOY.md).

---

## Environment variables

Secrets and tunables live in **`.env`** at the repository root (loaded by [`newzyx/config.py`](newzyx/config.py)). Copy **`.env.example`** and fill in values.

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | OpenAI API (scoring, script polish when `LLM_TYPE=openai`) |
| `OPENAI_MODEL` | e.g. `gpt-4o-mini` |
| `LLM_TYPE` | `openai` (default) or `perplexity` — see `.env.example` |
| `ELEVENLABS_API_KEY` | Text-to-speech |
| `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL_ID` | Voice and model (see ElevenLabs dashboard) |
| `S3_BUCKET` | Target bucket name |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Programmatic access (or use an IAM role on AWS compute where applicable) |
| `AWS_REGION` | Region for S3 client |
| `WEBSITE_URL` | Public base URL for RSS enclosure links (often CloudFront or S3 website URL; **no trailing slash issues** — code strips as needed) |
| `DISTRIBUTION_ID` | Optional CloudFront distribution ID for cache invalidation after upload |

Perplexity-related keys in **`.env.example`** apply only if `LLM_TYPE` is not `openai`.

---

## Article selection (summary)

- High-scoring candidates from recent days; **recency** and **source/topic diversity** adjust effective scores.
- If too few stories meet quality thresholds, the run **skips** publishing an episode (no upload for that day).

---

## Documentation

| Doc | Contents |
|-----|----------|
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | **Server** install, test, **systemd** `.service` + `.timer`, timezone, cron, rsync updates |
