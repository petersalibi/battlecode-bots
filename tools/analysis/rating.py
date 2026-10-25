import sys, datetime
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fetch_replays import ladder
teams = ladder(700)
me = next((t for t in teams if t["name"] == "Teto Territory"), None)
print(datetime.datetime.utcnow().strftime("%H:%M"), "rank", me and me["rank"], "rating", me and round(me["rating"]), "matches", me and me.get("matches"), "of", len(teams))
