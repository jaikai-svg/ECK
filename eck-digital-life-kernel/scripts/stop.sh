#!/usr/bin/env sh
set -eu
docker compose down
printf '%s\n' "ECK stopped. Named volumes were preserved."
