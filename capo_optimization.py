"""Suggest capo positions and easy guitar chord shapes without changing pitch."""
from __future__ import annotations

from dataclasses import dataclass
import re

ROOTS = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
ROOT_INDEX = {root: index for index, root in enumerate(ROOTS)}
ALIASES = {"Db": "C#", "D#": "Eb", "Gb": "F#", "G#": "Ab", "A#": "Bb"}


class CapoOptimizationError(ValueError):
    pass


@dataclass(frozen=True)
class ChordForm:
    name: str
    frets: tuple
    difficulty: int
    open_strings: int
    barre: bool

    def data(self):
        return {"name": self.name, "frets": list(self.frets), "difficulty": self.difficulty,
                "openStrings": self.open_strings, "barre": self.barre}


# 6th string to 1st string. x=mute and 0=open.
FORMS = {
    "C": ChordForm("C", ("x", 3, 2, 0, 1, 0), 2, 3, False),
    "D": ChordForm("D", ("x", "x", 0, 2, 3, 2), 1, 2, False),
    "E": ChordForm("E", (0, 2, 2, 1, 0, 0), 1, 4, False),
    "G": ChordForm("G", (3, 2, 0, 0, 0, 3), 2, 3, False),
    "A": ChordForm("A", ("x", 0, 2, 2, 2, 0), 1, 4, False),
    "Am": ChordForm("Am", ("x", 0, 0, 2, 1, 0), 1, 4, False),
    "Bm": ChordForm("Bm", ("x", 2, 4, 4, 3, 2), 4, 0, True),
    "Em": ChordForm("Em", (0, 2, 2, 0, 0, 0), 1, 4, False),
    "Dm": ChordForm("Dm", ("x", "x", 0, 2, 3, 1), 2, 2, False),
    "Asus4": ChordForm("Asus4", ("x", 0, 2, 2, 3, 0), 2, 3, False),
    "Dsus4": ChordForm("Dsus4", ("x", "x", 0, 2, 3, 3), 2, 1, False),
    "Esus4": ChordForm("Esus4", (0, 2, 2, 2, 0, 0), 2, 3, False),
    "A7": ChordForm("A7", ("x", 0, 2, 0, 2, 0), 1, 4, False),
    "E7": ChordForm("E7", (0, 2, 0, 1, 0, 0), 1, 4, False),
    "D7": ChordForm("D7", ("x", "x", 0, 2, 1, 2), 2, 2, False),
    "Am7": ChordForm("Am7", ("x", 0, 0, 2, 0, 1), 1, 4, False),
    "Em7": ChordForm("Em7", (0, 2, 0, 0, 0, 0), 1, 5, False),
    "Amaj7": ChordForm("Amaj7", ("x", 0, 2, 1, 2, 0), 2, 3, False),
    "Emaj7": ChordForm("Emaj7", (0, 2, 1, 1, 0, 0), 2, 3, False),
}


def parse_chord(chord):
    match = re.fullmatch(r"([A-G](?:#|b)?)(m7|maj7|M7|sus4|7|m)?(?:/([A-G](?:#|b)?))?", chord or "")
    if not match:
        raise CapoOptimizationError(f"未対応のコード形式です: {chord}")
    root, suffix, bass = match.groups()
    root = ALIASES.get(root, root)
    bass = ALIASES.get(bass, bass) if bass else None
    suffix = suffix or ""
    if suffix == "M7":
        suffix = "maj7"
    return root, suffix, bass


def transpose_root(root, semitones):
    return ROOTS[(ROOT_INDEX[root] + semitones) % 12]


def shape_for(sounding_chord, capo):
    root, suffix, bass = parse_chord(sounding_chord)
    shape_root = transpose_root(root, -capo)
    name = shape_root + suffix
    return FORMS.get(name), name, root + suffix


def _score(forms, unsupported, previous=None):
    usable = [form for form in forms if form]
    open_count = sum(form.open_strings for form in usable)
    barre_count = sum(form.barre for form in usable)
    fret_values = [fret for form in usable for fret in form.frets if isinstance(fret, int) and fret > 0]
    movement = 0
    if previous and usable:
        movement = abs((sum(f for f in fret_values) / len(fret_values)) - previous)
    return open_count * 3 - barre_count * 5 - (sum(fret_values) / len(fret_values) if fret_values else 0) - movement - len(unsupported) * 10


def optimize(chords, *, min_capo=0, max_capo=7, top=3):
    try:
        min_capo, max_capo, top = int(min_capo), int(max_capo), int(top)
    except (TypeError, ValueError) as exc:
        raise CapoOptimizationError("カポ範囲または候補数が不正です。") from exc
    if not 0 <= min_capo <= max_capo <= 12 or top < 1:
        raise CapoOptimizationError("カポ範囲は0〜12、候補数は1以上で指定してください。")
    results = []
    for capo in range(min_capo, max_capo + 1):
        converted, forms, unsupported = [], [], []
        for item in chords:
            sounding = item["chord"] if isinstance(item, dict) else item.chord
            try:
                form, shape, normalized = shape_for(sounding, capo)
            except CapoOptimizationError:
                form, shape, normalized = None, "未対応", sounding
            if form is None:
                unsupported.append(sounding)
            else:
                forms.append(form)
            converted.append({"measure": item.get("measure") if isinstance(item, dict) else item.measure,
                              "beat": item.get("beat") if isinstance(item, dict) else item.beat,
                              "soundingChord": normalized, "shapeChord": shape,
                              "fingering": form.data()["frets"] if form else None,
                              "warning": None if form else "対応するコードフォームがありません。"})
        score = _score(forms, unsupported)
        results.append({"capo": capo, "score": round(score, 3), "chords": converted,
                        "unsupported": sorted(set(unsupported)),
                        "openStrings": sum(form.open_strings for form in forms),
                        "barreCount": sum(form.barre for form in forms),
                        "difficulty": round(sum(form.difficulty for form in forms) / len(forms), 2) if forms else None})
    results.sort(key=lambda result: result["score"], reverse=True)
    for rank, result in enumerate(results[:top], 1):
        result["rank"] = rank
    return {"recommended": results[0] if results else None, "candidates": results[:top],
            "allCandidates": results}
