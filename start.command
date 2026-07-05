#!/bin/bash
# Double-click this file to launch clip-factory.
cd "$(dirname "$0")" || exit 1

echo "Starting clip-factory..."

# open the browser once the server is up
( sleep 3; open http://localhost:5050 ) &

# run the app
./.venv/bin/python app.py

echo ""
echo "clip-factory stopped. You can close this window."
