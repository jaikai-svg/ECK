#!/usr/bin/env sh
set -eu
docker compose up -d
printf '%s\n' "ECK is starting at http://127.0.0.1:8420"
docker compose ps

