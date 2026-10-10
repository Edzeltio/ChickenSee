# Poultry Sensor Logger

This is a Python/Arduino desktop hardware logger, not a web application. The
user requested recording changes rather than a Replit run setup; no web server
or run workflow should be added just to provide a preview.

Run on the camera/Arduino-connected computer using `start.bat` (Windows),
`./start.sh` (Linux), or `python python/main.py` with the dependencies installed.
Camera and Supabase credentials belong in environment variables, never in code.

Local verification: `python -m unittest discover -s tests -v`.
Camera tests use synthetic frames and temporary SQLite/MP4 files without devices.
Real camera connectivity, desktop hotkeys, and sensor hardware require the
connected computer; they cannot be verified from this cloud workspace.

Recording controls and the paired-file schema are documented in README.md.
