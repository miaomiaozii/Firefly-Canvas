@echo off
REM Inert stand-in for the pi CLI, referenced by tests/fake_agent_host.py.
REM Tests must never launch the real agent CLI: the quality gate records that as
REM a subprocess-executable boundary event and fails the gate step. This stub
REM produces no output and exits immediately, so even a stray spawn attempt
REM cannot start an agent.
exit /b 0
