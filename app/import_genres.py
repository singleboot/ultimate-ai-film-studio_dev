import shutil, json, re
from pathlib import Path

src_dir = Path(r"H:\DOWNLOAD\genre image")
dest_dir = Path(__file__).parent / "genres"
settings_file = Path(__file__).parent / "settings.json"

dest_dir.mkdir(exist_ok=True)

category_map = {
    "Action": "Action", "Adventure": "Adventure", "Animation": "Animation",
    "Biographical": "Biopic/Drama", "Comedy": "Comedy", "Crime": "Crime/Mystery",
    "Documentary": "Documentary", "Drama": "Drama", "Family": "Drama",
    "Fantasy": "Fantasy", "Film-Noir": "Noir", "History": "Historical",
    "Horror": "Horror", "Musical": "Musical", "Mystery": "Crime/Mystery",
    "Sci-Fi": "Sci-Fi", "Thriller": "Thriller", "War": "War",
    "Western": "Western", "Cyberpunk": "Sci-Fi", "Steampunk": "Sci-Fi",
    "Post-Apocalyptic": "Sci-Fi", "Space-Opera": "Sci-Fi",
    "Zombie": "Horror", "Slasher": "Horror",
    "Psychological-Horror": "Horror", "Body-Horror": "Horror",
    "Found-Footage": "Horror", "Giallo": "Horror",
    "Dark-Comedy": "Comedy", "Slapstick": "Comedy", "Mockumentary": "Comedy",
    "Melodrama": "Drama", "Period-Piece": "Historical",
    "Legal-Drama": "Drama", "Medical-Drama": "Drama",
    "Political-Thriller": "Thriller", "Psychological-Thriller": "Thriller",
    "Techno-Thriller": "Thriller", "Heist": "Crime/Mystery",
    "Martial-Arts": "Action", "Superhero": "Action", "Epic": "Action",
    "Experimental": "Experimental", "Surrealist": "Experimental",
    "Sci-Fi-Horror": "Horror", "Comedy-Horror": "Horror",
    "Action-Comedy": "Comedy", "Sci-Fi-Western": "Sci-Fi",
    "Cyberpunk-Noir": "Noir", "Historical-Fantasy": "Fantasy",
    "Space-Western": "Sci-Fi", "Comic-Fantasy": "Fantasy",
    "Steampunk-Fantasy": "Fantasy", "Biographical-Drama": "Biopic/Drama",
    "Tech-Noir": "Noir", "Nordic-Noir": "Noir", "Neo-Noir": "Noir",
    "Splatstick": "Horror", "Solarpunk": "Sci-Fi", "Biopunk": "Sci-Fi",
    "Cli-Fi": "Sci-Fi", "Weird-West": "Western", "Afrofuturism": "Sci-Fi",
    "Chop-Socky": "Action", "Wuxia": "Action", "Ozploitation": "Western",
    "Spaghetti-Western": "Western", "J-Horror": "Horror", "K-Horror": "Horror",
    "Mumblegore": "Horror", "Extreme-Cinema": "Horror",
    "Acid-Western": "Western", "Surrealist-Comedy": "Comedy",
    "Grimdark": "Fantasy", "Heroic-Bloodshed": "Action",
    "Submarine-Thriller": "Thriller", "Locked-Room-Mystery": "Crime/Mystery",
    "Whodunnit": "Crime/Mystery", "Cosmic-Horror": "Horror",
    "Folk-Horror": "Horror", "Arthouse-Action": "Action",
    "Chamber-Drama": "Drama", "Campus-Novel-Film": "Drama",
    "Sword-and-Sorcery": "Fantasy", "Gaslamp-Fantasy": "Fantasy",
    "Paranoid-Thriller": "Thriller",
}

pattern = re.compile(r'^\d+_(.+?)-Cinematic-film-still')
genres = []

for f in sorted(src_dir.iterdir()):
    if f.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
        continue
    m = pattern.match(f.stem)
    if not m:
        # Fallback: try splitting on first hyphen after the number
        parts = f.stem.split('-', 2)
        try:
            genre_name = parts[0].split('_', 1)[1].replace('-', ' ').title() if len(parts) > 0 else None
        except:
            genre_name = None
        if not genre_name:
            print(f"Skipping {f.name}: could not parse")
            continue
        category = category_map.get(parts[0].split('_', 1)[1], "Other")
    else:
        genre_name = m.group(1).replace('-', ' ').title()
        category = category_map.get(m.group(1), "Other")
    ext = f.suffix
    dest_name = f"{genre_name.lower().replace(' ', '_')}{ext}"
    dest_path = dest_dir / dest_name
    shutil.copy2(f, dest_path)
    genres.append({"name": genre_name, "image": dest_name, "category": category})
    print(f"  {genre_name} -> {category}")

# Update settings
if settings_file.exists():
    settings = json.loads(settings_file.read_text())
else:
    settings = {}
settings["genres"] = genres
settings_file.write_text(json.dumps(settings, indent=2))
print(f"\nDone! {len(genres)} genres imported to {dest_dir}")
