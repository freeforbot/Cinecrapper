import google.generativeai as genai
import os
import asyncio

genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-3.6-flash')
prompt = '''You are a button analyzer.
Here are the buttons from a Telegram bot message:
0,0: ?? Remove ads ??
0,1: S??? A??
1,0: ??????????????
1,1: ????????????????
1,2: ????????????

INSTRUCTIONS:
1. Find the button that clearly means "Send All", "Forward All", or "Get All Files" (even if written in weird fancy fonts like S??? A??).
2. Find the button that clearly means "Next Page" or "Next" (usually has an arrow).
3. Output your answer exactly in this strict format:
SEND_ALL=r,c
NEXT=r,c

If a button is not found, output NONE instead of r,c. Do not say anything else.'''

response = model.generate_content(prompt)
print(repr(response.text))
