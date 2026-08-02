import os
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Multiple Groq keys — add as GROQ_API_KEY_1, GROQ_API_KEY_2, etc.
# Falls back to GROQ_API_KEY if numbered ones not found
def _load_groq_keys():
    keys = []
    i = 1
    while True:
        k = os.getenv(f"GROQ_API_KEY_{i}")
        if not k:
            break
        keys.append(k)
        i += 1
    if not keys:
        k = os.getenv("GROQ_API_KEY")
        if k:
            keys.append(k)
    return keys

GROQ_API_KEYS = _load_groq_keys()

DATA_DIR = os.getenv("DATA_DIR", "/opt/render/project/src/data")
DB_PATH = os.path.join(DATA_DIR, "studybot.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")

ROLE_QUIZ_GROUP = "quiz_group"
ROLE_STUDY_GROUP = "study_group"

# Scheduler (UTC) — IST = UTC+5:30
DAILY_PING_HOUR = 15;    DAILY_PING_MINUTE = 30   # 9:00 PM IST
SUMMARY_HOUR = 16;       SUMMARY_MINUTE = 30       # 10:00 PM IST
CLEANUP_HOUR = 18;       CLEANUP_MINUTE = 29       # 11:59 PM IST
WEEKLY_SUMMARY_DAY = "sun"

HEALTH_PORT = int(os.getenv("PORT", "8080"))
