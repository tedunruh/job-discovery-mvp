#!/bin/bash
cd "$(dirname "$0")"
set -a
source .env.local
set +a
exec .venv/bin/python3 dashboard/app.py
