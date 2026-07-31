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
runpy.run_path('main.py', run_name='__main__')
