"""Convert recognized piano notes into guitar-playable note data."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import re


MIN_MIDI, MAX_MIDI = 40, 88  # standard guitar E2 through E6
PITCHES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
           "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
           "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
MIDI_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")


class PianoConversionError(ValueError):
    pass


@dataclass(frozen=True)
class ConvertedNote:
    pitch: str
    midi: int
    start: Fraction
    duration: Fraction
    measure: str
    staff: str | None
    voice: str | None
    original_pitch: str
    reason: str = ""
    transposed: bool = False

    def data(self):
        return {"pitch": self.pitch, "midi": self.midi, "start": str(self.start),
                "duration": str(self.duration), "measure": self.measure,
                "staff": self.staff, "voice": self.voice,
                "originalPitch": self.original_pitch, "reason": self.reason,
                "transposed": self.transposed}


def midi(pitch: str) -> int:
    match = re.fullmatch(r"([A-G](?:#|b)?)(-?\d+)", pitch or "")
    if not match or match.group(1) not in PITCHES:
        raise PianoConversionError(f"音名を解釈できません: {pitch}")
    return (int(match.group(2)) + 1) * 12 + PITCHES[match.group(1)]


def pitch_name(value: int) -> str:
    return f"{MIDI_NAMES[value % 12]}{value // 12 - 1}"


def _value(note):
    if isinstance(note, dict):
        return note["start"], note["duration"]
    return note.start, note.duration


def _get(note, name, default=None):
    if isinstance(note, dict):
        return note.get(name, default)
    return getattr(note, name, default)


def _normalize(note):
    start, duration = _value(note)
    return {"pitch": _get(note, "pitch"), "start": Fraction(start), "duration": Fraction(duration),
            "measure": str(_get(note, "measure", "")), "staff": _get(note, "staff"), "voice": _get(note, "voice")}


def _move_into_range(value, policy):
    if MIN_MIDI <= value <= MAX_MIDI:
        return value, False, ""
    if policy == "delete":
        return None, False, "ギターの音域外のため削除"
    if policy == "warn":
        return value, False, "ギターの音域外です"
    if policy not in ("up", "down"):
        raise PianoConversionError("音域外の処理が不正です。")
    moved = value
    while moved < MIN_MIDI:
        moved += 12
    while moved > MAX_MIDI:
        moved -= 12
    return moved, True, "ギターの音域に収めるためオクターブ移動"


def convert(notes, *, hand="both", mode="melody", max_voices=3,
            deduplicate=True, out_of_range="up"):
    if hand not in ("right", "left", "both"):
        raise PianoConversionError("変換対象が不正です。")
    if mode not in ("melody", "bass", "chord", "single", "preserve"):
        raise PianoConversionError("変換モードが不正です。")
    try:
        max_voices = int(max_voices)
    except (ValueError, TypeError) as exc:
        raise PianoConversionError("最大同時発音数が不正です。") from exc
    if not 1 <= max_voices <= 6:
        raise PianoConversionError("最大同時発音数は1〜6です。")
    normalized = [_normalize(note) for note in notes]
    if hand != "both":
        wanted = "1" if hand == "right" else "2"
        normalized = [note for note in normalized if str(note["staff"]) == wanted]
    groups = {}
    for note in normalized:
        groups.setdefault(note["start"], []).append(note)
    converted, removed, moved = [], [], []
    for start in sorted(groups):
        group = sorted(groups[start], key=lambda note: midi(note["pitch"]))
        if deduplicate:
            seen, unique = set(), []
            for note in group:
                key = midi(note["pitch"]) % 12
                if key in seen:
                    removed.append({"pitch": note["pitch"], "reason": "同じ音名の重複"})
                else:
                    seen.add(key)
                    unique.append(note)
            group = unique
        if mode == "single":
            group = [max(group, key=lambda note: midi(note["pitch"]))]
        elif mode == "melody":
            group = sorted(group, key=lambda note: midi(note["pitch"]), reverse=True)[:max_voices]
        elif mode == "bass":
            group = sorted(group, key=lambda note: midi(note["pitch"]))[:max_voices]
        elif mode == "chord":
            group = sorted(group, key=lambda note: (midi(note["pitch"]) % 12, midi(note["pitch"])))[:max_voices]
        elif mode == "preserve":
            # The caller has already selected the musically important notes.
            # Keep that order so bass and top melody are not discarded.
            if len(group) > max_voices:
                # ``group`` is pitch-sorted for normalization. Retain both
                # extremes first, then fill the remaining voices from the
                # middle of the chord.
                extremes = [group[0]]
                if max_voices > 1:
                    extremes.append(group[-1])
                middle = group[1:-1]
                group = extremes + middle[:max_voices - len(extremes)]
        if len(group) > max_voices:
            discarded = group[max_voices:]
            group = group[:max_voices]
            removed.extend({"pitch": note["pitch"], "reason": "最大同時発音数を超えたため"} for note in discarded)
        for note in group:
            original = midi(note["pitch"])
            value, was_moved, reason = _move_into_range(original, out_of_range)
            if value is None:
                removed.append({"pitch": note["pitch"], "reason": reason})
                continue
            if out_of_range == "warn" and not MIN_MIDI <= value <= MAX_MIDI:
                moved.append({"originalPitch": note["pitch"], "convertedPitch": note["pitch"], "reason": reason})
            elif was_moved:
                moved.append({"originalPitch": note["pitch"], "convertedPitch": pitch_name(value),
                              "transposeOctaves": (value - original) // 12, "reason": reason})
            converted.append(ConvertedNote(pitch_name(value), value, note["start"], note["duration"],
                                           note["measure"], note["staff"], note["voice"], note["pitch"],
                                           reason, was_moved))
    return {"originalNotes": [note["pitch"] for note in normalized],
            "convertedNotes": [note.data() for note in converted],
            "removedNotes": removed, "movedNotes": moved,
            "settings": {"hand": hand, "mode": mode, "maxVoices": max_voices,
                          "deduplicate": deduplicate, "outOfRange": out_of_range}}
