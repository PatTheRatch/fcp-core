#!/bin/sh
# Double-click to open the draft room for the night. Runs
# scripts/draft_night.py in the repository this file lives in, with its
# own virtualenv, and keeps the window open on a failure so the message
# can be read rather than vanishing with it.

cd "$(dirname "$0")/.." || exit 1

if [ ! -x .venv/bin/python ]; then
  echo "No .venv/bin/python in $(pwd)."
  echo "Make one (uv sync, or python -m venv .venv && pip install -e '.[live,projections]')."
  printf "Press Return to close this window. "
  read -r _
  exit 1
fi

if ! .venv/bin/python scripts/draft_night.py "$@"; then
  echo
  echo "The draft room did not start. The message above says why."
  printf "Press Return to close this window. "
  read -r _
  exit 1
fi
