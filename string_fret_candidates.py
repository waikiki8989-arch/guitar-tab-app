"""Generate playable string/fret candidates for standard-tuned guitar."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import re

# TAB string numbers: 1 is the highest-pitched string, 6 the lowest.
OPEN_STRING_MIDI = {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40}
MIN_FRET = 0


class StringFretError(ValueError):
    pass


@dataclass(frozen=True)
class Candidate:
    string: int
    fret: int

    def data(self):
        return {"string": self.string, "fret": self.fret}


def pitch_to_midi(pitch: str) -> int:
    match = re.fullmatch(r"([A-G](?:#|b)?)(-?\d+)", pitch or "")
    if not match:
        raise StringFretError(f"音名を解釈できません: {pitch}")
    names = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
             "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
             "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
    if match.group(1) not in names:
        raise StringFretError(f"音名を解釈できません: {pitch}")
    return (int(match.group(2)) + 1) * 12 + names[match.group(1)]


def candidates(pitch: str, *, max_fret: int = 20, open_strings: str = "prefer"):
    if not 0 <= int(max_fret) <= 36:
        raise StringFretError("最大フレットは0〜36です。")
    if open_strings not in ("prefer", "allow", "exclude"):
        raise StringFretError("開放弦の設定が不正です。")
    target = pitch_to_midi(pitch)
    result = [Candidate(string, target - opened)
              for string, opened in OPEN_STRING_MIDI.items()
              if MIN_FRET <= target - opened <= int(max_fret)
              and not (open_strings == "exclude" and target == opened)]
    result.sort(key=lambda item: (0 if item.fret == 0 else 1, item.fret, item.string))
    return result


def _get(note, name, default=None):
    if isinstance(note, dict):
        return note.get(name, default)
    return getattr(note, name, default)


def _note_data(note, *, max_fret=20, open_strings="prefer"):
    pitch = _get(note, "pitch")
    return {"pitch": pitch, "start": str(_get(note, "start", "0")),
            "duration": str(_get(note, "duration", "0")),
            "measure": str(_get(note, "measure", "")),
            "candidates": [candidate.data() for candidate in candidates(pitch, max_fret=max_fret, open_strings=open_strings)]}


def assign_chord(notes, *, max_fret: int = 20, open_strings: str = "prefer"):
    """Assign distinct strings to simultaneous notes when possible."""
    prepared = []
    for note in notes:
        pitch = _get(note, "pitch")
        prepared.append({"pitch": pitch, "start": str(_get(note, "start", "0")),
                         "duration": str(_get(note, "duration", "0")),
                         "measure": str(_get(note, "measure", "")),
                         "candidates": candidates(pitch, max_fret=max_fret, open_strings=open_strings)})
    if not prepared:
        return {"notes": [], "playable": True, "warnings": []}
    if any(not item["candidates"] for item in prepared):
        return {"notes": [{**item, "candidates": [c.data() for c in item["candidates"]]} for item in prepared],
                "playable": False, "warnings": ["音域外または最大フレットを超える音があります。"]}
    # Fewer candidates first keeps the search small and deterministic.
    order = sorted(range(len(prepared)), key=lambda index: len(prepared[index]["candidates"]))
    best = None

    def search(position, used, selected, score):
        nonlocal best
        if position == len(order):
            if best is None or score < best[0]:
                best = (score, dict(selected))
            return
        index = order[position]
        for candidate in prepared[index]["candidates"]:
            if candidate.string in used:
                continue
            cost = candidate.fret + (0 if candidate.fret == 0 else 1)
            search(position + 1, used | {candidate.string}, {**selected, index: candidate}, score + cost)

    search(0, set(), {}, 0)
    if best is None:
        return {"notes": [{**item, "candidates": [c.data() for c in item["candidates"]]} for item in prepared],
                "playable": False, "warnings": ["同時発音する音を別々の弦へ割り当てられません。"]}
    result = []
    for index, item in enumerate(prepared):
        selected = best[1][index]
        result.append({"pitch": item["pitch"], "string": selected.string, "fret": selected.fret,
                       "start": item["start"], "duration": item["duration"], "measure": item["measure"],
                       "candidates": [candidate.data() for candidate in item["candidates"]]})
    return {"notes": result, "playable": True, "warnings": []}


def build_candidate_data(notes, *, max_fret=20, open_strings="prefer"):
    return {"notes": [_note_data(note, max_fret=max_fret, open_strings=open_strings) for note in notes],
            "settings": {"maxFret": int(max_fret), "openStrings": open_strings}}
