import asyncio
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import os
from pyrogram import Client
from dotenv import load_dotenv

load_dotenv()

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
SESSION_STRING = os.environ.get("SESSION_STRING")

CONTROL_ROOM_ID = -1004371706867
DATABASE_CHANNEL_ID = -1003975570574

async def migrate_movies():
    print("Starting migration...")
    app = Client("migrator_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
    await app.start()
    print("Logged in successfully!")
    
    migrated_count = 0
    try:
        # Fetch all dialogs first to cache Peer IDs and avoid "Peer id invalid"
        print("Caching chats...")
        async for dialog in app.get_dialogs():
            pass
            
        print("Starting file extraction from Control Room...")
        # Loop through all messages in the Control Room
        async for message in app.get_chat_history(chat_id=CONTROL_ROOM_ID):
            if message.document or message.video:
                try:
                    await app.copy_message(
                        chat_id=DATABASE_CHANNEL_ID,
                        from_chat_id=CONTROL_ROOM_ID,
                        message_id=message.id,
                        caption=message.caption
                    )
                    migrated_count += 1
                    if migrated_count % 10 == 0:
                        print(f"Migrated {migrated_count} files...")
                    await asyncio.sleep(1.5) # Anti-flood delay
                except Exception as e:
                    print(f"Failed to copy message {message.id}: {e}")
                
    except Exception as e:
        print(f"Migration error: {e}")
        
    print(f"Migration Complete! Total files copied: {migrated_count}")
    await app.stop()

if __name__ == "__main__":
    asyncio.run(migrate_movies())
