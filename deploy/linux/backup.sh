#!/usr/bin/env bash
# Daily backup wrapper for Linux (call from cron / systemd timer).
# Example crontab:
#   17 3 * * * /srv/asagent/deploy/linux/backup.sh
set -euo pipefail

cd "$(dirname "$0")/../.."

BACKUP_KEEP="${BACKUP_KEEP:-7}"
exec uv run python scripts/backup.py --keep "$BACKUP_KEEP" "$@"
