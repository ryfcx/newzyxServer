import os
from pathlib import Path

# Project root is parent of the newzyx/ package; .env lives at repo root
_root = Path(__file__).resolve().parent.parent
PROJECT_ROOT = str(_root)
# Optional: put DB on USB SSD, e.g. NEWZYX_DB_PATH=/mnt/ssd/newzyx.db
DB_PATH = os.environ.get("NEWZYX_DB_PATH") or str(_root / "data" / "newzyx.db")
_env_path = _root / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

LLM_TYPE = os.environ.get("LLM_TYPE", "openai")

PERPLEXITY_API_URL = os.environ.get("PERPLEXITY_API_URL", "https://api.perplexity.ai/chat/completions")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
PERPLEXITY_MODEL = os.environ.get("PERPLEXITY_MODEL", "sonar")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_API_URL = os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

if LLM_TYPE == "openai":
    AI_KEY = OPENAI_API_KEY
    AI_URL = OPENAI_API_URL
    AI_MODEL = OPENAI_MODEL
else:
    AI_KEY = PERPLEXITY_API_KEY
    AI_URL = PERPLEXITY_API_URL
    AI_MODEL = PERPLEXITY_MODEL

ELEVENLABS_API_URL = os.environ.get("ELEVENLABS_API_URL", "https://api.elevenlabs.io/v1/text-to-dialogue")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")
ELEVENLABS_MODEL_ID_DIALOGUE = os.environ.get("ELEVENLABS_MODEL_ID_DIALOGUE", "eleven_v3")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "6yesjON9QKSLuPBdVCoB")
ELEVENLABS_VOICE_ID2 = os.environ.get("ELEVENLABS_VOICE_ID2", "IMcnLoWVvO9c3ycsIvkh")

S3_BUCKET = os.environ.get("S3_BUCKET", "kidsnewsfeed")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")
DISTRIBUTION_ID = os.environ.get("DISTRIBUTION_ID", "")
WEBSITE_URL = os.environ.get("WEBSITE_URL", "https://kidsnewsfeed.s3.us-east-2.amazonaws.com")
