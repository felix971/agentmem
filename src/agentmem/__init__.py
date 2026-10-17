"""Local shared conversation history, implemented with the Python standard library."""

import os

# Apply before opening any database, lock, note, or log, including API use.
os.umask(0o077)

__version__ = "0.1.0"
