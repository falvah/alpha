#!/bin/sh
set -eu
umask 077
python manage.py migrate --noinput
python manage.py collectstatic --noinput
if [ "${SANDBOX_MODE:-${DEMO_MODE:-true}}" = "true" ]; then
    python manage.py seed_workspace
fi
exec "$@"
