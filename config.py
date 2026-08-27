import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

# Render Internal PostgreSQL Database URL fallback to local SQLite for local testing
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///rider_bot.db")

NORMAL_RADIUS_KM = 1.0
BROADCAST_RADIUS_KM = 5.0
REQUEST_TIMEOUT = 120  # 2 Minutes
