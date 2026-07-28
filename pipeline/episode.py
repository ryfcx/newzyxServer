import json
import re
import shutil
import os
from openai import OpenAI
from newzyx import config, utils, workspace
from pipeline import db

# Literal markers used to split the polished script into separate TTS segments
# (stitched with real silence gaps by pydub in pipeline/tts.py, instead of
# relying on inline tags the TTS engine doesn't reliably honor).
QA_SPLIT_MARKER = "@@QASPLIT@@"  # between the news portion and the quiz portion
QA_Q_MARKER = "@@Q@@"  # before each quiz question
QA_A_MARKER = "@@A@@"  # before each quiz answer


def build_episode_description(articles):
    """RSS/Apple episode blurb listing the stories covered."""
    titles = [a["title"].strip() for a in articles if a["title"]]
    if not titles:
        return "Daily news for kids."
    if len(titles) == 1:
        return f"In this episode: {titles[0]}"
    return "In this episode: " + "; ".join(titles)


def load_episode_articles(ep_dir, date_str):
    """Read article titles from a generated episode page (for RSS backfill)."""
    html_path = os.path.join(ep_dir, f"{date_str}.html")
    if not os.path.exists(html_path):
        return []
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.search(
        r'<script id="articleData" type="application/json">(.*?)</script>',
        content,
        re.DOTALL,
    )
    if not match:
        return []
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return []


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
- Use natural, varied transitions between stories (e.g. "Not everyone is cheering, however...", "Speaking of big moments...", "In other news..."). Light, fitting wit or wordplay tied to the story is welcome, but stay factual and clear.
- Add a brief, warm, inspiring ending with something thought-provoking — NOT just "bye".
- CRITICAL: Do NOT promise a specific next episode time or date in the ending (no "see you tomorrow", "join us next week", "back on Monday", etc.). Keep the sign-off general and evergreen.
- CRITICAL: The script contains a literal marker "{QA_SPLIT_MARKER}" separating the news portion from the quiz portion. Keep this marker exactly as-is, in the same position, with no spaces added inside it and nothing else changed about it — it is used programmatically to split the audio.
- CRITICAL: In the quiz portion, every question is preceded by the literal marker "{QA_Q_MARKER}" and every answer is preceded by the literal marker "{QA_A_MARKER}". Keep every occurrence of both markers exactly as-is, immediately before their question/answer, in the same order — they are used programmatically to insert a real pause between each question and its answer so listeners have time to answer.

{script} """

        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are Zara, a warm and engaging podcast host for kids aged 12-16, in the style of a friendly morning news anchor. You're enthusiastic, relatable, and treat your audience as intelligent people who deserve real news delivered in an exciting way. Think: charismatic teacher meets YouTube personality - informative but fun, with a warm, welcoming delivery and light personality between stories.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  Script polish failed ({e}), using raw script")
        return script


def _parse_qa_pairs(qa_raw):
    """Split marker-delimited quiz text into an ordered list of (question, answer) tuples."""
    tokens = re.split(f"({re.escape(QA_Q_MARKER)}|{re.escape(QA_A_MARKER)})", qa_raw)
    pairs = []
    current_q = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == QA_Q_MARKER and i + 1 < len(tokens):
            current_q = tokens[i + 1].strip()
            i += 2
        elif tok == QA_A_MARKER and i + 1 < len(tokens):
            answer = tokens[i + 1].strip()
            if current_q:
                pairs.append((current_q, answer))
            current_q = None
            i += 2
        else:
            i += 1
    return pairs


def create_script(fname, ep, t=0):
    tag1 = " [silence] "
    tag2 = " [excited] "

    intro = (
        f"Good morning, and welcome to Newzyx! I'm Zara. "
        f"Today is {utils.ymd(t, '%A, %B %d, %Y')}..."
    )
    bridge = " That wraps up today's top stories. Now let's see what you remember with today's quiz..."

    news_parts = [a["pod_script"] for a in ep]
    qa_parts = [
        QA_Q_MARKER + a["pod_question"] + tag1 + QA_A_MARKER + tag2 + a["pod_answer"]
        for a in ep
    ]

    news_script = intro + tag1 + f"{tag1} ".join(news_parts) + tag1 + bridge
    qa_script = f"{tag1} ".join(qa_parts) + tag1

    script = news_script + tag1 + QA_SPLIT_MARKER + tag1 + qa_script

    script = _fix_script_flow(script)
    script = utils.cleanupTxt(script)

    if QA_SPLIT_MARKER in script:
        news_text, qa_raw = script.split(QA_SPLIT_MARKER, 1)
    else:
        # Polish step dropped the marker; fall back to one continuous segment.
        print("  Warning: QA split marker missing after polish, using single audio segment")
        news_text, qa_raw = script, ""

    news_text = news_text.strip()
    qa_pairs = _parse_qa_pairs(qa_raw)
    if qa_raw.strip() and not qa_pairs:
        print("  Warning: Q/A markers missing after polish, quiz section will have no answer pause")

    with open(fname, "w", encoding="utf-8") as f:
        f.write(news_text)
        if qa_pairs:
            f.write("\n\n--- Quiz Section ---\n\n")
            for q, a in qa_pairs:
                f.write(f"Q: {q}\nA: {a}\n\n")
        elif qa_raw.strip():
            f.write("\n\n--- Quiz Section ---\n\n")
            f.write(qa_raw.strip())

    date_str = utils.ymd(t)
    ep_dir = os.path.join(workspace.generated_website_dir(), "episodes", date_str)
    os.makedirs(ep_dir, exist_ok=True)
    shutil.copy(fname, os.path.join(ep_dir, "script.txt"))

    total_words = len(news_text.split()) + sum(len(q.split()) + len(a.split()) for q, a in qa_pairs)
    print(f"  Script saved ({total_words} words)")
    return news_text, qa_pairs


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
