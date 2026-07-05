#!/usr/bin/env bash
# Keep the free Hugging Face Space awake so a cold-emailed prospect never hits a
# ~40s cold start. Run on any always-on machine:  bash keep-warm.sh
# Stop with Ctrl-C. Override the URL as the first arg if it ever changes.
URL="${1:-https://dahuum-radraft.hf.space/}"
echo "Keeping $URL warm — pinging every 10 min. Ctrl-C to stop."
while true; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 "$URL")
  echo "$(date '+%Y-%m-%d %H:%M:%S')  ping -> ${code:-timeout}"
  sleep 600   # 10 min; the Space sleeps after ~15 min idle
done
