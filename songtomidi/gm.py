"""General MIDI program names and the default instrument set-up used for the KORG Pa900."""

GM_PROGRAMS = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet",
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics",
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2",
    "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute",
    "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)",
    "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]
assert len(GM_PROGRAMS) == 128

DRUM_CHANNEL = 9  # MIDI channel 10 (0-based 9)

# Parts produced from audio. key -> settings.
#   channel is 0-based (0 = MIDI ch 1).  program is 0-based GM.
AUDIO_PARTS = {
    "melody":  dict(label="Melody (from vocals)", channel=0, program=65, volume=90,  pan=64, reverb=40, chorus=0),
    "bass":    dict(label="Bass",                 channel=1, program=33, volume=105, pan=64, reverb=10, chorus=0),
    "piano":   dict(label="Piano",                channel=2, program=0,  volume=90,  pan=72, reverb=40, chorus=0),
    "guitar":  dict(label="Guitar",               channel=3, program=27, volume=85,  pan=48, reverb=35, chorus=20),
    "harmony": dict(label="Harmony / other",      channel=4, program=48, volume=85,  pan=80, reverb=50, chorus=10),
    "drums":   dict(label="Drums",                channel=DRUM_CHANNEL, program=0, volume=110, pan=64, reverb=30, chorus=0),
}


def program_label(p: int) -> str:
    return f"{p + 1:03d} {GM_PROGRAMS[p]}"


def parse_program_label(text: str) -> int:
    return max(0, min(127, int(text.split()[0]) - 1))
