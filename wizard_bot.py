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
from pyrogram import Client, filters, idle
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

@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    await message.reply_text("👋 Hello! I am your Movie Upload Wizard.\n\nType /addmovie to begin the step-by-step upload process!")

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
    if user_id in WIZARD_STATE and WIZARD_STATE[user_id]["step"] == "awaiting_files":
        if not WIZARD_STATE[user_id]["files"]:
            await message.reply_text("❌ You haven't sent any files! Send movie files or type /cancel.")
            return
            
        WIZARD_STATE[user_id]["step"] = "awaiting_keywords"
        await message.reply_text("✅ Files received! Now, please reply with the **Search Keywords / Tags** for this movie.\n\nExample: iron man, ironman 1")

@app.on_message(filters.text & ~filters.command(["start", "addmovie", "cancel", "done"]))
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
        
    elif state["step"] == "awaiting_keywords":
        state["keywords"] = message.text
        await finish_wizard(client, message, state)
        del WIZARD_STATE[user_id]

@app.on_message(filters.document | filters.video)
async def file_handler(client, message):
    user_id = message.from_user.id
    if user_id in WIZARD_STATE and WIZARD_STATE[user_id]["step"] == "awaiting_files":
        WIZARD_STATE[user_id]["files"].append(message)
        await message.reply_text("✅ File added to wizard! Send more, or type /done.")

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
        for f_msg in state["files"]:
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
            
            file_caption = generate_beautiful_caption(file_name, file_size)
            file_caption = file_caption.replace("[@CinePrimeHub]", f"🔍 **Search Tags:**\n{keywords}\n\n[@CinePrimeHub]")
            
            await client.copy_message(
                chat_id=DATABASE_CHANNEL_ID,
                from_chat_id=f_msg.chat.id,
                message_id=f_msg.id,
                caption=file_caption,
                reply_to_message_id=poster_msg.id
            )
            
            # Save to Mongo
            await movies_col.insert_one({"file_name": file_name})
            await asyncio.sleep(1)
            
        await msg.edit_text("✅ **Movie Successfully Added to Database!**\n\nThe poster and all files have been published to your Database Channel, and the database has been perfectly indexed! 🎉")
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

async def ensure_channel(client):
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
            print(f"❌ Cannot access DB channel {DATABASE_CHANNEL_ID}: {e}\n"
                  f"   -> Make the bot an admin of the channel, then post any message in the channel once.", flush=True)
            return False

@app.on_message(filters.chat(DATABASE_CHANNEL_ID), group=-2)
async def _seen_channel(client, message):
    await _save_peer()
    message.continue_propagation()

async def main():
    await app.start()
    me = await app.get_me()
    print(f"✅ Wizard Bot online as @{me.username} (id {me.id})", flush=True)
    await ensure_channel(app)
    await idle()
    await app.stop()

if __name__ == "__main__":
    print("Wizard Bot is starting with TMDB integration...", flush=True)
    app.run(main())
