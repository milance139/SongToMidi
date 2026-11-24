"""Shared note model, clean-up helpers and a Standard MIDI File writer tuned for the KORG Pa900.

All times here are in beats (quarter notes) so the same code serves audio and score imports.

What the Pa900 likes:
  * SMF type 1, one part per track, GM layout, drums on channel 10.
  * A GM System On message plus Bank Select / Program / Volume / Pan / Reverb / Chorus at
    the very start, before any note, so the arranger sets up its sounds correctly.
  * No notes on tick 0 (set-up first), no overlapping notes of the same pitch on one channel.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import mido

from .gm import DRUM_CHANNEL

PPQ = 480


@dataclass
class Note:
    start: float      # beats
    end: float        # beats
    pitch: int
    velocity: int = 100
    amp: float = 1.0  # raw strength from detection (audio only)


@dataclass
class Part:
    name: str
    channel: int                  # 0-based
    program: int = 0              # 0-based GM program
    notes: list[Note] = field(default_factory=list)
    volume: int = 100
    pan: int = 64
    reverb: int = 40
    chorus: int = 0
    bank_msb: int = 0
    bank_lsb: int = 0

    @property
    def is_drum(self) -> bool:
        return self.channel == DRUM_CHANNEL


# ----------------------------------------------------------------------------- clean-up helpers

def drop_short(notes: list[Note], min_len: float) -> list[Note]:
    return [n for n in notes if n.end - n.start >= min_len]


def merge_same_pitch(notes: list[Note], max_gap: float) -> list[Note]:
    """Join a note with the next one of the same pitch when the gap between them is tiny."""
    out: list[Note] = []
    last_by_pitch: dict[int, Note] = {}
    for n in sorted(notes, key=lambda n: n.start):
        prev = last_by_pitch.get(n.pitch)
        if prev is not None and 0 <= n.start - prev.end <= max_gap:
            prev.end = max(prev.end, n.end)
            prev.velocity = max(prev.velocity, n.velocity)
            prev.amp = max(prev.amp, n.amp)
            continue
        out.append(n)
        last_by_pitch[n.pitch] = n
    return out


def monophonic(notes: list[Note], prefer: str, min_len: float) -> list[Note]:
    """Keep one note at a time. prefer: 'low' (bass), 'loud' (melody)."""
    def better(a: Note, b: Note) -> bool:  # is a better than b?
        if prefer == "low":
            return a.pitch < b.pitch
        return a.amp * (a.end - a.start) ** 0.5 > b.amp * (b.end - b.start) ** 0.5

    out: list[Note] = []
    for n in sorted(notes, key=lambda n: (n.start, n.pitch)):
        if out and n.start < out[-1].end:
            last = out[-1]
            if better(n, last):
                last.end = n.start
                if last.end - last.start < min_len:
                    out.pop()
                out.append(n)
            elif n.end > last.end and n.end - last.end >= min_len:
                n.start = last.end
                out.append(n)
        else:
            out.append(n)
    return out


def fix_octave_glitches(notes: list[Note], max_len: float = 0.5) -> list[Note]:
    """In a melody, a short note an octave away from two equal neighbours is almost always a detection error."""
    for i in range(1, len(notes) - 1):
        a, b, c = notes[i - 1], notes[i], notes[i + 1]
        if b.end - b.start <= max_len and a.pitch == c.pitch and abs(b.pitch - a.pitch) in (12, 24):
            b.pitch = a.pitch
    return merge_same_pitch(notes, 0.05)


def limit_polyphony(notes: list[Note], max_voices: int) -> list[Note]:
    """Drop the weakest notes wherever more than max_voices sound at once."""
    notes = sorted(notes, key=lambda n: n.start)
    kept: list[Note] = []
    for n in notes:
        active = [k for k in kept if k.end > n.start + 1e-6]
        if len(active) < max_voices:
            kept.append(n)
            continue
        weakest = min(active, key=lambda k: k.amp)
        if n.amp > weakest.amp:
            weakest.end = n.start
            if weakest.end - weakest.start < 0.05:
                kept.remove(weakest)
            kept.append(n)
    return kept


def quantize(notes: list[Note], grid: float, drums: bool = False) -> list[Note]:
    if grid <= 0:
        return notes
    seen: dict[tuple[int, float], Note] = {}
    for n in notes:
        s = round(n.start / grid) * grid
        if drums:
            e = s + min(grid, 0.25)
        else:
            e = max(s + grid, round(n.end / grid) * grid)
        key = (n.pitch, s)
        if key in seen:  # two hits collapsed onto one grid point: keep the strongest
            k = seen[key]
            k.velocity = max(k.velocity, n.velocity)
            k.amp = max(k.amp, n.amp)
            k.end = max(k.end, e)
            continue
        seen[key] = Note(s, e, n.pitch, n.velocity, n.amp)
    return sorted(seen.values(), key=lambda n: (n.start, n.pitch))


def amps_to_velocity(notes: list[Note], lo: int = 45, hi: int = 120) -> None:
    if not notes:
        return
    amps = sorted(n.amp for n in notes)
    p10 = amps[int(0.10 * (len(amps) - 1))]
    p95 = amps[int(0.95 * (len(amps) - 1))]
    span = max(p95 - p10, 1e-6)
    for n in notes:
        x = min(1.0, max(0.0, (n.amp - p10) / span))
        n.velocity = int(round(lo + (hi - lo) * x))


# ----------------------------------------------------------------------------- writer

def safe_filename(name: str) -> str:
    """ASCII-only file name (the Pa900 display and USB handling are happiest with plain ASCII)."""
    name = name.replace("đ", "dj").replace("Đ", "Dj")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^A-Za-z0-9 _\-.()]+", "", name).strip(" .")
    return (name or "song")[:60]


def _clip7(v: int) -> int:
    return max(0, min(127, int(v)))


def write_pa900_midi(path: str, parts: list[Part], tempos: list[tuple[float, float]],
                     time_sigs: list[tuple[float, int, int]] | None = None, title: str = "") -> None:
    """tempos: [(beat, bpm)], time_sigs: [(beat, numerator, denominator)]."""
    time_sigs = sorted(time_sigs or [(0.0, 4, 4)])
    if time_sigs[0][0] > 0:
        time_sigs.insert(0, (0.0, time_sigs[0][1], time_sigs[0][2]))
    tempos = sorted(tempos or [(0.0, 120.0)])
    if tempos[0][0] > 0:
        tempos.insert(0, (0.0, tempos[0][1]))

    parts = [p for p in parts if p.notes]

    # Never start notes on tick 0: if anything starts in the first half beat, add one empty bar.
    first = min((n.start for p in parts for n in p.notes), default=1.0)
    if first < 0.5:
        bar = time_sigs[0][1] * 4.0 / time_sigs[0][2]
        shift = bar
        while first + shift < 0.5:
            shift += bar
        for p in parts:
            for n in p.notes:
                n.start += shift
                n.end += shift
        tempos = [tempos[0]] + [(b + shift, t) for b, t in tempos[1:]]
        time_sigs = [time_sigs[0]] + [(b + shift, nu, de) for b, nu, de in time_sigs[1:]]

    def tick(beat: float) -> int:
        return max(0, int(round(beat * PPQ)))

    mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)

    # Conductor track
    events: list[tuple[int, int, mido.Message | mido.MetaMessage]] = []
    if title:
        events.append((0, 0, mido.MetaMessage("track_name", name=safe_filename(title))))
    events.append((0, 1, mido.Message("sysex", data=[0x7E, 0x7F, 0x09, 0x01])))  # GM System On
    for b, nu, de in time_sigs:
        events.append((tick(b), 2, mido.MetaMessage("time_signature", numerator=nu, denominator=de,
                                                    clocks_per_click=24, notated_32nd_notes_per_beat=8)))
    last_bpm = None
    for b, bpm in tempos:
        bpm = max(20.0, min(300.0, bpm))
        if last_bpm is not None and abs(bpm - last_bpm) < 0.05:
            continue
        events.append((tick(b), 3, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))))
        last_bpm = bpm
    mid.tracks.append(_to_track(events))

    for p in parts:
        ch = p.channel
        ev: list[tuple[int, int, mido.Message | mido.MetaMessage]] = [
            (0, 0, mido.MetaMessage("track_name", name=safe_filename(p.name)[:30])),
            (0, 1, mido.Message("control_change", channel=ch, control=0, value=_clip7(p.bank_msb))),
            (0, 1, mido.Message("control_change", channel=ch, control=32, value=_clip7(p.bank_lsb))),
            (0, 2, mido.Message("program_change", channel=ch, program=_clip7(p.program))),
            (0, 3, mido.Message("control_change", channel=ch, control=7, value=_clip7(p.volume))),
            (0, 3, mido.Message("control_change", channel=ch, control=10, value=_clip7(p.pan))),
            (0, 3, mido.Message("control_change", channel=ch, control=91, value=_clip7(p.reverb))),
            (0, 3, mido.Message("control_change", channel=ch, control=93, value=_clip7(p.chorus))),
        ]
        # Resolve same-pitch overlaps so no note is cut off or left hanging.
        by_pitch: dict[int, list[Note]] = {}
        for n in p.notes:
            by_pitch.setdefault(n.pitch, []).append(n)
        for pitch, ns in by_pitch.items():
            if not 0 <= pitch <= 127:
                continue
            ns.sort(key=lambda n: n.start)
            for i, n in enumerate(ns):
                on, off = tick(n.start), tick(n.end)
                if i + 1 < len(ns):
                    off = min(off, tick(ns[i + 1].start))
                if off <= on:
                    off = on + max(1, PPQ // 32)
                    if i + 1 < len(ns) and off > tick(ns[i + 1].start):
                        continue
                ev.append((on, 5, mido.Message("note_on", channel=ch, note=pitch, velocity=max(1, _clip7(n.velocity)))))
                ev.append((off, 4, mido.Message("note_off", channel=ch, note=pitch, velocity=0)))
        mid.tracks.append(_to_track(ev))

    mid.save(path)


def _to_track(events) -> mido.MidiTrack:
    """events: (abs_tick, order, msg). Lower order goes first on the same tick (note-offs before note-ons)."""
    tr = mido.MidiTrack()
    now = 0
    for t, _, msg in sorted(events, key=lambda e: (e[0], e[1])):
        tr.append(msg.copy(time=t - now))
        now = t
    tr.append(mido.MetaMessage("end_of_track", time=0))
    return tr


# ----------------------------------------------------------------------------- reading MIDI back into parts

def read_midi_parts(path: str):
    """Read any MIDI file into (parts, tempos, time_sigs) in beats. Used by score imports and the MIDI 'fix' mode."""
    mf = mido.MidiFile(path)
    tpb = mf.ticks_per_beat or 480
    tempos: list[tuple[float, float]] = []
    sigs: list[tuple[float, int, int]] = []
    parts: dict[tuple[int, int], Part] = {}
    programs: dict[int, int] = {}
    banks: dict[int, tuple[int, int]] = {}

    for ti, track in enumerate(mf.tracks):
        now = 0
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}
        name = ""
        for msg in track:
            now += msg.time
            b = now / tpb
            if msg.type == "track_name":
                name = msg.name
            elif msg.type == "set_tempo":
                tempos.append((b, mido.tempo2bpm(msg.tempo)))
            elif msg.type == "time_signature":
                sigs.append((b, msg.numerator, msg.denominator))
            elif msg.type == "program_change":
                programs.setdefault(msg.channel, msg.program)
                key = (ti, msg.channel)
                if key in parts and not parts[key].notes:
                    parts[key].program = msg.program
            elif msg.type == "control_change" and msg.control in (0, 32):
                msb, lsb = banks.get(msg.channel, (0, 0))
                banks[msg.channel] = (msg.value, lsb) if msg.control == 0 else (msb, msg.value)
            elif msg.type == "note_on" and msg.velocity > 0:
                open_notes.setdefault((msg.channel, msg.note), []).append((now, msg.velocity))
            elif msg.type in ("note_off", "note_on"):
                stack = open_notes.get((msg.channel, msg.note))
                if stack:
                    start, vel = stack.pop(0)
                    key = (ti, msg.channel)
                    if key not in parts:
                        parts[key] = Part(name=name or f"Track {ti}", channel=msg.channel,
                                          program=programs.get(msg.channel, 0))
                    parts[key].notes.append(Note(start / tpb, now / tpb, msg.note, vel))
    for p in parts.values():
        p.program = programs.get(p.channel, p.program)
        p.bank_msb, p.bank_lsb = banks.get(p.channel, (0, 0))
    return assign_channels(list(parts.values())), tempos or [(0.0, 120.0)], sigs or [(0.0, 4, 4)]


def assign_channels(parts: list[Part]) -> list[Part]:
    """Drums to ch 10, everything else onto the 15 other channels (sharing only if there are more than 15 parts)."""
    free = [c for c in range(16) if c != DRUM_CHANNEL]
    melodic = [p for p in parts if not p.is_drum]
    for i, p in enumerate(melodic):
        p.channel = free[i % len(free)]
    return parts
