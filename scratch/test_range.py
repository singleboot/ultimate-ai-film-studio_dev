import requests

url = "http://127.0.0.1:7860/api/projects/MyProject_v4/saved-video/scene_1_shot_1.mp4?path=F%3A%5C000000%20test%5CMyProject_v4%5CMyProject_v4"

print("--- Testing WITHOUT Range Header ---")
r = requests.get(url, stream=True)
print("Status Code:", r.status_code)
print("Headers:", dict(r.headers))
chunk = next(r.iter_content(chunk_size=10))
print("First 10 bytes:", chunk)

print("\n--- Testing WITH Range Header (bytes=0-9) ---")
r2 = requests.get(url, headers={"Range": "bytes=0-9"})
print("Status Code:", r2.status_code)
print("Headers:", dict(r2.headers))
print("Content Length:", len(r2.content))
print("Content bytes:", r2.content)
