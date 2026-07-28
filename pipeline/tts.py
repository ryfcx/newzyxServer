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
    (re.compile(r"newzyx", re.IGNORECASE), "New-zix"),
]

# Real silence gaps, stitched locally with pydub rather than relying on inline
# TTS pause tags, which aren't reliably honored by the turbo model.
QA_GAP_MS = 1500        # between the news segment and the start of the quiz
QA_ANSWER_GAP_MS = 3000  # between a question and its answer, so listeners can answer
QA_PAIR_GAP_MS = 800    # between one quiz pair and the next


def _apply_pronunciation_fixes(text):
    for pattern, replacement in _PRONOUNCE_SUBS:
        text = pattern.sub(replacement, text)
    return text


def _synthesize(client, text):
    audio_bytes = b"".join(
        client.text_to_speech.convert(
            voice_id=config.ELEVENLABS_VOICE_ID,
            model_id=config.ELEVENLABS_MODEL_ID,
            voice_settings=VoiceSettings(stability=0.5, clarity=0.6, speed=1),
            text=_apply_pronunciation_fixes(text),
        )
    )
    return AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")


def tts(news_text, qa_pairs=None, t=0):
    date_str = (datetime.now() - timedelta(days=t)).strftime("%Y-%m-%d")

    ep_dir = os.path.join(workspace.generated_website_dir(), "episodes", date_str)
    os.makedirs(ep_dir, exist_ok=True)

    dated_mp3 = os.path.join(ep_dir, date_str + ".mp3")

    client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)

    combined = _synthesize(client, news_text)

    if qa_pairs:
        combined = combined + AudioSegment.silent(duration=QA_GAP_MS)
        answer_gap = AudioSegment.silent(duration=QA_ANSWER_GAP_MS)
        pair_gap = AudioSegment.silent(duration=QA_PAIR_GAP_MS)

        for i, (question, answer) in enumerate(qa_pairs):
            if i > 0:
                combined += pair_gap
            question_audio = _synthesize(client, question)
            answer_audio = _synthesize(client, answer)
            combined = combined + question_audio + answer_gap + answer_audio

    combined.export(dated_mp3, format="mp3")
    generated = [dated_mp3]

    if t == 0:
        today_mp3 = os.path.join(workspace.generated_website_dir(), "today.mp3")
        shutil.copy(dated_mp3, today_mp3)
        generated.append(today_mp3)

    print(f"  Audio saved: episodes/{date_str}/{date_str}.mp3")
    return generated
