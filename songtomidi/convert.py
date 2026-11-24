"""Pick the right converter for a file and work out the output name."""
from __future__ import annotations

import os

from .midiout import safe_filename

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".aif", ".webm", ".mp4"}
SCORE_EXTS = {".gp3", ".gp4", ".gp5", ".musicxml", ".xml", ".mxl", ".abc", ".krn", ".mid", ".midi", ".kar"}
ALL_EXTS = AUDIO_EXTS | SCORE_EXTS


def output_path(src: str, out_dir: str) -> str:
    base = safe_filename(os.path.splitext(os.path.basename(src))[0])
    path = os.path.join(out_dir, base + ".mid")
    n = 2
    while os.path.exists(path) or os.path.abspath(path) == os.path.abspath(src):
        path = os.path.join(out_dir, f"{base} ({n}).mid")
        n += 1
    return path


def convert_file(src: str, out_dir: str, audio_opts, log, progress=lambda f: None) -> str:
    ext = os.path.splitext(src)[1].lower()
    os.makedirs(out_dir, exist_ok=True)
    out = output_path(src, out_dir)
    if ext in AUDIO_EXTS:
        from .audio import convert_audio
        return convert_audio(src, out, audio_opts, log, progress)
    if ext in SCORE_EXTS:
        from .scores import convert_score
        return convert_score(src, out, log, progress)
    raise RuntimeError(f"Unsupported file type: {ext}")
