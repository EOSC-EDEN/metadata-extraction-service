#!/bin/sh
# Purge expired asynchronous jobs (and orphaned uploads) on a schedule derived from
# MES_JOB_TTL_SECONDS: a quarter of the TTL, between 1 minute and 1 hour (see apps/api/purge.py).
# Change MES_JOB_TTL_SECONDS and restart the container; there is no separate schedule setting.
#
# A plain loop instead of a cron daemon: the image runs as a non-root user and has no cron. To use
# a real cron or a Kubernetes CronJob instead, run `python manage.py purge_schedule` for the
# crontab expression and schedule `python manage.py purge_expired_jobs` with it.
set -eu

minutes=$(python manage.py purge_schedule --minutes)
echo "purging expired jobs every ${minutes} minute(s) (crontab: $(python manage.py purge_schedule))"

while true; do
    python manage.py purge_expired_jobs || echo "purge failed; retrying at the next interval" >&2
    sleep $((minutes * 60))
done
