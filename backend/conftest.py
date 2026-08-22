"""Pytest bootstrap: ensure the backend root (containing ``app``) is importable."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
