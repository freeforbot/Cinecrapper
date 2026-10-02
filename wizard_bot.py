import asyncio
import os
import re
import sys

# Pyrogram needs an event loop at import time (fails on Python 3.14 otherwise)
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import aiohttp
import random
import datetime
from pyrogram import Client, filters, idle
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.types import BotCommand
import pyrogram.utils as _pg_utils

# Pyrogram 2.0.106 rejects channel IDs above 2^31 ("Peer id invalid"). New channels
# have bigger IDs (e.g. -1003975570574), so widen the allowed range.
_pg_utils.MIN_CHANNEL_ID = -1007852516352
from pyrogram.enums import ParseMode
from motor.motor_asyncio import AsyncIOMotorClient

# Setup
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH")
MONGO_URI = os.environ.get("MONGO_URI")
DATABASE_CHANNEL_ID = int(os.environ.get("DATABASE_CHANNEL_ID", "-1003975570574"))
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")

# Tiny web server so Render's web service sees an open port
class _Ping(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Wizard bot is running")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *a):
        pass

def _serve():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), _Ping).serve_forever()

threading.Thread(target=_serve, daemon=True).start()

missing = [k for k in ("BOT_TOKEN", "API_ID", "API_HASH", "MONGO_URI", "TMDB_API_KEY") if not os.environ.get(k)]
if missing:
    print(f"❌ Wizard Bot: missing environment variables: {', '.join(missing)}", flush=True)
    sys.exit(1)

# Initialize Clients
app = Client("wizard_bot", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)
db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["cinesearch_db"]
movies_col = db["movies"]
wizard_movies = db["wizard_movies"]  # numbered movie registry (poster msg id, keywords, files)

# State Machine
WIZARD_STATE = {}

def format_size(size_bytes):
    if not size_bytes: return "Unknown"
    size_mb = size_bytes / (1024 * 1024)
    if size_mb > 1024:
        return f"{size_mb/1024:.2f} GB"
    return f"{size_mb:.2f} MB"

def generate_beautiful_caption(file_name, file_size_bytes):
    size_str = format_size(file_size_bytes)
    fname_lower = file_name.lower()
    
    quality = "Unknown"
    if "1080" in fname_lower: quality = "1080p"
    elif "720" in fname_lower: quality = "720p"
    elif "480" in fname_lower: quality = "480p"
    elif "360" in fname_lower: quality = "360p"
    elif "2160" in fname_lower or "4k" in fname_lower: quality = "2160p"
    
    audio = []
    if "tamil" in fname_lower: audio.append("Tamil")
    if "telugu" in fname_lower: audio.append("Telugu")
    if "hindi" in fname_lower: audio.append("Hindi")
    if "malayalam" in fname_lower: audio.append("Malayalam")
    if "kannada" in fname_lower: audio.append("Kannada")
    if "english" in fname_lower: audio.append("English")
    if "multi" in fname_lower and not audio: audio.append("Multi")
    audio_str = ", ".join(audio) if audio else "Unknown"
    
    subtitle = "Unknown"
    if "esub" in fname_lower: subtitle = "English"
    elif "msub" in fname_lower: subtitle = "Multiple"
        
    year = "Unknown"
    ymatch = re.search(r'\b(19\d{2}|20\d{2})\b', file_name)
    if ymatch:
        year = ymatch.group(1)
    
    return f'''🎬 Name: {file_name}
📂 Size: {size_str}
{file_name}

🗓 Year : {year}
💿 Quality : {quality}
🔊 Audio : {audio_str}
📝 Subtitle : {subtitle}

[@CinePrimeHub](https://t.me/+bTXW_3aYxlw3YWU1)'''

async def search_tmdb(query):
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            if data and data.get("results"):
                # Get the first result
                movie = data["results"][0]
                
                # Fetch detailed info (for genres and plot)
                movie_id = movie["id"]
                detail_url = f"https://api.themoviedb.org/3/movie/{movie_id}?api_key={TMDB_API_KEY}"
                async with session.get(detail_url) as d_resp:
                    detail_data = await d_resp.json()
                    
                    poster_path = detail_data.get("poster_path")
                    poster_url = f"https://image.tmdb.org/t/p/w600_and_h900_bestv2{poster_path}" if poster_path else None
                    
                    genres = [g["name"] for g in detail_data.get("genres", [])]
                    year = detail_data.get("release_date", "")[:4]
                    
                    return {
                        "title": f"{detail_data.get('title', 'Unknown')} ({year})" if year else detail_data.get("title", "Unknown"),
                        "rating": round(detail_data.get("vote_average", 0), 1) or "N/A",
                        "plot": detail_data.get("overview", "No plot available."),
                        "genres": ", ".join(genres),
                        "poster": poster_url
                    }
    return None

MENU_TEXT = (
    "📋 **Bot Menu**\n\n"
    "/start - Check bot status & database channel access\n"
    "/menu - Show all commands\n"
    "/addmovie - Add a new movie (poster + files)\n"
    "/add [code] - Add extra files to a movie\n"
    "/list - List all movies with their codes\n"
    "/recaption [code] - Change the caption of a movie's files\n"
    "/remove [code] - Delete a movie from the channel & database\n"
    "/done - Finish sending files\n"
    "/cancel - Cancel the current action"
)

BOT_COMMANDS = [
    BotCommand("start", "Check bot & channel access"),
    BotCommand("menu", "Show all commands"),
    BotCommand("addmovie", "Add a new movie"),
    BotCommand("add", "Add extra files: /add [code]"),
    BotCommand("list", "List all movies with codes"),
    BotCommand("recaption", "Change caption: /recaption [code]"),
    BotCommand("remove", "Delete a movie: /remove [code]"),
    BotCommand("done", "Finish sending files"),
    BotCommand("cancel", "Cancel current action"),
]

async def check_channel_access(client):
    """Returns a status text about the bot's access to the database channel."""
    ok = await ensure_channel(client)
    if not ok:
        return (f"❌ **Database channel: NO ACCESS**\n`{DATABASE_CHANNEL_ID}`\n\n"
                f"Reason: {LAST_CHANNEL_ERROR}\n\n"
                "Fix: add me as an **Admin** of the channel (with Post Messages), then post any message in the channel and send /start again.")
    chat = await client.get_chat(DATABASE_CHANNEL_ID)
    lines = [f"✅ **Database channel: ACCESS OK**\n📢 {chat.title}\n`{DATABASE_CHANNEL_ID}`"]
    try:
        me = await client.get_chat_member(DATABASE_CHANNEL_ID, "me")
        if me.status == ChatMemberStatus.OWNER:
            lines.append("👑 I am the owner - can post files")
        elif me.status == ChatMemberStatus.ADMINISTRATOR:
            can_post = getattr(me.privileges, "can_post_messages", None)
            if can_post is False:
                lines.append("⚠️ I am an admin but **cannot post messages** - enable 'Post Messages' for me")
            else:
                lines.append("🛡 I am an admin - can post files")
        else:
            lines.append("⚠️ I am **not an admin** in this channel - make me an admin so I can post files")
    except Exception as e:
        lines.append(f"⚠️ Could not read my admin rights: {e}")
    return "\n".join(lines)

@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    msg = await message.reply_text("👋 Hello! I am your **Movie Upload Wizard**.\n\n🔎 Checking database channel access...")
    status = await check_channel_access(client)
    await msg.edit_text("👋 Hello! I am your **Movie Upload Wizard**.\n\n" + status + "\n\nSend /menu to see all commands.")

@app.on_message(filters.command("menu"))
async def menu_cmd(client, message):
    await message.reply_text(MENU_TEXT)

@app.on_message(filters.command("addmovie"))
async def addmovie_cmd(client, message):
    user_id = message.from_user.id
    WIZARD_STATE[user_id] = {"step": "awaiting_name"}
    await message.reply_text("🎬 **Wizard Started!**\n\nPlease reply to me with the exact Movie Name you want to search on TMDB (e.g., Iron Man).\n\nType /cancel to abort at any time.")

@app.on_message(filters.command("cancel"))
async def cancel_cmd(client, message):
    user_id = message.from_user.id
    if user_id in WIZARD_STATE:
        del WIZARD_STATE[user_id]
        await message.reply_text("🛑 Wizard cancelled.")
    else:
        await message.reply_text("No active wizard to cancel.")

@app.on_message(filters.command("done"))
async def done_cmd(client, message):
    user_id = message.from_user.id
    st = WIZARD_STATE.get(user_id)
    if not st:
        return

    if st["step"] == "awaiting_files":
        if not st["files"]:
            await message.reply_text("❌ You haven't sent any files! Send movie files or type /cancel.")
            return
        st["step"] = "awaiting_keywords"
        await message.reply_text("✅ Files received! Now, please reply with the **caption** for these files.\n\nI will put exactly your text as the caption on every file (nothing added), and it is also used for search.\n\nExample: iron man, ironman 1")

    elif st["step"] == "adding_files":
        if not st["files"]:
            await message.reply_text("❌ You haven't sent any files! Send the extra files or type /cancel.")
            return
        no = st["movie_no"]
        del WIZARD_STATE[user_id]
        msg = await message.reply_text(f"🔄 Uploading {len(st['files'])} file(s) to movie {no}...")
        try:
            movie = await wizard_movies.find_one({"_id": no})
            await ensure_channel(client)
            before = len(movie["files"])
            names, ids = await upload_files(client, st["files"], movie["keywords"], movie["poster_msg_id"])
            await wizard_movies.update_one({"_id": no}, {"$push": {"files": {"$each": names}, "file_msg_ids": {"$each": ids}}})
            total = before + len(names)
            await msg.edit_text(f"✅ **Added {len(names)} file(s)**\n🎬 {movie['title']}\n🔢 Code: `{no}`\n📁 Total files: {total}")
        except Exception as e:
            await msg.edit_text(f"❌ Error while adding files: {e}")

@app.on_message(filters.command("add"))
async def add_cmd(client, message):
    user_id = message.from_user.id
    args = message.command[1:]
    if not args or not args[0].lstrip("#").isdigit():
        await message.reply_text("Usage: /add [movie code]\nExample: /add 48213\n\nUse /list to see movie codes.")
        return
    no = int(args[0].lstrip("#"))
    movie = await wizard_movies.find_one({"_id": no})
    if not movie:
        await message.reply_text(f"❌ No movie with code {no}. Use /list to see all movies.")
        return
    WIZARD_STATE[user_id] = {"step": "adding_files", "movie_no": no, "files": []}
    caption = (f"🎬 **{movie['title']}**\n🔢 Code: `{no}`\n📁 Files so far: {len(movie['files'])}\n\n"
               f"Send the extra files now. When finished, type /done.\nType /cancel to abort.")
    if movie.get("poster"):
        try:
            await message.reply_photo(photo=movie["poster"], caption=caption)
            return
        except Exception:
            pass
    await message.reply_text(caption)

@app.on_message(filters.command("list"))
async def list_cmd(client, message):
    query = " ".join(message.command[1:]).strip().lower()
    movies = await wizard_movies.find({}).sort([("created", 1), ("_id", 1)]).to_list(length=None)
    if query:
        movies = [m for m in movies if query in m["title"].lower() or query in m.get("keywords", "").lower()]
    if not movies:
        await message.reply_text("No movies found." if query else "No movies added yet. Use /addmovie to add one.")
        return
    lines = [f"`{m['_id']}` — {m['title']} ({len(m['files'])} files)" for m in movies]
    chunk = f"🎞 **Movies ({len(movies)}):**\n\n"
    for line in lines:
        if len(chunk) + len(line) > 3800:
            await message.reply_text(chunk)
            chunk = ""
        chunk += line + "\n"
    chunk += "\nTap a code to copy, then send: /add [code]\nSearch: /list [name]"
    await message.reply_text(chunk)

@app.on_message(filters.command("recaption"))
async def recaption_cmd(client, message):
    user_id = message.from_user.id
    args = message.command[1:]
    if not args or not args[0].lstrip("#").isdigit():
        await message.reply_text("Usage: /recaption [movie code]\nExample: /recaption 48213")
        return
    no = int(args[0].lstrip("#"))
    movie = await wizard_movies.find_one({"_id": no})
    if not movie:
        await message.reply_text(f"❌ No movie with code {no}. Use /list to see all movies.")
        return
    if not movie.get("file_msg_ids"):
        await message.reply_text(
            f"⚠️ **{movie['title']}** was uploaded before file tracking, so I cannot edit its captions.\n\n"
            f"Delete it with `/remove {no} confirm`, then upload it again with /addmovie.")
        return
    WIZARD_STATE[user_id] = {"step": "awaiting_recaption", "movie_no": no}
    await message.reply_text(
        f"✏️ **{movie['title']}** (code `{no}`)\n📁 {len(movie['file_msg_ids'])} file(s)\n\n"
        f"Send the NEW caption now. It will replace the caption on all its files.\nType /cancel to abort.")

@app.on_message(filters.command("remove"))
async def remove_cmd(client, message):
    args = message.command[1:]
    if not args or not args[0].lstrip("#").isdigit():
        await message.reply_text("Usage: /remove [movie code]\nExample: /remove 48213")
        return
    no = int(args[0].lstrip("#"))
    movie = await wizard_movies.find_one({"_id": no})
    if not movie:
        await message.reply_text(f"❌ No movie with code {no}. Use /list to see all movies.")
        return
    tracked = len(movie.get("file_msg_ids", []))
    if not (len(args) > 1 and args[1].lower() == "confirm"):
        note = "" if tracked == len(movie["files"]) else "\n⚠️ Some files were uploaded before tracking - delete those by hand in the channel."
        await message.reply_text(
            f"⚠️ This will delete **{movie['title']}** (code `{no}`): the poster post, {tracked} file post(s) in the channel, and its database entries.{note}\n\n"
            f"To confirm send:\n`/remove {no} confirm`")
        return
    msg = await message.reply_text("🗑 Removing...")
    try:
        await ensure_channel(client)
        ids = [movie["poster_msg_id"]] + list(movie.get("file_msg_ids", []))
        await client.delete_messages(DATABASE_CHANNEL_ID, ids)
    except Exception as e:
        print(f"⚠️ Could not delete channel posts: {e}", flush=True)
    await movies_col.delete_many({"file_name": {"$in": movie["files"]}})
    await wizard_movies.delete_one({"_id": no})
    await msg.edit_text(f"🗑 Removed **{movie['title']}** (code `{no}`).")

@app.on_message(filters.text & ~filters.command(["start", "menu", "addmovie", "add", "list", "recaption", "remove", "cancel", "done"]))
async def text_handler(client, message):
    user_id = message.from_user.id
    if user_id not in WIZARD_STATE:
        return
        
    state = WIZARD_STATE[user_id]
    
    if state["step"] == "awaiting_name":
        query = message.text
        msg = await message.reply_text("🔍 Searching TMDB...")
        
        movie_data = await search_tmdb(query)
        if not movie_data:
            await msg.edit_text("❌ No movies found! Try /addmovie again with a different name.")
            del WIZARD_STATE[user_id]
            return
            
        state["movie_data"] = movie_data
        state["step"] = "awaiting_files"
        state["files"] = []
        
        caption = f"✅ Found: **{movie_data['title']}**\n\nNow send/forward all the movie files (480p, 720p, etc) to me here. When you are finished sending all files, type /done."
        if movie_data["poster"]:
            try:
                await message.reply_photo(photo=movie_data["poster"], caption=caption)
            except:
                await message.reply_text(caption)
        else:
            await message.reply_text(caption)
            
        await msg.delete()
        
    elif state["step"] == "awaiting_recaption":
        no = state["movie_no"]
        new_caption = message.text[:1024]
        del WIZARD_STATE[user_id]
        msg = await message.reply_text("🔄 Updating captions...")
        movie = await wizard_movies.find_one({"_id": no})
        ok = fail = 0
        await ensure_channel(client)
        for mid in movie["file_msg_ids"]:
            try:
                await client.edit_message_caption(DATABASE_CHANNEL_ID, mid, new_caption, parse_mode=ParseMode.DISABLED)
                ok += 1
            except Exception as e:
                if "MESSAGE_NOT_MODIFIED" in str(e).upper() or "NOT MODIFIED" in str(e).upper():
                    ok += 1
                else:
                    fail += 1
                    print(f"⚠️ Could not edit {mid}: {e}", flush=True)
            await asyncio.sleep(1)
        await wizard_movies.update_one({"_id": no}, {"$set": {"keywords": new_caption}})
        await msg.edit_text(f"✅ Caption updated on {ok} file(s)." + (f"\n⚠️ {fail} failed (deleted or no permission)." if fail else ""))

    elif state["step"] == "awaiting_keywords":
        state["keywords"] = message.text
        await finish_wizard(client, message, state)
        del WIZARD_STATE[user_id]

@app.on_message(filters.document | filters.video)
async def file_handler(client, message):
    user_id = message.from_user.id
    if user_id in WIZARD_STATE and WIZARD_STATE[user_id]["step"] in ("awaiting_files", "adding_files"):
        WIZARD_STATE[user_id]["files"].append(message)
        await message.reply_text("✅ File added to wizard! Send more, or type /done.")

async def new_movie_code():
    """Unique fixed 5-digit code for a movie."""
    for _ in range(100):
        code = random.randint(10000, 99999)
        if not await wizard_movies.find_one({"_id": code}):
            return code
    raise RuntimeError("No free 5-digit movie codes left")

async def upload_files(client, files, keywords, poster_msg_id):
    """Copy files into the DB channel as replies to the poster; index names in Mongo."""
    names = []
    ids = []
    for f_msg in files:
        doc = f_msg.document or f_msg.video
        file_name = getattr(doc, 'file_name', None) or "Unknown_Movie.mkv"

        # Clean filename
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
        # Caption = exactly what the user typed (Telegram caption limit is 1024 chars)
        file_caption = keywords[:1024]

        sent = await client.copy_message(
            chat_id=DATABASE_CHANNEL_ID,
            from_chat_id=f_msg.chat.id,
            message_id=f_msg.id,
            caption=file_caption,
            parse_mode=ParseMode.DISABLED,
            reply_to_message_id=poster_msg_id
        )
        await movies_col.update_one({"file_name": file_name}, {"$set": {"file_name": file_name}}, upsert=True)
        names.append(file_name)
        ids.append(sent.id)
        await asyncio.sleep(1)
    return names, ids

async def finish_wizard(client, message, state):
    msg = await message.reply_text("🔄 Compiling and uploading everything to the Database Channel...")
    tmdb = state["movie_data"]
    keywords = state["keywords"]
    
    caption = f"🎬 **{tmdb['title']}**\n\n"
    if tmdb.get("rating") != 'N/A': caption += f"⭐ **Rating:** {tmdb['rating']}/10\n"
    if tmdb.get("genres"): caption += f"🎭 **Genre:** {tmdb['genres']}\n"
    if tmdb.get("plot"): caption += f"📖 **Plot:** {tmdb['plot'][:300]}...\n"
    
    caption += f"\n🔍 **Search Tags:**\n{keywords}\n\n[@CinePrimeHub](https://t.me/+bTXW_3aYxlw3YWU1)"
    
    try:
        # 0. Make sure the bot has resolved the DB channel (fresh sessions have an empty peer cache)
        await ensure_channel(client)
        # 1. Send Poster to DB
        if tmdb.get("poster"):
            try:
                poster_msg = await client.send_photo(DATABASE_CHANNEL_ID, tmdb["poster"], caption=caption)
            except:
                poster_msg = await client.send_message(DATABASE_CHANNEL_ID, caption)
        else:
            poster_msg = await client.send_message(DATABASE_CHANNEL_ID, caption)
            
        # 2. Copy files as replies to the poster
        names, ids = await upload_files(client, state["files"], keywords, poster_msg.id)

        # 3. Register the movie with a number
        movie_no = await new_movie_code()
        await wizard_movies.insert_one({
            "_id": movie_no, "title": tmdb["title"], "keywords": keywords,
            "poster_msg_id": poster_msg.id, "poster": tmdb.get("poster"), "files": names, "file_msg_ids": ids,
            "created": datetime.datetime.utcnow()})

        await msg.edit_text(
            f"✅ **Movie Successfully Added!**\n\n🎬 **{tmdb['title']}**\n🔢 **Movie Code:** `{movie_no}`\n📁 Files: {len(names)}\n\n"
            f"To add more files later send:\n`/add {movie_no}`\n\nSee all movies: /list")
    except Exception as e:
        await msg.edit_text(f"❌ Error during upload: {e}\n\nMake sure I am added as an Admin in your Database Channel ({DATABASE_CHANNEL_ID}) so I can post files!")

@app.on_message(filters.private, group=-1)
async def _log_updates(client, message):
    print(f"📩 Update from {message.from_user.id if message.from_user else '?'}: {(message.text or message.caption or '<media>')[:40]}", flush=True)
    message.continue_propagation()

state_col = db["bot_state"]

async def _save_peer():
    try:
        peer = await app.storage.get_peer_by_id(DATABASE_CHANNEL_ID)
        await state_col.update_one(
            {"_id": f"peer_{DATABASE_CHANNEL_ID}"},
            {"$set": {"access_hash": peer.access_hash}}, upsert=True)
    except Exception as e:
        print(f"⚠️ Could not cache channel access: {e}", flush=True)

async def _load_peer():
    doc = await state_col.find_one({"_id": f"peer_{DATABASE_CHANNEL_ID}"})
    if doc:
        await app.storage.update_peers([(DATABASE_CHANNEL_ID, doc["access_hash"], "channel", None, None)])

LAST_CHANNEL_ERROR = ""

async def ensure_channel(client):
    global LAST_CHANNEL_ERROR
    for attempt in (1, 2):
        try:
            chat = await client.get_chat(DATABASE_CHANNEL_ID)
            print(f"✅ DB channel resolved: {chat.title}", flush=True)
            await _save_peer()
            return True
        except Exception as e:
            if attempt == 1:
                try:
                    await _load_peer()
                    continue
                except Exception:
                    pass
            LAST_CHANNEL_ERROR = str(e)
            print(f"❌ Cannot access DB channel {DATABASE_CHANNEL_ID}: {e}\n"
                  f"   -> Make the bot an admin of the channel, then post any message in the channel once.", flush=True)
            return False

@app.on_message(filters.chat(DATABASE_CHANNEL_ID), group=-2)
async def _seen_channel(client, message):
    await _save_peer()
    message.continue_propagation()

async def migrate_legacy_codes():
    async for m in wizard_movies.find({"_id": {"$lt": 10000}}):
        code = await new_movie_code()
        old = m["_id"]
        m["_id"] = code
        m.setdefault("created", datetime.datetime.utcnow())
        await wizard_movies.insert_one(m)
        await wizard_movies.delete_one({"_id": old})
        print(f"🔁 Movie {old} -> code {code} ({m['title']})", flush=True)

async def main():
    await app.start()
    me = await app.get_me()
    print(f"✅ Wizard Bot online as @{me.username} (id {me.id})", flush=True)
    try:
        await migrate_legacy_codes()
    except Exception as e:
        print(f"⚠️ Code migration skipped: {e}", flush=True)
    await ensure_channel(app)
    try:
        await app.set_bot_commands(BOT_COMMANDS)
    except Exception as e:
        print(f"⚠️ Could not set bot commands: {e}", flush=True)
    await idle()
    await app.stop()

if __name__ == "__main__":
    print("Wizard Bot is starting with TMDB integration...", flush=True)
    app.run(main())
