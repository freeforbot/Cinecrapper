import asyncio
import os
import re
import aiohttp
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from motor.motor_asyncio import AsyncIOMotorClient

# Setup
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH")
MONGO_URI = os.environ.get("MONGO_URI")
DATABASE_CHANNEL_ID = -1003975570574
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")

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
        await msg.edit_text(f"❌ Error during upload: {e}\n\nMake sure I am added as an Admin in your Database Channel (-1003975570574) so I can post files!")

if __name__ == "__main__":
    print("Wizard Bot is starting with TMDB integration...")
    app.run()