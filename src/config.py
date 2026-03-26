from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv(override=True)


@dataclass
class Settings:
    # API keys
    anthropic_api_key: str
    voyage_api_key: str

    # Database
    database_url: str

    # Models
    claude_model: str = "claude-sonnet-4-20250514"
    voyage_model: str = "voyage-3"
    embedding_dimensions: int = 1024

    # Pipeline
    batch_size: int = 10
    rate_limit_delay: float = 1.0
    min_turns_for_analysis: int = 3
    session_time_window_minutes: int = 30
    session_similarity_threshold: float = 0.7

    # Preprocessing filters
    min_content_chars: int = 30
    min_browse_visit_count: int = 2

    # Model overrides per stage
    categorizer_model: str = "claude-haiku-4-5-20251001"

    # Paths
    data_dir: Path = Path("data")


def load_settings() -> Settings:
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        voyage_api_key=os.getenv("VOYAGE_API_KEY", ""),
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql://thought_os:thought_os_dev@localhost:5432/thought_os",
        ),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
        voyage_model=os.getenv("VOYAGE_MODEL", "voyage-3"),
        embedding_dimensions=int(os.getenv("EMBEDDING_DIMENSIONS", "1024")),
        batch_size=int(os.getenv("BATCH_SIZE", "10")),
        rate_limit_delay=float(os.getenv("RATE_LIMIT_DELAY", "1.0")),
        min_turns_for_analysis=int(os.getenv("MIN_TURNS_FOR_ANALYSIS", "3")),
        session_time_window_minutes=int(
            os.getenv("SESSION_TIME_WINDOW_MINUTES", "30")
        ),
        session_similarity_threshold=float(
            os.getenv("SESSION_SIMILARITY_THRESHOLD", "0.7")
        ),
        min_content_chars=int(os.getenv("MIN_CONTENT_CHARS", "30")),
        min_browse_visit_count=int(os.getenv("MIN_BROWSE_VISIT_COUNT", "2")),
        categorizer_model=os.getenv("CATEGORIZER_MODEL", "claude-haiku-4-5-20251001"),
    )
