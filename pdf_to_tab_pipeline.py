"""End-to-end orchestration from one recognized PDF page to TAB data."""
from __future__ import annotations

from piano_to_guitar import convert
from string_fret_candidates import candidates
from fingering_optimization import optimize as optimize_fingering
from capo_optimization import optimize as optimize_capo
from chord_progression import CHORD_DEFINITIONS


def _candidate_notes(converted, max_fret):
    notes = []
    for item in converted["convertedNotes"]:
        notes.append({**item, "candidates": [candidate.data() for candidate in candidates(
            item["pitch"], max_fret=max_fret)]})
    return notes


def _chord_at(chords, start):
    available = [chord for chord in chords if chord.start <= start]
    return max(available, key=lambda chord: chord.start) if available else None


def _chord_aware_notes(notes, chords):
    """Keep bass, chord tones, and the highest note as the melody."""
    if not chords:
        return notes
    groups = {}
    for note in notes:
        groups.setdefault(note.start, []).append(note)
    selected = []
    for start in sorted(groups):
        group = groups[start]
        highest = max(group, key=lambda note: note.midi if hasattr(note, "midi") else _pitch_midi(note.pitch))
        lowest = min(group, key=lambda note: _pitch_midi(note.pitch))
        chord = _chord_at(chords, start)
        wanted = set()
        if chord and chord.symbol.root and chord.symbol.kind in CHORD_DEFINITIONS:
            root = _pitch_class(chord.symbol.root)
            wanted = {(root + interval) % 12 for interval in CHORD_DEFINITIONS[chord.symbol.kind][1]}
        candidates = [note for note in group if not wanted or _pitch_midi(note.pitch) % 12 in wanted]
        kept = {id(highest), id(lowest)}
        selected.extend([lowest] if lowest is highest else [lowest, highest])
        for note in sorted(candidates, key=lambda item: _pitch_midi(item.pitch)):
            if id(note) not in kept:
                selected.append(note)
                kept.add(id(note))
    return tuple(selected)


def _pitch_class(pitch):
    names = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
             "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
             "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
    return names[pitch]


def _pitch_midi(pitch):
    import re
    match = re.fullmatch(r"([A-G](?:#|b)?)(-?\d+)", pitch)
    if not match:
        return 0
    return (int(match.group(2)) + 1) * 12 + _pitch_class(match.group(1))


def _tab_text(notes):
    """Render fixed-width, measure-separated six-line TAB text."""
    measures = {}
    for note in notes:
        measures.setdefault(str(note.get("measure", "")), []).append(note)
    blocks = []
    labels = {1: "e", 2: "B", 3: "G", 4: "D", 5: "A", 6: "E"}
    for number, measure_notes in measures.items():
        events = {}
        for note in measure_notes:
            events.setdefault(str(note.get("start", "0")), []).append(note)
        columns = []
        for start, event_notes in sorted(events.items(), key=lambda item: _fraction(item[0])):
            width = max([len(str(note["fret"])) for note in event_notes] + [1]) + 2
            columns.append((event_notes, width))
        lines = []
        for string in range(1, 7):
            content = ""
            for event_notes, width in columns:
                note = next((item for item in event_notes if item["string"] == string), None)
                token = str(note["fret"]) if note else ""
                content += token.center(width, "-")
            lines.append(f"{labels[string]}|{content}|")
        blocks.append(f"小節 {number}\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def _tab_layout(notes):
    """Build aligned staff/TAB events for the browser renderer."""
    grouped = {}
    for note in notes:
        grouped.setdefault(str(note.get("measure", "")), []).append(note)
    measures = []
    for number, measure_notes in grouped.items():
        events = {}
        for note in measure_notes:
            events.setdefault(str(note.get("start", "0")), []).append(note)
        columns = []
        for start, event_notes in sorted(events.items(), key=lambda item: _fraction(item[0])):
            columns.append({
                "start": start,
                "notes": [
                    {"id": f"m{note.get('measure')}-b{start}-s{note['string']}",
                     "pitch": note["pitch"], "string": note["string"],
                     "fret": note["fret"]}
                    for note in event_notes
                ],
            })
        measures.append({"number": number, "columns": columns})
    return measures


def _fraction(value):
    from fractions import Fraction
    return Fraction(value)


def build(notes, *, hand="both", mode="melody", max_voices=3, max_fret=20,
          fingering_mode="standard", capo=None, key_fifths=None, chords=()):
    if capo is None:
        from capo_optimization import capo_from_key
        capo = capo_from_key(key_fifths or 0)
    source_notes = _chord_aware_notes(notes, chords)
    # Chord-aware selection is ordered as bass -> top melody -> chord tones.
    # Preserve that order instead of letting melody mode discard the bass.
    conversion_mode = "preserve" if chords else mode
    converted = convert(source_notes, hand=hand, mode=conversion_mode, max_voices=max_voices)
    candidate_notes = _candidate_notes(converted, max_fret)
    fingering = optimize_fingering(candidate_notes, mode=fingering_mode, capo=capo)
    return {
        "settings": {"hand": hand, "mode": mode, "maxVoices": max_voices,
                     "maxFret": max_fret, "fingeringMode": fingering_mode, "capo": capo},
        "converted": converted,
        "candidates": candidate_notes,
        "fingering": fingering,
        "tabText": _tab_text(fingering["notes"]),
        "tabLayout": _tab_layout(fingering["notes"]),
        "warnings": converted["removedNotes"] + fingering["warnings"],
    }
