#!/usr/bin/env sh
# Mounted volumes (Fly, docker -v) arrive root-owned: fix /data ownership, then drop to the app user.
set -eu
if [ "$(id -u)" = "0" ]; then
  mkdir -p "${DATA_DIR:-/data}"
  chown -R app:app "${DATA_DIR:-/data}"
  exec setpriv --reuid=app --regid=app --init-groups ./run.sh
fi
exec ./run.sh
