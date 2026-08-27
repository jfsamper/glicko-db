---
applyTo: "**"
---
# Project general standards
## Basic information
- The project is a web application built with Flask, SQLite, and Python.
- See [README.md](../README.md) for the authoritative Spanish project description, requirements, and local run instructions. Keep [README.en.md](../README.en.md) and [README.pt.md](../README.pt.md) synchronized translations.

## Agent workflow and escalation policy
See [AGENT_RUN_PLAN.md](../agents/AGENT_RUN_PLAN.md) for the project-wide agent workflow and escalation policy.
If you have not been assigned a specific agent role, you must still follow the best practices below. 

## Script execution best practice
All agents must write a Python script (e.g. in `scripts/dev_only/` or temporary script files) and run it via the terminal instead of entering large amounts of Python code interactively on the console REPL.
After running a debug script in `scripts/dev_only/`, delete it unless it has ongoing utility. Never commit one-off debug scripts to version control.

Important SQLite rule for all agents:
- Do not run SQLite debugging snippets directly in the terminal with `python -c` or in the interactive REPL. This often fails due to path confusion, syntax, or the shell environment.
- Always create debug scripts in `scripts/dev_only/`. Do not use other temp file locations. Then run it with the project venv Python interpreter.
- Example workflow: create a debug script, save it, then run using the project venv interpreter, e.g. `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` on Unix. Resolve the correct path from the repo root.