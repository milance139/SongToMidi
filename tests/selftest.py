"""End-to-end check: synthesise a small song, convert it, and inspect the MIDI. Also checks ABC and repeats.

Run:  .venv\\Scripts\\python.exe -m tests.selftest
"""
import os
import sys
import tempfile
from types import SimpleNamespace

import mido
import numpy as np
import soundfile as sf

from songtomidi.audio import SR, AudioOptions, convert_audio
from songtomidi.scores import _measure_order, convert_score

BPM = 112.0


def synth_song(path: str) -> None:
    beat = 60.0 / BPM
    bars = 12
    n = int((bars * 4 * beat + 1.0) * SR)
    y = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(1)

    def add(sig, t):
        i = int(t * SR)
        j = min(n, i + len(sig))
        y[i:j] += sig[: j - i]

    def tone(midi, dur, harmonics=(1.0, 0.5, 0.25), amp=0.2):
        t = np.arange(int(dur * SR)) / SR
        f = 440.0 * 2 ** ((midi - 69) / 12)
        s = sum(a * np.sin(2 * np.pi * f * (k + 1) * t) for k, a in enumerate(harmonics))
        env = np.minimum(1, t / 0.01) * np.exp(-t * 1.5)
        return (amp * s * env).astype(np.float32)

    kick_t = np.arange(int(0.25 * SR)) / SR
    kick = (0.9 * np.sin(2 * np.pi * (50 + 80 * np.exp(-kick_t * 30)) * kick_t) * np.exp(-kick_t * 12)).astype(np.float32)
    snare = (0.5 * rng.standard_normal(int(0.2 * SR)) * np.exp(-np.arange(int(0.2 * SR)) / SR * 25)).astype(np.float32)
    hat_noise = rng.standard_normal(int(0.05 * SR))
    hat = (0.15 * np.diff(hat_noise, prepend=0) * np.exp(-np.arange(len(hat_noise)) / SR * 80)).astype(np.float32)

    chords = [(45, [57, 60, 64]), (41, [57, 60, 65]), (48, [55, 60, 64]), (43, [55, 59, 62])]
    melody = [69, 71, 72, 74, 76, 74, 72, 71]
    t0 = 0.5
    for bar in range(bars):
        root, chord = chords[bar % 4]
        for b in range(4):
            t = t0 + (bar * 4 + b) * beat
            add(kick if b in (0, 2) else snare, t)
            add(hat, t)
            add(hat, t + beat / 2)
            add(tone(root, beat * 0.9, amp=0.35), t)
            add(tone(melody[(bar * 4 + b) % 8], beat * 0.9, harmonics=(1.0, 0.6, 0.4, 0.3), amp=0.12), t)
        for p in chord:
            add(tone(p, 4 * beat * 0.95, amp=0.06), t0 + bar * 4 * beat)
    y /= np.abs(y).max() * 1.1
    sf.write(path, np.stack([y, y], axis=1), SR)


def check(cond, msg):
    print(("  ok    " if cond else "  FAIL  ") + msg)
    return bool(cond)


def main() -> int:
    good = True
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "test song.wav")
        out = os.path.join(tmp, "test.mid")
        synth_song(wav)
        print("Audio conversion (takes a minute)...")
        convert_audio(wav, out, AudioOptions(), lambda m: print("   ", m))
        mf = mido.MidiFile(out)
        tempos = [mido.tempo2bpm(m.tempo) for t in mf.tracks for m in t if m.type == "set_tempo"]
        drum_notes = {m.note for t in mf.tracks for m in t if m.type == "note_on" and m.channel == 9}
        bass = [m.note for t in mf.tracks for m in t if m.type == "note_on" and m.channel == 1]
        good &= check(mf.type == 1, "type 1 MIDI file")
        good &= check(tempos and any(abs(t * k - BPM) < 4 for t in tempos[:1] for k in (0.5, 1, 2)),
                      f"tempo detected {tempos[:1]} (expected about {BPM})")
        good &= check({36, 38} <= drum_notes, f"kick + snare on channel 10 (found {sorted(drum_notes)})")
        good &= check(len(bass) > 20 and set(n % 12 for n in bass) & {9, 5, 0, 7},
                      f"bass line found ({len(bass)} notes)")
        first_note = min(sum(m.time for m in t[: i + 1]) for t in mf.tracks
                         for i, m in enumerate(t) if m.type == "note_on") if bass else 0
        good &= check(first_note > 0, "no notes on tick 0")

        print("ABC conversion...")
        abc = os.path.join(tmp, "tune.abc")
        with open(abc, "w") as f:
            f.write("X:1\nT:Test\nM:4/4\nL:1/4\nQ:1/4=100\nK:G\n|: G A B c | d2 d2 :|\n|: B2 A2 | G4 :|\n")
        out2 = os.path.join(tmp, "tune.mid")
        convert_score(abc, out2, lambda m: print("   ", m))
        notes = [m for t in mido.MidiFile(out2).tracks for m in t if m.type == "note_on" and m.velocity > 0]
        good &= check(len(notes) == 18, f"ABC repeats expanded ({len(notes)} notes, expected 18)")

    print("Guitar Pro repeat order...")
    H = lambda o=False, c=-1, a=0: SimpleNamespace(isRepeatOpen=o, repeatClose=c, repeatAlternative=a)
    hs = [H(), H(o=True), H(), H(c=1, a=1), H(a=2), H()]
    order = _measure_order(hs)
    good &= check(order == [0, 1, 2, 3, 1, 2, 4, 5], f"volta order {order}")

    print("\nALL GOOD" if good else "\nSOME CHECKS FAILED")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
