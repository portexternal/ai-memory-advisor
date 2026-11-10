"""Picks the backend for this operating system. Everything OS-specific goes through here."""
import sys

if sys.platform == "darwin":
    from advisor.platform_mac import *  # noqa: F401,F403
    from advisor.platform_mac import IGNORE, NAME, RERUN_HINT, SYSTEM_PREFIXES  # noqa: F401
elif sys.platform == "win32":
    from advisor.platform_win import *  # noqa: F401,F403
    from advisor.platform_win import IGNORE, NAME, RERUN_HINT, SYSTEM_PREFIXES  # noqa: F401
else:
    raise ImportError(f"AI Memory Advisor supports Windows and macOS, not {sys.platform}")
