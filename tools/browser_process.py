"""Launch test browsers without showing a process window on Windows."""
from __future__ import annotations

import os
import subprocess


def hidden_browser_kwargs() -> dict:
    """Return Windows-only Popen settings; leave other platforms unchanged."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startupinfo}


def browser_popen(args, **kwargs):
    return subprocess.Popen(args, **kwargs, **hidden_browser_kwargs())


def browser_run(args, **kwargs):
    return subprocess.run(args, **kwargs, **hidden_browser_kwargs())
