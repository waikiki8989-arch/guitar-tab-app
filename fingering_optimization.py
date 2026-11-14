"""Choose easy guitar fingerings from string/fret candidates."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product


class FingeringOptimizationError(ValueError):
    pass


@dataclass(frozen=True)
class Placement:
    string: int
    fret: int


MODE_WEIGHTS = {
    "beginner": {"open": 4, "move": 3, "string": 2, "high": 2, "melodyPosition": 4, "barre": 8},
    "standard": {"open": 2, "move": 2, "string": 1, "high": 1, "melodyPosition": 3, "barre": 5},
    "original": {"open": 0, "move": 1, "string": 0, "high": 0, "melodyPosition": 1, "barre": 1},
}


def _get(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _candidates(note):
    values = _get(note, "candidates", ())
    result = []
    for value in values:
        string, fret = _get(value, "string"), _get(value, "fret")
        if string is None or fret is None:
            continue
        result.append(Placement(int(string), int(fret)))
    return result


def _groups(notes):
    groups = {}
    for note in notes:
        groups.setdefault(str(_get(note, "start", "0")), []).append(note)
    return [groups[key] for key in sorted(groups, key=lambda value: _fraction(value))]


def _fraction(value):
    from fractions import Fraction
    return Fraction(value)


def _form_score(placements, weights, group=()):
    if not placements:
        return 0
    strings = {item.string for item in placements}
    frets = [item.fret for item in placements if item.fret > 0]
    span = max(frets) - min(frets) if frets else 0
    melody_position = sum(max(0, placement.fret - 2) * weights["melodyPosition"]
                          for placement, note in zip(placements, group)
                          if _get(note, "role") == "melody" and placement.fret > 0)
    return (-sum(item.fret == 0 for item in placements) * weights["open"]
            + span * weights["move"] + sum(item.fret > 12 for item in placements) * weights["high"]
            + melody_position
            + (5 if len(strings) < len(placements) else 0) * weights["barre"])


def _group_options(group, weights):
    choices = [_candidates(note) for note in group]
    if any(not candidates for candidates in choices):
        return []
    options = []
    for placement_set in product(*choices):
        if len({item.string for item in placement_set}) != len(placement_set):
            continue
        options.append((tuple(placement_set), _form_score(placement_set, weights, group)))
    return sorted(options, key=lambda item: item[1])


def optimize(notes, *, mode="standard", capo=0, alternatives=3, large_move=10):
    if mode not in MODE_WEIGHTS:
        raise FingeringOptimizationError("運指モードが不正です。")
    if int(capo) < 0 or int(capo) > 12:
        raise FingeringOptimizationError("カポは0〜12で指定してください。")
    if int(alternatives) < 1:
        raise FingeringOptimizationError("代替候補数が不正です。")
    groups = _groups(notes)
    weights = MODE_WEIGHTS[mode]
    options = [_group_options(group, weights) for group in groups]
    warnings = []
    if any(not values for values in options):
        warnings.append("同時発音する音を別々の弦へ割り当てられない箇所があります。")
    if not groups or any(not values for values in options):
        return {"capo": int(capo), "mode": mode, "notes": [], "alternatives": [], "warnings": warnings}

    # Dynamic programming over time groups. State is the last placement tuple.
    states = {(): (0, [])}
    for group_options in options:
        next_states = {}
        for previous, (cost, path) in states.items():
            for placement_set, local_cost in group_options:
                distance = 0
                string_moves = 0
                if previous:
                    distance = sum(abs(now.fret - before.fret) for before, now in zip(previous, placement_set))
                    string_moves = sum(before.string != now.string for before, now in zip(previous, placement_set))
                transition = distance * weights["move"] + string_moves * weights["string"]
                total = cost + local_cost + transition
                current_path = path + [placement_set]
                key = tuple(placement_set)
                if key not in next_states or total < next_states[key][0]:
                    next_states[key] = (total, current_path)
        states = next_states
    ranked = sorted(states.values(), key=lambda value: value[0])
    best_cost, best_path = ranked[0]
    selected = []
    for group, placements in zip(groups, best_path):
        for note, placement in zip(group, placements):
            selected.append({"noteId": _get(note, "noteId", _get(note, "note_id")),
                             "pitch": _get(note, "pitch"), "start": str(_get(note, "start", "0")),
                             "duration": str(_get(note, "duration", "0")), "measure": _get(note, "measure", ""),
                             "string": placement.string, "fret": placement.fret,
                             "position": placement.fret, "difficulty": 1 if placement.fret <= 5 else 2,
                             "reason": "前後の音との移動距離を評価して選択"})
    if any(abs(selected[index]["fret"] - selected[index - 1]["fret"]) >= int(large_move)
           for index in range(1, len(selected))):
        warnings.append(f"この曲には{large_move}フレット以上の移動があります。")
    return {"capo": int(capo), "mode": mode, "notes": selected,
            "alternatives": [{"score": score, "placements": [
                {"string": item.string, "fret": item.fret} for group in path for item in group]}
                for score, path in ranked[1:int(alternatives)]],
            "score": best_cost, "warnings": warnings}
