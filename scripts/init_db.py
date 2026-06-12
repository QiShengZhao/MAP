"""Apply database schema via Alembic (preferred over raw create_all)."""
from pathlib import Path

from alembic import command
from alembic.config import Config


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")
    print("database migrated (alembic upgrade head)")


if __name__ == "__main__":
    main()
