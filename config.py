import os

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

load_dotenv()

DEFAULT_DATABASE_URL = "postgresql+psycopg2://aml:aml_password@localhost:5432/aml_db"

# Suspicious Transaction Alert thresholds (0-100 risk score)
HIGH_RISK_THRESHOLD = 80
MEDIUM_RISK_THRESHOLD = 50

# Typical reporting / auto-alert threshold that smurfing tries to evade (JPY)
REPORTING_THRESHOLD_JPY = 1_000_000

# Public internet deployment: disables operations that are expensive or mutate shared data
PUBLIC_DEMO = os.getenv("PUBLIC_DEMO", "false").strip().lower() in {"1", "true", "yes"}


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_engine() -> Engine:
    return create_engine(get_database_url(), pool_pre_ping=True)
