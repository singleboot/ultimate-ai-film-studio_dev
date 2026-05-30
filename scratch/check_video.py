import sys
import struct

def analyze_mp4(filepath):
    print(f"Analyzing {filepath}")
    with open(filepath, 'rb') as f:
        data = f.read(100000) # Read first 100KB to find codecs
        
    # Search for 'mp4a', 'avc1', 'hvc1', 'vp09', 'hev1'
    codecs = []
    for c in [b'avc1', b'hvc1', b'hev1', b'vp09', b'mp4v', b'mp4a']:
        idx = data.find(c)
        if idx != -1:
            codecs.append(c.decode('utf-8'))
            print(f"Found codec atom: {c.decode('utf-8')} at byte {idx}")
            
    if not codecs:
        print("No standard video codec atoms found in the first 100KB.")

if __name__ == "__main__":
    analyze_mp4("F:\\000000 test\\00001\\videos\\scene_1.mp4")
