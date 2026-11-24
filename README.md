# SongToMidi

Turns songs into Standard MIDI Files laid out for the KORG Pa900: type 1 files in General MIDI
layout, drums on channel 10, sounds set at the start, and plain ASCII file names.

## Install (once)

Double-click `setup.bat`. It needs internet and about 4 GB of disk space. It installs the official
Python 3.11 with winget if it's missing, puts all libraries into `.venv` in this folder, downloads
the AI models and runs a self-test. (On this PC it has already been done.)

## Use

Double-click `SongToMidi.bat`. Add songs, check the options, then press **Convert**. By default the
MIDI files go into a `Pa900 MIDI` folder next to each song. Copy them to a USB stick and play them in
Song Play.

From the command line:
`.venv\Scripts\python.exe -m songtomidi song.mp3 other.gp5 -o D:\PA900 --detailed --bpm 128`

## What goes in, how good it comes out

| Input | How | Quality |
|---|---|---|
| Guitar Pro 3/4/5 (`.gp3 .gp4 .gp5`) | read note by note, repeats expanded | exact |
| MusicXML / MXL, ABC, Humdrum | music21 | exact |
| MIDI / KAR | re-written in Pa900 layout | unchanged notes |
| MP3, WAV, FLAC, M4A, OGG, ... | AI: Demucs splits the instruments, Basic Pitch finds the notes, beat tracking + snapping to a grid | drums and bass are usable, the melody is decent, chords are rough |

Newer Guitar Pro files (`.gp`, `.gpx`) and MuseScore files (`.mscz`): open them in the free MuseScore 4,
export as MusicXML, then convert that file.

## Tracks from audio

| Ch | Part | Default sound |
|---|---|---|
| 1 | Melody (from the vocals) | Alto Sax |
| 2 | Bass | Fingered bass |
| 3 | Piano (Detailed mode only) | Grand piano |
| 4 | Guitar (Detailed mode only) | Clean electric guitar |
| 5 | Harmony / everything else | Strings |
| 10 | Drums (kick, snare, hi-hat) | Standard kit |

On the Pa900 you can mute or change any of these tracks.

## Tips

* If the tempo comes out at half or double speed, type the real BPM into **Tempo** and convert again.
* For waltzes and 3/4 folk songs, set **Beats per bar** to 3.
* Set **Snap notes to** to *Off* for songs played freely (rubato), and *1/8* for straight, simple songs.

---

# Progress log

## 2026-11-22: version 1.0 working

**Done**
- New standalone app, separate from `midi-app`: Tkinter window, command line, batch conversion,
  settings remembered in `%APPDATA%\SongToMidi\settings.json`.
- Audio pipeline: ffmpeg decode → Demucs stems (4 or 6) → beat tracking → Basic Pitch notes per stem
  → drum onsets per frequency band → clean-up → Pa900 MIDI.
- Score pipeline: Guitar Pro 3/4/5 (with repeats and alternative endings), MusicXML/MXL/ABC/Humdrum
  via music21, and MIDI/KAR re-written in Pa900 layout.
- Python 3.11.9 installed (winget, user scope) and `.venv` built on this PC. PyTorch uses the RTX 3050 GPU.
- Self-test (`tests/selftest.py`) passes: a generated 112 BPM song, an ABC tune with repeats, and the Guitar Pro repeat order.
- Also tested by hand: MP3 with a non-ASCII name in Detailed mode, a Bach chorale in MusicXML, a generated GP5 file, and the window.

**Bugs found by testing and fixed**
- Repeated bass notes were merged into one long note. Fragment joining now applies to the melody only.
- The command line crashed printing names with č ć š ž đ to the Windows console. It now uses UTF-8 output.

**Not tested yet**
- Real commercial MP3s. There were none on this PC; every audio test so far used generated songs.
- Loading and playing the files on the actual KORG Pa900.

---

# Developer notes

**Layout**
| File | What it does |
|---|---|
| `songtomidi/audio.py` | Audio pipeline: decode, separate, beat grid, transcribe, clean-up, tempo map |
| `songtomidi/scores.py` | Guitar Pro reader + repeat expansion; music21 bridge |
| `songtomidi/midiout.py` | `Note`/`Part` model, clean-up helpers, Pa900 MIDI writer, MIDI reader |
| `songtomidi/gm.py` | GM program names, default channels and sounds for audio parts |
| `songtomidi/convert.py` | Picks the converter by file extension, builds the output file name |
| `songtomidi/gui.py` / `__main__.py` | Window / command line |
| `tests/selftest.py` | End-to-end checks (`.venv\Scripts\python.exe -m tests.selftest`) |

**Key decisions and pins**
- All note times are in **beats**, placed relative to the detected beats. Because of that, a live
  recording that drifts still snaps cleanly to the grid, and the file can use one steady tempo.
- The downbeat is chosen so that kicks land on beat 1 and snares on beats 2 and 4. There is always one empty bar at the start.
- `torch==2.5.1`: Demucs 4.0.1 can't load its models with torch 2.6+ (`weights_only` change).
- `numpy<2`, `setuptools<81` (pretty_midi needs `pkg_resources`).
- Basic Pitch 0.4 installs TensorFlow on Windows (about 2 GB of the 6.5 GB `.venv`). It uses
  whichever backend it finds.
- Python itself is at `%LOCALAPPDATA%\Programs\Python\Python311\python.exe`.
- Model caches: Demucs → `%USERPROFILE%\.cache\torch\hub\checkpoints`.

**Clean-up thresholds** (in `audio.py`, `convert_audio`) are first guesses tuned only on generated
audio. These are the first numbers to adjust once real songs are tested:
`STEM_SETTINGS` (Basic Pitch thresholds per stem), amplitude cut-offs (`x.amp >= …`),
`max_harmony_voices`, and the drum bands and `delta` values in `transcribe_drums`.

---

# Improvement plan

Ordered by value for the Pa900. Each item is small enough for one work session.

## 1. Validate on real songs (do first)
- [ ] Convert 5–10 real MP3s of different styles: pop, folk/narodna in 7/8 and 9/8, a waltz, a ballad.
- [ ] On the Pa900: does the file load, is the tempo right, are the sounds right, do track mutes work,
      does transpose work? Note any problems per song.
- [ ] Add a `--keep-stems` option to save the separated WAVs, so each step can be checked by ear.
- [ ] Tune the thresholds listed above against these songs.

## 2. Better drums
- [ ] Replace the band-onset heuristic with a trained drum transcription model, for example ADTOF or
      Omnizart drums. Goal: toms, crash, ride and open hi-hat as well as kick, snare and closed hi-hat.
- [ ] Until then: put a crash on the first beat of detected sections, and detect open hi-hats from long decays.

## 3. Clean chords instead of messy notes (biggest gain for "music only")
- [ ] Detect chords: chroma + chord templates, or a chord-recognition model.
- [ ] Write the harmony as clean voiced chords (pad or piano) instead of the raw Basic Pitch notes.
      Option: "Harmony from: notes / chords".
- [ ] Optional chord track so the Pa900's styles could play along (investigate how the Pa900 reads chords from a MIDI file).
- [ ] Key detection, plus an option to snap stray notes to the scale.

## 4. Better melody and bass
- [ ] Melody: use a dedicated single-voice pitch tracker (torchcrepe or pYIN) on the vocal stem instead of Basic Pitch.
- [ ] Bass: try pYIN on the bass stem and compare with Basic Pitch.
- [ ] Offer `htdemucs_ft` (better separation, about 4× slower) as a "Best quality" option.

## 5. Rhythm and tempo
- [ ] Automatic half/double-tempo correction (prefer 70–160 BPM unless a BPM is given).
- [ ] Detect the time signature automatically (3/4 vs 4/4). Support for Balkan odd meters (7/8, 9/8, 11/8) as a manual setting.
- [ ] Optional swing/triplet grid.

## 6. More input formats
- [ ] Guitar Pro 6/7/8 (`.gpx`, `.gp`). A `.gp` file is a zip containing `Content/score.gpif` (XML), so it can be parsed directly.
- [ ] MuseScore `.mscz` by calling MuseScore's command line, if MuseScore is installed.
- [ ] MIDI repair mode: fix downloaded MIDI files for the Pa900 (map non-GM sounds, move drums to channel 10, strip junk SysEx).

## 7. App comfort
- [ ] Drag and drop files onto the window (`tkinterdnd2`).
- [ ] Preview: play the result through the Windows GM synth, with per-track mute, and A/B against the original.
- [ ] Per-song settings (BPM, beats per bar) in the file list.
- [ ] Show progress per stage, and a Cancel button.

## 8. Size and packaging
- [ ] Drop TensorFlow: install `basic-pitch` without dependencies and force its ONNX model.
      Should save about 2 GB.
- [ ] Portable zip or installer so the app can move to another PC without running setup.
- [x] Put the project in git (`git init`) before larger changes. Public on GitHub.
