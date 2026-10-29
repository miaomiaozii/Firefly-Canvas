@echo off
REM #239 deterministic fake pi wrapper: cmd.exe -> python fake_pi.py -> (optional) grandchild.
REM The cmd wrapper mirrors the real pi Windows .cmd form (npx wrapper), so tests can
REM prove taskkill /PID <pid> /T /F kills the whole process tree, not just the top Popen.
python "%~dp0fake_pi.py" %*
