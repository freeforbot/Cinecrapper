#!/bin/bash
echo "🚀 Starting Wizard Bot ($(python --version))"
while true; do
  python wizard_bot.py
  echo "⚠️ Wizard bot exited. Restarting in 5 seconds..."
  sleep 5
done
