"""
HEIC -> PNG Converter
Drag files or folders from File Explorer onto the window.
Each .heic/.heif file is converted to .png alongside it, and the original
is deleted only after the PNG has been written and verified.
"""

import os
import sys
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image
import pillow_heif
from tkinterdnd2 import TkinterDnD, DND_FILES

pillow_heif.register_heif_opener()

HEIC_EXTS = {".heic", ".heif"}
APP_TITLE = "HEIC to PNG Converter"
APP_ID = "ICT.HEICtoPNG.Converter.1"


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


def collect_heic_files(paths, recursive=True):
    """Expand dropped paths (files and/or folders) into a list of HEIC files."""
    found = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _dirs, files in os.walk(p):
                    for f in files:
                        if os.path.splitext(f)[1].lower() in HEIC_EXTS:
                            found.append(os.path.join(root, f))
            else:
                for f in os.listdir(p):
                    full = os.path.join(p, f)
                    if os.path.isfile(full) and os.path.splitext(f)[1].lower() in HEIC_EXTS:
                        found.append(full)
        elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in HEIC_EXTS:
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


def unique_png_path(heic_path):
    base = os.path.splitext(heic_path)[0]
    candidate = base + ".png"
    n = 1
    while os.path.exists(candidate):
        candidate = f"{base} ({n}).png"
        n += 1
    return candidate


def convert_one(heic_path, delete_original=True):
    """Convert a single file. Returns the PNG path. Raises on failure."""
    png_path = unique_png_path(heic_path)
    tmp_path = png_path + ".part"
    try:
        with Image.open(heic_path) as im:
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

        # Verify the PNG is readable before touching the original
        with Image.open(tmp_path) as check:
            check.verify()
        os.replace(tmp_path, png_path)

        # Keep original timestamps on the new file
        st = os.stat(heic_path)
        os.utime(png_path, (st.st_atime, st.st_mtime))

        if delete_original:
            os.remove(heic_path)
        return png_path
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


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
        root.geometry("640x460")
        root.minsize(480, 360)

        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)

        # Drop zone
        self.drop = tk.Label(
            main,
            text="Drag HEIC files or folders here\n\n(or click to browse)",
            relief="groove", bd=2, bg="#f4f6f8", fg="#333",
            font=("Segoe UI", 13), height=6, cursor="hand2",
        )
        self.drop.pack(fill="x")
        self.drop.drop_target_register(DND_FILES)
        self.drop.dnd_bind("<<DropEnter>>", lambda e: self._hl(True) or e.action)
        self.drop.dnd_bind("<<DropLeave>>", lambda e: self._hl(False) or e.action)
        self.drop.dnd_bind("<<Drop>>", self.on_drop)
        self.drop.bind("<Button-1>", self.on_browse)

        # Options
        opts = ttk.Frame(main)
        opts.pack(fill="x", pady=(8, 4))
        self.var_recursive = tk.BooleanVar(value=True)
        self.var_delete = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Include subfolders", variable=self.var_recursive).pack(side="left")
        ttk.Checkbutton(opts, text="Delete HEIC after conversion", variable=self.var_delete).pack(side="left", padx=16)

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

        # Also accept drops on the whole window
        root.drop_target_register(DND_FILES)
        root.dnd_bind("<<Drop>>", self.on_drop)

        self.root.after(100, self._poll)

    # ---- UI helpers -------------------------------------------------------
    def _hl(self, on):
        self.drop.configure(bg="#dceefc" if on else "#f4f6f8")

    def _log(self, msg, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---- Inputs -----------------------------------------------------------
    def on_drop(self, event):
        self._hl(False)
        self.start(parse_drop_data(self.root, event.data))
        return event.action

    def on_browse(self, _event=None):
        if self.busy:
            return
        files = filedialog.askopenfilenames(
            title="Select HEIC files",
            filetypes=[("HEIC images", "*.heic *.heif"), ("All files", "*.*")],
        )
        if files:
            self.start(list(files))

    # ---- Work -------------------------------------------------------------
    def start(self, paths):
        if self.busy:
            messagebox.showinfo(APP_TITLE, "A conversion is already running.")
            return
        files = collect_heic_files(paths, self.var_recursive.get())
        if not files:
            self._log("No .heic/.heif files found in what was dropped.", "err")
            return
        self.busy = True
        self.progress.configure(maximum=len(files), value=0)
        self.status.configure(text=f"Converting {len(files)} file(s)...")
        self._log(f"--- {len(files)} file(s) queued ---")
        t = threading.Thread(target=self._worker, args=(files, self.var_delete.get()), daemon=True)
        t.start()

    def _worker(self, files, delete):
        ok = fail = 0
        for i, f in enumerate(files, 1):
            try:
                png = convert_one(f, delete)
                ok += 1
                self.q.put(("log", f"OK   {f}  ->  {os.path.basename(png)}", "ok"))
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
        root.after(300, lambda: app.start(sys.argv[1:]))
    root.mainloop()


if __name__ == "__main__":
    main()
