import os
import requests

CLIENT_ID = os.environ["PCO_CLIENT_ID"]
SECRET = os.environ["PCO_SECRET"]
BASE = "https://api.planningcenteronline.com/publishing/v2"

episode_ids = ["724491", "724488", "724486", "724484", "724478", "723878"]

for eid in episode_ids:
    resp = requests.get(f"{BASE}/episodes/{eid}", auth=(CLIENT_ID, SECRET))
    data = resp.json()["data"]["attributes"]
    print(f"Episode {eid}: {data['title']}")
    print(f"  video_url: {data['video_url']}")
