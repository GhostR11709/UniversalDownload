"""PyInstaller entry point for the named UD Server.exe process."""

from __future__ import annotations

import os
import sys
from pathlib import Path


# The packaged executable lives beside the project files. Keep the same layout
# when it is launched by the startup shortcut, setup script, or Task Manager.
PROJECT_ROOT = Path(os.getenv("UD_PROJECT_ROOT") or Path(sys.executable).resolve().parent)
os.environ["UD_PROJECT_ROOT"] = str(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.__main__ import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
