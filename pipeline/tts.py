from elevenlabs import ElevenLabs
from elevenlabs.types import VoiceSettings
from pydub import AudioSegment
from datetime import datetime, timedelta
import io
import re
import shutil
import os
from newzyx import config, workspace

# Keep in sync with pipeline.episode.HOST_NAME (avoid importing episode here).
HOST_NAME = "Zara"

# Brand spelling only — do NOT respell the host name; that made "Zara" unintelligible.
_PRONOUNCE_SUBS = [
    (re.compile(r"\bnewzyx\b", re.IGNORECASE), "New-zix"),
]

# Ordinals like "8th"/"eighth" get mangled by turbo ("die-jist"). Prefer cardinals.
_ORDINAL_WORD_TO_CARDINAL = {
    "first": "one",
    "second": "two",
    "third": "three",
    "fourth": "four",
    "fifth": "five",
    "sixth": "six",
    "seventh": "seven",
    "eighth": "eight",
    "ninth": "nine",
    "tenth": "ten",
    "eleventh": "eleven",
    "twelfth": "twelve",
    "thirteenth": "thirteen",
    "fourteenth": "fourteen",
    "fifteenth": "fifteen",
    "sixteenth": "sixteen",
    "seventeenth": "seventeen",
    "eighteenth": "eighteen",
    "nineteenth": "nineteen",
    "twentieth": "twenty",
    "thirtieth": "thirty",
}
_DAY_CARDINALS = {
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

# Real silence / bed gaps only between major sections (stories, quiz, outro).
# Do NOT insert silence mid-sentence — that is what made delivery sound choppy.
INTRO_TO_VOICE_MS = 300
TOPIC_GAP_BEFORE_STING_MS = 280
TOPIC_GAP_AFTER_STING_MS = 220
BRIDGE_GAP_MS = 550
QA_GAP_MS = 1000
QA_ANSWER_GAP_MS = 3000
QA_PAIR_GAP_MS = 550
OUTRO_GAP_MS = 550

_AUDIO_DIR = os.path.join(config.PROJECT_ROOT, "audio")
_INTRO_MUSIC_PATH = os.path.join(_AUDIO_DIR, "intro_music.mp3")
_TOPIC_STING_PATH = os.path.join(_AUDIO_DIR, "topic_transition.mp3")

# Energetic but steady: higher stability + lower style = fewer weird mid-word pauses.
# Speed stays quick without the stop-start feel of high style exaggeration.
_VOICE_SETTINGS = VoiceSettings(
    stability=0.62,
    similarity_boost=0.8,
    style=0.15,
    use_speaker_boost=True,
    speed=1.08,
)


_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = [
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
]


def _year_words(year):
    if 2000 <= year <= 2099:
        ones = year % 100
        if ones == 0:
            return "two thousand"
        if ones < 10:
            return f"two thousand {_DAY_CARDINALS[ones]}"
        return f"twenty {_DAY_CARDINALS[ones]}"
    return str(year)


def _under_1000_words(n):
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] if ones == 0 else f"{_TENS[tens]} {_ONES[ones]}"
    hundreds, rest = divmod(n, 100)
    if rest == 0:
        return f"{_ONES[hundreds]} hundred"
    return f"{_ONES[hundreds]} hundred {_under_1000_words(rest)}"


def _int_to_words(n):
    """Spell integers so TTS says 'fifty thousand' instead of '50 oh oh oh'."""
    if n < 0:
        return "minus " + _int_to_words(-n)
    if n < 1000:
        return _under_1000_words(n)
    scales = [
        (1_000_000_000, "billion"),
        (1_000_000, "million"),
        (1_000, "thousand"),
    ]
    parts = []
    for value, label in scales:
        if n >= value:
            qty, n = divmod(n, value)
            parts.append(f"{_under_1000_words(qty)} {label}")
    if n:
        parts.append(_under_1000_words(n))
    return " ".join(parts)


def _apply_pronunciation_fixes(text):
    for pattern, replacement in _PRONOUNCE_SUBS:
        text = pattern.sub(replacement, text)
    return text


def _normalize_hard_tts_phrases(text):
    """Rewrite date/number patterns that TTS commonly mangles."""
    # 8th / 08th → eight
    def _ordinal_num(match):
        n = int(match.group(1))
        return _DAY_CARDINALS.get(n, str(n))

    text = re.sub(r"\b0?([1-9]|[12]\d|3[01])(st|nd|rd|th)\b", _ordinal_num, text, flags=re.I)

    # Only rewrite written ordinals in date phrases ("August eighth"), not story words like "first".
    ordinal_alt = "|".join(re.escape(k) for k in _ORDINAL_WORD_TO_CARDINAL)
    text = re.sub(
        rf"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+({ordinal_alt})\b",
        lambda m: f"{m.group(1)} {_ORDINAL_WORD_TO_CARDINAL[m.group(2).lower()]}",
        text,
        flags=re.I,
    )

    # years 20xx as digits → spoken words (before generic number expansion)
    text = re.sub(
        r"\b(20\d{2})\b",
        lambda m: _year_words(int(m.group(1))),
        text,
    )

    # leading-zero day numbers sitting alone: "August 08," → "August eight,"
    text = re.sub(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+0?([1-9]|[12]\d|3[01])\b",
        lambda m: f"{m.group(1)} {_DAY_CARDINALS[int(m.group(2))]}",
        text,
        flags=re.I,
    )

    # Comma-formatted numbers: 50,000 → fifty thousand
    text = re.sub(
        r"\b\d{1,3}(?:,\d{3})+\b",
        lambda m: _int_to_words(int(m.group(0).replace(",", ""))),
        text,
    )

    # Plain integers with 4+ digits (and not already handled years): 50000 → fifty thousand
    text = re.sub(
        r"\b\d{4,}\b",
        lambda m: _int_to_words(int(m.group(0))),
        text,
    )

    # Decimals like 3.5 → three point five (avoid digit-by-digit mush)
    def _decimal_words(match):
        whole, frac = match.group(1), match.group(2)
        whole_words = _int_to_words(int(whole))
        frac_words = " ".join(_ONES[int(d)] for d in frac)
        return f"{whole_words} point {frac_words}"

    text = re.sub(r"\b(\d+)\.(\d+)\b", _decimal_words, text)
    return text


def _prepare_tts_text(text):
    """Cleanup that favors natural spoken flow (fewer fake pauses)."""
    text = (text or "").strip()
    if not text:
        return text
    text = _normalize_hard_tts_phrases(text)
    text = _apply_pronunciation_fixes(text)
    # Em/en dashes and ellipses make multilingual TTS take long awkward pauses.
    text = text.replace("—", ", ").replace("–", ", ").replace("…", ", ")
    text = re.sub(r"\.{3,}", ", ", text)
    text = re.sub(r"!{2,}", "!", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*\.\s*", ". ", text)
    text = re.sub(r"\s*\?\s*", "? ", text)
    text = re.sub(r"\s*!\s*", "! ", text)
    text = re.sub(r"\s*,\s*,+", ", ", text)
    return text.strip()


def _synthesize(client, text):
    prepared = _prepare_tts_text(text)
    if not prepared:
        return AudioSegment.silent(duration=1)
    audio_bytes = b"".join(
        client.text_to_speech.convert(
            voice_id=config.ELEVENLABS_VOICE_ID,
            model_id=config.ELEVENLABS_MODEL_ID,
            voice_settings=_VOICE_SETTINGS,
            text=prepared,
        )
    )
    return AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")


def _load_bed(path, target_dbfs=-18.0):
    """Load a music bed/sting; return None if missing. Soften loud beds."""
    if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
        return None
    try:
        bed = AudioSegment.from_file(path, format="mp3")
    except Exception as e:
        print(f"  Warning: could not load audio bed {path}: {e}")
        return None
    if bed.dBFS != float("-inf") and bed.dBFS > target_dbfs:
        bed = bed.apply_gain(target_dbfs - bed.dBFS)
    return bed


def _match_voice(seg, voice_ref):
    """Match sample rate/channels to voice clips so concatenation is clean."""
    return seg.set_frame_rate(voice_ref.frame_rate).set_channels(voice_ref.channels)


def _append_intro(segments, client, script_parts):
    """
    Speak the full intro as one continuous clip so greeting/name/date flow
    like a real host open (separate clips sounded choppy).
    """
    intro_parts = script_parts.get("intro_parts") or {}
    pieces = [
        (intro_parts.get("greeting") or "").strip(),
        (intro_parts.get("name") or "").strip(),
        (intro_parts.get("date") or "").strip(),
    ]
    intro_text = " ".join(p for p in pieces if p) or (script_parts.get("intro") or "").strip()

    intro_music = _load_bed(_INTRO_MUSIC_PATH, target_dbfs=-16.0)
    intro_audio = _synthesize(client, intro_text) if intro_text else AudioSegment.silent(duration=1)
    ref = intro_audio

    if intro_music is not None:
        music = _match_voice(intro_music.fade_out(600), ref)
        segments.append(music)
        segments.append(AudioSegment.silent(duration=INTRO_TO_VOICE_MS))

    segments.append(intro_audio)
    return ref


def tts(script_parts, t=0):
    """
    Build the episode MP3 from structured script parts.

    script_parts keys:
      intro / intro_parts, topics, bridge, qa_pairs, outro
    """
    date_str = (datetime.now() - timedelta(days=t)).strftime("%Y-%m-%d")

    ep_dir = os.path.join(workspace.generated_website_dir(), "episodes", date_str)
    os.makedirs(ep_dir, exist_ok=True)

    dated_mp3 = os.path.join(ep_dir, date_str + ".mp3")
    client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)

    topics = [s.strip() for s in (script_parts.get("topics") or []) if s and s.strip()]
    bridge = (script_parts.get("bridge") or "").strip()
    qa_pairs = script_parts.get("qa_pairs") or []
    outro = (script_parts.get("outro") or "").strip()

    # Fallback: old callers may still pass a single news blob.
    if not script_parts.get("intro") and not script_parts.get("intro_parts") and script_parts.get("news_text"):
        script_parts = dict(script_parts)
        script_parts["intro"] = script_parts["news_text"]
        topics = []

    segments = []
    topic_sting = _load_bed(_TOPIC_STING_PATH, target_dbfs=-20.0)
    ref = _append_intro(segments, client, script_parts)

    for i, topic in enumerate(topics):
        topic_audio = _synthesize(client, topic)
        if i == 0:
            segments.append(AudioSegment.silent(duration=TOPIC_GAP_AFTER_STING_MS))
        else:
            segments.append(AudioSegment.silent(duration=TOPIC_GAP_BEFORE_STING_MS))
            if topic_sting is not None:
                segments.append(_match_voice(topic_sting, ref))
                segments.append(AudioSegment.silent(duration=TOPIC_GAP_AFTER_STING_MS))
            else:
                segments.append(AudioSegment.silent(duration=700))
        segments.append(topic_audio)

    if bridge:
        segments.append(AudioSegment.silent(duration=BRIDGE_GAP_MS))
        if topic_sting is not None:
            segments.append(_match_voice(topic_sting, ref))
            segments.append(AudioSegment.silent(duration=TOPIC_GAP_AFTER_STING_MS))
        # One continuous clip — splitting on the host name made the bridge sound robotic.
        segments.append(_synthesize(client, bridge))

    if qa_pairs:
        segments.append(AudioSegment.silent(duration=QA_GAP_MS))
        answer_gap = AudioSegment.silent(duration=QA_ANSWER_GAP_MS)
        pair_gap = AudioSegment.silent(duration=QA_PAIR_GAP_MS)
        for i, (question, answer) in enumerate(qa_pairs):
            if i > 0:
                segments.append(pair_gap)
            segments.append(_synthesize(client, question))
            segments.append(answer_gap)
            segments.append(_synthesize(client, answer))

    if outro:
        segments.append(AudioSegment.silent(duration=OUTRO_GAP_MS))
        if topic_sting is not None:
            segments.append(_match_voice(topic_sting, ref))
            segments.append(AudioSegment.silent(duration=TOPIC_GAP_AFTER_STING_MS))
        segments.append(_synthesize(client, outro))
        intro_music = _load_bed(_INTRO_MUSIC_PATH, target_dbfs=-18.0)
        if intro_music is not None:
            bed = _match_voice(intro_music.fade_in(200).fade_out(800), ref)
            segments.append(AudioSegment.silent(duration=250))
            segments.append(bed)

    combined = segments[0]
    for seg in segments[1:]:
        combined += seg

    combined.export(dated_mp3, format="mp3")
    generated = [dated_mp3]

    if t == 0:
        today_mp3 = os.path.join(workspace.generated_website_dir(), "today.mp3")
        shutil.copy(dated_mp3, today_mp3)
        generated.append(today_mp3)

    print(f"  Audio saved: episodes/{date_str}/{date_str}.mp3")
    return generated
