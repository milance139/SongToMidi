"""Small Tkinter window: pick files, pick options, convert in the background."""
from __future__ import annotations

import json
import os
import queue
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .gm import AUDIO_PARTS, parse_program_label, program_label

SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SongToMidi", "settings.json")
PROGRAM_LABELS = [program_label(i) for i in range(128)]
QUANT = {"Off": 0.0, "1/8": 0.5, "1/16": 0.25}


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SongToMidi - MIDI for KORG Pa900")
        self.geometry("860x720")
        self.minsize(760, 600)
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        s = load_settings()

        pad = dict(padx=8, pady=4)

        # ---- files
        ff = ttk.LabelFrame(self, text="Songs to convert")
        ff.pack(fill="both", expand=True, **pad)
        inner = ttk.Frame(ff)
        inner.pack(fill="both", expand=True, padx=6, pady=6)
        self.files = tk.Listbox(inner, selectmode="extended", height=8)
        sb = ttk.Scrollbar(inner, command=self.files.yview)
        self.files.config(yscrollcommand=sb.set)
        self.files.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        btns = ttk.Frame(inner)
        btns.pack(side="left", fill="y", padx=(6, 0))
        for text, cmd in (("Add files...", self.add_files), ("Add folder...", self.add_folder),
                          ("Remove", self.remove_sel), ("Clear", lambda: self.files.delete(0, "end"))):
            ttk.Button(btns, text=text, command=cmd, width=14).pack(pady=2)
        ttk.Label(ff, text="Audio: MP3 WAV FLAC M4A OGG ...   Exact: Guitar Pro 3-5, MusicXML/MXL, ABC, MIDI/KAR",
                  foreground="#666").pack(anchor="w", padx=8, pady=(0, 6))

        # ---- output
        of = ttk.LabelFrame(self, text="Save MIDI files to")
        of.pack(fill="x", **pad)
        self.out_mode = tk.StringVar(value=s.get("out_mode", "beside"))
        self.out_dir = tk.StringVar(value=s.get("out_dir", ""))
        ttk.Radiobutton(of, text='A "Pa900 MIDI" folder next to each song', value="beside",
                        variable=self.out_mode).grid(row=0, column=0, columnspan=3, sticky="w", padx=6, pady=2)
        ttk.Radiobutton(of, text="This folder:", value="folder", variable=self.out_mode).grid(
            row=1, column=0, sticky="w", padx=6, pady=2)
        ttk.Entry(of, textvariable=self.out_dir).grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Button(of, text="Browse...", command=self.pick_out).grid(row=1, column=2, padx=6, pady=2)
        of.columnconfigure(1, weight=1)

        # ---- audio options + instruments side by side
        row = ttk.Frame(self)
        row.pack(fill="x", **pad)
        ao = ttk.LabelFrame(row, text="Audio options")
        ao.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.sep = tk.StringVar(value=s.get("sep", "Standard (drums, bass, melody, harmony)"))
        self.quant = tk.StringVar(value=s.get("quant", "1/16"))
        self.bpm = tk.StringVar(value="")
        self.beats = tk.StringVar(value=s.get("beats", "4"))
        self.steady = tk.BooleanVar(value=s.get("steady", True))
        self.gpu = tk.BooleanVar(value=s.get("gpu", True))
        r = 0
        ttk.Label(ao, text="Separation").grid(row=r, column=0, sticky="w", padx=6, pady=3)
        ttk.Combobox(ao, textvariable=self.sep, state="readonly", width=36,
                     values=["Standard (drums, bass, melody, harmony)",
                             "Detailed (+ separate piano and guitar)"]).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(ao, text="Snap notes to").grid(row=r, column=0, sticky="w", padx=6, pady=3)
        ttk.Combobox(ao, textvariable=self.quant, state="readonly", width=8,
                     values=list(QUANT)).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(ao, text="Tempo (BPM)").grid(row=r, column=0, sticky="w", padx=6, pady=3)
        f = ttk.Frame(ao)
        f.grid(row=r, column=1, sticky="w")
        ttk.Entry(f, textvariable=self.bpm, width=8).pack(side="left")
        ttk.Label(f, text="  empty = detect automatically", foreground="#666").pack(side="left")
        r += 1
        ttk.Label(ao, text="Beats per bar").grid(row=r, column=0, sticky="w", padx=6, pady=3)
        ttk.Combobox(ao, textvariable=self.beats, state="readonly", width=8,
                     values=["4", "3", "2", "6"]).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Checkbutton(ao, text="Steady tempo (one BPM for the whole song)", variable=self.steady).grid(
            row=r, column=0, columnspan=2, sticky="w", padx=6, pady=2)
        r += 1
        ttk.Checkbutton(ao, text="Use graphics card if available (much faster)", variable=self.gpu).grid(
            row=r, column=0, columnspan=2, sticky="w", padx=6, pady=2)

        ins = ttk.LabelFrame(row, text="Tracks and sounds (General MIDI)")
        ins.pack(side="left", fill="both", expand=True, padx=(4, 0))
        self.part_on: dict[str, tk.BooleanVar] = {}
        self.part_prog: dict[str, tk.StringVar] = {}
        saved_parts = s.get("parts", {})
        for i, (key, cfg) in enumerate(AUDIO_PARTS.items()):
            sp = saved_parts.get(key, {})
            on = tk.BooleanVar(value=sp.get("enabled", True))
            self.part_on[key] = on
            label = cfg["label"] + f"  (ch {cfg['channel'] + 1})"
            if key in ("piano", "guitar"):
                label += " *"
            ttk.Checkbutton(ins, text=label, variable=on).grid(row=i, column=0, sticky="w", padx=6, pady=2)
            if key == "drums":
                ttk.Label(ins, text="Standard kit").grid(row=i, column=1, sticky="w")
                continue
            pv = tk.StringVar(value=program_label(int(sp.get("program", cfg["program"]))))
            self.part_prog[key] = pv
            ttk.Combobox(ins, textvariable=pv, values=PROGRAM_LABELS, state="readonly", width=26).grid(
                row=i, column=1, sticky="w", padx=(0, 6))
        ttk.Label(ins, text="* only with Detailed separation", foreground="#666").grid(
            row=len(AUDIO_PARTS), column=0, columnspan=2, sticky="w", padx=6, pady=(2, 4))

        # ---- run
        rf = ttk.Frame(self)
        rf.pack(fill="x", **pad)
        self.go = ttk.Button(rf, text="Convert", command=self.start)
        self.go.pack(side="left")
        self.bar = ttk.Progressbar(rf, maximum=1.0)
        self.bar.pack(side="left", fill="x", expand=True, padx=8)
        self.status = ttk.Label(rf, text="")
        self.status.pack(side="left")

        lf = ttk.Frame(self)
        lf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(lf, height=10, state="disabled", wrap="word")
        lsb = ttk.Scrollbar(lf, command=self.log.yview)
        self.log.config(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="left", fill="y")

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.poll)
        self.write("Add songs, check the options, then press Convert.\n"
                   "Tip: Guitar Pro, MusicXML and MIDI files convert exactly. Audio (MP3) is an AI guess: "
                   "good for drums and bass, rougher for chords.")

    # ---------------------------------------------------------------- files
    def add_files(self):
        from .convert import ALL_EXTS
        pats = " ".join("*" + e for e in sorted(ALL_EXTS))
        for p in filedialog.askopenfilenames(title="Choose songs", filetypes=[("Songs", pats), ("All files", "*.*")]):
            self._add(p)

    def add_folder(self):
        from .convert import ALL_EXTS
        d = filedialog.askdirectory(title="Choose a folder with songs")
        if not d:
            return
        for name in sorted(os.listdir(d)):
            if os.path.splitext(name)[1].lower() in ALL_EXTS:
                self._add(os.path.join(d, name))

    def _add(self, p):
        p = os.path.normpath(p)
        if p not in self.files.get(0, "end"):
            self.files.insert("end", p)

    def remove_sel(self):
        for i in reversed(self.files.curselection()):
            self.files.delete(i)

    def pick_out(self):
        d = filedialog.askdirectory(title="Save MIDI files to")
        if d:
            self.out_dir.set(d)
            self.out_mode.set("folder")

    # ---------------------------------------------------------------- options
    def collect(self):
        from .audio import AudioOptions
        bpm_text = self.bpm.get().strip().replace(",", ".")
        try:
            bpm = float(bpm_text) if bpm_text else 0.0
        except ValueError:
            raise ValueError("Tempo must be a number, or left empty.")
        if bpm and not 30 <= bpm <= 300:
            raise ValueError("Tempo must be between 30 and 300 BPM.")
        parts = {}
        for key in AUDIO_PARTS:
            parts[key] = dict(enabled=self.part_on[key].get(),
                              program=parse_program_label(self.part_prog[key].get()) if key in self.part_prog else 0)
        return AudioOptions(detailed=self.sep.get().startswith("Detailed"), quantize=QUANT[self.quant.get()],
                            bpm_hint=bpm, beats_per_bar=int(self.beats.get()), steady_tempo=self.steady.get(),
                            device="auto" if self.gpu.get() else "cpu", parts=parts)

    def remember(self, opts):
        save_settings(dict(out_mode=self.out_mode.get(), out_dir=self.out_dir.get(), sep=self.sep.get(),
                           quant=self.quant.get(), beats=self.beats.get(), steady=self.steady.get(),
                           gpu=self.gpu.get(), parts=opts.parts))

    # ---------------------------------------------------------------- run
    def start(self):
        if self.worker and self.worker.is_alive():
            return
        files = list(self.files.get(0, "end"))
        if not files:
            messagebox.showinfo("SongToMidi", "Add at least one song first.")
            return
        if self.out_mode.get() == "folder" and not self.out_dir.get().strip():
            messagebox.showinfo("SongToMidi", "Choose the output folder first.")
            return
        try:
            opts = self.collect()
        except ValueError as e:
            messagebox.showerror("SongToMidi", str(e))
            return
        self.remember(opts)
        out_fixed = self.out_dir.get().strip() if self.out_mode.get() == "folder" else None
        self.go.config(state="disabled")
        self.bar["value"] = 0
        self.worker = threading.Thread(target=self.work, args=(files, out_fixed, opts), daemon=True)
        self.worker.start()

    def work(self, files, out_fixed, opts):
        from .convert import convert_file
        ok, failed, outputs = 0, [], []
        n = len(files)
        for i, f in enumerate(files):
            self.q.put(("status", f"{i + 1} / {n}"))
            out_dir = out_fixed or os.path.join(os.path.dirname(f), "Pa900 MIDI")
            try:
                out = convert_file(f, out_dir, opts, lambda m: self.q.put(("log", m)),
                                   lambda p, i=i: self.q.put(("progress", (i + p) / n)))
                outputs.append(out)
                ok += 1
            except Exception as e:
                failed.append(os.path.basename(f))
                self.q.put(("log", f"FAILED {os.path.basename(f)}: {e}"))
                self.q.put(("log", traceback.format_exc(limit=3)))
        self.q.put(("done", (ok, failed, outputs)))

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.write(val)
                elif kind == "progress":
                    self.bar["value"] = val
                elif kind == "status":
                    self.status.config(text=val)
                elif kind == "done":
                    ok, failed, outputs = val
                    self.go.config(state="normal")
                    self.bar["value"] = 1.0
                    self.status.config(text="Done")
                    msg = f"Converted {ok} file(s)."
                    if failed:
                        msg += "\nFailed: " + ", ".join(failed)
                    self.write(msg)
                    if outputs and messagebox.askyesno("SongToMidi", msg + "\n\nOpen the output folder?"):
                        os.startfile(os.path.dirname(outputs[-1]))
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def write(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("SongToMidi", "A conversion is still running. Quit anyway?"):
                return
        self.destroy()


def run():
    App().mainloop()
