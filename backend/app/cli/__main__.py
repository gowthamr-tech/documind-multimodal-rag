"""Operational commands (run with owner credentials where noted).

python -m app.cli setup-checkpointer   create LangGraph checkpoint tables + grants (owner role)
"""

import asyncio
import sys

from app.agents.service import setup_checkpointer
from app.core.config import get_settings


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup-checkpointer":
        asyncio.run(setup_checkpointer(get_settings().migration_database_url))
        print("checkpointer tables ready")
        return
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    main()
