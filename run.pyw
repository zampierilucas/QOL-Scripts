"""Launcher for QOL-Scripts (dev / source).

Runs the app straight from this repo — no build step, no bundled exe. The
``.pyw`` extension means Windows starts it with ``pythonw``, so there is no
console window (double-click it, or let the Startup shortcut invoke it at
login). It simply executes ``src/app.py``'s entry point, which logs to
``%LOCALAPPDATA%/QOL-Scripts/app.log``.

Pass ``--debug`` through for verbose logging.
"""
import os
import sys
import runpy

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
sys.path.insert(0, _SRC)

runpy.run_path(os.path.join(_SRC, "app.py"), run_name="__main__")
