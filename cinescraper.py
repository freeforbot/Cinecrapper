import os
import re
import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from dotenv import load_dotenv
from pyrogram import Client, filters
from motor.motor_asyncio import AsyncIOMotorClient

# Load credentials from .env
load_dotenv()
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
SESSION_STRING_1 = os.environ.get("SESSION_STRING")
SESSION_STRING_2 = os.environ.get("SESSION_STRING_2", "")
TARGET_BOT = os.environ.get("TARGET_BOT", "@Movie_Provider_Infinity_Bot")
MONGO_URI = os.environ.get("MONGO_URI")

import google.generativeai as genai

# Load up to 5 Gemini keys
GEMINI_KEYS = []
for i in range(1, 6):
    key = os.environ.get(f"GEMINI_API_KEY_{i}")
    if key:
        GEMINI_KEYS.append(key)
        
# Fallback to the original GEMINI_API_KEY if no numbered keys exist
if not GEMINI_KEYS:
    fallback_key = os.environ.get("GEMINI_API_KEY")
    if fallback_key:
        GEMINI_KEYS.append(fallback_key)

active_gemini_index = 0

async def ask_gemini(prompt):
    global active_gemini_index
    if not GEMINI_KEYS:
        raise Exception("No GEMINI_API_KEY configured!")
        
    for _ in range(len(GEMINI_KEYS)):
        try:
            current_key = GEMINI_KEYS[active_gemini_index]
            genai.configure(api_key=current_key)
            model = genai.GenerativeModel('gemini-3.6-flash')
            response = await asyncio.get_event_loop().run_in_executor(None, lambda: model.generate_content(prompt))
            return response.text.strip()
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "quota" in error_str or "exhausted" in error_str:
                print(f"⚠️ Gemini Key {active_gemini_index + 1} exhausted! Rotating to next key...")
                active_gemini_index = (active_gemini_index + 1) % len(GEMINI_KEYS)
            else:
                raise e
    raise Exception("❌ All Gemini API Keys are currently rate limited!")

# Dual Account Rotation Logic (Persistent via MongoDB)
import time
ACTIVE_ACCOUNT = "1"
LAST_SWITCH_TIME = time.time()
try:
    from pymongo import MongoClient
    sync_client = MongoClient(MONGO_URI)
    sync_state = sync_client["cinesearch_db"]["scraper_state"]
    account_doc = sync_state.find_one({"_id": "active_account"})
    if account_doc:
        ACTIVE_ACCOUNT = account_doc.get("account", "1")
        LAST_SWITCH_TIME = account_doc.get("last_switch_time", time.time())
except Exception as e:
    print(f"Failed to fetch active account from DB: {e}")

if ACTIVE_ACCOUNT == "2" and SESSION_STRING_2:
    SESSION_STRING = SESSION_STRING_2
    print("🔄 [DUAL-ACCOUNT ENGINE] Booting up using Account 2!")
elif ACTIVE_ACCOUNT == "2" and not SESSION_STRING_2:
    SESSION_STRING = SESSION_STRING_1
    ACTIVE_ACCOUNT = "1"
    print("⚠️ [DUAL-ACCOUNT ENGINE] SESSION_STRING_2 is missing! Falling back to Account 1!")
else:
    SESSION_STRING = SESSION_STRING_1
    ACTIVE_ACCOUNT = "1"
    print("🔄 [DUAL-ACCOUNT ENGINE] Booting up using Account 1!")

CONTROL_CHANNEL_ID = int(os.environ.get("CONTROL_CHANNEL_ID", "-1004371706867"))
# Send files directly to the Control Channel for CineSearch Bot to process
CINESEARCH_BOT = CONTROL_CHANNEL_ID

# MongoDB Setup
db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["cinesearch_db"]
queue_col = db["scrape_queue"]
movies_col = db_client["telegram_bot"]["movies"]
state_col = db["scraper_state"]

queue_col_v2 = db["scrape_queue_v2"]
state_col_v2 = db["scraper_state_v2"]
TARGET_BOT_V2 = "@Aiv2trbot"


import time
from datetime import datetime, timezone, timedelta

async def ensure_dashboard():
    await asyncio.sleep(5)
    state = await state_col.find_one({"_id": "main_state"}) or {}
    if not state.get("is_paused", True):
        if not state.get("dashboard_msg_id"):
            msg = await app.send_message(CONTROL_CHANNEL_ID, "🚀 **Scraper Rebooted!** Generating Dashboard...")
            try:
                await msg.pin()
            except:
                pass
            await state_col.update_one(
                {"_id": "main_state"}, 
                {"$set": {"dashboard_msg_id": msg.id, "session_start": time.time()}}, 
                upsert=True
            )
            await update_dashboard(app)

async def update_dashboard(client):
    try:
        state = await state_col.find_one({"_id": "main_state"}) or {}
        if not state.get("dashboard_msg_id"): return
        
        status = "⏸️ PAUSED" if state.get("is_paused") else "▶️ RUNNING"
        
        # Calculate IST Times
        ist_offset = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(ist_offset)
        last_updated = now.strftime('%I:%M:%S %p')
        
        start_ts = state.get("session_start", time.time())
        start_time_str = datetime.fromtimestamp(start_ts, ist_offset).strftime('%I:%M %p')
        end_time_str = datetime.fromtimestamp(start_ts + (5 * 3600), ist_offset).strftime('%I:%M %p')
        
        # Pull global queue counts
        total_pending = await queue_col.count_documents({"status": "pending"})
        total_completed = await queue_col.count_documents({"status": "completed"})
        total_failed = await queue_col.count_documents({"status": "failed"})
        
        current = state.get("current_movie")
        current_text = f"\n🔍 **Processing:** `{current}`\n" if current else ""
        
        text = f"""📊 **Scraper Live Dashboard**

🟢 **Active Account:** {ACTIVE_ACCOUNT}
⏰ **Shift Time:** {start_time_str} - {end_time_str} (IST)
⚙️ **Status:** {status}
{current_text}
✅ **Total Completed:** {total_completed}
❌ **Total Failed:** {total_failed}
⏳ **Queue Remaining:** {total_pending}

_Last updated: {last_updated}_"""
        await client.edit_message_text(CONTROL_CHANNEL_ID, state["dashboard_msg_id"], text)
    except Exception as e:
        print(f"Dashboard Update Error: {e}")

def format_size(size_bytes):
    if not size_bytes:
        return "Unknown"
    size_mb = size_bytes / (1024 * 1024)
    if size_mb > 1024:
        return f"{size_mb/1024:.2f} GB"
    return f"{size_mb:.2f} MB"

def generate_beautiful_caption(file_name, file_size_bytes):
    size_str = format_size(file_size_bytes)
    fname_lower = file_name.lower()
    
    # Detect Quality
    quality = "Unknown"
    if "1080" in fname_lower: quality = "1080p"
    elif "720" in fname_lower: quality = "720p"
    elif "480" in fname_lower: quality = "480p"
    elif "360" in fname_lower: quality = "360p"
    elif "2160" in fname_lower or "4k" in fname_lower: quality = "2160p"
    
    # Detect Audio
    audio = []
    if "tamil" in fname_lower: audio.append("Tamil")
    if "telugu" in fname_lower: audio.append("Telugu")
    if "hindi" in fname_lower: audio.append("Hindi")
    if "malayalam" in fname_lower: audio.append("Malayalam")
    if "kannada" in fname_lower: audio.append("Kannada")
    if "english" in fname_lower: audio.append("English")
    if "multi" in fname_lower and not audio: audio.append("Multi")
    audio_str = ", ".join(audio) if audio else "Unknown"    # Detect Subtitles
    subtitle = "Unknown"
    if "esub" in fname_lower: subtitle = "English"
    elif "msub" in fname_lower: subtitle = "Multiple"
        
    # Detect Year
    import re
    year = "Unknown"
    ymatch = re.search(r'\b(19\d{2}|20\d{2})\b', file_name)
    if ymatch:
        year = ymatch.group(1)
    
    # Build Caption
    return f"""📜 Name: {file_name}
┏ 💾 Size: {size_str}
{file_name}

    👉 🗓 Year : {year}
◧ 🎬 Quality : {quality}
◧ 🔊 Audio : {audio_str}
◧ 💬 Subtitle : {subtitle}

[@CinePrimeHub](https://t.me/+bTXW_3aYxlw3YWU1)"""

# Initialize Pyrogram Userbot
app = Client(
    "cinescraper",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

import logging
from logging.handlers import RotatingFileHandler

# Set up logging to a file
logger = logging.getLogger("cinescraper")
logger.setLevel(logging.DEBUG)
handler = RotatingFileHandler("scraper.log", maxBytes=1024*1024, backupCount=1)
formatter = logging.Formatter('%(asctime)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Overwrite built-in print to also log to file
import builtins
_original_print = builtins.print
def custom_print(*args, **kwargs):
    _original_print(*args, **kwargs)
    message = " ".join(str(a) for a in args)
    logger.info(message)
builtins.print = custom_print

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("logs", prefixes="/"))
async def logs_command(client, message):
    try:
        with open("scraper.log", "r", encoding="utf-8") as f:
            lines = f.readlines()
        last_lines = "".join(lines[-30:])
        if not last_lines:
            last_lines = "No logs yet."
        await message.reply_text(f"""📝 **Recent Logs:**

`
{last_lines}
`""")
    except Exception as e:
        await message.reply_text(f"Error reading logs: {e}")


@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("start", prefixes="/"))
async def start_command(client, message):
    await state_col.update_one(
        {"_id": "main_state"},
        {"$set": {"is_paused": False, "session_start": time.time()}},
        upsert=True
    )
    msg = await message.reply_text("🚀 **Scraper Started!** Creating Live Dashboard...")
    try:
        await msg.pin()
    except Exception:
        pass
    
    await state_col.update_one({"_id": "main_state"}, {"$set": {"dashboard_msg_id": msg.id}})
    await update_dashboard(client)

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("pause", prefixes="/"))
async def pause_command(client, message):
    await state_col.update_one({"_id": "main_state"}, {"$set": {"is_paused": True}}, upsert=True)
    await update_dashboard(client)
    await message.reply_text("⏸️ **Scraper Paused!** Queue is preserved.")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("stop", prefixes="/"))
async def stop_command(client, message):
    await state_col.update_one({"_id": "main_state"}, {"$set": {"is_paused": True}}, upsert=True)
    result = await queue_col.delete_many({"status": "pending"})
    await update_dashboard(client)
    await message.reply_text(f"🛑 **Scraper Stopped!**\nDeleted {result.deleted_count} pending movies.")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("switch", prefixes="/"))
async def switch_command(client, message):
    text = message.text.replace("/switch", "").strip()
    if text not in ["1", "2"]:
        await message.reply_text("⚠️ Please specify an account to switch to. Example: `/switch 1` or `/switch 2`")
        return
        
    if text == "2" and not SESSION_STRING_2:
        await message.reply_text("❌ **ERROR:** You cannot switch to Account 2 because `SESSION_STRING_2` is missing from your Environment Variables! Please add it in Render first.")
        return
        
    if text == ACTIVE_ACCOUNT:
        await message.reply_text(f"⚠️ Already running on Account {text}!")
        return
        
    msg = await message.reply_text(f"🔄 **Manual Override:** Switching to Account {text}...")
    
    # Delete current dashboard and force auto-resume state so next account creates a fresh one immediately
    try:
        state = await state_col.find_one({"_id": "main_state"}) or {}
        if state.get("dashboard_msg_id"):
            await app.delete_messages(CONTROL_CHANNEL_ID, state["dashboard_msg_id"])
        
        # Ensure the bot automatically starts running without needing a manual /start
        await state_col.update_one(
            {"_id": "main_state"}, 
            {"$set": {"dashboard_msg_id": None, "is_paused": False}}
        )
    except Exception:
        pass
        
    await state_col.update_one({"_id": "active_account"}, {"$set": {"account": text, "last_switch_time": time.time()}}, upsert=True)
        
    await msg.edit_text(f"✅ Rebooting as **Account {text}**...\n*(Render will automatically restart the bot in 5-10 seconds)*")
    import os
    os._exit(1)

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("debugenv", prefixes="/"))
async def debugenv_command(client, message):
    s2 = os.environ.get("SESSION_STRING_2", "")
    await message.reply_text(f"🛠️ **Debug Info:**\n- SESSION_STRING_2 length: {len(s2)}\n- ACTIVE_ACCOUNT from DB: {ACTIVE_ACCOUNT}")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("version", prefixes="/"))
async def version_command(client, message):
    await message.reply_text("✅ **Version:** `1.2 - Diagnostic Click Active`")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("testclick", prefixes="/"))
async def testclick_command(client, message):
    try:
        async for msg in client.get_chat_history(TARGET_BOT, limit=5):
            if msg.reply_markup and msg.reply_markup.inline_keyboard:
                debug_text = "🛠️ **Test Click Diagnostic:**\n"
                target_btn = None
                for row in msg.reply_markup.inline_keyboard:
                    for btn in row:
                        if getattr(btn, 'text', None):
                            debug_text += f"- `{btn.text}` (URL: {bool(getattr(btn, 'url', None))}, Callback: {bool(getattr(btn, 'callback_data', None))})\n"
                            if "send" in normalize_text(btn.text) and not target_btn:
                                target_btn = btn
                
                await message.reply_text(debug_text)
                
                if target_btn and getattr(target_btn, 'callback_data', None):
                    try:
                        await message.reply_text("⏳ Attempting to click Send All...")
                        res = await client.request_callback_answer(chat_id=TARGET_BOT, message_id=msg.id, callback_data=target_btn.callback_data, timeout=10)
                        await message.reply_text(f"✅ **Callback Response:**\n`{res}`")
                    except Exception as e:
                        await message.reply_text(f"❌ **Callback Error:** `{e}`")
                elif target_btn and getattr(target_btn, 'url', None):
                    await message.reply_text(f"⚠️ **Button has URL instead of Callback:** `{target_btn.url}`")
                
                return
        await message.reply_text("❌ No recent menu found!")
    except Exception as e:
        await message.reply_text(f"❌ **Error:** `{e}`")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("add", prefixes="/"))
async def add_command(client, message):
    text = message.text.replace("/add", "").strip()
    if not text:
        await message.reply_text("⚠️ Please provide a list of movies.\nExample:\n`/add\nIron Man\nThe Avengers`")
        return
        
    original_movies = [re.sub(r'^\d+\.\s*', '', m.strip()) for m in text.split("\n") if m.strip()]
    status_msg = await message.reply_text("🤖 **AI Processor Initiated!**\nFetching database records to prevent duplicates...")
    
    try:
        if not GEMINI_KEYS:
            await status_msg.edit_text("⚠️ **No GEMINI_API_KEY found!** Using classic queue.")
            movies_to_queue = original_movies
        else:
            list_string = "\n".join(original_movies)
            await status_msg.edit_text("🤖 **AI Processor:** Fixing spelling and formatting your list...")
            prompt = f"""You are a movie title expert. 
Here is a list of movies someone wants to search for:
<list>
{list_string}
</list>

INSTRUCTIONS:
1. Fix any obvious spelling mistakes (e.g. "Ironman" -> "Iron Man").
2. Remove any extra garbage text.
3. Return ONLY a plain text list of the cleaned movie titles, one per line. Do not say anything else.
"""
            clean_list_text = await ask_gemini(prompt)
            ai_cleaned_movies = [m.strip() for m in clean_list_text.split("\n") if m.strip()]
            await status_msg.edit_text(f"✅ **AI Analysis Complete!**\nNow cross-referencing {len(ai_cleaned_movies)} movies against your database...")
    except Exception as e:
        await message.reply_text(f"❌ AI Processor Error: {e}\nFalling back to classic queue...")
        ai_cleaned_movies = original_movies
    
    added_count = 0
    skipped_count = 0
    
    total_movies = len(ai_cleaned_movies)
    
    # --- FAST IN-MEMORY CHECK ---
    await status_msg.edit_text(f"⚡ **Fast Scanning Database...** Loading index...")
    db_cursor = movies_col.find({}, {"file_name": 1})
    db_files = [doc.get("file_name", "").lower() for doc in await db_cursor.to_list(length=None)]
    
    queue_cursor = queue_col.find({}, {"movie_name": 1})
    queue_items = {doc.get("movie_name", "").lower() for doc in await queue_cursor.to_list(length=None)}
    
    docs_to_insert = []
    for i, movie in enumerate(ai_cleaned_movies, 1):
        if i % 1000 == 0:
            try:
                await status_msg.edit_text(f"⚡ **Fast Scanning:** {i} / {total_movies} checked...")
            except Exception:
                pass
                
        movie_lower = movie.lower()
        if movie_lower in queue_items:
            skipped_count += 1
            continue
            
        words = re.findall(r'[a-zA-Z0-9]+', movie_lower)
        already_in_db = False
        if words:
            for db_file in db_files:
                if all(word in db_file for word in words):
                    already_in_db = True
                    break
        
        if already_in_db:
            skipped_count += 1
            docs_to_insert.append({"movie_name": movie, "status": "completed"})
            continue
            
        docs_to_insert.append({"movie_name": movie, "status": "pending"})
        added_count += 1
        
    if docs_to_insert:
        await status_msg.edit_text("💾 **Saving to Queue...**")
        chunk_size = 1000
        for i in range(0, len(docs_to_insert), chunk_size):
            await queue_col.insert_many(docs_to_insert[i:i+chunk_size])
            
    await message.reply_text(f"✅ **Successfully queued {added_count} missing movies!**\n(Skipped {skipped_count} movies already in database/queue).")


@app.on_message(filters.command("ping", prefixes="/"))
async def ping_command(client, message):
    await message.reply_text("🏓 **PONG!** The bot is alive and receiving messages!\nYour Chat ID is: `" + str(message.chat.id) + "`")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("host", prefixes="/"))
async def host_command(client, message):
    import socket
    hostname = socket.gethostname()
    host_env = "Render" if "RENDER" in os.environ else "Back4App/Other"
    await message.reply_text(f"🌐 **Current Host Environment:** {host_env}\n🖥️ **Server ID:** {hostname}\n👤 **Active Telegram Account:** {ACTIVE_ACCOUNT}")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("resetqueue", prefixes="/"))
async def resetqueue_command(client, message):
    """Delete all wrongly-completed queue entries so movies can be re-queued from a PDF."""
    args = message.text.split(None, 1)
    try:
        status_msg = await message.reply_text("🔄 **Resetting queue...**\nScanning for wrongly-completed entries...")
        
        # Load all movie file_names from the real database
        db_cursor = movies_col.find({}, {"file_name": 1})
        db_files = set(doc.get("file_name", "").lower() for doc in await db_cursor.to_list(length=None))
        
        # Find all "completed" entries in the queue
        completed_cursor = queue_col.find({"status": "completed"}, {"_id": 1, "movie_name": 1})
        completed_docs = await completed_cursor.to_list(length=None)
        
        # Check which ones are NOT actually in the main database (i.e., wrongly marked)
        wrong_ids = []
        for doc in completed_docs:
            movie = doc.get("movie_name", "").lower()
            words = re.findall(r'[a-zA-Z0-9]+', movie)
            in_db = False
            if words:
                for db_file in db_files:
                    if all(word in db_file for word in words):
                        in_db = True
                        break
            if not in_db:
                wrong_ids.append(doc["_id"])
        
        if wrong_ids:
            result = await queue_col.delete_many({"_id": {"$in": wrong_ids}})
            await status_msg.edit_text(
                f"✅ **Queue Reset Complete!**\n"
                f"🗑️ Deleted **{result.deleted_count}** wrongly-completed entries.\n"
                f"📤 You can now re-upload your PDF file and all movies will be re-queued!"
            )
        else:
            await status_msg.edit_text("✅ **Queue is clean!** No wrongly-completed entries found.")
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {e}")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("force", prefixes="/"))
async def force_command(client, message):
    try:
        movie = message.text.split(" ", 1)[1].strip()
        if not movie:
            raise ValueError()
        await queue_col.insert_one({"movie_name": movie, "status": "pending", "force": True, "is_urgent": True})
        await message.reply_text(f"""✅ **Forced:** {movie}
It has been urgently queued and will completely bypass the database check!""")
    except Exception:
        await message.reply_text("⚠️ Usage: /force Movie Name 2024")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("clean_tags", prefixes="/"))
async def clean_tags_command(client, message):
    status_msg = await message.reply_text("dY-,? **Bulk Cleaner Started!**\nScanning entire Database Channel history to remove @ tags and junk...")
    
    DATABASE_CHANNEL_ID = -1003975570574
    updated_count = 0
    db_updated = 0
    import re
    
    try:
        async for msg in client.get_chat_history(DATABASE_CHANNEL_ID):
            if msg.caption:
                new_caption = re.sub(r'@[a-zA-Z0-9_]+', '', msg.caption)
                new_caption = re.sub(r'(?i)CharmeLeon', '', new_caption)
                
                # Also clean Name: field in caption if it has a hyphen encoder
                lines = new_caption.split('\n')
                for i, line in enumerate(lines):
                    if line.startswith('📁 Name:') or line.startswith('Name:'):
                        if ' - ' in line:
                            parts = line.rsplit(' - ', 1)
                            ext = ''
                            if '.' in parts[1]:
                                ext = '.' + parts[1].rsplit('.', 1)[-1]
                            lines[i] = parts[0] + ext
                new_caption = '\n'.join(lines)
                
                if new_caption != msg.caption:
                    try:
                        await client.edit_message_caption(
                            chat_id=DATABASE_CHANNEL_ID,
                            message_id=msg.id,
                            caption=new_caption,
                            reply_markup=msg.reply_markup
                        )
                        updated_count += 1
                        await asyncio.sleep(2)
                    except Exception as e:
                        pass
                        
        # Clean MongoDB too
        async for doc in movies_col.find({}):
            fname = doc.get("file_name", "")
            new_fname = re.sub(r'@[a-zA-Z0-9_]+', '', fname)
            new_fname = re.sub(r'(?i)CharmeLeon', '', new_fname)
            
            if ' - ' in new_fname:
                parts = new_fname.rsplit(' - ', 1)
                ext = ''
                if '.' in parts[1]:
                    ext = '.' + parts[1].rsplit('.', 1)[-1]
                new_fname = parts[0] + ext
            
            if new_fname != fname:
                await movies_col.update_one({"_id": doc["_id"]}, {"$set": {"file_name": new_fname}})
                db_updated += 1
                
        await status_msg.edit_text(f"✅ **Bulk Clean Complete!**\n\n🧹 Removed tags from **{updated_count}** messages.\n💾 Cleaned **{db_updated}** records in Database.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {e}")


@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("rename", prefixes="/"))
async def rename_command(client, message):
    try:
        args = message.text.split(" ", 1)[1].strip()
        if "|" not in args:
            raise ValueError()
            
        old_word, new_word = [x.strip() for x in args.split("|", 1)]
        if not old_word or not new_word:
            raise ValueError()
    except Exception:
        await message.reply_text("⚠️ Usage: `/rename old_name | new_name`\nExample: `/rename Modha | Mudhal Rathiri`")
        return
        
    status_msg = await message.reply_text(f"🔍 **Searching Database Channel for:** `{old_word}`...")
    
    DATABASE_CHANNEL_ID = -1003975570574
    
    updated_count = 0
    try:
        async for msg in client.search_messages(chat_id=DATABASE_CHANNEL_ID, query=old_word):
            if msg.caption:
                import re
                new_caption = re.sub(re.escape(old_word), new_word, msg.caption, flags=re.IGNORECASE)
                if new_caption != msg.caption:
                    try:
                        await client.edit_message_caption(
                            chat_id=DATABASE_CHANNEL_ID,
                            message_id=msg.id,
                            caption=new_caption,
                            reply_markup=msg.reply_markup
                        )
                        updated_count += 1
                        await asyncio.sleep(2)
                    except Exception as e:
                        print(f"Error editing message {msg.id}: {e}")
                        
        db_updated = 0
        import re
        regex = re.compile(re.escape(old_word), re.IGNORECASE)
        async for doc in movies_col.find({"file_name": regex}):
            new_file_name = regex.sub(new_word, doc["file_name"])
            await movies_col.update_one({"_id": doc["_id"]}, {"$set": {"file_name": new_file_name}})
            db_updated += 1
            
        await status_msg.edit_text(f"✅ **Rename Complete!**\n✏️ Edited **{updated_count}** messages in the Database Channel.\n💾 Updated **{db_updated}** records in MongoDB.\n\n(`{old_word}` ➡️ `{new_word}`)")
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Error during rename: {e}")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & (filters.document | filters.command(["queue", "forcequeue"], prefixes="/")))
async def unified_file_handler(client, message):
    """Unified handler for PDF/TXT uploads. ALWAYS queues everything with force: True."""
    doc_message = message
    
    if message.text:
        if not message.reply_to_message or not getattr(message.reply_to_message, 'document', None):
            await message.reply_text("⚠️ Please reply to a PDF or TXT file!")
            return
        doc_message = message.reply_to_message
            
    file_name = doc_message.document.file_name.lower()
    if not (file_name.endswith(".txt") or file_name.endswith(".pdf")):
        return
        
    status_msg = await message.reply_text("⚡ **Processing File...** Downloading...")
    file_path = await doc_message.download()
    
    try:
        import re
        movies = []
        if file_name.endswith(".txt"):
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            movies = [re.sub(r'^\d+\.\s*', '', m.strip()) for m in lines if m.strip()]
        elif file_name.endswith(".pdf"):
            import pypdf
            await status_msg.edit_text("⚡ Parsing PDF file...")
            with open(file_path, "rb") as fh:
                reader = pypdf.PdfReader(fh)
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        lines = text.split("\n")
                        page_movies = [re.sub(r'^\d+\.\s*', '', m.strip()) for m in lines if m.strip()]
                        movies.extend(page_movies)
        
        import os
        os.remove(file_path)
        
        movies = list(dict.fromkeys(movies))
        movies = [m for m in movies if len(m) > 2]
        
        if not movies:
            await status_msg.edit_text("❌ No movies found in the file.")
            return

        total = len(movies)
        
        await status_msg.edit_text(f"⚡ **POWER MODE ACTIVE!**\nInjecting {total} movies into queue without scanning database...")
        
        chunk_size = 1000
        inserted = 0
        for i in range(0, total, chunk_size):
            chunk = movies[i:i+chunk_size]
            docs = [{"movie_name": m, "status": "pending", "force": True} for m in chunk]
            await queue_col.insert_many(docs)
            inserted += len(chunk)
        
        await status_msg.edit_text(f"✅ **Queue Complete!**\n🚀 {inserted} movies queued instantly.\nThey will be scraped even if they exist in the database.")
        await update_dashboard(client)

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {e}")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("status", prefixes="/"))
async def status_command(client, message):
    status_msg = await message.reply_text("🤖 **AI Status Analyzer Initiated!** Fetching stats...")
    pending = await queue_col.count_documents({"status": "pending"})
    completed = await queue_col.count_documents({"status": "completed"})
    failed = await queue_col.count_documents({"status": "failed"})
    
    if not GEMINI_KEYS:
        await status_msg.edit_text(f"📊 **Scrape Status**\n✅ Completed: {completed}\n❌ Failed: {failed}\n⏳ Upcoming: {pending}")
        return
        
    try:
        # Get some recent failures and upcoming to pass to AI
        fail_list = await queue_col.find({"status": "failed"}).limit(10).to_list(length=None)
        fail_text = ", ".join([d["movie_name"] for d in fail_list]) or "None"
        up_list = await queue_col.find({"status": "pending"}).limit(10).to_list(length=None)
        up_text = ", ".join([d["movie_name"] for d in up_list]) or "None"
        
        prompt = f"Act as an enthusiastic AI assistant. Write a short, beautifully formatted Telegram status report using emojis. Stats: {completed} completed, {failed} failed, {pending} upcoming. Recent failures: {fail_text}. Next up: {up_text}. Keep it brief!"
        clean_status_text = await ask_gemini(prompt)
        await status_msg.edit_text(clean_status_text)
    except Exception as e:
        await status_msg.edit_text(f"📊 **Scrape Status**\n✅ Completed: {completed}\n❌ Failed: {failed}\n⏳ Upcoming: {pending}")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("list", prefixes="/"))
async def list_command(client, message):
    status_msg = await message.reply_text("🔄 Generating full Excel (CSV) export...")
    try:
        import os, csv
        file_path = "Movies_Report.csv"
        
        all_movies = await queue_col.find({}).to_list(length=None)
        
        # Deduplicate by movie name (keeping 'completed' over 'pending' or 'failed' if there's a clash)
        unique_movies = {}
        for doc in all_movies:
            name = doc.get("movie_name", "Unknown").strip().title()
            if name not in unique_movies:
                unique_movies[name] = doc
            else:
                if doc.get("status") == "completed":
                    unique_movies[name] = doc
        
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Movie Name (AI)", "Status", "Original File Name", "TMDB ID", "Type"])
            
            for doc in unique_movies.values():
                writer.writerow([
                    doc.get("movie_name", "Unknown"),
                    doc.get("status", "Unknown"),
                    doc.get("original_file_name", ""),
                    doc.get("tmdb_id", ""),
                    doc.get("type", "")
                ])
                
        await client.send_document(chat_id=message.chat.id, document=file_path, caption=f"📊 **Movies Database Report**\nContains {len(unique_movies)} unique movies.\nOpen this CSV file in Microsoft Excel.")
        await status_msg.delete()
        os.remove(file_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed: {e}")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("menu", prefixes="/"))
async def menu_command(client, message):
    menu_text = """🎮 **CineScraper Control Menu**

**🌙 Moonknight Bot (Movies)**
/start - Boot up the Moonknight scraper
/pause - Temporarily pause Moonknight
/stop - Stop Moonknight and WIPE queue
/status - Moonknight status report
/add - Add movies to Moonknight
/list - Export Moonknight history
/queue_auto - Fetch & queue TMDB trending movies

**🤖 Aiv2trbot (Alphabet)**
/start2 - Boot up the Aiv2trbot scraper
/pause2 - Temporarily pause Aiv2trbot
/stop2 - Stop Aiv2trbot and WIPE queue
/status2 - Aiv2trbot status report
/add2 - Add letters/lists to Aiv2trbot

**⚙️ System Controls**
/start_all - Boot up BOTH bots simultaneously
/pause_all - Pause BOTH bots
/stop_all - Stop BOTH bots and WIPE both queues
/status_all - Status report for BOTH bots
/switch 1 - Force switch to Account 1
/switch 2 - Force switch to Account 2
/migrate - Migrate Control Room files to Database
/menu - Show this help menu"""
    await message.reply_text(menu_text)

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("start_all", prefixes="/"))
async def start_all_command(client, message):
    await start_command(client, message)
    import asyncio
    await asyncio.sleep(1)
    await start_command_v2(client, message)

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("pause_all", prefixes="/"))
async def pause_all_command(client, message):
    await pause_command(client, message)
    await pause_command_v2(client, message)

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("stop_all", prefixes="/"))
async def stop_all_command(client, message):
    await stop_command(client, message)
    await stop_command_v2(client, message)

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("status_all", prefixes="/"))
async def status_all_command(client, message):
    await status_command(client, message)
    import asyncio
    await asyncio.sleep(1)
    await status_command_v2(client, message)


@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("migrate", prefixes="/"))
async def migrate_command(client, message):
    status_msg = await message.reply_text("🔄 **Starting Background Migration!**\n\nI am now pulling all files from this Control Room and sending them to the Database Channel.\n\nThis will take approximately 1-2 hours. You can continue using the bot normally while this runs in the background!")
    
    # Run the migration in an asynchronous background task so it doesn't block the bot
    async def run_migration():
        DATABASE_CHANNEL_ID = -1003975570574
        migrated_count = 0
        duplicate_count = 0
        try:
            async for msg in client.get_chat_history(chat_id=CONTROL_CHANNEL_ID):
                if msg.document or msg.video:
                    try:
                        file_obj = msg.document or msg.video
                        unique_id = getattr(file_obj, 'file_unique_id', None)
                        
                        if unique_id:
                            if await db["migrated_files"].find_one({"file_unique_id": unique_id}):
                                duplicate_count += 1
                                continue
                                
                        await client.copy_message(
                            chat_id=DATABASE_CHANNEL_ID,
                            from_chat_id=CONTROL_CHANNEL_ID,
                            message_id=msg.id,
                            caption=msg.caption
                        )
                        
                        if unique_id:
                            await db["migrated_files"].insert_one({"file_unique_id": unique_id})
                            
                        migrated_count += 1
                        if migrated_count % 50 == 0:
                            await status_msg.edit_text(f"🚀 **Migration in Progress...**\nSuccessfully moved {migrated_count} files.\nIgnored {duplicate_count} duplicates.")
                        await asyncio.sleep(1.5)
                    except Exception as e:
                        print(f"Failed to copy message {msg.id}: {e}")
            await status_msg.edit_text(f"✅ **Migration Complete!**\nSuccessfully moved {migrated_count} files.\nIgnored {duplicate_count} duplicates.")
        except Exception as e:
            await status_msg.edit_text(f"❌ **Migration Failed!**\nError: {e}")
            
    asyncio.create_task(run_migration())

import aiohttp

async def fetch_tmdb_movies(pages=5):
    """Fetches trending and new Tamil/Global movies from TMDB."""
    movies = []
    TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
    async with aiohttp.ClientSession() as session:
        # Fetch Popular TAMIL Movies
        for page in range(1, int(pages/2) + 1):
            url = f"https://api.tmdb.org/3/discover/movie?api_key={TMDB_API_KEY}&with_original_language=ta&sort_by=popularity.desc&page={page}"
            async with session.get(url) as resp:
                data = await resp.json()
                for item in data.get('results', []):
                    if item.get('release_date'):
                        movies.append(item.get('title'))
                        
        # Fetch New TAMIL Movies
        for page in range(1, int(pages/2) + 1):
            url = f"https://api.tmdb.org/3/discover/movie?api_key={TMDB_API_KEY}&with_original_language=ta&sort_by=primary_release_date.desc&page={page}"
            async with session.get(url) as resp:
                data = await resp.json()
                for item in data.get('results', []):
                    if item.get('release_date'):
                        movies.append(item.get('title'))
                        
        # Fetch Trending Global Movies
        for page in range(1, 3):
            url = f"https://api.tmdb.org/3/trending/movie/week?api_key={TMDB_API_KEY}&page={page}"
            async with session.get(url) as resp:
                data = await resp.json()
                for item in data.get('results', []):
                    if item.get('release_date'):
                        movies.append(item.get('title'))
    
    # Deduplicate
    return list(dict.fromkeys([m for m in movies if m]))

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("queue_auto", prefixes="/"))
async def queue_auto_command(client, message):
    status_msg = await message.reply_text("🔄 **Auto-Queue Started!**\nConnecting to TMDB to fetch trending and new releases...")
    try:
        tmdb_movies = await fetch_tmdb_movies(pages=20) # Fetches ~400 movies
        if not tmdb_movies:
            await status_msg.edit_text("❌ Failed to fetch movies from TMDB.")
            return
            
        await status_msg.edit_text(f"✅ **TMDB Fetch Complete!**\nFound {len(tmdb_movies)} trending movies.\nNow high-speed cross-referencing against your database...")
        
        added_count = 0
        skipped_count = 0
        docs_to_insert = []
        
        total_movies = len(tmdb_movies)
        for i, movie in enumerate(tmdb_movies, 1):
            if i % 100 == 0:
                try:
                    await status_msg.edit_text(f"🔍 **Scanning Database:** {i} / {total_movies} movies checked...")
                except Exception:
                    pass

            # Check queue
            existing_queue = await queue_col.find_one({"movie_name": movie})
            if existing_queue:
                skipped_count += 1
                continue
                
            # Check DB
            words = re.findall(r'[a-zA-Z0-9]+', movie)
            conditions = [{"file_name": {"$regex": word, "$options": "i"}} for word in words]
            already_in_db = False
            if conditions:
                already_in_db = await movies_col.find_one({"$and": conditions})
                
            if already_in_db:
                skipped_count += 1
                docs_to_insert.append({"movie_name": movie, "status": "completed"})
                continue
                
            docs_to_insert.append({"movie_name": movie, "status": "pending"})
            added_count += 1
            
        if docs_to_insert:
            await queue_col.insert_many(docs_to_insert)
                
        await status_msg.edit_text(f"✅ **Auto-Queue Complete!**\nSuccessfully queued {added_count} new movies from TMDB!\n(Skipped {skipped_count} movies already in database/queue).")
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Error during auto-queue: {e}")


async def process_movie(client, movie, target_bot):
    current_page = 1
    total_success = False
    
    while True:
        # 1. Send query to Target Bot
        await client.send_message(target_bot, movie)
        
        menu_msg_id = None
        active_msg = None
        
        # Find the menu message
        for _ in range(10):
            await asyncio.sleep(2)
            async for msg in client.get_chat_history(target_bot, limit=5):
                if msg.reply_markup and msg.reply_markup.inline_keyboard:
                    # Verify this is the actual results menu and not a random ad
                    is_menu = False
                    for row in msg.reply_markup.inline_keyboard:
                        for btn in row:
                            if getattr(btn, 'text', None) and "send" in normalize_text(btn.text):
                                is_menu = True
                                break
                        if is_menu: break
                        
                    if is_menu:
                        active_msg = msg
                        menu_msg_id = msg.id
                        break
            if active_msg:
                break
                
        if not menu_msg_id:
            return total_success
            
        print(f"🔍 Found results for '{movie}'. Resuming from page {current_page} if needed...")
        
        # Fast-forward to the current_page if we are resuming
        fast_forward_success = True
        for i in range(1, current_page):
            active_msg = await client.get_messages(target_bot, menu_msg_id)
            if not active_msg or getattr(active_msg, 'empty', False) or not active_msg.reply_markup:
                print(f"⚠️ Message deleted during fast-forward! Restarting search...")
                fast_forward_success = False
                break
                
            next_button_callback = None
            for row in active_msg.reply_markup.inline_keyboard:
                for btn in row:
                    if getattr(btn, 'text', None) and "next" in normalize_text(btn.text) and getattr(btn, 'callback_data', None):
                        next_button_callback = btn.callback_data
                        
            if next_button_callback:
                try:
                    await client.request_callback_answer(
                        chat_id=target_bot,
                        message_id=active_msg.id,
                        callback_data=next_button_callback
                    )
                    await asyncio.sleep(4)
                except Exception as e:
                    print(f"⚠️ Fast-forward Next Error: {e}")
                    await asyncio.sleep(4)
            else:
                # No next button found, maybe reached the end?
                return total_success
                
        if not fast_forward_success:
            continue # Message was deleted while fast-forwarding, retry search
            
        # Now continue processing pages from current_page
        finished_all_pages = False
        while True:

            active_msg = await client.get_messages(target_bot, menu_msg_id)
            if not active_msg or getattr(active_msg, 'empty', False) or not active_msg.reply_markup:
                print(f"⏱️ Message auto-deleted by Moon Bot after 5 mins! Stopping gracefully.")
                finished_all_pages = True
                break
                
            send_all_btn = None
            next_button_callback = None
            
            for row in active_msg.reply_markup.inline_keyboard:
                for btn in row:
                    if getattr(btn, 'text', None) and "send" in normalize_text(btn.text):
                        send_all_btn = btn
                    elif getattr(btn, 'text', None) and "next" in normalize_text(btn.text) and getattr(btn, 'callback_data', None):
                        next_button_callback = btn.callback_data
            
            if send_all_btn:
                total_success = True
                if getattr(send_all_btn, 'callback_data', None):
                    try:
                        response = await client.request_callback_answer(
                            chat_id=target_bot,
                            message_id=active_msg.id,
                            callback_data=send_all_btn.callback_data,
                            timeout=15
                        )
                        if getattr(response, 'has_url', False) and getattr(response, 'url', None):
                            url = response.url
                            if "start=" in url:
                                payload = url.split("start=")[-1]
                                await client.send_message(target_bot, f"/start {payload}")
                    except Exception as e:
                        print(f"⚠️ Callback Error: {e}")
                elif getattr(send_all_btn, 'url', None):
                    url = send_all_btn.url
                    if "start=" in url:
                        payload = url.split("start=")[-1]
                        await client.send_message(target_bot, f"/start {payload}")
            else:
                finished_all_pages = True
                break # No send all button found, must be done
                
            await asyncio.sleep(0.3) 
            
            if next_button_callback:
                try:
                    await client.request_callback_answer(
                        chat_id=target_bot,
                        message_id=active_msg.id,
                        callback_data=next_button_callback
                    )
                except Exception as e:
                    print(f"⚠️ Next Button Error: {e}")
                    
                current_page += 1
                await asyncio.sleep(0.3)
            else:
                finished_all_pages = True
                break # No NEXT button found, reached the very last page!
                
        if finished_all_pages:
            return total_success
            
    return total_success


def normalize_text(text):
    if not text: return ''
    t_map = str.maketrans('ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ𝗮𝗯𝗰𝗱𝗲𝗳ɢ𝗵𝗶𝗷ᴋʟ𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇', 
                          'abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz')
    return text.lower().translate(t_map)

async def queue_worker():
    print("Background Worker Started! Waiting for queued movies...")
    # Give the Pyrogram client a moment to fully initialize
    await asyncio.sleep(10) 
    
    while True:
        try:
            # Check pause state before pulling from queue
            state = await state_col.find_one({"_id": "main_state"}) or {}
            if state.get("is_paused", True):
                await asyncio.sleep(10)
                continue
                
            # 1. Try to find an urgent pending movie first
            task = await queue_col.find_one_and_update(
                {"status": "pending", "is_urgent": True},
                {"$set": {"status": "processing"}},
                return_document=True
            )
            
            # 2. If no urgent movies, fall back to normal pending movies
            if not task:
                task = await queue_col.find_one_and_update(
                    {"status": "pending", "is_urgent": {"$ne": True}},
                    {"$set": {"status": "processing"}},
                    return_document=True
                )
                
            if not task:
                await asyncio.sleep(10) # Check again in 10s
                continue
                
            movie = task["movie_name"]
            
            # --- AI SPELLING AUTOCORRECT ---
            try:
                if GEMINI_KEYS:
                    prompt = f"You are a spell-checker. Correct the spelling of this movie or TV show title to perfectly match its official IMDB name. Do NOT add years or extra text. Output strictly ONLY the corrected title and nothing else. If it is already correct, output it exactly. Input: {movie}"
                    corrected = await ask_gemini(prompt)
                    if corrected and len(corrected) < 40 and "sorry" not in corrected.lower() and "cannot" not in corrected.lower():
                        await queue_col.update_one({"_id": task["_id"]}, {"$set": {"movie_name": corrected}})
                        if movie != corrected:
                            print(f"✨ AI Spelling Correction: '{movie}' -> '{corrected}'")
                        movie = corrected
            except Exception as e:
                print(f"Gemini spelling correction failed for {movie}: {e}")
            # -------------------------------
            
            # Cross-check the CineSearch database before scraping
            words = re.findall(r'[a-zA-Z0-9]+', movie)
            conditions = [{"file_name": {"$regex": word, "$options": "i"}} for word in words]
            
            already_in_db = False
            if conditions:
                already_in_db = await movies_col.find_one({"$and": conditions})
                
            if already_in_db and not task.get("force", False):
                await queue_col.update_one({"_id": task["_id"]}, {"$set": {"status": "completed"}})
                continue
            
            # Update Live Dashboard with Current Movie
            await state_col.update_one({"_id": "main_state"}, {"$set": {"current_movie": movie}})
            await update_dashboard(app)
            
            success = await process_movie(app, movie)
            
            # Clear current movie
            await state_col.update_one({"_id": "main_state"}, {"$set": {"current_movie": None}})
            
            if success:
                if task.get("is_urgent", False):
                    await queue_col.update_one({"_id": task["_id"]}, {"$set": {"status": "completed"}})
                else:
                    await queue_col.delete_one({"_id": task["_id"]})
                await update_dashboard(app)
                print(f"✅ Completed `{movie}`!\nSleeping for 30 seconds to prevent bans...")
                
                # --- Post to Update Channel ---
                try:
                    update_channel = os.environ.get("UPDATE_CHANNEL_ID", "-1004355720244")
                    if update_channel:
                        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                        
                        
                        bot_url = os.environ.get("SEARCH_BOT_URL", "https://t.me/cinevalut")
                        
                        found_qualities = set()
                        found_audios = set()
                        movie_year = ""
                        
                        for bot in target_bots:
                            async for m in app.get_chat_history(bot, limit=30):
                                if time.time() - m.date.timestamp() > 300:
                                    continue
                                doc = m.document or m.video
                                if doc:
                                    file_name = getattr(doc, 'file_name', '')
                                    if file_name:
                                        fname_lower = file_name.lower()
                                        if "1080" in fname_lower: found_qualities.add("1080p")
                                        elif "720" in fname_lower: found_qualities.add("720p")
                                        elif "480" in fname_lower: found_qualities.add("480p")
                                        elif "2160" in fname_lower or "4k" in fname_lower: found_qualities.add("2160p")
                                        elif "webrip" in fname_lower: found_qualities.add("WEBRip")
                                        elif "bluray" in fname_lower or "bdrip" in fname_lower: found_qualities.add("BluRay")
                                        
                                        if "tamil" in fname_lower: found_audios.add("Tamil")
                                        if "telugu" in fname_lower: found_audios.add("Telugu")
                                        if "hindi" in fname_lower: found_audios.add("Hindi")
                                        if "malayalam" in fname_lower: found_audios.add("Malayalam")
                                        if "kannada" in fname_lower: found_audios.add("Kannada")
                                        if "english" in fname_lower: found_audios.add("English")
                                        if "multi" in fname_lower: found_audios.add("Multi")
                                        
                                        if not movie_year:
                                            ymatch = re.search(r'\b(19\d{2}|20\d{2})\b', file_name)
                                            if ymatch:
                                                movie_year = ymatch.group(1)
    
                        q_str = ", ".join(sorted(found_qualities)) if found_qualities else "Unknown"
                        a_str = ", ".join(sorted(found_audios)) if found_audios else "Unknown"
                        
                        display_movie = movie
                        if movie_year and str(movie_year) not in display_movie:
                            display_movie = f"{movie} {movie_year}"
                            
                        # Format as a hyperlink to make it blue in Telegram (markdown style)
                        title_str = f"[{display_movie}]({bot_url})"
                        
                        text = f"#New_File_Added \u2705 #cinevalut\n\n#Movie\n\n\U0001f3ac Title: **{title_str}**\n\n\U0001f3a5 Quality: **{q_str}**\n\n\U0001f50a Audio: **{a_str}**"
                        
                        reply_markup = InlineKeyboardMarkup([
                            [InlineKeyboardButton("\u27a1\ufe0f Get Files \u2b05\ufe0f", url=bot_url)]
                        ])
                        await app.send_message(int(update_channel), text, reply_markup=reply_markup, disable_web_page_preview=True)
                        print(f"Posted update to channel for {movie}")
                except Exception as ex:
                    print(f"Failed to post to update channel: {ex}")
                # ------------------------------
            else:
                await queue_col.update_one({"_id": task["_id"]}, {"$set": {"status": "failed"}})
                await update_dashboard(app)
                print(f"⚠️ Failed to find SEND ALL for `{movie}`.\nSleeping for 30 seconds...")
            
            # Anti-spam delay between movies
            await asyncio.sleep(30)
            
        except Exception as e:
            print(f"Queue Worker Error: {e}")
            await asyncio.sleep(10)

# Throttle Queue to prevent spamming CineSearch bot
forward_queue = asyncio.Queue()

async def forward_worker():
    """Forwards files 1 by 1 with a delay to let CineSearch breathe."""
    from pyrogram.enums import ParseMode
    DATABASE_CHANNEL_ID = -1003975570574
    while True:
        client, chat_id, from_chat_id, msg_id, caption, file_name = await forward_queue.get()
        try:
            # 1. Forward directly to Database Channel to guarantee backup
            await client.copy_message(
                    chat_id=DATABASE_CHANNEL_ID,
                    from_chat_id=from_chat_id,
                    message_id=msg_id,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN
                )
                
            # Update DB so we know we have it
            await movies_col.update_one({"file_name": file_name}, {"": {"file_name": file_name}}, upsert=True)
            
            # 2. Forward to Control Room (chat_id) for visual feedback
            if chat_id != DATABASE_CHANNEL_ID:
                await client.copy_message(
                        chat_id=chat_id,
                        from_chat_id=from_chat_id,
                        message_id=msg_id,
                        caption=caption,
                        parse_mode=ParseMode.MARKDOWN
                    )
                
            print(f"✅ Forwarded: {file_name}")
        except Exception as e:
            print(f"CRASH ERROR on file {file_name}: {e}")
            
        # Give CineSearch a 2-second breather to process the DB write!
        await asyncio.sleep(2) 
        forward_queue.task_done()


@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & (filters.document | filters.video))
async def manual_upload_handler(client, message):
    doc = message.document or message.video
    file_name = getattr(doc, 'file_name', None) or "Unknown_Movie.mkv"
    
    import re
    file_name = re.sub(r'@[a-zA-Z0-9_]+', '', file_name)
    file_name = re.sub(r'(?i)CharmeLeon', '', file_name)
    if ' - ' in file_name:
        parts = file_name.rsplit(' - ', 1)
        ext = ''
        if '.' in parts[1]:
            ext = '.' + parts[1].rsplit('.', 1)[-1]
        file_name = parts[0] + ext
        
    file_name = file_name.strip()
    if not file_name.endswith(('.mkv', '.mp4', '.avi')):
        file_name += ".mkv"
        

    file_size = getattr(doc, 'file_size', 0)
    if file_size > 0:
        file_size = round(file_size / (1024 * 1024), 2)
    else:
        file_size = "Unknown"
        
    beautiful_caption = generate_beautiful_caption(file_name, file_size)
    
    # Check for custom keywords in the caption (ignore if forwarded)
    if not message.forward_date and message.caption:
        tags = message.caption.strip()
        tags_block = f"\n\n🔍 **Search Tags:**\n{tags}\n"
        beautiful_caption = beautiful_caption.replace("[@CinePrimeHub]", tags_block + "[@CinePrimeHub]")
    
    await forward_queue.put((
        client, 
        CONTROL_CHANNEL_ID, 
        message.chat.id, 
        message.id, 
        beautiful_caption, 
        file_name
    ))
    await message.reply_text("✅ **File Uploaded!**\nIt has been branded with @CinePrimeHub and successfully added to your database!")


@app.on_message(filters.document | filters.video)
async def incoming_file_handler(client, message):
    # Foolproof check to ensure it's from Moonknight
    if not message.chat or not message.chat.username:
        return
        
    target_username_1 = TARGET_BOT.replace("@", "").lower()
    target_username_2 = "Aiv2trbot".lower()
    if message.chat.username.lower() not in [target_username_1, target_username_2]:
        return

    doc = message.document or message.video
    
    # Safely handle missing file names
    file_name = getattr(doc, 'file_name', None)
    if not file_name:
        file_name = "Unknown_Movie.mkv"
        
    import re
    # Auto-clean junk tags and usernames from file name
    file_name = re.sub(r'@[a-zA-Z0-9_]+', '', file_name)
    file_name = re.sub(r'(?i)CharmeLeon', '', file_name)
    
    # Strip encoder groups after the last hyphen (e.g. " - mkvCine.mkv")
    if ' - ' in file_name:
        parts = file_name.rsplit(' - ', 1)
        ext = ''
        if '.' in parts[1]:
            ext = '.' + parts[1].rsplit('.', 1)[-1]
        file_name = parts[0] + ext
        
    file_name = re.sub(r'\s+', ' ', file_name).strip()
        
    file_size = doc.file_size
    
    # Generate Beautiful Caption
    beautiful_caption = generate_beautiful_caption(file_name, file_size)
    
    # Put in the throttle queue instead of forwarding instantly
    await forward_queue.put((
        client, 
        CINESEARCH_BOT, 
        message.chat.id, 
        message.id, 
        beautiful_caption, 
        file_name
    ))

print("CineScraper Userbot is running! Send /queue with a movie list in your Saved Messages to start!")

# --- DUMMY WEB SERVER FOR RENDER ---
# Render Web Services require an open port to stay alive.
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"Bot is running securely!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    print(f"Started dummy web server on port {port} to keep Render happy.")
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()
# -----------------------------------

# Schedule the Queue Worker to run asynchronously with the Pyrogram client

# ==========================================
# AIV2TRBOT SCRAPER (V2) LOGIC
# ==========================================

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("start2", prefixes="/"))
async def start_command_v2(client, message):
    await state_col_v2.update_one({"_id": "main_state"}, {"$set": {"is_paused": False, "session_start": time.time()}}, upsert=True)
    msg = await message.reply_text("🚀 **Aiv2trbot Scraper Started!** Creating Live Dashboard...")
    try: await msg.pin()
    except: pass
    await state_col_v2.update_one({"_id": "dashboard"}, {"$set": {"msg_id": msg.id}}, upsert=True)
    await update_dashboard_v2(client)

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("pause2", prefixes="/"))
async def pause_command_v2(client, message):
    await state_col_v2.update_one({"_id": "main_state"}, {"$set": {"is_paused": True}}, upsert=True)
    await update_dashboard_v2(client)
    await message.reply_text("⏸️ **Aiv2trbot Paused!**")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("stop2", prefixes="/"))
async def stop_command_v2(client, message):
    await state_col_v2.update_one({"_id": "main_state"}, {"$set": {"is_paused": True}}, upsert=True)
    result = await queue_col_v2.delete_many({"status": "pending"})
    await update_dashboard_v2(client)
    await message.reply_text(f"🛑 **Aiv2trbot Stopped!**\nDeleted {result.deleted_count} pending movies.")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("add2", prefixes="/"))
async def add_command_v2(client, message):
    text = message.text.replace("/add2", "").strip()
    if not text:
        if message.reply_to_message and message.reply_to_message.document:
            await queue_file_handler_v2(client, message.reply_to_message)
            return
        await message.reply_text("⚠️ Please provide a list of items or reply to a .txt file.\nExample:\n/add2\nA\nB")
        return
        
    movies = [re.sub(r'^\d+\.\s*', '', m.strip()) for m in text.split("\n") if m.strip()]
    status_msg = await message.reply_text("⏳ Queueing to Aiv2trbot...")
    added = 0
    for m in movies:
        if not await queue_col_v2.find_one({"movie_name": m}):
            await queue_col_v2.insert_one({"movie_name": m, "status": "pending", "added_at": time.time()})
            added += 1
    await update_dashboard_v2(client)
    await status_msg.edit_text(f"✅ Added {added} items to Aiv2trbot Queue! (Ignored {len(movies)-added} duplicates)")

async def queue_file_handler_v2(client, message):
    file_name = message.document.file_name.lower()
    if not file_name.endswith(".txt"): return
    status_msg = await message.reply_text("📥 Downloading your Aiv2trbot list...")
    file_path = await message.download()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        movies = [line.strip() for line in lines if line.strip()]
        added = 0
        for m in movies:
            if not await queue_col_v2.find_one({"movie_name": m}):
                await queue_col_v2.insert_one({"movie_name": m, "status": "pending", "added_at": time.time()})
                added += 1
        await update_dashboard_v2(client)
        await status_msg.edit_text(f"✅ Added {added} items from file to Aiv2trbot Queue!")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {e}")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("status2", prefixes="/"))
async def status_command_v2(client, message):
    pending = await queue_col_v2.count_documents({"status": "pending"})
    completed = await queue_col_v2.count_documents({"status": "completed"})
    failed = await queue_col_v2.count_documents({"status": "failed"})
    await message.reply_text(f"📊 **Aiv2trbot Scrape Status**\n✅ Completed: {completed}\n❌ Failed: {failed}\n⏳ Upcoming: {pending}")

async def update_dashboard_v2(client):
    try:
        state = await state_col_v2.find_one({"_id": "main_state"}) or {}
        dash = await state_col_v2.find_one({"_id": "dashboard"})
        if not dash or "msg_id" not in dash: return
        
        is_paused = state.get("is_paused", True)
        status = "⏸️ PAUSED" if is_paused else "▶️ RUNNING"
        
        pending = await queue_col_v2.count_documents({"status": "pending"})
        completed = await queue_col_v2.count_documents({"status": "completed"})
        failed = await queue_col_v2.count_documents({"status": "failed"})
        
        text = f"""📊 **Aiv2trbot Scraper Dashboard**

🔄 **Status:** {status}
✅ **Total Completed:** {completed}
❌ **Total Failed:** {failed}
⏳ **Queue Remaining:** {pending}

_Last updated: {time.strftime('%I:%M:%S %p')}_"""
        
        await client.edit_message_text(CONTROL_CHANNEL_ID, dash["msg_id"], text)
    except Exception:
        pass

async def process_movie_v2(client, movie):
    current_page = 1
    total_success = False
    search_msg = await client.send_message(TARGET_BOT_V2, movie)
    
    active_menu_id = None
    requested_files_msg_id = None
    from pyrogram import enums
    for _ in range(10):
        await asyncio.sleep(2)
        async for msg in client.get_chat_history(TARGET_BOT_V2, limit=10):
            if msg.id <= search_msg.id:
                continue
            if msg.reply_markup and msg.reply_markup.inline_keyboard:
                active_menu_id = msg.id
            if msg.entities and any(e.type == enums.MessageEntityType.TEXT_LINK for e in msg.entities):
                requested_files_msg_id = msg.id
        if active_menu_id and requested_files_msg_id: break
        
    if not active_menu_id or not requested_files_msg_id: return False
    
    processed_payloads = set()
    
    while True:
        from pyrogram import enums
        
        # Check if paused or cancelled
        state = await state_col_v2.find_one({"_id": "main_state"}) or {}
        if state.get("is_paused", True):
            print("Aiv2trbot Paused! Stopping current movie...")
            break
            
        task = await queue_col_v2.find_one({"movie_name": movie, "status": "pending"})
        if not task:
            print("Movie removed from queue. Stopping...")
            break
            
        files_extracted = 0
        for _ in range(10):
            msg = await client.get_messages(TARGET_BOT_V2, requested_files_msg_id)
            if msg and msg.entities:
                for entity in msg.entities:
                    if entity.type == enums.MessageEntityType.TEXT_LINK:
                        url = entity.url
                        if "start=file" in url or "start=" in url:
                            payload = url.split("start=")[-1]
                            if payload not in processed_payloads:
                                processed_payloads.add(payload)
                                await client.send_message(TARGET_BOT_V2, f"/start {payload}")
                                files_extracted += 1
                                total_success = True
                                await asyncio.sleep(2)
            if files_extracted > 0:
                break
            await asyncio.sleep(2)
            
        print(f"📥 Aiv2trbot Requested {files_extracted} files on page {current_page}.")
        
        active_msg = await client.get_messages(TARGET_BOT_V2, active_menu_id)
        next_button_callback = None
        if active_msg and active_msg.reply_markup and active_msg.reply_markup.inline_keyboard:
            for row in active_msg.reply_markup.inline_keyboard:
                for btn in row:
                    if getattr(btn, 'text', None) and "next" in normalize_text(btn.text) and getattr(btn, 'callback_data', None):
                        next_button_callback = btn.callback_data
                        
        if next_button_callback:
            try:
                print(f"➡️ Aiv2trbot Clicking Next for page {current_page + 1}...")
                await client.request_callback_answer(chat_id=TARGET_BOT_V2, message_id=active_menu_id, callback_data=next_button_callback, timeout=15)
                current_page += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"⚠️ Error clicking Next: {e}")
                break
        else:
            print("🏁 No more pages!")
            break
            
    return total_success

async def queue_worker_v2():
    await asyncio.sleep(12)
    while True:
        try:
            state = await state_col_v2.find_one({"_id": "main_state"}) or {}
            if state.get("is_paused", True):
                await asyncio.sleep(5)
                continue
                
            task = await queue_col_v2.find_one({"status": "pending"})
            if not task:
                await asyncio.sleep(5)
                continue
                
            movie = task["movie_name"]
            success = await process_movie_v2(app, movie)
            
            if success:
                await queue_col_v2.update_one({"_id": task["_id"]}, {"$set": {"status": "completed"}})
                # --- Post to Update Channel ---
                try:
                    update_channel = os.environ.get("UPDATE_CHANNEL_ID", "-1004355720244")
                    if update_channel:
                        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                        
                        
                        bot_url = os.environ.get("SEARCH_BOT_URL", "https://t.me/cinevalut")
                        
                        found_qualities = set()
                        found_audios = set()
                        movie_year = ""
                        
                        async for m in app.get_chat_history(TARGET_BOT_V2, limit=30):
                            if time.time() - m.date.timestamp() > 300:
                                continue
                            doc = m.document or m.video
                            if doc:
                                file_name = getattr(doc, 'file_name', '')
                                if file_name:
                                    fname_lower = file_name.lower()
                                    if "1080" in fname_lower: found_qualities.add("1080p")
                                    elif "720" in fname_lower: found_qualities.add("720p")
                                    elif "480" in fname_lower: found_qualities.add("480p")
                                    elif "2160" in fname_lower or "4k" in fname_lower: found_qualities.add("2160p")
                                    elif "webrip" in fname_lower: found_qualities.add("WEBRip")
                                    elif "bluray" in fname_lower or "bdrip" in fname_lower: found_qualities.add("BluRay")
                                    
                                    if "tamil" in fname_lower: found_audios.add("Tamil")
                                    if "telugu" in fname_lower: found_audios.add("Telugu")
                                    if "hindi" in fname_lower: found_audios.add("Hindi")
                                    if "malayalam" in fname_lower: found_audios.add("Malayalam")
                                    if "kannada" in fname_lower: found_audios.add("Kannada")
                                    if "english" in fname_lower: found_audios.add("English")
                                    if "multi" in fname_lower: found_audios.add("Multi")
                                    
                                    if not movie_year:
                                        ymatch = re.search(r'\b(19\d{2}|20\d{2})\b', file_name)
                                        if ymatch:
                                            movie_year = ymatch.group(1)

                        q_str = ", ".join(sorted(found_qualities)) if found_qualities else "Unknown"
                        a_str = ", ".join(sorted(found_audios)) if found_audios else "Unknown"
                        
                        display_movie = movie
                        if movie_year and str(movie_year) not in display_movie:
                            display_movie = f"{movie} {movie_year}"
                            
                        # Format as a hyperlink to make it blue in Telegram (markdown style)
                        title_str = f"[{display_movie}]({bot_url})"
                        
                        text = f"#New_File_Added \u2705 #cinevalut\n\n#Movie\n\n\U0001f3ac Title: **{title_str}**\n\n\U0001f3a5 Quality: **{q_str}**\n\n\U0001f50a Audio: **{a_str}**"
                        
                        reply_markup = InlineKeyboardMarkup([
                            [InlineKeyboardButton("\u27a1\ufe0f Get Files \u2b05\ufe0f", url=bot_url)]
                        ])
                        await app.send_message(int(update_channel), text, reply_markup=reply_markup, disable_web_page_preview=True)
                        print(f"Posted update to channel for {movie}")
                except Exception as ex:
                    print(f"Failed to post to update channel: {ex}")
                # ------------------------------
            else:
                await queue_col_v2.update_one({"_id": task["_id"]}, {"$set": {"status": "failed"}})
                
            await update_dashboard_v2(app)
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Error in queue_worker_v2: {e}")
            await asyncio.sleep(5)






@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("scan_tags", prefixes="/"))
async def scan_tags_command(client, message):
    msg = await message.reply_text("🔍 Scanning database for garbage @tags... This might take a minute.")
    try:
        from collections import Counter
        import re
        tags = Counter()
        async for doc in movies_col.find({}):
            fname = doc.get("file_name", "")
            if fname:
                words = re.findall(r'@[a-zA-Z0-9_]+', fname)
                for w in words:
                    tags[w] += 1
                    
        report = "Top @tags found in database:\n\n"
        for tag, count in tags.most_common(500):
            report += f"{tag} ({count} files)\n"
            
        with open("tags_report.txt", "w", encoding="utf-8") as f:
            f.write(report)
            
        await message.reply_document("tags_report.txt", caption="✅ Scan complete! Here is the list of all tags.")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

app.start()

async def populate_peers():
    print('dY~ Fetching dialogs to populate peer cache...')
    try:
        async for dialog in app.get_dialogs(): pass
    except Exception as e: print(e)

app.loop.run_until_complete(populate_peers())

async def rotation_worker():
    global LAST_SWITCH_TIME
    while True:
        elapsed = time.time() - LAST_SWITCH_TIME
        remaining = 18000 - elapsed
        
        if remaining > 0:
            print(f"🔄 [DUAL-ACCOUNT ENGINE] {int(remaining/3600)}h {int((remaining%3600)/60)}m left until account switch...")
            await asyncio.sleep(remaining)
            
        print("\n🔄 [DUAL-ACCOUNT ENGINE] 5 HOURS PASSED! Initiating hot-swap to prevent spam ban...")
        
        # Delete current dashboard and force auto-resume state so next account creates a fresh one immediately
        try:
            state = await state_col.find_one({"_id": "main_state"}) or {}
            if state.get("dashboard_msg_id"):
                await app.delete_messages(CONTROL_CHANNEL_ID, state["dashboard_msg_id"])
                
            # Ensure the bot automatically starts running without needing a manual /start
            await state_col.update_one(
                {"_id": "main_state"}, 
                {"$set": {"dashboard_msg_id": None, "is_paused": False}}
            )
        except Exception:
            pass
        
        # Toggle state
        next_acc = "2" if ACTIVE_ACCOUNT == "1" else "1"
        await state_col.update_one(
            {"_id": "active_account"}, 
            {"$set": {"account": next_acc, "last_switch_time": time.time()}}, 
            upsert=True
        )
            
        print(f"✅ Account toggled to {next_acc}. Restarting process...")
        # Force crash exit so Render automatically restarts the bot instantly
        os._exit(1)

app.loop.create_task(ensure_dashboard())
app.loop.create_task(queue_worker())
app.loop.create_task(queue_worker_v2())
app.loop.create_task(forward_worker())
if not os.environ.get("FORCE_ACCOUNT"):
    app.loop.create_task(rotation_worker())
else:
    print("⚠️ [DUAL-ACCOUNT ENGINE] Rotation disabled because FORCE_ACCOUNT is active.")


from pyrogram import idle
idle()
app.stop()
