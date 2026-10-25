#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
umask 077
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py seed_workspace
exec .venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload
