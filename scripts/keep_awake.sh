#!/usr/bin/env bash
# Keep the Render dashboard from sleeping during waking hours.
#
# Render's free plan puts a web service to sleep after 15 minutes without traffic; the
# next visitor then waits about a minute behind Render's "waking up" page. The collector
# workflow calls this at the start and end of every run (~15 min apart, so the gap
# between pings stays under 13.5 min) and the request itself is the traffic that counts.
#
# It only pings from KEEP_AWAKE_FROM_HOUR (default 6 AM) until midnight, America/Chicago.
# That's ~560 instance hours a month against Render's 750 free, instead of ~744 for
# round-the-clock, which would leave almost no margin. Overnight it sleeps as before.
#
# Fire-and-forget: a 5s timeout is plenty to register the request (even if the service is
# asleep and still booting), and nothing here can fail the workflow.
set -u

URL="${DASHBOARD_URL:-https://job-discovery-dashboard.onrender.com}/login"
FROM="${KEEP_AWAKE_FROM_HOUR:-6}"
HOUR=$(TZ="${KEEP_AWAKE_TZ:-America/Chicago}" date +%H)

if [ $((10#$HOUR)) -ge "$FROM" ]; then
  code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$URL" || true)
  echo "keep-awake: local hour ${HOUR}, pinged ${URL} -> HTTP ${code:-timeout/none}"
else
  echo "keep-awake: local hour ${HOUR} is before ${FROM}:00, quiet hours, not pinging"
fi
exit 0
