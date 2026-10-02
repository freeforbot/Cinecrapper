import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
import csv
import time

async def main():
    print("Connecting to MongoDB...")
    client = AsyncIOMotorClient(os.environ.get('MONGO_URI'))
    db = client['cinesearch_db']
    col = db['scrape_queue']
    
    print("Fetching documents...")
    cursor = col.find({})
    docs = await cursor.to_list(length=None)
    
    print(f"Found {len(docs)} movies in queue. Exporting to CSV...")
    
    csv_path = r'C:\Users\dhara\Desktop\Movies_AI_Report.csv'
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Movie Name (AI Output)', 'Status', 'Original File Name', 'TMDB ID', 'Type', 'Added On'])
        for d in docs:
            writer.writerow([
                d.get('movie_name', 'Unknown'),
                d.get('status', 'Unknown'),
                d.get('original_file_name', ''),
                d.get('tmdb_id', ''),
                d.get('type', ''),
                str(d.get('added_on', ''))
            ])
            
    print(f"Export complete: {csv_path}")

asyncio.run(main())
