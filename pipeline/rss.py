from email.utils import formatdate
from uuid import uuid5, NAMESPACE_URL
from urllib.parse import quote
from lxml import etree
from pathlib import Path
from datetime import datetime, timedelta
import os
from newzyx import config, utils, workspace
from pipeline import episode as episode_mod

PODCAST_TITLE = "Daily News Podcast for Kids - Newzyx"
PODCAST_DESCRIPTION = (
    "Looking for a fun way to stay on top of what's happening in the world? "
    "Newzyx is the ultimate daily news podcast designed just for teens and tweens aged 10-16! "
    "In quick, bite-sized episodes (6-7 minutes each), everyday we bring you the biggest stories "
    "from around the globe, explained clearly, fairly, and without the confusing jargon. "
    "From breakthroughs in science and technology to space exploration, the environment, health, "
    "business, sports, and world events, we help you understand not just what happened, but why it matters. "
    "Our goal is simple: to help the next generation become informed, thoughtful, and curious citizens of the world. "
    "Whether you're listening on the way to school, over breakfast, or with your family, we're glad you're here."
)
PODCAST_AUTHOR = "Ryan"
PODCAST_LANGUAGE = "en-us"
# Apple Podcasts: "Education for Kids" is a subcategory under "Kids & Family".
PODCAST_CATEGORY = "Kids & Family"
PODCAST_SUBCATEGORY = "Education for Kids"
PODCAST_EMAIL = "ryanngupta@gmail.com"
# Show artwork (must exist under website/ and be uploaded to S3 with the site)
PODCAST_ARTWORK_BASENAME = "NewzyxV2-Podcast.jpg"
PODCAST_PHONE = "312-709-5982"
PODCAST_WEBSITE = "https://newzyx.com"
CLOUDFRONT_URL = config.WEBSITE_URL.rstrip("/")

# Shown in RSS/itunes:duration only (does not change TTS or MP3 length).
RSS_EPISODE_DURATION = "00:06:30"
PODCAST_UPDATE_FREQUENCY_LABEL = "Daily"
PODCAST_UPDATE_RRULE = "FREQ=DAILY"

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
SYNDICATION_NS = "http://purl.org/rss/1.0/modules/syndication/"
PODCAST_NS = "https://podcastindex.org/namespace/1.0"


def _file_size(filepath):
    return os.path.getsize(filepath) if os.path.exists(filepath) else 0


def _set_itunes_categories(channel):
    """Apple-valid nested category: Kids & Family > Education for Kids."""
    for old in channel.findall(f"{{{ITUNES_NS}}}category"):
        channel.remove(old)
    cat = etree.SubElement(channel, f"{{{ITUNES_NS}}}category")
    cat.set("text", PODCAST_CATEGORY)
    sub = etree.SubElement(cat, f"{{{ITUNES_NS}}}category")
    sub.set("text", PODCAST_SUBCATEGORY)


def _set_or_update_text(parent, tag, text):
    el = parent.find(tag)
    if el is None:
        el = etree.SubElement(parent, tag)
    el.text = text


def _set_update_schedule(channel):
    """RSS hints for daily release (syndication + Podcasting 2.0)."""
    _set_or_update_text(channel, f"{{{SYNDICATION_NS}}}updatePeriod", "daily")
    _set_or_update_text(channel, f"{{{SYNDICATION_NS}}}updateFrequency", "1")
    for old in channel.findall(f"{{{PODCAST_NS}}}updateFrequency"):
        channel.remove(old)
    freq = etree.SubElement(channel, f"{{{PODCAST_NS}}}updateFrequency")
    freq.set("rrule", PODCAST_UPDATE_RRULE)
    freq.text = PODCAST_UPDATE_FREQUENCY_LABEL


def _sync_episode_durations(channel):
    """Refresh itunes:duration on all items for consistent Apple display."""
    for item in channel.findall("item"):
        dur = item.find(f"{{{ITUNES_NS}}}duration")
        if dur is None:
            dur = etree.SubElement(item, f"{{{ITUNES_NS}}}duration")
        dur.text = RSS_EPISODE_DURATION


def create_feed(feed_path="website/feed.xml"):
    nsmap = {"itunes": ITUNES_NS, "sy": SYNDICATION_NS, "podcast": PODCAST_NS}
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
    _set_update_schedule(ch)
    etree.SubElement(ch, f"{{{ITUNES_NS}}}author").text = PODCAST_AUTHOR
    etree.SubElement(ch, f"{{{ITUNES_NS}}}summary").text = PODCAST_DESCRIPTION
    etree.SubElement(ch, f"{{{ITUNES_NS}}}explicit").text = "no"
    etree.SubElement(ch, f"{{{ITUNES_NS}}}type").text = "episodic"
    _art = quote(PODCAST_ARTWORK_BASENAME, safe="")
    etree.SubElement(
        ch, f"{{{ITUNES_NS}}}image", href=f"{CLOUDFRONT_URL}/{_art}"
    )
    _set_itunes_categories(ch)
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
                episode_title=None, episode_description=None,
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
    if episode_description is None:
        episode_description = "Daily news for kids."

    length = _file_size(mp3_path)
    if length == 0:
        length = 10 * 1024 * 1024
    duration = RSS_EPISODE_DURATION
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
    """Keep channel metadata in sync when merging with an existing feed."""
    fp = Path(feed_path)
    if not fp.exists():
        return
    tree = etree.parse(str(fp))
    ch = tree.getroot().find("channel")
    if ch is None:
        return
    desc = ch.find("description")
    if desc is not None:
        desc.text = PODCAST_DESCRIPTION
    summary = ch.find(f"{{{ITUNES_NS}}}summary")
    if summary is not None:
        summary.text = PODCAST_DESCRIPTION
    _set_itunes_categories(ch)
    _set_update_schedule(ch)
    im = ch.find(f"{{{ITUNES_NS}}}image")
    if im is not None:
        _art = quote(PODCAST_ARTWORK_BASENAME, safe="")
        im.set("href", f"{CLOUDFRONT_URL}/{_art}")
    lk = ch.find("link")
    if lk is not None:
        lk.text = PODCAST_WEBSITE
    _sync_episode_durations(ch)
    tree.write(
        str(fp),
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=True,
    )


def incremental_append_current_episode(feed_path, mp3_abs_path, t=0, articles=None):
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
    episode_description = episode_mod.build_episode_description(articles or [])
    add_episode(
        feed_path=str(fp),
        date_str=date_str,
        mp3_path=mp3_abs_path,
        episode_description=episode_description,
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

        articles = episode_mod.load_episode_articles(str(ep_dir), date_str)
        add_episode(
            feed_path=feed_path,
            date_str=date_str,
            mp3_path=str(mp3_path),
            episode_description=episode_mod.build_episode_description(articles),
            episode_date=ep_date,
            t=t,
        )
        count += 1

    print(f"  RSS feed updated with {count} episodes")
