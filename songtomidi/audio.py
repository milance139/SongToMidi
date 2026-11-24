"""Audio (MP3, WAV, FLAC, M4A, OGG, ...) -> multi-track MIDI for the KORG Pa900.

Pipeline
  1. Decode with ffmpeg (bundled through imageio-ffmpeg) to 44.1 kHz stereo.
  2. Split into instrument stems with Demucs (htdemucs: drums/bass/other/vocals,
     or htdemucs_6s which also gives guitar and piano).
  3. Find the beat on the drum (or full) signal with librosa; every note is placed
     relative to the real beats, so a recording that drifts in tempo still lands on the grid.
  4. Pitched stems -> notes with Spotify's Basic Pitch; drums -> kick/snare/hi-hat from
     band-limited onset detection.
  5. Per-part clean-up (mono bass, mono melody, polyphony limit, quantise) and write
     a GM-layout SMF with drums on channel 10.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .gm import AUDIO_PARTS, DRUM_CHANNEL
from .midiout import (Note, Part, amps_to_velocity, drop_short, fix_octave_glitches, limit_polyphony,
                      merge_same_pitch, monophonic, quantize, write_pa900_midi)

SR = 44100

Log = Callable[[str], None]


@dataclass
class AudioOptions:
    detailed: bool = False          # htdemucs_6s (adds piano + guitar stems)
    quantize: float = 0.25          # grid in beats: 0 = off, 0.5 = 1/8, 0.25 = 1/16
    bpm_hint: float = 0.0           # 0 = detect
    beats_per_bar: int = 4
    steady_tempo: bool = True       # one tempo for the whole song (else follow the recording bar by bar)
    max_harmony_voices: int = 6
    device: str = "auto"            # auto | cuda | cpu
    parts: dict = field(default_factory=lambda: {k: dict(enabled=True, program=v["program"])
                                                 for k, v in AUDIO_PARTS.items()})


# ----------------------------------------------------------------------------- decoding

def load_audio(path: str) -> np.ndarray:
    """Return float32 array (2, n) at 44.1 kHz."""
    import imageio_ffmpeg
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [exe, "-v", "error", "-nostdin", "-i", path, "-vn", "-f", "f32le", "-acodec", "pcm_f32le",
           "-ac", "2", "-ar", str(SR), "-"]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    res = subprocess.run(cmd, capture_output=True, creationflags=flags)
    if res.returncode != 0 or not res.stdout:
        raise RuntimeError("Could not read audio: " + res.stderr.decode(errors="replace").strip())
    y = np.frombuffer(res.stdout, dtype=np.float32).reshape(-1, 2).T.copy()
    return y


# ----------------------------------------------------------------------------- separation

_models: dict = {}


def pick_device(pref: str) -> str:
    import torch
    if pref == "cpu":
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def separate(y: np.ndarray, detailed: bool, device: str, log: Log) -> dict[str, np.ndarray]:
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    name = "htdemucs_6s" if detailed else "htdemucs"
    if name not in _models:
        log(f"Loading separation model {name} (downloaded once on first use)...")
        m = get_model(name)
        m.eval()
        _models[name] = m
    model = _models[name]

    wav = torch.from_numpy(y)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    x = ((wav - mean) / std)[None]

    def run(dev: str):
        model.to(dev)
        with torch.no_grad():
            return apply_model(model, x, device=dev, shifts=1, split=True, overlap=0.25, progress=False)[0]

    try:
        out = run(device)
    except RuntimeError as e:
        if device == "cuda" and "out of memory" in str(e).lower():
            log("Graphics card ran out of memory, continuing on the CPU (slower)...")
            torch.cuda.empty_cache()
            out = run("cpu")
        else:
            raise
    out = (out * std + mean).cpu().numpy()
    if device == "cuda":
        model.to("cpu")
        torch.cuda.empty_cache()
    return {src: out[i] for i, src in enumerate(model.sources)}


# ----------------------------------------------------------------------------- beat grid

class BeatGrid:
    """Maps seconds <-> beat positions using the detected beats (extended to cover the whole song)."""

    def __init__(self, beat_times: np.ndarray, duration: float):
        b = np.asarray(beat_times, dtype=float)
        if len(b) < 4:
            b = np.arange(0.0, duration + 0.5, 0.5)  # fallback: 120 bpm
        d0 = float(np.median(np.diff(b[:9])))
        d1 = float(np.median(np.diff(b[-9:])))
        head = []
        t = b[0] - d0
        while t > -d0:
            head.append(t)
            t -= d0
        tail = []
        t = b[-1] + d1
        while t < duration + 2 * d1:
            tail.append(t)
            t += d1
        self.times = np.concatenate([np.array(head[::-1]), b, np.array(tail)])
        self.index = np.arange(len(self.times), dtype=float)
        self.offset = 0.0  # added to every beat position (downbeat alignment + lead-in)

    def beat(self, seconds) -> np.ndarray:
        return np.interp(seconds, self.times, self.index) + self.offset

    def seconds_of(self, beat_pos: float) -> float:
        return float(np.interp(beat_pos - self.offset, self.index, self.times))

    @property
    def median_bpm(self) -> float:
        return 60.0 / float(np.median(np.diff(self.times)))


def track_beats(signal: np.ndarray, sr: int, bpm_hint: float) -> np.ndarray:
    import librosa
    y = librosa.resample(signal, orig_sr=sr, target_sr=22050)
    env = librosa.onset.onset_strength(y=y, sr=22050, hop_length=512, aggregate=np.median)
    kw = dict(onset_envelope=env, sr=22050, hop_length=512, units="time", tightness=200)
    if bpm_hint > 0:
        kw["bpm"] = bpm_hint
    _, beats = librosa.beat.beat_track(**kw)
    return np.asarray(beats, dtype=float)


# ----------------------------------------------------------------------------- drums

def transcribe_drums(stereo: np.ndarray, sr: int) -> list[tuple[float, int, float]]:
    """Return [(seconds, gm_note, strength 0..1)] for kick (36), snare (38) and closed hi-hat (42)."""
    import librosa
    y = stereo.mean(0)
    hop = 256
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    fps = sr / hop
    bands = [(36, 35, 120, 0.10), (38, 1000, 4500, 0.12), (42, 7500, 16000, 0.12)]
    hits = []
    for note, lo, hi, delta in bands:
        sel = (freqs >= lo) & (freqs < hi)
        e = np.log1p(1000.0 * S[sel].sum(axis=0))
        flux = np.maximum(0.0, np.diff(e, prepend=e[0]))
        scale = np.percentile(flux, 99.5) + 1e-9
        flux = np.clip(flux / scale, 0, 1.5)
        peaks = librosa.util.peak_pick(flux, pre_max=3, post_max=3, pre_avg=12, post_avg=12,
                                       delta=delta, wait=int(0.07 * fps))
        for p in peaks:
            hits.append((p / fps, note, float(min(1.0, flux[p]))))
    hits.sort()
    return hits


# ----------------------------------------------------------------------------- pitched stems

STEM_SETTINGS = {
    # stem: (onset_threshold, frame_threshold, min_note_ms, min_hz, max_hz)
    "vocals": (0.55, 0.35, 120, 80, 1100),
    "bass":   (0.50, 0.30, 100, 30, 400),
    "piano":  (0.50, 0.30, 80, 27, 4200),
    "guitar": (0.50, 0.30, 80, 70, 2500),
    "other":  (0.55, 0.32, 90, 50, 4200),
}

_bp_model = None


def transcribe_pitched(stereo: np.ndarray, stem: str, tmpdir: str):
    """Return Basic Pitch note events [(start_s, end_s, pitch, amplitude)]."""
    global _bp_model
    import soundfile as sf
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import Model, predict

    if _bp_model is None:
        _bp_model = Model(ICASSP_2022_MODEL_PATH)
    wav_path = os.path.join(tmpdir, f"{stem}.wav")
    sf.write(wav_path, stereo.mean(0), SR, subtype="FLOAT")
    on, fr, min_ms, fmin, fmax = STEM_SETTINGS[stem]
    _, _, events = predict(wav_path, _bp_model, onset_threshold=on, frame_threshold=fr,
                           minimum_note_length=min_ms, minimum_frequency=fmin, maximum_frequency=fmax,
                           multiple_pitch_bends=False, melodia_trick=True)
    return [(float(e[0]), float(e[1]), int(e[2]), float(e[3])) for e in events]


def is_silent(stereo: np.ndarray, mix_rms: float) -> bool:
    return float(np.sqrt(np.mean(stereo ** 2))) < 0.02 * mix_rms


# ----------------------------------------------------------------------------- main entry

def convert_audio(path: str, out_path: str, opts: AudioOptions, log: Log,
                  progress: Callable[[float], None] = lambda f: None) -> str:
    title = os.path.splitext(os.path.basename(path))[0]
    log(f"Reading {os.path.basename(path)}")
    y = load_audio(path)
    duration = y.shape[1] / SR
    mix_rms = float(np.sqrt(np.mean(y ** 2))) + 1e-9
    progress(0.05)

    device = pick_device(opts.device)
    log(f"Separating instruments on {'graphics card' if device == 'cuda' else 'CPU'}...")
    stems = separate(y, opts.detailed, device, log)
    progress(0.45)

    log("Finding the beat...")
    drums = stems.get("drums")
    beat_src = drums.mean(0) if drums is not None and not is_silent(drums, mix_rms) else y.mean(0)
    grid = BeatGrid(track_beats(beat_src, SR, opts.bpm_hint), duration)
    bpm = grid.median_bpm
    log(f"Tempo about {bpm:.1f} BPM")
    progress(0.5)

    # stem name -> part key
    stem_part = {"vocals": "melody", "bass": "bass", "piano": "piano", "guitar": "guitar", "other": "harmony"}

    raw: dict[str, list[Note]] = {}
    drum_hits = []
    with tempfile.TemporaryDirectory(prefix="songtomidi_") as tmp:
        pitched = [s for s in stems if s != "drums"]
        for i, stem in enumerate(pitched):
            key = stem_part.get(stem)
            if key is None or not opts.parts.get(key, {}).get("enabled", True):
                continue
            if is_silent(stems[stem], mix_rms):
                log(f"  {stem}: almost silent, skipped")
                continue
            log(f"Detecting notes: {stem}...")
            events = transcribe_pitched(stems[stem], stem, tmp)
            notes = []
            for s, e, p, a in events:
                bs, be = grid.beat([s, e])
                notes.append(Note(float(bs), float(be), p, 100, a))
            raw[key] = notes
            progress(0.5 + 0.4 * (i + 1) / max(1, len(pitched)))

    if drums is not None and opts.parts.get("drums", {}).get("enabled", True) and not is_silent(drums, mix_rms):
        log("Detecting drums...")
        drum_hits = transcribe_drums(drums, SR)

    # ---- bar alignment: choose the downbeat so kicks land on 1 (and snares on 2 & 4)
    bpb = max(2, opts.beats_per_bar)
    base = grid.beat(np.array([h[0] for h in drum_hits])) if drum_hits else np.array([])
    phase = 0
    if len(base):
        scores = np.zeros(bpb)
        for pos, (_, note, strength) in zip(base, drum_hits):
            r = round(pos)
            if abs(pos - r) > 0.15:
                continue
            for ph in range(bpb):
                k = (r - ph) % bpb
                if note == 36 and k == 0:
                    scores[ph] += 2 * strength
                elif note == 36 and bpb == 4 and k == 2:
                    scores[ph] += 0.5 * strength
                elif note == 38 and bpb == 4 and k in (1, 3):
                    scores[ph] += 1.0 * strength
        phase = int(np.argmax(scores))
    # shift so beat `phase` becomes bar 1 beat 1, and keep at least one empty bar in front
    all_starts = [n.start for ns in raw.values() for n in ns] + list(base)
    first = min(all_starts) if all_starts else 0.0
    shift = (-phase) % bpb
    while first + shift < bpb:
        shift += bpb
    grid.offset = float(shift)
    for ns in raw.values():
        for n in ns:
            n.start += shift
            n.end += shift

    # ---- per-part clean-up
    q = opts.quantize
    parts: list[Part] = []

    def make_part(key: str, notes: list[Note]) -> None:
        cfg = AUDIO_PARTS[key]
        user = opts.parts.get(key, {})
        if not notes or not user.get("enabled", True):
            return
        amps_to_velocity(notes)
        parts.append(Part(name=cfg["label"].split(" (")[0].split(" /")[0], channel=cfg["channel"],
                          program=int(user.get("program", cfg["program"])), notes=notes,
                          volume=cfg["volume"], pan=cfg["pan"], reverb=cfg["reverb"], chorus=cfg["chorus"]))

    if "melody" in raw:
        n = [x for x in raw["melody"] if 40 <= x.pitch <= 88 and x.amp >= 0.25]
        n = merge_same_pitch(n, 0.12)
        n = monophonic(n, "loud", 0.1)
        n = fix_octave_glitches(n)
        n = drop_short(quantize(n, q), 0.1)
        make_part("melody", n)

    if "bass" in raw:
        n = [x for x in raw["bass"] if 24 <= x.pitch <= 64 and x.amp >= 0.2]
        n = monophonic(n, "low", 0.1)
        n = quantize(n, q)
        make_part("bass", n)

    for key in ("piano", "guitar", "harmony"):
        if key in raw:
            n = [x for x in raw[key] if 28 <= x.pitch <= 103 and x.amp >= 0.28]
            n = drop_short(n, 0.12)
            n = limit_polyphony(n, opts.max_harmony_voices)
            n = quantize(n, q)
            make_part(key, n)

    if drum_hits:
        dn = [Note(float(p) + shift, float(p) + shift + 0.1, note, 100, s)
              for p, (_, note, s) in zip(base, drum_hits)]
        dn = quantize(dn, q if q > 0 else 0, drums=True)
        amps_to_velocity(dn, 50, 125)
        make_part("drums", dn)
        for p in parts:
            if p.name == "Drums":
                p.channel = DRUM_CHANNEL

    if not parts:
        raise RuntimeError("No notes were found in this file.")

    # ---- tempo map
    if opts.steady_tempo:
        tempos = [(0.0, round(bpm, 2))]
    else:
        tempos = []
        last_bar = int(grid.beat(duration)) + bpb
        for bar_start in range(0, last_bar, bpb):
            t0 = grid.seconds_of(bar_start)
            t1 = grid.seconds_of(bar_start + bpb)
            if t1 > t0:
                tempos.append((float(bar_start), round(60.0 * bpb / (t1 - t0), 1)))
        # smooth tiny wobbles
        smoothed = []
        for b, t in tempos:
            if not smoothed or abs(t - smoothed[-1][1]) >= 0.5:
                smoothed.append((b, t))
        tempos = smoothed or [(0.0, bpm)]

    write_pa900_midi(out_path, parts, tempos, [(0.0, bpb, 4)], title=title)
    progress(1.0)
    summary = ", ".join(f"{p.name} {len(p.notes)}" for p in parts)
    log(f"Saved {os.path.basename(out_path)}  ({summary} notes)")
    return out_path
