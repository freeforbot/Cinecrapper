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
TARGET_BOT = os.environ.get("TARGET_BOT", "@Aiv2trbot")
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
    sync_state = sync_client["cinesearch_db"]["scraper_state_v2"]
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
state_col = db["scraper_state_v2"]

import time
from datetime import datetime, timezone, timedelta

async def ensure_dashboard():
    await asyncio.sleep(5)
    state = await state_col.find_one({"_id": "main_state"}) or {}
    if not state.get("is_paused", True):
        if not state.get("dashboard_msg_id"):
            msg = await app.send_message(CONTROL_CHANNEL_ID, "🚀 **Aiv2trbot Scraper Rebooted!** Generating Dashboard...")
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
    audio_str = ", ".join(audio) if audio else "Unknown"
    
    # Detect Subtitles
    subtitle = "Unknown"
    if "esub" in fname_lower: subtitle = "English"
    elif "msub" in fname_lower: subtitle = "Multiple"
    
    # Build Caption
    return f"""📜 Name: {file_name}
┏ 💾 Size: {size_str}
{file_name}

◧ 🎬 Quality : {quality}
◧ 🔊 Audio : {audio_str}
◧ 💬 Subtitle : {subtitle}"""

# Initialize Pyrogram Userbot
app = Client(
    "cinescraper2",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("start2", prefixes="/"))
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

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("pause2", prefixes="/"))
async def pause_command(client, message):
    await state_col.update_one({"_id": "main_state"}, {"$set": {"is_paused": True}}, upsert=True)
    await update_dashboard(client)
    await message.reply_text("⏸️ **Scraper Paused!** Queue is preserved.")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("stop2", prefixes="/"))
async def stop_command(client, message):
    await state_col.update_one({"_id": "main_state"}, {"$set": {"is_paused": True}}, upsert=True)
    result = await queue_col.delete_many({"status": "pending"})
    await update_dashboard(client)
    await message.reply_text(f"🛑 **Scraper Stopped!**\nDeleted {result.deleted_count} pending movies.")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("switch2", prefixes="/"))
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

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("debugenv2", prefixes="/"))
async def debugenv_command(client, message):
    s2 = os.environ.get("SESSION_STRING_2", "")
    await message.reply_text(f"🛠️ **Debug Info:**\n- SESSION_STRING_2 length: {len(s2)}\n- ACTIVE_ACCOUNT from DB: {ACTIVE_ACCOUNT}")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("version2", prefixes="/"))
async def version_command(client, message):
    await message.reply_text("✅ **Version:** `1.2 - Diagnostic Click Active`")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("testclick2", prefixes="/"))
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

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("add2", prefixes="/"))
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
    for i, movie in enumerate(ai_cleaned_movies, 1):
        if i % 15 == 0:
            try:
                await status_msg.edit_text(f"🔍 **Scanning Database:** {i} / {total_movies} movies checked...")
            except Exception:
                pass
                
        # Check if already in queue
        existing_queue = await queue_col.find_one({"movie_name": movie})
        if existing_queue:
            skipped_count += 1
            continue
            
        # Check if already in main database using strict regex
        words = re.findall(r'[a-zA-Z0-9]+', movie)
        conditions = [{"file_name": {"$regex": word, "$options": "i"}} for word in words]
        already_in_db = False
        if conditions:
            already_in_db = await movies_col.find_one({"$and": conditions})
            
        if already_in_db:
            skipped_count += 1
            # Mark it as completed directly in queue history so it doesn't get processed again
            await queue_col.insert_one({"movie_name": movie, "status": "completed"})
            continue
            
        # Add to pending queue!
        await queue_col.insert_one({"movie_name": movie, "status": "pending"})
        added_count += 1
            
    await message.reply_text(f"✅ **Successfully queued {added_count} missing movies!**\n(Skipped {skipped_count} movies already in database/queue).")

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("status2", prefixes="/"))
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

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("list2", prefixes="/"))
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

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("menu2", prefixes="/"))
async def menu_command(client, message):
    menu_text = """⚙️ **CineScraper Control Menu**

`/add` - Add movies to the queue (paste list below command)
`/start` - Boot up the scraper and pin the Live Dashboard
`/pause` - Temporarily pause scraping (preserves queue)
`/stop` - Stop scraping and WIPE the pending queue
`/status` - Ask AI for a status report on your queue
`/list` - Generate a full .txt export of your movie history
`/switch 1` - Manually force switch to Account 1
`/switch 2` - Manually force switch to Account 2
`/migrate` - Migrate all old Control Room files to Database Channel
`/queue_auto` - Automatically fetch trending/new movies from TMDB and queue them
`/menu` - Show this help menu
"""
    await message.reply_text(menu_text)

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("migrate2", prefixes="/"))
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

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.command("queue_auto2", prefixes="/"))
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
            if i % 25 == 0:
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

@app.on_message(filters.chat(CONTROL_CHANNEL_ID) & filters.document)
async def queue_file_handler(client, message):
    file_name = message.document.file_name.lower()
    if not (file_name.endswith(".txt") or file_name.endswith(".pdf")):
        return
        
    status_msg = await message.reply_text("📥 Downloading your movie list...")
    file_path = await message.download()
    
    try:
        movies = []
        if file_name.endswith(".txt"):
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            movies = [re.sub(r'^\d+\.\s*', '', m.strip()) for m in lines if m.strip()]
        
        elif file_name.endswith(".pdf"):
            import pypdf
            await status_msg.edit_text("📄 Parsing PDF file...")
            with open(file_path, "rb") as f:
                reader = pypdf.PdfReader(f)
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        lines = text.split("\n")
                        page_movies = [re.sub(r'^\d+\.\s*', '', m.strip()) for m in lines if m.strip()]
                        movies.extend(page_movies)
        
        # Deduplicate the raw list
        movies = list(dict.fromkeys(movies))
        
        if not movies:
            await status_msg.edit_text("⚠️ No movies found in the file.")
            return

        # Skip bulk AI processing for very large lists to prevent timeouts/hanging.
        # The background worker already spell-checks each movie individually anyway!
        if len(movies) > 100:
            await status_msg.edit_text(f"🚀 Large list detected ({len(movies)} movies). Skipping bulk AI format to save time...")
            ai_cleaned_movies = movies
        else:
            await status_msg.edit_text("🤖 **AI Processor:** Fixing spelling and cleaning garbage text from your file...")
            
            list_string = "\n".join(movies)
            prompt = f"""You are a movie title expert. 
Here is a list of movies extracted from a document:
<list>
{list_string}
</list>

INSTRUCTIONS:
1. Fix any obvious spelling mistakes.
2. Remove any extra garbage text, page numbers, or bullet points.
3. Return ONLY a plain text list of the cleaned movie titles, one per line. Do not say anything else.
"""
            try:
                clean_list_text = await ask_gemini(prompt)
                ai_cleaned_movies = [m.strip() for m in clean_list_text.split("\n") if m.strip()]
            except Exception as e:
                print(f"AI Processor Error: {e}")
                ai_cleaned_movies = movies
        
        await status_msg.edit_text(f"✅ **AI Analysis Complete!**\nNow high-speed cross-referencing {len(ai_cleaned_movies)} movies against your database...")
        
        added_count = 0
        skipped_count = 0
        docs_to_insert = []
        
        total_movies = len(ai_cleaned_movies)
        for i, movie in enumerate(ai_cleaned_movies, 1):
            if i % 15 == 0:
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
                
        await status_msg.edit_text(f"✅ Successfully queued {added_count} new movies from your file!\n(Skipped {skipped_count} movies already in database/queue).")
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Error reading file: {e}")
    finally:
        import os
        if os.path.exists(file_path):
            os.remove(file_path)


async def process_movie(client, movie):
    current_page = 1
    total_success = False
    
    # Send query to Target Bot
    await client.send_message(TARGET_BOT, movie)
    
    menu_msg_id = None
    active_msg = None
    
    for _ in range(10):
        import asyncio
        await asyncio.sleep(2)
        async for msg in client.get_chat_history(TARGET_BOT, limit=5):
            if msg.entities and ("TOTAL FILES" in (msg.text or "") or "Requested Files" in (msg.text or "")):
                active_msg = msg
                menu_msg_id = msg.id
                break
        if active_msg:
            break
            
    if not menu_msg_id:
        return total_success
        
    print(f"✅ Found results for '{movie}'. Starting extraction...")
    
    while True:
        import asyncio
        from pyrogram import enums
        active_msg = await client.get_messages(TARGET_BOT, menu_msg_id)
        if not active_msg or getattr(active_msg, 'empty', False):
            print("⚠️ Menu message not found.")
            break
            
        files_extracted = 0
        if active_msg.entities:
            for entity in active_msg.entities:
                if entity.type == enums.MessageEntityType.TEXT_LINK:
                    url = entity.url
                    if "start=file" in url or "start=" in url:
                        payload = url.split("start=")[-1]
                        await client.send_message(TARGET_BOT, f"/start {payload}")
                        files_extracted += 1
                        total_success = True
                        await asyncio.sleep(2)
                        
        print(f"📥 Requested {files_extracted} files on page {current_page}.")
        
        next_button_callback = None
        if active_msg.reply_markup and active_msg.reply_markup.inline_keyboard:
            for row in active_msg.reply_markup.inline_keyboard:
                for btn in row:
                    if getattr(btn, 'text', None) and "next" in normalize_text(btn.text) and getattr(btn, 'callback_data', None):
                        next_button_callback = btn.callback_data
                        
        if next_button_callback:
            try:
                print(f"➡️ Clicking Next for page {current_page + 1}...")
                await client.request_callback_answer(
                    chat_id=TARGET_BOT,
                    message_id=active_msg.id,
                    callback_data=next_button_callback,
                    timeout=15
                )
                current_page += 1
                await asyncio.sleep(4)
            except Exception as e:
                print(f"⚠️ Error clicking Next: {e}")
                break
        else:
            print("🏁 No more pages!")
            break
            
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
                
            if already_in_db:
                await queue_col.update_one({"_id": task["_id"]}, {"$set": {"status": "completed"}})
                continue
            
            # Update Live Dashboard with Current Movie
            await state_col.update_one({"_id": "main_state"}, {"$set": {"current_movie": movie}})
            await update_dashboard(app)
            
            success = await process_movie(app, movie)
            
            # Clear current movie
            await state_col.update_one({"_id": "main_state"}, {"$set": {"current_movie": None}})
            
            if success:
                if task.get("is_urgent"):
                    await queue_col.update_one({"_id": task["_id"]}, {"$set": {"status": "completed"}})
                else:
                    await queue_col.delete_one({"_id": task["_id"]})
                await update_dashboard(app)
                print(f"✅ Completed `{movie}`!\nSleeping for 60 seconds to prevent bans...")
            else:
                await queue_col.update_one({"_id": task["_id"]}, {"$set": {"status": "failed"}})
                await update_dashboard(app)
                print(f"❌ Failed to find SEND ALL for `{movie}`.\nSleeping for 60 seconds...")
            
            # Anti-spam delay between movies
            await asyncio.sleep(60)
            
        except Exception as e:
            print(f"Queue Worker Error: {e}")
            await asyncio.sleep(10)

# Throttle Queue to prevent spamming CineSearch bot
forward_queue = asyncio.Queue()

async def forward_worker():
    """Forwards files 1 by 1 with a delay to let CineSearch breathe."""
    DATABASE_CHANNEL_ID = -1003975570574
    while True:
        client, chat_id, from_chat_id, msg_id, caption, file_name = await forward_queue.get()
        try:
            # 1. Forward directly to Database Channel to guarantee backup
            await client.copy_message(
                chat_id=DATABASE_CHANNEL_ID,
                from_chat_id=from_chat_id,
                message_id=msg_id,
                caption=caption
            )
            
            # 2. Forward to Control Room (chat_id) for visual feedback
            if chat_id != DATABASE_CHANNEL_ID:
                await client.copy_message(
                    chat_id=chat_id,
                    from_chat_id=from_chat_id,
                    message_id=msg_id,
                    caption=caption
                )
            
            print(f"✅ Forwarded: {file_name}")
        except Exception as e:
            print(f"CRASH ERROR on file {file_name}: {e}")
            
        # Give CineSearch a 2-second breather to process the DB write!
        await asyncio.sleep(2) 
        forward_queue.task_done()

@app.on_message(filters.document | filters.video)
async def incoming_file_handler(client, message):
    # Foolproof check to ensure it's from Moonknight
    if not message.chat or not message.chat.username:
        return
        
    target_username = TARGET_BOT.replace("@", "").lower()
    if message.chat.username.lower() != target_username:
        return

    doc = message.document or message.video
    
    # Safely handle missing file names
    file_name = getattr(doc, 'file_name', None)
    if not file_name:
        file_name = "Unknown_Movie.mkv"
        
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
app.loop.create_task(forward_worker())
app.loop.create_task(rotation_worker())

from aiohttp import web
async def handle_health_check(request):
    return web.Response(text="CineScraper bot is running OK!")

async def start_web_server():
    app_web = web.Application()
    app_web.router.add_get('/', handle_health_check)
    runner = web.AppRunner(app_web)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"✅ Dummy web server started on port {port}")

# app.loop.create_task(start_web_server()) # Disabled to prevent Port Conflict

from pyrogram import idle
idle()
app.stop()
