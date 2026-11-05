"""Print screenshots into the job log as base64 chunks (artifact downloads are not reachable
from the development sandbox, job logs are). Usage: python tools/dump_images.py DIR"""
import base64
import pathlib
import sys

CHUNK = 20000
for path in sorted(pathlib.Path(sys.argv[1]).glob("*.jpg")):
    data = base64.b64encode(path.read_bytes()).decode()
    parts = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)]
    for i, part in enumerate(parts):
        print(f"IMGB64 {path.name} {i} {len(parts)} {part}")
