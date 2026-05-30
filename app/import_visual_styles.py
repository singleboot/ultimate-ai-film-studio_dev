import shutil, json, re
from pathlib import Path

src_dir = Path(r"H:\DOWNLOAD\visual style image")
dest_dir = Path(__file__).parent / "visual_styles"
settings_file = Path(__file__).parent / "settings.json"

dest_dir.mkdir(exist_ok=True)

pattern = re.compile(r'^\d+_(.+?)\.(jpg|jpeg|png|webp)$', re.IGNORECASE)
styles = []

for f in sorted(src_dir.iterdir()):
    if f.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
        continue
    m = pattern.match(f.stem)
    raw_name = m.group(1) if m else f.stem.split('_', 1)[-1]
    # Try to extract name before -Style- (or just -Style at end)
    name = raw_name
    if '-Style-' in raw_name:
        name = raw_name.split('-Style-')[0]
    elif raw_name.endswith('-Style'):
        name = raw_name[:-6]
    name = name.replace('-', ' ').title().strip()
    ext = f.suffix
    dest_name = f.name
    dest_path = dest_dir / dest_name
    if not dest_path.exists():
        shutil.copy2(f, dest_path)
    styles.append({"name": name, "image": dest_name})
    print(f"  {name}")

if settings_file.exists():
    settings = json.loads(settings_file.read_text())
else:
    settings = {}
settings["visual_styles"] = styles
settings_file.write_text(json.dumps(settings, indent=2))
print(f"\nDone! {len(styles)} visual styles imported to {dest_dir}")
