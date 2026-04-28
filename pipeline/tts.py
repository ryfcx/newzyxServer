from elevenlabs import ElevenLabs, save
from elevenlabs.types import VoiceSettings
from datetime import datetime, timedelta
import shutil
import os
from newzyx import config, utils, workspace


def tts(fname, t=0):
    date_str = (datetime.now() - timedelta(days=t)).strftime("%Y-%m-%d")

    ep_dir = os.path.join(workspace.generated_website_dir(), "episodes", date_str)
    os.makedirs(ep_dir, exist_ok=True)

    dated_mp3 = os.path.join(ep_dir, date_str + ".mp3")

    with open(fname, "r", encoding="utf-8") as f:
        text = f.read()

    client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
    audio = client.text_to_speech.convert(
        voice_id=config.ELEVENLABS_VOICE_ID,
        model_id=config.ELEVENLABS_MODEL_ID,
        voice_settings=VoiceSettings(stability=0.5, clarity=0.6, speed=1),
        text=text,
    )

    save(audio, dated_mp3)
    generated = [dated_mp3]

    if t == 0:
        today_mp3 = os.path.join(workspace.generated_website_dir(), "today.mp3")
        shutil.copy(dated_mp3, today_mp3)
        generated.append(today_mp3)

    print(f"  Audio saved: episodes/{date_str}/{date_str}.mp3")
    return generated
