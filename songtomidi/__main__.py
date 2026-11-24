"""python -m songtomidi              -> window
python -m songtomidi FILES... [-o OUTDIR] [--detailed] [--quantize 16|8|0] [--bpm N] [--follow-tempo]
"""
import argparse
import os
import sys


def main() -> int:
    if len(sys.argv) == 1:
        from .gui import run
        run()
        return 0

    # Windows consoles default to a code page without č ć š ž đ; never crash on printing a song name.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    from .audio import AudioOptions
    from .convert import convert_file

    ap = argparse.ArgumentParser(prog="songtomidi", description="Convert songs to MIDI for the KORG Pa900")
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--out", help="output folder (default: 'Pa900 MIDI' next to each file)")
    ap.add_argument("--detailed", action="store_true", help="6-stem separation (adds piano and guitar)")
    ap.add_argument("--quantize", type=int, default=16, choices=[0, 8, 16])
    ap.add_argument("--bpm", type=float, default=0.0, help="tempo hint if detection gets it wrong")
    ap.add_argument("--beats", type=int, default=4, help="beats per bar (4 or 3)")
    ap.add_argument("--follow-tempo", action="store_true", help="keep the recording's tempo changes")
    ap.add_argument("--cpu", action="store_true", help="do not use the graphics card")
    a = ap.parse_args()

    opts = AudioOptions(detailed=a.detailed, quantize={0: 0.0, 8: 0.5, 16: 0.25}[a.quantize], bpm_hint=a.bpm,
                        beats_per_bar=a.beats, steady_tempo=not a.follow_tempo, device="cpu" if a.cpu else "auto")
    failed = 0
    for f in a.files:
        out_dir = a.out or os.path.join(os.path.dirname(os.path.abspath(f)), "Pa900 MIDI")
        try:
            convert_file(f, out_dir, opts, print)
        except Exception as e:  # keep going with the rest of the batch
            failed += 1
            print(f"FAILED {f}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
