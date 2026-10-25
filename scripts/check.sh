#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
.venv/bin/python -m pip check
if command -v node >/dev/null 2>&1; then
    node --check static/js/app.js
    node --test tests/frontend.test.cjs
fi
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test tests -v 2
