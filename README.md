# Convert HEIC to PNG

A small Windows app that converts HEIC/HEIF photos (e.g. from iPhones) to PNG.

![icon](app.png)

## Features
- Drag files **or folders** from File Explorer onto the window (or click to browse)
- Optional recursion into subfolders
- Each PNG is written next to the original, keeping timestamps, EXIF and ICC profile
- The HEIC is deleted **only after** the PNG has been written and verified (optional)
- Never overwrites: name clashes become `photo (1).png`
- Files/folders can also be dropped onto the `.exe` icon

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
