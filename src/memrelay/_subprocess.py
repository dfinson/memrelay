"""Cross-platform subprocess options shared by runtime code and tests."""

from __future__ import annotations

import subprocess

# CREATE_NO_WINDOW exists only on Windows. Passing zero is the documented no-op
# creationflags value on other platforms.
NO_WINDOW_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
