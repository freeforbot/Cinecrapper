import os
import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client

print("=== CineScraper Dual-Account Session Generator ===")
print("You will need your API_ID and API_HASH from my.telegram.org")
api_id = input("Enter your API_ID: ")
api_hash = input("Enter your API_HASH: ")

print("\n--- Starting Login for Account 1 ---")
print("You will be asked to enter your phone number (including country code, e.g., +91...)")
app1 = Client("my_account_1", api_id=api_id, api_hash=api_hash, in_memory=True)

with app1:
    session_string_1 = app1.export_session_string()
    print("✅ Account 1 successfully logged in!")

session_string_2 = ""
print("\n--- Starting Login for Account 2 ---")
print("If you only want one account, you can press Ctrl+C to skip this step.")
try:
    app2 = Client("my_account_2", api_id=api_id, api_hash=api_hash, in_memory=True)
    with app2:
        session_string_2 = app2.export_session_string()
        print("✅ Account 2 successfully logged in!")
except KeyboardInterrupt:
    print("\n⚠️ Skipped generating session for Account 2.")

with open(".env", "w") as f:
    f.write(f"API_ID={api_id}\n")
    f.write(f"API_HASH={api_hash}\n")
    f.write(f"SESSION_STRING={session_string_1}\n")
    if session_string_2:
        f.write(f"SESSION_STRING_2={session_string_2}\n")
    f.write("MONGO_URI=" + input("Enter your MONGO_URI: ").strip() + "\n")
    f.write("TARGET_BOT=@Aiv2trbot\n")

print("\n✅ Successfully generated session string(s) and saved to .env file!")
print("⚠️ Please open the .env file, copy SESSION_STRING and SESSION_STRING_2,")
print("⚠️ and update your Environment Variables in the Render dashboard.")
