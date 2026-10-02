#!/bin/bash
echo "🚀 Starting CineScraper Unified Worker..."

# Wizard bot: started ONCE in its own restart loop (never duplicated)
(
  while true; do
    echo "🤖 Starting TMDB Wizard Bot..."
    python wizard_bot.py
    echo "⚠️ Wizard bot exited. Restarting in 5 seconds..."
    sleep 5
  done
) &

# Userbot: restarts on its own without touching the wizard bot
while true; do
  echo "🎬 Starting CineScraper Userbot..."
  python cinescraper.py
  echo "⚠️ Userbot exited. Restarting in 5 seconds..."
  sleep 5
done
