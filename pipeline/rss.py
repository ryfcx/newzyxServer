from email.utils import formatdate
from uuid import uuid5, NAMESPACE_URL
from urllib.parse import quote
from lxml import etree
from pathlib import Path
from datetime import datetime, timedelta
import os
from newzyx import config, utils, workspace

PODCAST_TITLE = "Daily News Podcast for Kids - Newzyx"
PODCAST_DESCRIPTION = "Daily news podcast designed for kids aged 10-16, making current events fun, educational, and engaging."
PODCAST_AUTHOR = "Ryan G"
PODCAST_LANGUAGE = "en-us"
PODCAST_CATEGORY = "Kids & Family"
PODCAST_EMAIL = "ryanngupta@gmail.com"
# Show artwork (must exist under website/ and be uploaded to S3 with the site)
PODCAST_ARTWORK_BASENAME = "NewzyxV2-Podcast.jpg"
PODCAST_PHONE = "312-709-5982"
PODCAST_WEBSITE = "https://newzyx.com"
CLOUDFRONT_URL = config.WEBSITE_URL.rstrip("/")

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
SYNDICATION_NS = "http://purl.org/rss/1.0/modules/syndication/"


def _file_size(filepath):
    return os.path.getsize(filepath) if os.path.exists(filepath) else 0


def _duration_estimate(filepath):
    try:
        from mutagen.mp3 import MP3
        audio = MP3(filepath)
        s = int(audio.info.length)
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    except Exception:
        mb = _file_size(filepath) / (1024 * 1024)
        return f"00:{int(mb):02d}:00"


def create_feed(feed_path="website/feed.xml"):
    nsmap = {"itunes": ITUNES_NS, "sy": SYNDICATION_NS}
    rss = etree.Element("rss", version="2.0", nsmap=nsmap)
    rss.set(f"{{{ITUNES_NS}}}version", "2.0")
    ch = etree.SubElement(rss, "channel")

    etree.SubElement(ch, "title").text = PODCAST_TITLE
    etree.SubElement(ch, "link").text = PODCAST_WEBSITE
    etree.SubElement(ch, "description").text = PODCAST_DESCRIPTION
    etree.SubElement(ch, "language").text = PODCAST_LANGUAGE
    etree.SubElement(ch, "lastBuildDate").text = formatdate(usegmt=True)
    etree.SubElement(ch, "pubDate").text = formatdate(usegmt=True)
    etree.SubElement(ch, "generator").text = "Newzyx RSS Generator"
    etree.SubElement(ch, f"{{{SYNDICATION_NS}}}updatePeriod").text = "daily"
    etree.SubElement(ch, f"{{{SYNDICATION_NS}}}updateFrequency").text = "1"
    etree.SubElement(ch, f"{{{ITUNES_NS}}}author").text = PODCAST_AUTHOR
    etree.SubElement(ch, f"{{{ITUNES_NS}}}summary").text = PODCAST_DESCRIPTION
    etree.SubElement(ch, f"{{{ITUNES_NS}}}explicit").text = "no"
    etree.SubElement(ch, f"{{{ITUNES_NS}}}type").text = "episodic"
    _art = quote(PODCAST_ARTWORK_BASENAME, safe="")
    etree.SubElement(
        ch, f"{{{ITUNES_NS}}}image", href=f"{CLOUDFRONT_URL}/{_art}"
    )
    cat = etree.SubElement(ch, f"{{{ITUNES_NS}}}category")
    cat.set("text", PODCAST_CATEGORY)
    owner = etree.SubElement(ch, f"{{{ITUNES_NS}}}owner")
    etree.SubElement(owner, f"{{{ITUNES_NS}}}name").text = PODCAST_AUTHOR
    etree.SubElement(owner, f"{{{ITUNES_NS}}}email").text = PODCAST_EMAIL
    etree.SubElement(ch, "managingEditor").text = (
        f"{PODCAST_EMAIL} (phone: {PODCAST_PHONE})"
    )

    tree = etree.ElementTree(rss)
    tree.write(str(feed_path), encoding="utf-8", xml_declaration=True, pretty_print=True)
    print(f"  Created RSS feed: {feed_path}")


def add_episode(feed_path="website/feed.xml", date_str=None, mp3_path=None,
                episode_title=None, episode_description="Today's top stories for kids!",
                episode_date=None, t=0):
    feed_path = Path(feed_path)
    if not feed_path.exists():
        create_feed(feed_path)

    tree = etree.parse(str(feed_path))
    channel = tree.getroot().find("channel")

    if episode_date is None:
        episode_date = datetime.now() - timedelta(days=t)
    if date_str is None:
        date_str = utils.ymd(t)
    if mp3_path is None:
        mp3_path = f"website/episodes/{date_str}/{date_str}.mp3"

    mp3_s3_key = f"episodes/{date_str}/{date_str}.mp3"
    episode_url = f"{CLOUDFRONT_URL}/{mp3_s3_key}"

    if episode_title is None:
        episode_title = f"Daily Kids News \u2013 {episode_date.strftime('%b %d, %Y')}"

    length = _file_size(mp3_path)
    if length == 0:
        length = 10 * 1024 * 1024
    duration = _duration_estimate(mp3_path)
    guid_str = str(uuid5(NAMESPACE_URL, episode_url))

    for item in channel.findall("item"):
        g = item.find("guid")
        if g is not None and g.text == guid_str:
            channel.remove(item)
            break

    item = etree.SubElement(channel, "item")
    etree.SubElement(item, "title").text = episode_title
    etree.SubElement(item, "description").text = episode_description
    etree.SubElement(item, "pubDate").text = formatdate(episode_date.timestamp(), usegmt=True)
    guid = etree.SubElement(item, "guid")
    guid.text = guid_str
    guid.set("isPermaLink", "false")
    enc = etree.SubElement(item, "enclosure")
    enc.set("url", episode_url)
    enc.set("length", str(length))
    enc.set("type", "audio/mpeg")
    etree.SubElement(item, f"{{{ITUNES_NS}}}title").text = episode_title
    etree.SubElement(item, f"{{{ITUNES_NS}}}summary").text = episode_description
    etree.SubElement(item, f"{{{ITUNES_NS}}}duration").text = duration
    etree.SubElement(item, f"{{{ITUNES_NS}}}explicit").text = "no"
    etree.SubElement(item, f"{{{ITUNES_NS}}}episodeType").text = "full"

    lb = channel.find("lastBuildDate")
    if lb is not None:
        lb.text = formatdate(usegmt=True)

    tree.write(str(feed_path), encoding="utf-8", xml_declaration=True, pretty_print=True)
    return mp3_s3_key


def refresh_feed_channel_metadata(feed_path):
    """Keep channel URLs in sync when merging with an existing feed."""
    fp = Path(feed_path)
    if not fp.exists():
        return
    tree = etree.parse(str(fp))
    ch = tree.getroot().find("channel")
    if ch is None:
        return
    im = ch.find(f"{{{ITUNES_NS}}}image")
    if im is not None:
        _art = quote(PODCAST_ARTWORK_BASENAME, safe="")
        im.set("href", f"{CLOUDFRONT_URL}/{_art}")
    lk = ch.find("link")
    if lk is not None:
        lk.text = PODCAST_WEBSITE
    tree.write(
        str(fp),
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=True,
    )


def incremental_append_current_episode(feed_path, mp3_abs_path, t=0):
    """
    Fetch canonical feed.xml from S3 if present, then append this episode only.
    Suitable for ephemeral workspaces without a full local episodes/ history.
    """
    from pipeline import upload as upload_mod

    fp = Path(feed_path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    ok = upload_mod.download_object_if_exists("feed.xml", str(fp))
    if not ok or not fp.exists() or fp.stat().st_size == 0:
        create_feed(str(fp))
    refresh_feed_channel_metadata(str(fp))
    episode_date = datetime.now() - timedelta(days=t)
    date_str = utils.ymd(t)
    add_episode(
        feed_path=str(fp),
        date_str=date_str,
        mp3_path=mp3_abs_path,
        episode_date=episode_date,
        t=t,
    )


def update_all_episodes(feed_path="website/feed.xml", max_episodes=500):
    episodes_dir = Path(workspace.generated_website_dir()) / "episodes"
    if not episodes_dir.exists():
        print("  website/episodes/ directory not found")
        return

    ep_dirs = sorted(
        [d for d in episodes_dir.iterdir() if d.is_dir()],
        key=lambda x: x.name,
        reverse=True,
    )

    if not ep_dirs:
        print("  No episode folders found")
        return

    if not Path(feed_path).exists():
        create_feed(feed_path)
    else:
        refresh_feed_channel_metadata(feed_path)

    count = 0
    for ep_dir in ep_dirs[:max_episodes]:
        date_str = ep_dir.name
        mp3_path = ep_dir / f"{date_str}.mp3"
        if not mp3_path.exists():
            continue

        try:
            ep_date = datetime.strptime(date_str, "%Y-%m-%d")
            t = (datetime.now() - ep_date).days
        except ValueError:
            continue

        add_episode(
            feed_path=feed_path,
            date_str=date_str,
            mp3_path=str(mp3_path),
            episode_date=ep_date,
            t=t,
        )
        count += 1

    print(f"  RSS feed updated with {count} episodes")
