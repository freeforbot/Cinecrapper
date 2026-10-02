import pandas as pd
import re

years = [2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018]
movies = []

for year in years:
    try:
        url = f"https://en.wikipedia.org/wiki/List_of_Tamil_films_of_{year}"
        tables = pd.read_html(url)
        for df in tables:
            if 'Title' in df.columns:
                movies.extend(df['Title'].dropna().tolist())
    except Exception as e:
        pass

cleaned = []
for m in set(movies):
    m = str(m).strip()
    # Remove references like [1]
    m = re.sub(r'\[\d+\]', '', m)
    if len(m) > 1 and "film" not in m.lower() and "untitled" not in m.lower():
        cleaned.append(m)

with open("d:\\cinescraper\\tamil_movies_list.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(cleaned))
    
print(f"Success! Saved {len(cleaned)} movies to tamil_movies_list.txt")
