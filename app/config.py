import os
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Northern Europe Security Brief")
    database_path: Path = Path(os.getenv("DATABASE_PATH", "/data/briefs.sqlite3"))
    import_token: str = os.getenv("IMPORT_TOKEN", "")
    site_username: str = os.getenv("SITE_USERNAME", "brief")
    site_password: str = os.getenv("SITE_PASSWORD", "")
    public_reports: bool = os.getenv("PUBLIC_REPORTS", "false").lower() == "true"
    timezone: str = os.getenv("TIMEZONE", "Europe/Stockholm")

settings = Settings()
