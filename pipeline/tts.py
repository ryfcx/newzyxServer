from elevenlabs import ElevenLabs
from elevenlabs.types import VoiceSettings
from pydub import AudioSegment
from datetime import datetime, timedelta
import io
import re
import shutil
import os
from newzyx import config, workspace

# ElevenLabs reads "Newzyx" as "newsy-x"; respell phonetically for TTS input only
# (display text/branding elsewhere keeps the real spelling).
_PRONOUNCE_SUBS = [
    (re.compile(r"\bnewzyx\b", re.IGNORECASE), "New-zix"),
    (re.compile(r"\bzara\b", re.IGNORECASE), "Zar-uh"),
]

# Real silence / bed gaps, stitched with pydub rather than relying on inline TTS tags.
INTRO_TO_VOICE_MS = 350
TOPIC_GAP_BEFORE_STING_MS = 450
TOPIC_GAP_AFTER_STING_MS = 350
BRIDGE_GAP_MS = 900
QA_GAP_MS = 1500
QA_ANSWER_GAP_MS = 3000
QA_PAIR_GAP_MS = 800

_AUDIO_DIR = os.path.join(config.PROJECT_ROOT, "audio")
_INTRO_MUSIC_PATH = os.path.join(_AUDIO_DIR, "intro_music.mp3")
_TOPIC_STING_PATH = os.path.join(_AUDIO_DIR, "topic_transition.mp3")

# Clearer, more consistent delivery — turbo can slur when speed is high / stability low.
_VOICE_SETTINGS = VoiceSettings(
    stability=0.62,
    similarity_boost=0.78,
    style=0.0,
    use_speaker_boost=True,
    speed=0.94,
)


def _apply_pronunciation_fixes(text):
    for pattern, replacement in _PRONOUNCE_SUBS:
        text = pattern.sub(replacement, text)
    return text


def _prepare_tts_text(text):
    """Light cleanup so ElevenLabs is less likely to slur or rush words."""
    text = (text or "").strip()
    if not text:
        return text
    text = _apply_pronunciation_fixes(text)
    # Prefer spoken pauses over run-ons.
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*\.\s*", ". ", text)
    text = re.sub(r"\s*\?\s*", "? ", text)
    text = re.sub(r"\s*!\s*", "! ", text)
    # Soften dense digit clusters a bit by spacing years already spoken as words when possible.
    text = text.replace("...", "... ")
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


def tts(script_parts, t=0):
    """
    Build the episode MP3 from structured script parts.

    script_parts keys:
      intro (str), topics (list[str]), bridge (str), qa_pairs (list[(q, a)])
    """
    date_str = (datetime.now() - timedelta(days=t)).strftime("%Y-%m-%d")

    ep_dir = os.path.join(workspace.generated_website_dir(), "episodes", date_str)
    os.makedirs(ep_dir, exist_ok=True)

    dated_mp3 = os.path.join(ep_dir, date_str + ".mp3")
    client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)

    intro = (script_parts.get("intro") or "").strip()
    topics = [s.strip() for s in (script_parts.get("topics") or []) if s and s.strip()]
    bridge = (script_parts.get("bridge") or "").strip()
    qa_pairs = script_parts.get("qa_pairs") or []

    # Fallback: old callers may still pass a single news blob.
    if not intro and script_parts.get("news_text"):
        intro = script_parts["news_text"]
        topics = []

    segments = []

    intro_music = _load_bed(_INTRO_MUSIC_PATH, target_dbfs=-16.0)
    topic_sting = _load_bed(_TOPIC_STING_PATH, target_dbfs=-20.0)

    intro_audio = _synthesize(client, intro) if intro else AudioSegment.silent(duration=1)
    ref = intro_audio

    if intro_music is not None:
        music = _match_voice(intro_music.fade_out(500), ref)
        segments.append(music)
        segments.append(AudioSegment.silent(duration=INTRO_TO_VOICE_MS))

    segments.append(intro_audio)

    for i, topic in enumerate(topics):
        topic_audio = _synthesize(client, topic)
        if i == 0:
            # Short breath before first story after intro.
            segments.append(AudioSegment.silent(duration=TOPIC_GAP_AFTER_STING_MS))
        else:
            segments.append(AudioSegment.silent(duration=TOPIC_GAP_BEFORE_STING_MS))
            if topic_sting is not None:
                segments.append(_match_voice(topic_sting, ref))
                segments.append(AudioSegment.silent(duration=TOPIC_GAP_AFTER_STING_MS))
            else:
                # Clear topic break even without a sting file.
                segments.append(AudioSegment.silent(duration=700))
        segments.append(topic_audio)

    if bridge:
        segments.append(AudioSegment.silent(duration=BRIDGE_GAP_MS))
        if topic_sting is not None:
            segments.append(_match_voice(topic_sting, ref))
            segments.append(AudioSegment.silent(duration=TOPIC_GAP_AFTER_STING_MS))
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
