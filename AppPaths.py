"""Separate bundled read-only resources from portable user data."""
import sys
from pathlib import Path

RESOURCE_ROOT = Path(__file__).resolve().parent
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else RESOURCE_ROOT
