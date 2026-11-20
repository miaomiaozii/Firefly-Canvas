"""Start Firefly Canvas from a detached launcher.

The batch file is only a short-lived launcher. Keeping this tiny entrypoint
separate avoids the quoting and import-path problems of invoking ``main.py``
with a long ``python -c`` command on a fresh Windows installation.
"""

import os
import runpy
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

if sys.argv[1:] == ["--desktop"]:
    # The tray assigns the child to its Windows Job before allowing app startup.
    if not os.environ.get("FIREFLY_DESKTOP_READY_FILE") or sys.stdin.buffer.readline() != b"start\n":
        raise SystemExit("Missing desktop owner handshake.")
    from contextlib import redirect_stdout

    # The embeddable interpreter may omit the script directory from sys.path.
    with redirect_stdout(sys.stderr):
        if runpy.run_path("tools/python_version_guard.py")["main"]():
            raise SystemExit(1)
        if runpy.run_path("tools/check_dependencies.py")["main"](["--quiet"]):
            raise SystemExit(1)

runpy.run_path('main.py', run_name='__main__')
