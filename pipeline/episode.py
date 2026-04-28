import json
import shutil
import os
from openai import OpenAI
from newzyx import config, utils, workspace
from pipeline import db


def select_articles(news_date=None):
    ep = db.select_episode(
        min_score=90,
        max_age_days=3,
        target=6,
        min_articles=4,
        news_date=news_date,
    )
    if not ep:
        if news_date:
            print(
                f"  Not enough articles scoring 80+ with news date {news_date} — "
                "check collect sources for that day or run again with more variety"
            )
        else:
            print(
                "  Not enough articles scoring 90+ in the last few days — collect more or rerun process"
            )
        return []
    print(f"  Selected {len(ep)} articles for episode:")
    for a in ep:
        print(f"    [{a['score']}] {a['source']}/{a['topic']}: {a['title'][:60]}")
    return ep


def _fix_script_flow(script):
    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        model = config.OPENAI_MODEL

        prompt = f"""
CRITICAL:
- Make sure the script is 600-700 words long to make a 5 minute podcast.
- Try your best to keep the original script content and do not add any extra information.
- This script will be fed to elevenlabs text-to-speech
- Make the script flow well, remove any redundant Hello and Hi
- Remove any greetings in the middle of the script.
- Remove any duplicate news items both from the news details as well as related Q&A in the end.
- Keep the [break] and [excited] tags and '...' markers as-is, just remove the extra greetings in the middle.
- If every new story starts with 'did you know' or 'imagine' or 'hey kids', feel free to add variety to start of these stories.
- Add a brief inspiring ending, with something thought-provoking, not just "bye"

{script} """

        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an engaging podcast host for kids aged 12-16. You're enthusiastic, relatable, and treat your audience as intelligent people who deserve real news delivered in an exciting way. Think: charismatic teacher meets YouTube personality - informative but fun.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  Script polish failed ({e}), using raw script")
        return script


def create_script(fname, ep, t=0):
    tag1 = " [silence] "
    tag2 = " [excited] "

    intro = f"Welcome to newzyx podcast for kids. Today is {utils.ymd(t, '%B %d, %Y')}..."
    bridge = " This is all for today and lets jump to our review section..."

    news_parts = [a["pod_script"] for a in ep]
    qa_parts = [a["pod_question"] + tag1 + tag2 + a["pod_answer"] for a in ep]

    script = intro + tag1 + f"{tag1} ".join(news_parts) + tag1 + bridge + tag1 + f"{tag1} ".join(qa_parts) + tag1

    script = _fix_script_flow(script)
    script = utils.cleanupTxt(script)

    with open(fname, "w", encoding="utf-8") as f:
        f.write(script)

    date_str = utils.ymd(t)
    ep_dir = os.path.join(workspace.generated_website_dir(), "episodes", date_str)
    os.makedirs(ep_dir, exist_ok=True)
    shutil.copy(fname, os.path.join(ep_dir, "script.txt"))

    print(f"  Script saved ({len(script.split())} words)")


def create_site(ep, t=0):
    web_dir = workspace.generated_website_dir()
    template_path = os.path.join(workspace.project_website_dir(), "template.html")

    articles_json = json.dumps(
        [
            {
                "title": a["title"],
                "summary": a["summary"],
                "score": a["score"],
                "dt": a["news_dt"] or a["collect_dt"] or "",
                "source": a["source"] or "",
                "url": a["url"],
            }
            for a in ep
        ],
        ensure_ascii=False,
    )

    data_tag = f'<script id="articleData" type="application/json">{articles_json}</script>\n'

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    curr_dt = utils.ymd(t)
    curr_dt2 = utils.ymd(t, "%b %-d, %Y")

    episode_html = data_tag + template
    episode_html = episode_html.replace('data-episode=""', f'data-episode="{curr_dt}"')
    episode_html = episode_html.replace("today.mp3", curr_dt + ".mp3")
    episode_html = episode_html.replace("</title>", curr_dt2 + "</title>")
    episode_html = episode_html.replace('src="NewzyxV2-removebg.png"', 'src="../../NewzyxV2-removebg.png"')
    episode_html = episode_html.replace('href="NewzyxV2Favicon.ico"', 'href="../../NewzyxV2Favicon.ico"')
    episode_html = episode_html.replace('href="index.html"', 'href="../../index.html"')
    episode_html = episode_html.replace(
        "window.location.href = dtInput.value + '.html'",
        "window.location.href = '../' + dtInput.value + '/' + dtInput.value + '.html'"
    )

    ep_dir = os.path.join(web_dir, "episodes", curr_dt)
    os.makedirs(ep_dir, exist_ok=True)
    dated_path = os.path.join(ep_dir, curr_dt + ".html")
    with open(dated_path, "w", encoding="utf-8") as f:
        f.write(episode_html)

    generated = [os.path.join("episodes", curr_dt, curr_dt + ".html")]

    if t == 0:
        index_html = data_tag + template
        index_html = index_html.replace('data-episode=""', f'data-episode="{curr_dt}"')
        # Use dated episode file so homepage audio always matches articleData (today.mp3 can be stale/cached).
        index_html = index_html.replace(
            "today.mp3", f"episodes/{curr_dt}/{curr_dt}.mp3"
        )
        index_html = index_html.replace("</title>", curr_dt2 + "</title>")
        index_html = index_html.replace(
            "window.location.href = dtInput.value + '.html'",
            "window.location.href = 'episodes/' + dtInput.value + '/' + dtInput.value + '.html'"
        )
        index_path = os.path.join(web_dir, "index.html")
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(index_html)
        generated.append("index.html")

    _write_summary(ep, curr_dt, ep_dir)
    print(f"  Site generated: {', '.join(generated)}")
    return generated


def _write_summary(ep, date_str, ep_dir):
    text = ""
    for i, a in enumerate(ep):
        text += f"{i + 1}. <B>{a['title']}</B>\n{a['summary']}\n<a href='{a['url']}'>{a['source']}</a>, Relevance: {a['score']}\n\n"
    fname = os.path.join(ep_dir, date_str + "_summary.txt")
    with open(fname, "w", encoding="utf-8") as f:
        f.write(text)
