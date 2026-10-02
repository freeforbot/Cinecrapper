#!/bin/bash
echo "🚀 Starting CineScraper Unified Worker..."

while true; do
  echo "🤖 Starting TMDB Wizard Bot..."
  python wizard_bot.py &
  
  echo "🎬 Starting CineScraper Userbot..."
  python cinescraper.py
  
  echo "⚠️ Process exited. Restarting in 5 seconds..."
  sleep 5
done