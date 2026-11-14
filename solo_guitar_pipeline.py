"""MusicXML/OMR note data to a playable solo-guitar score and TAB.

The PDF importer and the MusicXML importer intentionally meet at the same
boundary: a sequence of note-like objects with exact ``start`` and
``duration`` values.  This module keeps the source note immutable and adds
the guitar-specific representation alongside it.
"""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
import re

from capo_optimization import capo_for_key
from fingering_optimization import optimize as optimize_fingering
from piano_to_guitar import midi, pitch_name
from string_fret_candidates import OPEN_STRING_MIDI


class SoloGuitarPipelineError(ValueError):
    """A user-correctable conversion setting or note-data error."""


PITCH_CLASSES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
                 "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
                 "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
ROLE_PRIORITY = {"melody": 0, "bass": 1, "inner": 2, "accompaniment": 3}
STRING_LABELS = {1: "e", 2: "B", 3: "G", 4: "D", 5: "A", 6: "E"}


def _get(note, name, default=None):
    if isinstance(note, dict):
        return note.get(name, default)
    return getattr(note, name, default)


def _pitch_class(pitch):
    match = re.fullmatch(r"([A-G](?:#|b)?)-?\d+", pitch or "")
    return PITCH_CLASSES[match.group(1)] if match else None


def _normalize(notes):
    result = []
    for index, note in enumerate(notes, 1):
        source_pitch = _get(note, "source_pitch", _get(note, "sourcePitch", _get(note, "pitch")))
        if not source_pitch:
            raise SoloGuitarPipelineError("音符に音名がありません。")
        try:
            source_midi = midi(source_pitch)
            start = Fraction(_get(note, "start", 0))
            duration = Fraction(_get(note, "duration", 0))
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise SoloGuitarPipelineError("音符の音高または時間情報が不正です。") from exc
        if start < 0 or duration <= 0:
            raise SoloGuitarPipelineError("音符の開始位置・音価は正しい分数で指定してください。")
        source_id = _get(note, "note_id", _get(note, "noteId", "")) or f"note-{index}"
        sounding_midi = source_midi - 12
        result.append({
            "noteId": str(source_id), "sourcePitch": source_pitch,
            "sourceMidi": source_midi, "guitarSoundingPitch": pitch_name(sounding_midi),
            "guitarSoundingMidi": sounding_midi,
            "guitarWrittenPitch": pitch_name(source_midi),
            "start": start, "duration": duration,
            "measure": str(_get(note, "measure", "")),
            "partId": _get(note, "part_id", _get(note, "partId")),
            "partName": _get(note, "part_name", _get(note, "partName")),
            "staff": _get(note, "staff"), "voice": _get(note, "voice"),
            "tie": _get(note, "tie"), "dots": int(_get(note, "dots", 0) or 0),
            "tupletActual": _get(note, "tuplet_actual", _get(note, "tupletActual")),
            "tupletNormal": _get(note, "tuplet_normal", _get(note, "tupletNormal")),
        })
    return result


def _filter_hand(notes, hand):
    if hand not in ("both", "right", "left"):
        raise SoloGuitarPipelineError("変換対象が不正です。")
    if hand == "both":
        return notes
    staff = "1" if hand == "right" else "2"
    return [note for note in notes if str(note["staff"]) == staff]


def _classify(notes, mode):
    """Classify simultaneous notes without changing their source pitches."""
    groups = defaultdict(list)
    for note in notes:
        groups[note["start"]].append(note)
    for group in groups.values():
        ordered = sorted(group, key=lambda item: (item["sourceMidi"], item["noteId"]))
        for note in group:
            note["role"] = "accompaniment" if mode == "chord" else "melody"
        if mode == "chord":
            # Chord mode keeps the complete voicing but still exposes the
            # outer voices so a caller can explain any later omission.
            if ordered:
                ordered[0]["role"] = "bass"
                ordered[-1]["role"] = "melody"
            for note in ordered[1:-1]:
                note["role"] = "inner"
        elif len(ordered) > 1:
            ordered[0]["role"] = "bass"
            ordered[-1]["role"] = "melody"
            for note in ordered[1:-1]:
                note["role"] = "inner"
    return groups


def _select(groups, max_voices):
    selected = []
    removed = []
    for start in sorted(groups):
        group = sorted(groups[start], key=lambda item: (ROLE_PRIORITY[item["role"]], item["sourceMidi"], item["noteId"]))
        keep = group[:max_voices]
        selected.extend(keep)
        removed.extend({"noteId": note["noteId"], "pitch": note["sourcePitch"],
                        "reason": "同時発音数の上限を超えたため内声または伴奏を省略"}
                       for note in group[max_voices:])
    return selected, removed


def _select_voice(groups, role):
    """Select one consistent voice for legacy melody/bass/single modes."""
    selected, removed = [], []
    for start in sorted(groups):
        group = sorted(groups[start], key=lambda item: (item["sourceMidi"], item["noteId"]))
        chosen = group[-1] if role == "melody" else group[0]
        selected.append(chosen)
        pitch_class_counts = defaultdict(int)
        for item in group:
            pitch_class_counts[item["sourceMidi"] % 12] += 1
        for note in group:
            if note is chosen:
                continue
            duplicate = pitch_class_counts[note["sourceMidi"] % 12] > 1
            removed.append({"noteId": note["noteId"], "pitch": note["sourcePitch"],
                            "reason": ("同じ音名の重複を省略" if duplicate
                                       else f"{role}優先のため同時発音グループから省略")})
    return selected, removed


def _tie_groups(notes):
    chains = []
    by_key = defaultdict(list)
    for note in notes:
        by_key[(note["partId"], note["staff"], note["voice"], note["sourcePitch"])].append(note)
    for values in by_key.values():
        current = []
        for note in sorted(values, key=lambda item: item["start"]):
            previous = current[-1] if current else None
            connected = (previous is not None and previous["start"] + previous["duration"] == note["start"]
                         and previous.get("tie") in ("start", "continue")
                         and note.get("tie") in ("stop", "continue"))
            if not connected and current:
                if len(current) > 1:
                    chains.append(current)
                current = []
            current.append(note)
        if len(current) > 1:
            chains.append(current)
    for index, chain in enumerate(chains, 1):
        for note in chain:
            note["tieGroup"] = f"tie-{index}"
    return chains


def _candidates(note, *, capo, max_fret):
    target = note["guitarSoundingMidi"]
    role = note["role"]
    preferred = range(1, 5) if role == "melody" else (range(5, 7) if role == "bass" else range(3, 7))

    def make(strings):
        return [{"string": string, "fret": target - OPEN_STRING_MIDI[string] - capo,
                 "physicalFret": target - OPEN_STRING_MIDI[string]}
                for string in strings
                if 0 <= target - OPEN_STRING_MIDI[string] - capo <= max_fret]

    values = make(preferred)
    fallback = None
    if not values and role == "melody":
        fallback = "メロディーを1〜4弦へ割り当てられないため、他の弦を候補にしました。"
        values = make(range(1, 7))
    elif not values and role == "bass":
        fallback = "ベースを5・6弦へ割り当てられないため、4弦を候補にしました。"
        values = make((4,))
    return values, fallback


def _tie_consistent(notes, placements):
    by_id = {item.get("noteId"): item for item in placements}
    warnings = []
    for chain in _tie_groups(notes):
        available = [by_id.get(note["noteId"]) for note in chain if by_id.get(note["noteId"])]
        chosen = next((item for item in available if item.get("string") is not None), None)
        if chosen is None:
            continue
        for note in chain:
            item = by_id.get(note["noteId"])
            if item is None:
                continue
            if item.get("string") is not None and item["string"] != chosen["string"]:
                warnings.append({"noteId": note["noteId"], "pitch": note["sourcePitch"],
                                 "reason": "タイでつながる音の運指を統一しました。"})
            item["string"], item["fret"], item["physicalFret"] = chosen["string"], chosen["fret"], chosen.get("physicalFret", chosen["fret"])
    return warnings


def _tab_text(notes):
    measures = defaultdict(list)
    for note in notes:
        measures[str(note.get("measure", ""))].append(note)
    blocks = []
    for number, measure_notes in measures.items():
        events = defaultdict(list)
        for note in measure_notes:
            events[str(note["start"])].append(note)
        ordered_events = sorted(events.items(), key=lambda item: Fraction(item[0]))
        widths = [max([len(str(note.get("fret", "×"))) for note in values] + [1]) + 2
                  for _, values in ordered_events]
        lines = []
        for string in range(1, 7):
            content = ""
            for (_, values), width in zip(ordered_events, widths):
                match = next((item for item in values if item.get("string") == string), None)
                token = "×" if any(item.get("missing") and item.get("role") in ("melody", "bass") for item in values) and match is None else (str(match["fret"]) if match else "")
                content += token.center(width, "-")
            lines.append(f"{STRING_LABELS[string]}|{content}|")
        blocks.append(f"小節 {number}\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def _tab_layout(notes):
    grouped = defaultdict(list)
    for note in notes:
        grouped[str(note.get("measure", ""))].append(note)
    result = []
    for number, measure_notes in grouped.items():
        events = defaultdict(list)
        for note in measure_notes:
            events[str(note["start"])].append(note)
        columns = []
        for start, values in sorted(events.items(), key=lambda item: Fraction(item[0])):
            def display_string(note):
                return note.get("string") or {"melody": 1, "bass": 6,
                                               "inner": 3, "accompaniment": 4}[note["role"]]

            columns.append({"start": start, "notes": [
                {"id": note["noteId"], "pitch": note["guitarWrittenPitch"],
                 "sourcePitch": note["sourcePitch"], "guitarSoundingPitch": note["guitarSoundingPitch"],
                 "role": note["role"], "string": display_string(note),
                 "fret": "×" if note.get("missing", False) else note.get("fret"),
                 "missing": note.get("missing", False), "displayString": display_string(note)}
                for note in values]})
        result.append({"number": number, "columns": columns})
    return result


def build(notes, *, hand="both", mode="auto", max_voices=6, max_fret=20,
          fingering_mode="standard", capo=None, key_fifths=0, key_mode="major",
          chords=()):
    """Build one shared arrangement used by score, TAB, and playback callers."""
    try:
        max_voices, max_fret = int(max_voices), int(max_fret)
    except (TypeError, ValueError) as exc:
        raise SoloGuitarPipelineError("最大同時発音数または最大フレットが不正です。") from exc
    if not 1 <= max_voices <= 6:
        raise SoloGuitarPipelineError("最大同時発音数は1〜6です。")
    if not 0 <= max_fret <= 36:
        raise SoloGuitarPipelineError("最大フレットは0〜36です。")
    if mode not in ("auto", "solo", "chord", "melody", "bass", "single"):
        raise SoloGuitarPipelineError("演奏モードが不正です。")
    if key_mode not in ("major", "minor"):
        raise SoloGuitarPipelineError("キーのモードが不正です。")
    normalized = _filter_hand(_normalize(notes), hand)
    if not normalized:
        raise SoloGuitarPipelineError("変換対象の音符がありません。")
    selected_mode = "solo" if mode == "auto" else mode
    groups = _classify(normalized, "chord" if selected_mode == "chord" else "solo")
    if selected_mode in ("melody", "single"):
        selected, removed = _select_voice(groups, "melody")
    elif selected_mode == "bass":
        selected, removed = _select_voice(groups, "bass")
    else:
        selected, removed = _select(groups, max_voices)
    if capo is None:
        capo = capo_for_key(key_fifths, key_mode=key_mode)
        capo_source = "key"
    else:
        try:
            capo = int(capo)
        except (TypeError, ValueError) as exc:
            raise SoloGuitarPipelineError("カポ位置が不正です。") from exc
        capo_source = "manual"
    if not 0 <= capo <= 12:
        raise SoloGuitarPipelineError("カポは0〜12で指定してください。")

    candidate_rows, warnings = [], []
    for note in selected:
        values, fallback_warning = _candidates(note, capo=capo, max_fret=max_fret)
        if fallback_warning:
            warnings.append({"noteId": note["noteId"], "pitch": note["sourcePitch"], "reason": fallback_warning})
        row = {**note, "pitch": note["guitarSoundingPitch"],
               "originalPitch": note["sourcePitch"],
               "candidates": values}
        candidate_rows.append(row)
        if not values:
            warnings.append({"noteId": note["noteId"], "pitch": note["sourcePitch"],
                             "reason": "音域外または最大フレットを超えるため未変換（TAB上は×）"})

    playable = [row for row in candidate_rows if row["candidates"]]
    fingering = optimize_fingering(playable, mode=fingering_mode, capo=capo) if playable else {
        "capo": capo, "mode": fingering_mode, "notes": [], "alternatives": [],
        "warnings": [], "score": 0,
    }
    by_id = {item.get("noteId"): item for item in fingering["notes"]}
    output_notes = []
    for row in candidate_rows:
        item = by_id.get(row["noteId"])
        output = {key: row[key] for key in ("noteId", "sourcePitch", "sourceMidi", "guitarSoundingPitch",
                                             "guitarSoundingMidi", "guitarWrittenPitch", "start", "duration",
                                             "measure", "partId", "staff", "voice", "tie", "dots",
                                             "tupletActual", "tupletNormal", "role")}
        output.update({"pitch": row["pitch"], "string": item["string"] if item else None,
                       "fret": item["fret"] if item else None,
                       "physicalFret": capo + item["fret"] if item else None,
                       "missing": item is None, "candidates": row["candidates"]})
        output.update({"originalPitch": output["sourcePitch"],
                       "source_pitch": output["sourcePitch"],
                       "guitar_sounding_pitch": output["guitarSoundingPitch"],
                       "guitar_written_pitch": output["guitarWrittenPitch"]})
        output_notes.append(output)
    warnings.extend(_tie_consistent(candidate_rows, output_notes))
    for item in output_notes:
        for key in ("start", "duration"):
            item[key] = str(item[key])
        for candidate in item["candidates"]:
            candidate["fret"] = int(candidate["fret"])
    warnings.extend({"pitch": item["pitch"], "reason": item["reason"], "noteId": item["noteId"]}
                    for item in removed)
    warnings.extend({"reason": item} if isinstance(item, str) else {"reason": item}
                    for item in fingering.get("warnings", []))
    public_candidates = []
    for row in candidate_rows:
        public = {**row, "start": str(row["start"]), "duration": str(row["duration"])}
        public_candidates.append(public)
    return {
        "settings": {"hand": hand, "mode": selected_mode, "maxVoices": max_voices,
                     "maxFret": max_fret, "fingeringMode": fingering_mode,
                     "capo": capo, "capoSource": capo_source, "keyFifths": int(key_fifths),
                     "keyMode": key_mode, "capoForm": "Cm" if key_mode == "minor" else "C"},
        "classification": [{"noteId": note["noteId"], "role": note["role"]} for note in selected],
        "converted": {"convertedNotes": output_notes, "removedNotes": removed,
                       "settings": {"hand": hand, "mode": selected_mode, "maxVoices": max_voices}},
        "candidates": public_candidates,
        "fingering": {**fingering, "notes": output_notes},
        "notes": output_notes,
        "tabText": _tab_text(output_notes), "tabLayout": _tab_layout(output_notes),
        "warnings": warnings,
    }


build_musicxml_tab = build
build_omr_tab = build
