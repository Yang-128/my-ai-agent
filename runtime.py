import os
import sys
from pathlib import Path

RESOURCE_DIR = Path(getattr(
    sys, "_MEIPASS", Path(__file__).resolve().parent
))

if os.getenv("MY_AI_AGENT_DATA_DIR"):
    DATA_DIR = Path(os.environ["MY_AI_AGENT_DATA_DIR"]).expanduser()
elif getattr(sys, "frozen", False):
    DATA_DIR = Path.home() / "Library" / "Application Support" / "My AI Agent"
else:
    DATA_DIR = Path(__file__).resolve().parent

DATA_DIR.mkdir(parents=True, exist_ok=True)
ENV_FILE = DATA_DIR / ".env"
DB_PATH = DATA_DIR / "conversations.db"
