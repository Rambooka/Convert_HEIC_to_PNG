# Convert HEIC to PNG

A small Windows app that converts HEIC/HEIF photos (e.g. from iPhones) to PNG.

![icon](app.png)

## Features
- Two drop zones: **HEIC → PNG** and **JPG / PNG → HEIC**
- Drag files **or folders** from File Explorer onto a drop zone (or click it to browse)
- JPG/PNG → HEIC has a **Compression %** box: 0 = best quality / largest file,
  100 = smallest file (default 50). Camera JPGs stored sideways are rotated upright,
  EXIF and ICC profile are kept, and the JPG/PNG is kept unless you tick
  "Delete JPG/PNG after conversion"
- A HEIC is only kept if it is **smaller** than the original; otherwise it is discarded,
  the original is left untouched, and the log shows `SKIP` with both sizes
- Converts up to 8 files at once (fewer on smaller PCs) — HEIC → PNG is ~6× faster
  on a big batch; JPG → HEIC gains less because the HEIC encoder already uses every core
- Optional recursion into subfolders
- Each PNG is written next to the original, keeping timestamps, EXIF and ICC profile
- The HEIC is deleted **only after** the PNG has been written and verified (optional)
- Never overwrites: name clashes become `photo (1).png`
- Files/folders can also be dropped onto the `.exe` icon (converted HEIC → PNG)

## Download
Grab `HEIC_to_PNG.exe` from the [Releases](../../releases) page — single file, no install needed.

## Run from source
```bat
pip install -r requirements.txt
python heic_to_png.py
```

## Build the exe
```bat
build.bat
```
Creates a venv, installs dependencies, and builds `dist\HEIC_to_PNG.exe` with PyInstaller.
`make_icon.py` regenerates `app.ico` / `app.png`.

## Dependencies
- [Pillow](https://pypi.org/project/pillow/)
- [pillow-heif](https://pypi.org/project/pillow-heif/)
- [tkinterdnd2](https://pypi.org/project/tkinterdnd2/)
- [PyInstaller](https://pypi.org/project/pyinstaller/) (build only)
