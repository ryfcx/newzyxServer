import json
import re
import shutil
import os
from openai import OpenAI
from newzyx import config, utils, workspace
from pipeline import db

# Literal markers used to split the polished script into separate TTS segments
# (stitched with real silence / music beds by pydub in pipeline/tts.py).
HOST_NAME = "Zara"
TOPIC_SPLIT_MARKER = "@@TOPIC@@"  # between individual news stories
BRIDGE_SPLIT_MARKER = "@@BRIDGE@@"  # before the quiz lead-in
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
    print(f"  Selected {len(ep)} articles for episode ({news_date or 'recent window'}):")
    for a in ep:
        dt = a["news_dt"] or a["collect_dt"] or "?"
        print(f"    [{a['score']}] {dt} {a['source']}/{a['topic']}: {a['title'][:60]}")
    return ep


def _polish_segment(text, kind="story"):
    """Polish one story or the quiz lead-in. Markers are never sent to the LLM."""
    if not text or not text.strip():
        return text
    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        model = config.OPENAI_MODEL
        if kind == "bridge":
            task = (
                "Polish this short quiz lead-in for a kids news podcast.\n"
                "- Keep it to one or two spoken sentences.\n"
                "- Do not say goodbye, wrap the show, or invent quiz questions.\n"
                "- Return ONLY the polished lead-in."
            )
        else:
            task = (
                "Polish this SINGLE news-story segment for a kids podcast.\n"
                "- Keep the same facts. Do not add extra information.\n"
                "- This is one story only. Do not merge in other news, a quiz, or an outro.\n"
                "- Do not greet the audience or say a host name.\n"
                "- Write for spoken TTS: about 12-18 word sentences, no ellipsis, no em dashes, "
                "at most one exclamation mark.\n"
                "- Keep roughly the same length as the original.\n"
                "- Return ONLY the polished story text."
            )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are {HOST_NAME}, a high-energy morning news host for kids aged 12-16. "
                        "You're excited, curious, and upbeat, but you speak in a natural conversational "
                        "flow like a real radio host. Keep it clear and factual. "
                        f"Do NOT say your name ({HOST_NAME})."
                    ),
                },
                {"role": "user", "content": f"{task}\n\n{text}"},
            ],
        )
        polished = (response.choices[0].message.content or "").strip()
        return polished or text
    except Exception as e:
        print(f"  Script polish failed ({e}), using raw {kind}")
        return text


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


_DAY_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
    20: "twenty",
    21: "twenty one",
    22: "twenty two",
    23: "twenty three",
    24: "twenty four",
    25: "twenty five",
    26: "twenty six",
    27: "twenty seven",
    28: "twenty eight",
    29: "twenty nine",
    30: "thirty",
    31: "thirty one",
}


def _two_digit_words(n):
    """Speak 0-99 without depending on the day-of-month table."""
    if n in _DAY_WORDS:
        return _DAY_WORDS[n]
    if n < 0:
        return str(n)
    tens, ones = divmod(n, 10)
    tens_names = {
        2: "twenty",
        3: "thirty",
        4: "forty",
        5: "fifty",
        6: "sixty",
        7: "seventy",
        8: "eighty",
        9: "ninety",
    }
    if ones == 0:
        return tens_names.get(tens, str(n))
    return f"{tens_names.get(tens, str(tens * 10))} {_DAY_WORDS.get(ones, ones)}"


def _spoken_year(year):
    """Spell years for TTS so models don't slur digit clusters like 2026."""
    if 2000 <= year <= 2099:
        ones = year % 100
        if ones == 0:
            return "two thousand"
        spoken = _two_digit_words(ones)
        if ones < 10:
            return f"two thousand {spoken}"
        return f"twenty {spoken}"
    return str(year)


def _spoken_date(t=0):
    """Plain spoken date — avoid ordinals and extra commas that create odd pauses."""
    from datetime import datetime, timedelta

    d = datetime.now() - timedelta(days=t)
    weekday = d.strftime("%A")
    month = d.strftime("%B")
    day = _DAY_WORDS.get(d.day, str(d.day))
    year = _spoken_year(d.year)
    # One comma only: fewer mid-date pauses from the TTS engine.
    return f"{weekday} {month} {day}, {year}"


def _canonical_intro_parts(t=0):
    """
    Fixed daily open (kept out of polish). Spoken as one continuous TTS clip.
    """
    return {
        "greeting": "Good morning, and welcome to Newzyx!",
        "name": f"I'm {HOST_NAME}.",
        "date": (
            f"Today is {_spoken_date(t)}, so let's dive into the news."
            if t == 0
            else f"This edition is for {_spoken_date(t)}. Let's dive into the news."
        ),
    }


def _canonical_intro(t=0):
    parts = _canonical_intro_parts(t)
    return f"{parts['greeting']} {parts['name']} {parts['date']}"


def _default_bridge():
    return "Those are today's top stories, and it's quick quiz time. Let's see what you remember."


def _canonical_outro():
    """
    Fixed closing after the quiz — never polished, so the episode doesn't end
    on the last answer. Name is only spoken in the intro.
    """
    return (
        "That's a wrap on today's Newzyx. "
        "Thanks for listening, stay curious, and catch you on the next one."
    )


def _strip_host_name(text):
    """Remove host self-intros so the name is only heard in the fixed daily open."""
    if not text:
        return text
    text = re.sub(
        rf"\b(?:I'm|I am)\s+{re.escape(HOST_NAME)}\b[,.]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"\b(?:this is|it's)\s+{re.escape(HOST_NAME)}\b[,.]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(rf"\b{re.escape(HOST_NAME)}\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return text.strip()


def _generate_quiz_from_topics(topics):
    """
    Build quiz Q&A from the final spoken story text only.

    Avoids asking about article details that never made it into the podcast script.
    """
    if not topics:
        return []
    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        model = config.OPENAI_MODEL
        numbered = "\n\n".join(
            f"STORY {i}:\n{topic}" for i, topic in enumerate(topics, 1)
        )
        schema = {
            "name": "episode_quiz",
            "schema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "minItems": len(topics),
                        "maxItems": len(topics),
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string"},
                                "answer": {"type": "string"},
                            },
                            "required": ["question", "answer"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["items"],
                "additionalProperties": False,
            },
            "strict": True,
        }
        prompt = f"""
You write the end-of-episode quiz for a kids news podcast.

For EACH story below, write exactly one question and one short answer.
CRITICAL rules:
- Use ONLY facts explicitly stated in that story's text.
- A listener who heard only the podcast must be able to answer.
- Do NOT use outside knowledge or details that are not in the story text.
- Prefer concrete names, numbers, places, or clear facts that were actually spoken.
- Keep questions kid-friendly and answers short (a few words to one short sentence).
- Return exactly {len(topics)} items, in the same order as the stories.

{numbered}
"""
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You create fair podcast quizzes. Every answer must be findable "
                        "in the provided story text alone."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_schema", "json_schema": schema},
        )
        data = json.loads(response.choices[0].message.content)
        items = data.get("items") or []
        pairs = []
        for item in items:
            q = (item.get("question") or "").strip()
            a = (item.get("answer") or "").strip()
            if q and a:
                pairs.append((q, a))
        if len(pairs) != len(topics):
            print(
                f"  Warning: quiz regen returned {len(pairs)} items for {len(topics)} stories"
            )
        return pairs
    except Exception as e:
        print(f"  Quiz regen from spoken script failed ({e})")
        return []


def _clean_spoken(text):
    text = utils.cleanupTxt(text or "")
    return _strip_host_name(text.strip())


def create_script(fname, ep, t=0):
    # Intro/outro stay out of polish so the host name and closing can't get dropped.
    intro_parts = _canonical_intro_parts(t)
    intro = _canonical_intro(t)
    outro = _canonical_outro()
    news_parts = [a["pod_script"] for a in ep if a["pod_script"]]

    # Polish each story on its own. Sending the whole episode through one LLM
    # call used to delete @@TOPIC@@ markers, merging 6 stories into 1 and
    # producing a single quiz question.
    topics = []
    for part in news_parts:
        polished = _clean_spoken(_polish_segment(part, kind="story"))
        if polished:
            topics.append(polished)

    if len(topics) != len(news_parts):
        print(
            f"  Warning: polish dropped stories ({len(topics)}/{len(news_parts)}), "
            "using original segments"
        )
        topics = [t for t in (_clean_spoken(p) for p in news_parts) if t]

    topic_cues = (
        "Next up,",
        "Our next story,",
        "Switching gears,",
        "Also today,",
        "One more for you,",
        "And finally,",
    )
    cue_re = re.compile(
        r"^(next up|our next story|switching gears|also today|one more for you|"
        r"and finally|in other news|speaking of)\b",
        re.IGNORECASE,
    )
    for i in range(1, len(topics)):
        if not cue_re.match(topics[i]):
            topics[i] = f"{topic_cues[(i - 1) % len(topic_cues)]} {topics[i]}"

    bridge = _clean_spoken(_polish_segment(_default_bridge(), kind="bridge"))
    bridge = bridge or _default_bridge()

    qa_pairs = _generate_quiz_from_topics(topics)
    fallback = [
        (
            _strip_host_name(a["pod_question"]) or a["pod_question"],
            _strip_host_name(a["pod_answer"]) or a["pod_answer"],
        )
        for a in ep
        if a["pod_question"] and a["pod_answer"]
    ]
    if not qa_pairs:
        print("  Warning: using original article quiz as fallback")
        qa_pairs = fallback
    elif len(qa_pairs) < len(topics) and fallback:
        print("  Warning: padding quiz from original article questions")
        for item in fallback:
            if len(qa_pairs) >= len(topics):
                break
            if item not in qa_pairs:
                qa_pairs.append(item)

    script_parts = {
        "intro": intro,
        "intro_parts": intro_parts,
        "topics": topics,
        "bridge": bridge,
        "qa_pairs": qa_pairs,
        "outro": outro,
    }

    with open(fname, "w", encoding="utf-8") as f:
        f.write(intro + "\n\n")
        for i, topic in enumerate(topics, 1):
            f.write(f"--- Story {i} ---\n{topic}\n\n")
        f.write(f"--- Bridge ---\n{bridge}\n")
        if qa_pairs:
            f.write("\n--- Quiz Section ---\n\n")
            for q, a in qa_pairs:
                f.write(f"Q: {q}\nA: {a}\n\n")
        f.write(f"--- Outro ---\n{outro}\n")

    date_str = utils.ymd(t)
    ep_dir = os.path.join(workspace.generated_website_dir(), "episodes", date_str)
    os.makedirs(ep_dir, exist_ok=True)
    shutil.copy(fname, os.path.join(ep_dir, "script.txt"))

    total_words = (
        len(intro.split())
        + sum(len(s.split()) for s in topics)
        + len(bridge.split())
        + sum(len(q.split()) + len(a.split()) for q, a in qa_pairs)
        + len(outro.split())
    )
    print(
        f"  Script saved ({total_words} words, host={HOST_NAME}, "
        f"stories={len(topics)}, quiz={len(qa_pairs)})"
    )
    return script_parts


def create_site(ep, t=0):
    web_dir = workspace.generated_website_dir()
    # Switch layouts without deleting the old one:
    #   NEWZYX_SITE_TEMPLATE=template.html      (classic)
    #   NEWZYX_SITE_TEMPLATE=template_news.html (Newsdesk)
    template_name = os.environ.get("NEWZYX_SITE_TEMPLATE", "template.html").strip() or "template.html"
    template_path = os.path.join(workspace.project_website_dir(), template_name)
    if not os.path.isfile(template_path):
        print(f"  Warning: template {template_name!r} missing, falling back to template.html")
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
        latest_path = os.path.join(web_dir, "latest.json")
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump({"date": curr_dt}, f)
        generated.append("latest.json")

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
