"""
HEIC <-> PNG/JPG Converter
Drag files or folders from File Explorer onto one of the two drop zones:

* HEIC -> PNG: each .heic/.heif file is converted to .png alongside it.
* JPG/PNG -> HEIC: each .jpg/.jpeg/.png file is converted to .heic alongside
  it, at the compression % chosen in the window.

The original is deleted (optional) only after the new file has been written
and verified to decode.
"""

import json
import os
import re
import sys
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageOps
import pillow_heif
from tkinterdnd2 import TkinterDnD, DND_FILES

pillow_heif.register_heif_opener()

HEIC_EXTS = {".heic", ".heif"}
TO_HEIC_EXTS = {".jpg", ".jpeg", ".png"}
APP_TITLE = "HEIC <-> PNG/JPG Converter"
APP_ID = "ICT.HEICtoPNG.Converter.1"

# Compression % for JPG/PNG -> HEIC. HEIC quality = 100 - compression, so the
# default of 50 is pillow-heif's own default quality.
DEFAULT_COMPRESSION = 50

# How many files are converted at once.
WORKERS = max(1, min(8, os.cpu_count() or 1))

# Options and window position are remembered between runs here. Not next to
# the program: the one-file .exe unpacks itself to a new temp folder each run.
SETTINGS_FILE = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "HEIC_to_PNG", "settings.json")
DEFAULT_GEOMETRY = (780, 540)

ZONE_BG = "#f4f6f8"
ZONE_BG_HOVER = "#dceefc"


def resource_path(name):
    """Path to a bundled file, both when run as .py and as a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def set_window_icon(root):
    ico = resource_path("app.ico")
    png = resource_path("app.png")
    try:
        if os.path.exists(ico):
            root.iconbitmap(default=ico)  # title bar + taskbar on Windows
    except tk.TclError:
        pass
    try:
        if os.path.exists(png):
            root._icon_img = tk.PhotoImage(file=png)  # keep a reference
            root.iconphoto(True, root._icon_img)
    except tk.TclError:
        pass


def collect_files(paths, exts, recursive=True):
    """Expand dropped paths (files and/or folders) into the files whose
    extension is in `exts`."""
    found = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _dirs, files in os.walk(p):
                    for f in files:
                        if os.path.splitext(f)[1].lower() in exts:
                            found.append(os.path.join(root, f))
            else:
                for f in os.listdir(p):
                    full = os.path.join(p, f)
                    if os.path.isfile(full) and os.path.splitext(f)[1].lower() in exts:
                        found.append(full)
        elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in exts:
            found.append(p)
    # de-duplicate, keep order
    seen = set()
    out = []
    for f in found:
        key = os.path.normcase(os.path.abspath(f))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


# Output names claimed by a conversion still in progress. Files are converted
# in parallel, so `photo.jpg` and `photo.png` could otherwise both pick
# `photo.heic` before either has written it.
_name_lock = threading.Lock()
_reserved_names = set()


def reserve_unique_path(src_path, new_ext):
    """`photo.heic` -> `photo.png`, or `photo (1).png` if that already exists
    or another worker has claimed it. Pair with release_path()."""
    base = os.path.splitext(src_path)[0]
    candidate = base + new_ext
    n = 1
    with _name_lock:
        while os.path.exists(candidate) or os.path.normcase(candidate) in _reserved_names:
            candidate = f"{base} ({n}){new_ext}"
            n += 1
        _reserved_names.add(os.path.normcase(candidate))
    return candidate


def release_path(path):
    with _name_lock:
        _reserved_names.discard(os.path.normcase(path))


class NotSmallerError(Exception):
    """The new file came out no smaller than the original, so it was
    discarded and the original kept."""


def human_size(n):
    if n < 1024:
        return f"{n:,} bytes"
    if n < 1024 * 1024:
        return f"{n / 1024:,.1f} KB"
    return f"{n / (1024 * 1024):,.2f} MB"


def _convert_file(src_path, new_ext, save, delete_original, must_be_smaller=False):
    """Shared plumbing for both directions: write to a .part file, prove it
    decodes, rename it into place, keep the original's timestamps, and only
    then (optionally) delete the original. Returns the new path; raises on
    failure, leaving the original untouched.

    With `must_be_smaller`, a new file that isn't strictly smaller than the
    original is thrown away (NotSmallerError) and the original is kept, even
    when `delete_original` is set."""
    out_path = reserve_unique_path(src_path, new_ext)
    tmp_path = out_path + ".part"
    try:
        with Image.open(src_path) as im:
            save(im, tmp_path)

        # Verify the new file decodes before touching the original
        with Image.open(tmp_path) as check:
            check.load()

        if must_be_smaller:
            new_size = os.path.getsize(tmp_path)
            old_size = os.path.getsize(src_path)
            if new_size >= old_size:
                raise NotSmallerError(
                    f"{new_ext.lstrip('.').upper()} would be {human_size(new_size)}, "
                    f"original is {human_size(old_size)} - kept the original only")
        os.replace(tmp_path, out_path)

        # Keep original timestamps on the new file
        st = os.stat(src_path)
        os.utime(out_path, (st.st_atime, st.st_mtime))

        if delete_original:
            os.remove(src_path)
        return out_path
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        # Either the file now exists on disk or it was never written; the
        # in-memory claim is no longer needed in both cases.
        release_path(out_path)


def _save_png(im, tmp_path):
    exif = im.info.get("exif")
    icc = im.info.get("icc_profile")
    if im.mode not in ("RGB", "RGBA", "L", "LA"):
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    save_kwargs = {"format": "PNG", "optimize": False}
    if exif:
        save_kwargs["exif"] = exif
    if icc:
        save_kwargs["icc_profile"] = icc
    im.save(tmp_path, **save_kwargs)


def convert_to_png(heic_path, delete_original=True):
    """Convert a single HEIC/HEIF file to PNG. Returns the PNG path."""
    return _convert_file(heic_path, ".png", _save_png, delete_original)


def convert_to_heic(src_path, compression, delete_original=False):
    """Convert a single JPG/PNG file to HEIC. Returns the HEIC path.

    `compression` is 0-100 %: 0 = best quality / largest file,
    100 = smallest file / worst quality (HEIC quality = 100 - compression).

    The HEIC is only kept if it is smaller than the original; otherwise it is
    discarded, the original is left alone, and NotSmallerError is raised.
    """
    if not 0 <= compression <= 100:
        raise ValueError(f"compression must be 0-100 %, got {compression}")

    def save(src, tmp_path):
        # A CMYK JPEG's ICC profile describes CMYK, so it can't ride along
        # once the pixels are converted to RGB.
        icc = src.info.get("icc_profile") if src.mode != "CMYK" else None
        # Rotate the pixels upright: a camera JPG is often stored sideways
        # with an EXIF Orientation tag, which HEIC viewers ignore.
        im = ImageOps.exif_transpose(src)
        exif = im.getexif()
        if im.mode in ("I", "I;16", "I;16B", "I;16L"):
            # 16-bit greyscale PNG: scale 0-65535 down to 0-255 (a plain
            # convert would clip everything above 255 to white)
            im = im.convert("I").point(lambda v: v * (1 / 256)).convert("L")
        if im.mode not in ("RGB", "RGBA", "L"):
            has_alpha = "A" in im.getbands() or "transparency" in im.info
            im = im.convert("RGBA" if has_alpha else "RGB")
        save_kwargs = {"format": "HEIF", "quality": 100 - compression}
        if exif:
            save_kwargs["exif"] = exif.tobytes()
        if icc:
            save_kwargs["icc_profile"] = icc
        im.save(tmp_path, **save_kwargs)

    return _convert_file(src_path, ".heic", save, delete_original, must_be_smaller=True)


def load_settings(path):
    """The saved settings dict, or {} when there is none yet or it can't be
    read (each value is still type-checked by the caller)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):  # unreadable or corrupt -> start from defaults
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(path, data):
    """Best effort: failing to remember a setting must never get in the way
    of converting, so a write error is ignored. Written to a temp file and
    renamed, so a crash mid-write can't leave a half-written file."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def virtual_screen():
    """(left, top, width, height) of the desktop spanning every monitor, or
    None when it can't be found (non-Windows)."""
    try:
        from ctypes import windll
        gsm = windll.user32.GetSystemMetrics
        return gsm(76), gsm(77), gsm(78), gsm(79)  # SM_X/Y/CX/CYVIRTUALSCREEN
    except Exception:  # noqa: BLE001
        return None


def parse_drop_data(root, data):
    """tkdnd gives a Tcl list; paths with spaces are wrapped in {braces}."""
    try:
        return list(root.tk.splitlist(data))
    except tk.TclError:
        return [data]


class App:
    def __init__(self, root, settings_path=SETTINGS_FILE):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        self.settings_path = settings_path
        self.settings = load_settings(settings_path)

        root.title(APP_TITLE)
        root.minsize(640, 440)
        self._restore_window()

        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)

        # Two drop zones side by side, each with its own options underneath
        zones = ttk.Frame(main)
        zones.pack(fill="x")
        zones.columnconfigure(0, weight=1, uniform="zone")
        zones.columnconfigure(1, weight=1, uniform="zone")

        # -- Left: HEIC -> PNG
        self.drop = self._make_drop_zone(
            zones,
            "HEIC  →  PNG\n\nDrag HEIC files or folders here\n(or click to browse)",
            self.start_to_png,
            self.browse_to_png,
        )
        self.drop.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left_opts = ttk.Frame(zones)
        left_opts.grid(row=1, column=0, sticky="nw", padx=(0, 5), pady=(6, 0))
        self.var_delete = tk.BooleanVar(value=self._saved_bool("delete_heic", True))
        ttk.Checkbutton(left_opts, text="Delete HEIC after conversion",
                        variable=self.var_delete).pack(anchor="w")

        # -- Right: JPG/PNG -> HEIC
        self.drop_heic = self._make_drop_zone(
            zones,
            "JPG / PNG  →  HEIC\n\nDrag JPG or PNG files or folders here\n(or click to browse)",
            self.start_to_heic,
            self.browse_to_heic,
        )
        self.drop_heic.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right_opts = ttk.Frame(zones)
        right_opts.grid(row=1, column=1, sticky="nw", padx=(5, 0), pady=(6, 0))
        comp_row = ttk.Frame(right_opts)
        comp_row.pack(anchor="w")
        ttk.Label(comp_row, text="Compression %:").pack(side="left")
        self.var_compression = tk.StringVar(value=str(self._saved_compression()))
        only_digits = (root.register(lambda s: s == "" or (s.isdigit() and len(s) <= 3)), "%P")
        self.compression_box = ttk.Spinbox(
            comp_row, from_=0, to=100, increment=5, width=5,
            textvariable=self.var_compression,
            validate="key", validatecommand=only_digits,
        )
        self.compression_box.pack(side="left", padx=(6, 0))
        ttk.Label(right_opts, text="0 = best quality, 100 = smallest file",
                  foreground="#666").pack(anchor="w")
        self.var_delete_src = tk.BooleanVar(value=self._saved_bool("delete_src", False))
        ttk.Checkbutton(right_opts, text="Delete JPG/PNG after conversion",
                        variable=self.var_delete_src).pack(anchor="w")

        # Shared options
        opts = ttk.Frame(main)
        opts.pack(fill="x", pady=(8, 4))
        self.var_recursive = tk.BooleanVar(value=self._saved_bool("recursive", True))
        ttk.Checkbutton(opts, text="Include subfolders", variable=self.var_recursive).pack(side="left")

        # Progress
        self.progress = ttk.Progressbar(main, mode="determinate")
        self.progress.pack(fill="x", pady=4)
        self.status = ttk.Label(main, text="Ready.")
        self.status.pack(anchor="w")

        # Log
        logf = ttk.Frame(main)
        logf.pack(fill="both", expand=True, pady=(6, 0))
        self.log = tk.Text(logf, height=10, wrap="none", font=("Consolas", 9), state="disabled")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.log.tag_configure("err", foreground="#b00020")
        self.log.tag_configure("ok", foreground="#1b5e20")
        self.log.tag_configure("skip", foreground="#8a6d00")

        # A drop anywhere else on the window is HEIC -> PNG (the original behaviour)
        root.drop_target_register(DND_FILES)
        root.dnd_bind("<<Drop>>", self.on_window_drop)

        # Remember every option as soon as it changes, and the window on close
        for var in (self.var_delete, self.var_delete_src, self.var_recursive, self.var_compression):
            var.trace_add("write", lambda *_: self._save_settings())
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._poll_id = self.root.after(100, self._poll)

    # ---- Remembered settings ---------------------------------------------
    def _saved_bool(self, key, default):
        value = self.settings.get(key)
        return value if isinstance(value, bool) else default

    def _saved_compression(self):
        value = self.settings.get("compression")
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100:
            return value
        return DEFAULT_COMPRESSION

    def _restore_window(self):
        """Last run's size, and its position if that is still on a monitor
        (a monitor may have been unplugged since)."""
        width, height = DEFAULT_GEOMETRY
        position = ""
        m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", str(self.settings.get("geometry", "")))
        if m:
            width, height, x, y = (int(g) for g in m.groups())
            screen = virtual_screen()
            if screen:
                left, top, sw, sh = screen
                # keep it only if the title bar's left part is on the desktop
                if left <= x <= left + sw - 100 and top <= y <= top + sh - 50:
                    position = f"+{x}+{y}"
        self.root.geometry(f"{width}x{height}{position}")
        if self.settings.get("maximized") is True:
            self.root.after(0, lambda: self.root.state("zoomed"))

    def _save_settings(self, include_window=False):
        data = dict(self.settings)
        data["delete_heic"] = self.var_delete.get()
        data["delete_src"] = self.var_delete_src.get()
        data["recursive"] = self.var_recursive.get()
        text = self.var_compression.get().strip()
        if text.isdigit() and 0 <= int(text) <= 100:  # half-typed values aren't saved
            data["compression"] = int(text)
        if include_window:
            maximized = self.root.state() == "zoomed"
            data["maximized"] = maximized
            if not maximized and self.root.state() == "normal":
                # wm_geometry is "WxH+X+Y"; Tk writes a negative X as "+-120"
                data["geometry"] = self.root.wm_geometry()
        self.settings = data
        save_settings(self.settings_path, data)

    def on_close(self):
        self._save_settings(include_window=True)
        self.root.after_cancel(self._poll_id)
        self.root.destroy()

    # ---- UI helpers -------------------------------------------------------
    def _make_drop_zone(self, parent, text, on_paths, on_browse):
        zone = tk.Label(
            parent, text=text,
            relief="groove", bd=2, bg=ZONE_BG, fg="#333",
            font=("Segoe UI", 12), height=6, cursor="hand2",
        )
        zone.drop_target_register(DND_FILES)
        zone.dnd_bind("<<DropEnter>>", lambda e: self._hl(zone, True) or e.action)
        zone.dnd_bind("<<DropLeave>>", lambda e: self._hl(zone, False) or e.action)

        def on_drop(event):
            self._hl(zone, False)
            on_paths(parse_drop_data(self.root, event.data))
            return event.action

        zone.dnd_bind("<<Drop>>", on_drop)
        zone.bind("<Button-1>", on_browse)
        return zone

    def _hl(self, zone, on):
        zone.configure(bg=ZONE_BG_HOVER if on else ZONE_BG)

    def _log(self, msg, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _read_compression(self):
        """The compression % box as an int 0-100, or None (after telling the
        user) when it holds anything else."""
        text = self.var_compression.get().strip()
        if text.isdigit() and 0 <= int(text) <= 100:
            return int(text)
        messagebox.showerror(APP_TITLE, "Compression % must be a whole number from 0 to 100.")
        self.compression_box.focus_set()
        return None

    # ---- Inputs -----------------------------------------------------------
    def on_window_drop(self, event):
        self.start_to_png(parse_drop_data(self.root, event.data))
        return event.action

    def browse_to_png(self, _event=None):
        if self.busy:
            return
        files = filedialog.askopenfilenames(
            title="Select HEIC files",
            filetypes=[("HEIC images", "*.heic *.heif"), ("All files", "*.*")],
        )
        if files:
            self.start_to_png(list(files))

    def browse_to_heic(self, _event=None):
        if self.busy:
            return
        files = filedialog.askopenfilenames(
            title="Select JPG or PNG files",
            filetypes=[("JPG / PNG images", "*.jpg *.jpeg *.png"), ("All files", "*.*")],
        )
        if files:
            self.start_to_heic(list(files))

    # ---- Work -------------------------------------------------------------
    def _refuse_if_busy(self):
        if self.busy:
            messagebox.showinfo(APP_TITLE, "A conversion is already running.")
        return self.busy

    def start_to_png(self, paths):
        if self._refuse_if_busy():
            return
        files = collect_files(paths, HEIC_EXTS, self.var_recursive.get())
        if not files:
            self._log("No .heic/.heif files found in what was dropped. "
                      "(To make HEIC files, use the JPG / PNG → HEIC box.)", "err")
            return
        delete = self.var_delete.get()
        self._run(files, lambda f: convert_to_png(f, delete), "PNG")

    def start_to_heic(self, paths):
        if self._refuse_if_busy():
            return
        compression = self._read_compression()
        if compression is None:
            return
        files = collect_files(paths, TO_HEIC_EXTS, self.var_recursive.get())
        if not files:
            self._log("No .jpg/.jpeg/.png files found in what was dropped.", "err")
            return
        delete = self.var_delete_src.get()
        self._run(files, lambda f: convert_to_heic(f, compression, delete),
                  f"HEIC ({compression}% compression)")

    def _run(self, files, job, target):
        self.busy = True
        self.progress.configure(maximum=len(files), value=0)
        threads = min(WORKERS, len(files))
        self.status.configure(text=f"Converting {len(files)} file(s) to {target}...")
        self._log(f"--- {len(files)} file(s) queued -> {target}, {threads} at a time ---")
        threading.Thread(target=self._worker, args=(files, job), daemon=True).start()

    def _worker(self, files, job):
        """Runs on a background thread: converts the files in parallel and
        reports each one back to the UI (in completion order) via the queue."""
        ok = skipped = fail = 0
        with ThreadPoolExecutor(max_workers=min(WORKERS, len(files))) as pool:
            futures = {pool.submit(job, f): f for f in files}
            for i, fut in enumerate(as_completed(futures), 1):
                f = futures[fut]
                try:
                    out = fut.result()
                    ok += 1
                    self.q.put(("log", f"OK   {f}  ->  {os.path.basename(out)}", "ok"))
                except NotSmallerError as ex:
                    skipped += 1
                    self.q.put(("log", f"SKIP {f}: {ex}", "skip"))
                except Exception as ex:  # noqa: BLE001
                    fail += 1
                    self.q.put(("log", f"FAIL {f}: {ex}", "err"))
                self.q.put(("progress", i, len(files)))
        self.q.put(("done", ok, skipped, fail))

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._log(item[1], item[2])
                elif kind == "progress":
                    self.progress.configure(value=item[1])
                    self.status.configure(text=f"Converting {item[1]}/{item[2]}...")
                elif kind == "done":
                    self.busy = False
                    ok, skipped, fail = item[1], item[2], item[3]
                    msg = f"Done. {ok} converted, "
                    if skipped:
                        msg += f"{skipped} skipped (HEIC not smaller), "
                    msg += f"{fail} failed."
                    self.status.configure(text=msg)
                    self._log(msg, "ok" if fail == 0 else "err")
        except queue.Empty:
            pass
        self._poll_id = self.root.after(100, self._poll)


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
        # Own taskbar identity, so Windows shows our icon rather than Python's
        windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # noqa: BLE001
        pass
    root = TkinterDnD.Tk()
    set_window_icon(root)
    app = App(root)
    # Files/folders passed on the command line (e.g. dropped onto the .exe icon)
    if len(sys.argv) > 1:
        root.after(300, lambda: app.start_to_png(sys.argv[1:]))
    root.mainloop()


if __name__ == "__main__":
    main()
