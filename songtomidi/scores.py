"""Written-music formats -> Pa900 MIDI. These are exact (no guessing), so results are far better than from audio.

  * Guitar Pro 3/4/5 (.gp3 .gp4 .gp5)   via PyGuitarPro, repeats expanded
  * MusicXML (.musicxml .xml .mxl), ABC (.abc), Humdrum (.krn) via music21
  * MIDI / karaoke (.mid .midi .kar)    re-written in Pa900 layout (GM set-up, drums on ch 10)
"""
from __future__ import annotations

import os
import tempfile
from typing import Callable

from .gm import DRUM_CHANNEL
from .midiout import Note, Part, assign_channels, read_midi_parts, write_pa900_midi

GP_EXTS = {".gp3", ".gp4", ".gp5"}
M21_EXTS = {".musicxml", ".xml", ".mxl", ".abc", ".krn"}
MIDI_EXTS = {".mid", ".midi", ".kar"}
SCORE_EXTS = GP_EXTS | M21_EXTS | MIDI_EXTS

Log = Callable[[str], None]


def convert_score(path: str, out_path: str, log: Log, progress: Callable[[float], None] = lambda f: None) -> str:
    ext = os.path.splitext(path)[1].lower()
    title = os.path.splitext(os.path.basename(path))[0]
    log(f"Reading {os.path.basename(path)}")
    if ext in GP_EXTS:
        parts, tempos, sigs = _guitar_pro(path)
    elif ext in M21_EXTS:
        parts, tempos, sigs = _music21(path, log)
    elif ext in MIDI_EXTS:
        parts, tempos, sigs = read_midi_parts(path)
    else:
        raise RuntimeError(f"Unsupported file type {ext}")
    progress(0.8)
    parts = [p for p in parts if p.notes]
    if not parts:
        raise RuntimeError("No notes were found in this file.")
    write_pa900_midi(out_path, parts, tempos, sigs, title=title)
    progress(1.0)
    log(f"Saved {os.path.basename(out_path)}  ({len(parts)} tracks)")
    return out_path


# ----------------------------------------------------------------------------- music21

def _music21(path: str, log: Log):
    import music21
    score = music21.converter.parse(path)
    try:
        score = score.expandRepeats()
    except Exception:
        log("  (repeats could not be expanded, writing as notated)")
    with tempfile.TemporaryDirectory(prefix="songtomidi_") as tmp:
        tmp_mid = os.path.join(tmp, "score.mid")
        mf = music21.midi.translate.streamToMidiFile(score)
        mf.open(tmp_mid, "wb")
        mf.write()
        mf.close()
        return read_midi_parts(tmp_mid)


# ----------------------------------------------------------------------------- Guitar Pro

GP_QUARTER = 960


def _measure_order(headers) -> list[int]:
    """Play order of measures with repeats and alternative endings expanded."""
    order: list[int] = []
    n = len(headers)
    i = 0
    repeat_start = 0
    pass_no = 1
    jumps: dict[int, int] = {}   # index of a repeat-close measure -> jumps already made
    looping = False              # True right after jumping back
    while i < n and len(order) < 20000:
        h = headers[i]
        jumped_here, looping = looping, False
        if h.isRepeatOpen and not jumped_here:
            repeat_start = i
            pass_no = 1
        alt = getattr(h, "repeatAlternative", 0) or 0
        if alt and not (alt >> (pass_no - 1)) & 1:
            i += 1
            continue
        order.append(i)
        close = getattr(h, "repeatClose", -1)
        if close is not None and close >= 0:
            want = min(8, max(1, close))
            done = jumps.get(i, 0)
            if done < want:
                jumps[i] = done + 1
                pass_no += 1
                looping = True
                i = repeat_start
                continue
            jumps[i] = 0
            repeat_start = i + 1
            pass_no = 1
        i += 1
    return order


def _guitar_pro(path: str):
    import guitarpro

    song = guitarpro.parse(path)
    headers = song.measureHeaders
    order = _measure_order(headers)

    # Output start (in GP ticks) of every played measure
    out_start: list[int] = []
    t = 0
    for idx in order:
        out_start.append(t)
        t += headers[idx].length

    sigs: list[tuple[float, int, int]] = []
    last_sig = None
    for k, idx in enumerate(order):
        ts = headers[idx].timeSignature
        sig = (ts.numerator, ts.denominator.value)
        if sig != last_sig:
            sigs.append((out_start[k] / GP_QUARTER, sig[0], sig[1]))
            last_sig = sig

    tempos: list[tuple[float, float]] = [(0.0, float(song.tempo))]
    parts: list[Part] = []

    for track in song.tracks:
        drum = bool(getattr(track, "isPercussionTrack", False))
        tuning = {s.number: s.value for s in track.strings}
        capo = int(getattr(track, "offset", 0) or 0)
        ch = track.channel
        part = Part(name=(track.name or "Track").strip()[:30], channel=DRUM_CHANNEL if drum else 0,
                    program=0 if drum else int(ch.instrument) % 128,
                    volume=int(getattr(ch, "volume", 100)), pan=int(getattr(ch, "balance", 64)),
                    reverb=int(getattr(ch, "reverb", 40)), chorus=int(getattr(ch, "chorus", 0)))
        last_on_string: dict[int, Note] = {}
        for k, idx in enumerate(order):
            if idx >= len(track.measures):
                continue
            measure = track.measures[idx]
            m_start = headers[idx].start
            for voice in measure.voices:
                for beat in voice.beats:
                    # tempo changes from the mix table (take them from the first track only)
                    mtc = getattr(beat.effect, "mixTableChange", None)
                    if track is song.tracks[0] and mtc is not None and getattr(mtc, "tempo", None) is not None:
                        tv = getattr(mtc.tempo, "value", None)
                        if tv:
                            tempos.append(((out_start[k] + beat.start - m_start) / GP_QUARTER, float(tv)))
                    if not beat.notes:
                        continue
                    st = out_start[k] + (beat.start - m_start)
                    dur = beat.duration.time
                    s_beats, e_beats = st / GP_QUARTER, (st + dur) / GP_QUARTER
                    for note in beat.notes:
                        ntype = getattr(note.type, "name", str(note.type))
                        if ntype == "rest":
                            continue
                        if ntype == "tie":
                            prev = last_on_string.get(note.string)
                            if prev is not None:
                                prev.end = max(prev.end, e_beats)
                            continue
                        if drum:
                            pitch = note.value
                        else:
                            pitch = tuning.get(note.string, 40) + note.value + capo
                        if ntype == "dead":
                            n = Note(s_beats, s_beats + min(0.125, e_beats - s_beats), pitch, 50)
                        else:
                            n = Note(s_beats, e_beats, pitch, int(note.velocity))
                        part.notes.append(n)
                        last_on_string[note.string] = n
        if part.notes:
            parts.append(part)

    return assign_channels(parts), tempos, sigs or [(0.0, 4, 4)]
