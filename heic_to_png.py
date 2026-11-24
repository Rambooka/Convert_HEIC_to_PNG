"""
HEIC <-> PNG/JPG Converter
Drag files or folders from File Explorer onto one of the two drop zones:

* HEIC -> PNG: each .heic/.heif file is converted to .png alongside it.
* JPG/PNG -> HEIC: each .jpg/.jpeg/.png file is converted to .heic alongside
  it, at the compression % chosen in the window.

The original is deleted (optional) only after the new file has been written
and verified to decode.
"""

import os
import sys
import threading
import queue
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


def unique_path(src_path, new_ext):
    """`photo.heic` -> `photo.png`, or `photo (1).png` if that already exists."""
    base = os.path.splitext(src_path)[0]
    candidate = base + new_ext
    n = 1
    while os.path.exists(candidate):
        candidate = f"{base} ({n}){new_ext}"
        n += 1
    return candidate


def _convert_file(src_path, new_ext, save, delete_original):
    """Shared plumbing for both directions: write to a .part file, prove it
    decodes, rename it into place, keep the original's timestamps, and only
    then (optionally) delete the original. Returns the new path; raises on
    failure, leaving the original untouched."""
    out_path = unique_path(src_path, new_ext)
    tmp_path = out_path + ".part"
    try:
        with Image.open(src_path) as im:
            save(im, tmp_path)

        # Verify the new file decodes before touching the original
        with Image.open(tmp_path) as check:
            check.load()
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

    return _convert_file(src_path, ".heic", save, delete_original)


def parse_drop_data(root, data):
    """tkdnd gives a Tcl list; paths with spaces are wrapped in {braces}."""
    try:
        return list(root.tk.splitlist(data))
    except tk.TclError:
        return [data]


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.busy = False

        root.title(APP_TITLE)
        root.geometry("780x540")
        root.minsize(640, 440)

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
        self.var_delete = tk.BooleanVar(value=True)
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
        self.var_compression = tk.StringVar(value=str(DEFAULT_COMPRESSION))
        only_digits = (root.register(lambda s: s == "" or (s.isdigit() and len(s) <= 3)), "%P")
        self.compression_box = ttk.Spinbox(
            comp_row, from_=0, to=100, increment=5, width=5,
            textvariable=self.var_compression,
            validate="key", validatecommand=only_digits,
        )
        self.compression_box.pack(side="left", padx=(6, 0))
        ttk.Label(right_opts, text="0 = best quality, 100 = smallest file",
                  foreground="#666").pack(anchor="w")
        self.var_delete_src = tk.BooleanVar(value=False)
        ttk.Checkbutton(right_opts, text="Delete JPG/PNG after conversion",
                        variable=self.var_delete_src).pack(anchor="w")

        # Shared options
        opts = ttk.Frame(main)
        opts.pack(fill="x", pady=(8, 4))
        self.var_recursive = tk.BooleanVar(value=True)
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

        # A drop anywhere else on the window is HEIC -> PNG (the original behaviour)
        root.drop_target_register(DND_FILES)
        root.dnd_bind("<<Drop>>", self.on_window_drop)

        self.root.after(100, self._poll)

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
        self.status.configure(text=f"Converting {len(files)} file(s) to {target}...")
        self._log(f"--- {len(files)} file(s) queued -> {target} ---")
        threading.Thread(target=self._worker, args=(files, job), daemon=True).start()

    def _worker(self, files, job):
        ok = fail = 0
        for i, f in enumerate(files, 1):
            try:
                out = job(f)
                ok += 1
                self.q.put(("log", f"OK   {f}  ->  {os.path.basename(out)}", "ok"))
            except Exception as ex:  # noqa: BLE001
                fail += 1
                self.q.put(("log", f"FAIL {f}: {ex}", "err"))
            self.q.put(("progress", i, len(files)))
        self.q.put(("done", ok, fail))

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
                    msg = f"Done. {item[1]} converted, {item[2]} failed."
                    self.status.configure(text=msg)
                    self._log(msg, "ok" if item[2] == 0 else "err")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)


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
