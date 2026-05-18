import shutil, json, re
from pathlib import Path

src_dir = Path(r"H:\DOWNLOAD\film style image")
dest_dir = Path(__file__).parent / "film_aesthetics"
settings_file = Path(__file__).parent / "settings.json"

dest_dir.mkdir(exist_ok=True)

pattern = re.compile(r'^\d+_(.+?)\.(jpg|jpeg|png|webp)$', re.IGNORECASE)
aesthetics = []

for f in sorted(src_dir.iterdir()):
    if f.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
        continue
    m = pattern.match(f.stem)
    raw_name = m.group(1) if m else f.stem.split('_', 1)[-1]
    # Try to extract name before -Aesthetic- (or just -Aesthetic at end)
    name = raw_name
    if '-Aesthetic-' in raw_name:
        name = raw_name.split('-Aesthetic-')[0]
    elif raw_name.endswith('-Aesthetic'):
        name = raw_name[:-10]
    name = name.replace('-', ' ').title().strip()
    ext = f.suffix
    dest_name = f.name
    dest_path = dest_dir / dest_name
    if not dest_path.exists():
        shutil.copy2(f, dest_path)
    aesthetics.append({"name": name, "image": dest_name})
    print(f"  {name}")

if settings_file.exists():
    settings = json.loads(settings_file.read_text())
else:
    settings = {}
settings["film_aesthetics"] = aesthetics
settings_file.write_text(json.dumps(settings, indent=2))
print(f"\nDone! {len(aesthetics)} film aesthetics imported to {dest_dir}")
