import json
import requests
import time
from newzyx import config, utils
from pipeline import db

JSON_SCHEMA = {
    "name": "article_analysis",
    "schema": {
        "type": "object",
        "properties": {
            "score": {"type": "number"},
            "summary": {"type": "string"},
            "podScript": {"type": "string"},
            "podQuestion": {"type": "string"},
            "podAnswer": {"type": "string"},
        },
        "required": ["score", "summary", "podScript", "podQuestion", "podAnswer"],
        "additionalProperties": False,
    },
    "strict": True,
}

PROMPT_TEMPLATE = """
You are a strict news curator for a daily kids podcast (ages 12-16).

IMPORTANT: The show ONLY publishes stories scored 90 or higher. Use the full 0-100 range so we can filter:
  - Typical feed filler, minor updates, niche beats → 35-65
  - Decent but not episode-worthy → 66-79
  - Strong kids angle, clear educational or "wow" value → 80-89
  - Episode-worthy: would excite a curious 13-year-old, classroom-worthy, memorable → 90-96
  - Exceptional: major global/science/sports moment, rare discovery, viral human-interest → 97-100

Within 90-96, spread scores (do not give every good story a 92). Use decimals mentally then round to integer.

HARD SCORE CAPS (override everything above):
  - Product launch, review, deal, "best of" list → max 35
  - Political process/legislation horse-race (not outcome kids care about) → max 55
  - Stock prices, corporate earnings, banking → max 25
  - Niche league/team coverage (not final/championship/record) → max 65
  - Opinion/editorial or pure punditry → max 45

ASK: "Would a 13-year-old tell a friend about this?" If no → under 80.

Summarize the article in 7-8 simple, factual, positive sentences for a young audience.
Write a 140-150 word podcast script segment — conversational, educational, fun. Jump straight
into the story (no greetings, no "Hey kids", no "Imagine this"). Vary your openings.
Create one fact-based quiz question with a short answer.

Return JSON matching this schema: {json_schema}

Article:
\"\"\"{article_text}\"\"\"
"""


def _call_llm(article_text, timeout=90, retries=3):
    prompt = PROMPT_TEMPLATE.format(
        article_text=article_text, json_schema=json.dumps(JSON_SCHEMA)
    )
    headers = {
        "Authorization": f"Bearer {config.AI_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": config.AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema", "json_schema": JSON_SCHEMA},
        "max_completion_tokens": 1024,
    }

    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(config.AI_URL, headers=headers, json=data, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                wait = 2.0 * (2 ** attempt)
                print(f"  Rate limited ({r.status_code}), retrying in {wait:.0f}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2.0 * (2 ** attempt))

    print(f"  LLM call failed after {retries} retries: {last_err}")
    return None


def _validate_output(output):
    required = ["score", "summary", "podScript", "podQuestion", "podAnswer"]
    if not isinstance(output, dict):
        return False
    for key in required:
        if key not in output:
            return False
    try:
        int(output["score"])
    except (ValueError, TypeError):
        return False
    return True


def process_content(num_per_topic=12, only_news_date=None):
    rows = db.get_extracted(
        limit_per_topic=num_per_topic, only_news_date=only_news_date
    )
    print(f"  {len(rows)} articles to process")
    processed = 0

    for row in rows:
        aid = row["id"]
        article_text = row["article"]
        try:
            start = time.time()
            output = _call_llm(article_text)

            if not output or not _validate_output(output):
                print(f"  Invalid LLM response for {aid[:8]}, skipping")
                continue

            db.mark_scored(
                aid,
                score=int(output["score"]),
                summary=output["summary"],
                pod_script=output["podScript"],
                pod_question=output["podQuestion"],
                pod_answer=output["podAnswer"],
            )
            processed += 1
            elapsed = round(time.time() - start, 2)
            print(f"  Scored: {aid[:8]} = {output['score']} ({elapsed}s)")

        except Exception as e:
            print(f"  Error processing {aid[:8]}: {e}")

    print(f"Processed {processed} of {len(rows)} articles")
    return processed
