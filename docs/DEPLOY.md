# Deploying Newzyx (Server)

This guide is for the **server version** of Newzyx V2: run the pipeline on a **Linux** server or **Raspberry Pi** **once per day**. The program **exits after each run**; scheduling is handled by **systemd** (recommended) or **cron**, not by a long-lived Python process.

## Requirements

- **Python 3.10+**
- Network access (scraping, OpenAI, ElevenLabs, AWS)
- `.env` at the **repository root** (same directory as `scripts/run_once.py`), filled from `.env.example`
- AWS credentials allowed to **`s3:PutObject`** (and optionally **CloudFront** invalidation) for your bucket

### Raspberry Pi notes

- Use a **good power supply** and consider **`NEWZYX_DB_PATH`** pointing to **USB SSD** or external storage to limit SQLite wear on the SD card.
- Default **`NEWZYX_EPHEMERAL=1`** keeps large episode builds off disk except during the run.
- Set the system **timezone** so the systemd timer fires at the intended local time (`timedatectl`).

---

## 1. Install

```bash
cd /path/to/Newzyx    # your clone directory
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env               # or your editor — set all required keys
```

Create data and log directories if needed:

```bash
mkdir -p data logs
```

---

## 2. Smoke test

From the **repository root** (where `scripts/` and `newzyx/` live):

```bash
source venv/bin/activate
python3 scripts/run_once.py
```

Equivalent: `python3 main.py` or `python3 run_once.py` from the repo root.

Successful runs log lines like `[newzyx 1/9] …` and upload to S3. If there are not enough quality articles, the run may skip an episode and still exit successfully.

---

## 3. Schedule with systemd (recommended)

Use a **oneshot** service plus a **timer**. Adjust **`YOUR_USER`** and paths to match your home directory and clone location.

### Service unit

Create `/etc/systemd/system/newzyx.service`:

```ini
[Unit]
Description=Newzyx podcast pipeline (single run)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/Newzyx
Environment=PATH=/home/YOUR_USER/Newzyx/venv/bin:/usr/local/bin:/usr/bin
ExecStart=/home/YOUR_USER/Newzyx/venv/bin/python3 /home/YOUR_USER/Newzyx/scripts/run_once.py
StandardOutput=journal
StandardError=journal
```

`WorkingDirectory` must be the repo root so **`.env`** is found by `newzyx/config.py`.

### Timer unit

Create `/etc/systemd/system/newzyx.timer`:

```ini
[Unit]
Description=Daily Newzyx podcast run

[Timer]
OnCalendar=*-*-* 05:02:00
Persistent=true

[Install]
WantedBy=timers.target
```

Change **`05:02:00`** to your preferred local time (after setting timezone, below).

### Enable and verify

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now newzyx.timer
sudo systemctl list-timers newzyx.timer
```

### Logs

```bash
sudo journalctl -u newzyx.service -n 200 --no-pager
sudo journalctl -u newzyx.service -f
```

### Timezone

```bash
timedatectl
sudo timedatectl set-timezone America/New_York   # example
```

---

## 4. Schedule with cron

Example: daily at **05:02** (user’s crontab):

```cron
2 5 * * * cd /home/you/Newzyx && /home/you/Newzyx/venv/bin/python3 scripts/run_once.py >> /home/you/Newzyx/logs/cron.log 2>&1
```

---

## 5. Deploying code updates

Example **rsync** from a dev machine (excludes virtualenv and local DB):

```bash
rsync -avz \
  --exclude='venv/' \
  --exclude='.venv/' \
  --exclude='data/' \
  --exclude='.env' \
  ./Newzyx/ user@pi:/home/user/Newzyx/
```

Copy or merge **`.env`** on the server separately; do not overwrite production secrets.

Timer/cron keeps firing; you only need `systemctl daemon-reload` if you change unit files.

---

## 6. Troubleshooting

| Issue | What to check |
|-------|----------------|
| `AWS credentials not available` / upload errors | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, bucket name; IAM policy for `s3:PutObject` |
| RSS or MP3 URLs wrong in podcatchers | `WEBSITE_URL` matches how listeners reach files (CloudFront vs S3 URL) |
| `.env` ignored | File at **repo root**; no typo in name; process cwd is repo root (systemd `WorkingDirectory`) |
| Episode skipped | Normal if not enough articles pass thresholds; check logs and DB stats line at end of run |
