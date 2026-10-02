import os
import pymongo
from dotenv import load_dotenv

load_dotenv('d:/cinescraper/.env')
client = pymongo.MongoClient(os.environ.get('MONGO_URI'))
db = client['cinesearch_db']
queue = db['scrape_queue']

result = queue.update_many({'status': {'$in': ['processing', 'failed']}}, {'$set': {'status': 'pending'}})
print(f'Reset {result.modified_count} movies back to pending!')
