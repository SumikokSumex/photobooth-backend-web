import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} belum diisi")
    return value


def _parse_allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "*").strip()
    if not raw or raw == "*":
        return ["*"]
    return [item.strip() for item in raw.split(",") if item.strip()]


GOOGLE_DRIVE_PARENT_FOLDER_ID = _require_env("GOOGLE_DRIVE_PARENT_FOLDER_ID")
NANO_BANANA_API_KEY = _require_env("NANO_BANANA_API_KEY")
PUBLIC_BASE_URL = _require_env("PUBLIC_BASE_URL")

ALLOWED_ORIGINS = _parse_allowed_origins()

GOOGLE_SERVICE_ACCOUNT_FILE = Path(
    os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials/service_account.json")
).resolve()
