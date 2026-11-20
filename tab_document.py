"""Editable, server-validated guitar TAB documents.

The PDF pipeline intentionally returns a presentation-oriented dictionary.  This
module is the small, durable boundary used by the dedicated TAB editor instead:
the source note remains identifiable while the editable string, fret and timing
are kept in one exact (Fraction-backed) representation.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from math import lcm
from xml.etree import ElementTree as ET

from music21.pitch import Pitch

from fingering import TUNING

VERSION = 1
MAX_EVENTS = 10000
MAX_FRET = 24
ROLES = {"melody", "bass", "inner", "accompaniment"}
TIE_VALUES = {None, "start", "stop", "continue"}
EVENT_FIELDS = (
    "event_id", "source_note_id", "role", "start", "duration", "pitch",
    "string", "fret", "tie", "dots", "rest",
)


def _fail(path, reason="値・形式が不正です"):
    raise ValueError(f"TAB編集データの{path}：{reason}。")


def _fraction(value, path):
    if type(value) is not str:
        _fail(path, "整数または分数を文字列で指定してください")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError):
        _fail(path, "分数が不正です")
    if result < 0 or result > 1000000:
        _fail(path, "数値が大きすぎます")
    return result


def _pitch_midi(value, path="音高"):
    if type(value) is not str or len(value) > 16:
        _fail(path, "音高が不正です")
    try:
        pitch = Pitch(value.replace("b", "-"))
        midi = Fraction(str(pitch.ps))
    except Exception as exc:
        raise ValueError(f"TAB編集データの{path}：音高が不正です。") from exc
    if midi.denominator != 1:
        _fail(path, "微分音は未対応です")
    return int(midi)


def _int(value, path, low, high):
    if type(value) is not int or not low <= value <= high:
        _fail(path, f"{low}〜{high}の整数が必要です")
    return value


def _end(document):
    return Fraction(document["end"])


def _measure_for(measures, start):
    for measure in measures:
        left = Fraction(measure["start"])
        if left <= start < left + Fraction(measure["duration"]):
            return measure["number"]
    return ""


def normalize(document):
    """Return a copy with defaults filled in, without accepting unsafe values."""
    if type(document) is not dict:
        _fail("全体")
    value = deepcopy(document)
    value.setdefault("version", VERSION)
    value.setdefault("end", "0")
    value.setdefault("capo", 0)
    value.setdefault("measures", [])
    events = value.setdefault("events", [])
    for event in events:
        event.setdefault("source_note_id", None)
        event.setdefault("role", "melody")
        event.setdefault("tie", None)
        event.setdefault("dots", 0)
        event.setdefault("rest", False)
    return value


def validate(document, *, end=None):
    document = normalize(document)
    if set(document) != {"version", "end", "capo", "measures", "events"}:
        _fail("全体", "未対応の項目があります")
    _int(document["version"], "形式バージョン", VERSION, VERSION)
    _int(document["capo"], "カポ", 0, 12)
    document_end = _fraction(document["end"], "曲の終端")
    if document_end <= 0 or (end is not None and document_end != Fraction(end)):
        _fail("曲の終端", "曲の範囲が不正です")
    measures = document["measures"]
    if type(measures) is not list or len(measures) > 2000:
        _fail("小節", "2000件以下の配列が必要です")
    previous = Fraction(0)
    for measure in measures:
        if type(measure) is not dict or set(measure) != {"number", "start", "duration"}:
            _fail("小節", "項目が不正です")
        if type(measure["number"]) is not str or len(measure["number"]) > 64:
            _fail("小節番号")
        start = _fraction(measure["start"], "小節の開始位置")
        duration = _fraction(measure["duration"], "小節の長さ")
        if duration <= 0 or start != previous or start + duration > document_end:
            _fail("小節", "時間が連続していないか、曲の範囲外です")
        previous = start + duration
    if measures and previous != document_end:
        _fail("小節", "曲の終端まで連続していません")
    events = document["events"]
    if type(events) is not list or len(events) > MAX_EVENTS:
        _fail("音符", f"{MAX_EVENTS}件以下の配列が必要です")
    ids, occupied = set(), []
    for event in events:
        if type(event) is not dict or set(event) != set(EVENT_FIELDS):
            _fail("音符", "項目が不正です")
        event_id = event["event_id"]
        if type(event_id) is not str or not event_id or event_id in ids or len(event_id) > 200:
            _fail("音符ID", "空か重複しています")
        ids.add(event_id)
        source_id = event["source_note_id"]
        if source_id is not None and (type(source_id) is not str or len(source_id) > 200):
            _fail("元音符ID")
        if event["role"] not in ROLES:
            _fail("役割")
        start = _fraction(event["start"], "音符の開始位置")
        duration = _fraction(event["duration"], "音符の長さ")
        if duration <= 0 or start + duration > document_end:
            _fail("音符", "開始位置・長さが曲の範囲外です")
        if type(event["rest"]) is not bool:
            _fail("休符")
        _int(event["dots"], "付点", 0, 3)
        if event["tie"] not in TIE_VALUES:
            _fail("タイ")
        if event["rest"]:
            if event["pitch"] is not None or event["string"] is not None or event["fret"] is not None:
                _fail("休符", "音高・弦・フレットは指定できません")
            continue
        midi = _pitch_midi(event["pitch"])
        if (event["string"] is None) != (event["fret"] is None):
            _fail("運指", "弦とフレットは同時に指定してください")
        if event["string"] is not None:
            string = _int(event["string"], "弦番号", 1, 6)
            fret = _int(event["fret"], "フレット", 0, MAX_FRET)
            expected = TUNING[string] + fret + document["capo"]
            if expected != midi:
                _fail("音高", "弦・フレットと一致しません")
            occupied.append((string, start, start + duration, event_id))
    for index, (string, left, right, event_id) in enumerate(occupied):
        for other_string, other_left, other_right, other_id in occupied[index + 1:]:
            if string == other_string and left < other_right and other_left < right:
                _fail("音符の重なり", f"{event_id}と{other_id}が同じ弦で重なっています")
    return document


def from_pipeline(result, measures):
    """Convert ``solo_guitar_pipeline.build`` output to the durable editor model."""
    normalized_measures = [{"number": str(m["number"]), "start": str(m["start"]),
                            "duration": str(m["duration"])} for m in measures]
    end = max((Fraction(m["start"]) + Fraction(m["duration"]) for m in normalized_measures), default=Fraction(0))
    capo = int(result.get("settings", {}).get("capo", 0))
    events = []
    occupied = []
    pipeline_notes = sorted(
        result.get("fingering", {}).get("notes", result.get("notes", ())),
        key=lambda note: (Fraction(note["start"]), Fraction(note["duration"]), str(note.get("noteId", ""))),
    )
    for index, note in enumerate(pipeline_notes, 1):
        string, fret = note.get("string"), note.get("fret")
        start = Fraction(note["start"])
        finish = start + Fraction(note["duration"])

        # The arrangement optimizer keeps the best musical position, but a
        # generated inner voice can still overlap a sustained note on that
        # string.  Re-pick from the note's own valid candidates before exposing
        # the result to the stricter document validator.
        options = []
        if string is not None and fret is not None:
            options.append((string, fret))
        options.extend((candidate.get("string"), candidate.get("fret"))
                       for candidate in note.get("candidates", ())
                       if candidate.get("string") is not None and candidate.get("fret") is not None)
        chosen = None
        seen = set()
        for option in options:
            option = (int(option[0]), int(option[1]))
            if option in seen:
                continue
            seen.add(option)
            if not any(other_string == option[0] and start < other_finish and other_start < finish
                       for other_string, other_start, other_finish in occupied):
                chosen = option
                break
        if chosen is None:
            # Keep the source/timing information so the editor can repair it;
            # a missing position is represented as an unconverted TAB event.
            string, fret = None, None
        else:
            string, fret = chosen
            occupied.append((string, start, finish))
        event = {
            "event_id": f"event-{note.get('noteId') or index}",
            "source_note_id": None if note.get("generated") else note.get("noteId"),
            "role": note.get("role", "melody"),
            "start": str(note["start"]), "duration": str(note["duration"]),
            "pitch": note.get("pitch"), "string": string, "fret": fret,
            "tie": note.get("tie"), "dots": int(note.get("dots", 0) or 0),
            "rest": False,
        }
        events.append(event)
    document = {"version": VERSION, "end": str(end), "capo": capo,
                "measures": normalized_measures, "events": events}
    return validate(document)


def _event(document, event_id):
    return next((event for event in document["events"] if event["event_id"] == event_id), None)


def _new_id(document):
    used = {event["event_id"] for event in document["events"]}
    index = 1
    while f"event-new-{index}" in used:
        index += 1
    return f"event-new-{index}"


def apply_command(document, command):
    """Apply one editor command and validate the complete result atomically."""
    current = validate(document)
    if type(command) is not dict or type(command.get("type")) is not str:
        _fail("操作", "操作種別が必要です")
    action = command["type"]
    updated = deepcopy(current)
    event_id = command.get("event_id")
    event = _event(updated, event_id) if event_id is not None else None
    if action in {"set_fret", "set_string", "set_position", "set_duration", "set_tie", "set_rest", "delete_event"} and event is None:
        _fail("操作", "対象音符が存在しません")
    if action == "set_fret":
        if event["rest"] or event["string"] is None:
            _fail("フレット", "休符・未変換音には設定できません")
        event["fret"] = _int(command.get("fret"), "フレット", 0, MAX_FRET)
        event["pitch"] = _pitch_name(TUNING[event["string"]] + event["fret"] + updated["capo"])
    elif action == "set_string":
        if event["rest"] or event["fret"] is None:
            _fail("弦", "休符・未変換音には設定できません")
        event["string"] = _int(command.get("string"), "弦番号", 1, 6)
        event["pitch"] = _pitch_name(TUNING[event["string"]] + event["fret"] + updated["capo"])
    elif action == "set_position":
        if event["rest"]:
            _fail("運指", "休符には設定できません")
        string = _int(command.get("string"), "弦番号", 1, 6)
        fret = _int(command.get("fret"), "フレット", 0, MAX_FRET)
        event.update(string=string, fret=fret,
                     pitch=_pitch_name(TUNING[string] + fret + updated["capo"]))
    elif action == "set_duration":
        duration = _fraction(command.get("duration"), "音符の長さ")
        if duration <= 0:
            _fail("音符の長さ", "0より大きい値が必要です")
        event["duration"] = str(duration)
        event["dots"] = _int(command.get("dots", event["dots"]), "付点", 0, 3)
    elif action == "set_tie":
        if command.get("tie") not in TIE_VALUES:
            _fail("タイ")
        event["tie"] = command.get("tie")
    elif action == "set_rest":
        rest = command.get("rest")
        if type(rest) is not bool:
            _fail("休符", "真偽値が必要です")
        event["rest"] = rest
        if rest:
            event.update(pitch=None, string=None, fret=None, tie=None)
        else:
            string = _int(command.get("string", 1), "弦番号", 1, 6)
            fret = _int(command.get("fret", 0), "フレット", 0, MAX_FRET)
            event.update(string=string, fret=fret,
                         pitch=_pitch_name(TUNING[string] + fret + updated["capo"]))
    elif action == "delete_event":
        updated["events"].remove(event)
    elif action == "add_event":
        raw = command.get("event")
        if type(raw) is not dict:
            _fail("追加音符")
        new_event = {field: raw.get(field) for field in EVENT_FIELDS}
        new_event["event_id"] = raw.get("event_id") or _new_id(updated)
        new_event.setdefault("source_note_id", None)
        new_event.setdefault("role", "melody")
        new_event.setdefault("tie", None)
        new_event.setdefault("dots", 0)
        new_event.setdefault("rest", False)
        if not new_event["rest"]:
            string = _int(new_event.get("string"), "弦番号", 1, 6)
            fret = _int(new_event.get("fret"), "フレット", 0, MAX_FRET)
            new_event["pitch"] = _pitch_name(TUNING[string] + fret + updated["capo"])
        updated["events"].append(new_event)
    else:
        _fail("操作", "未対応の操作です")
    return validate(updated)


def _pitch_name(midi):
    return Pitch(midi).nameWithOctave.replace("-", "b")


def playback_events(document):
    result = []
    for event in sorted(document["events"], key=lambda item: Fraction(item["start"])):
        if event["rest"] or event["pitch"] is None or event["string"] is None:
            continue
        result.append({"start": float(Fraction(event["start"])),
                       "end": float(Fraction(event["start"]) + Fraction(event["duration"])),
                       "midi": _pitch_midi(event["pitch"]), "channel": "melody",
                       "event_id": event["event_id"]})
    return result


def layout(document):
    """Build stable columns for the TAB-only HTML editor."""
    document = validate(document)
    result = []
    for measure in document["measures"]:
        left = Fraction(measure["start"])
        right = left + Fraction(measure["duration"])
        events = [event for event in document["events"]
                  if left <= Fraction(event["start"]) < right]
        starts = sorted({event["start"] for event in events}, key=Fraction)
        columns = []
        for start in starts:
            columns.append({"start": start, "events": [event for event in events if event["start"] == start]})
        result.append({"number": measure["number"], "start": measure["start"],
                       "duration": measure["duration"], "columns": columns})
    return result


def to_musicxml(document, title="TAB編集"):
    document = validate(document)
    root = ET.Element("score-partwise", version="4.0")
    ET.SubElement(ET.SubElement(root, "work"), "work-title").text = title
    part_list = ET.SubElement(root, "part-list")
    score_part = ET.SubElement(part_list, "score-part", id="guitar")
    ET.SubElement(score_part, "part-name").text = "Guitar TAB"
    part = ET.SubElement(root, "part", id="guitar")
    for index, measure in enumerate(document["measures"]):
        target = ET.SubElement(part, "measure", number=measure["number"])
        left = Fraction(measure["start"])
        right = left + Fraction(measure["duration"])
        local = sorted((event for event in document["events"]
                        if left <= Fraction(event["start"]) < right),
                       key=lambda event: (Fraction(event["start"]), event["event_id"]))
        denominators = [Fraction(measure["duration"]).denominator]
        denominators.extend(Fraction(event["start"]) - left for event in local)
        denominators.extend(Fraction(event["duration"]) for event in local)
        denominators = [value.denominator for value in denominators]
        divisions = lcm(*(denominators or [1]))
        if index == 0:
            attrs = ET.SubElement(target, "attributes")
            ET.SubElement(attrs, "divisions").text = str(divisions)
            time = ET.SubElement(attrs, "time")
            ET.SubElement(time, "beats").text = "4"
            ET.SubElement(time, "beat-type").text = "4"
            clef = ET.SubElement(attrs, "clef")
            ET.SubElement(clef, "sign").text = "TAB"
            ET.SubElement(clef, "line").text = "5"
            details = ET.SubElement(attrs, "staff-details")
            ET.SubElement(details, "staff-lines").text = "6"
            for line, string in enumerate(range(6, 0, -1), 1):
                tuning = ET.SubElement(details, "staff-tuning", line=str(line))
                name = _pitch_name(TUNING[string])
                ET.SubElement(tuning, "tuning-step").text = name[0]
                ET.SubElement(tuning, "tuning-octave").text = name[-1]
        cursor = left
        for event in local:
            start = Fraction(event["start"])
            if start > cursor:
                _append_note(target, None, start - cursor, divisions)
            same_start = [item for item in local if Fraction(item["start"]) == start]
            for chord_index, item in enumerate(same_start):
                _append_note(target, item, Fraction(item["duration"]), divisions, chord_index > 0)
            cursor = max(cursor, start + max(Fraction(item["duration"]) for item in same_start))
        if cursor < right:
            _append_note(target, None, right - cursor, divisions)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _append_note(parent, event, duration, divisions, chord=False):
    note = ET.SubElement(parent, "note")
    if chord:
        ET.SubElement(note, "chord")
    if event is None or event["rest"] or event["string"] is None:
        ET.SubElement(note, "rest")
    else:
        pitch = Pitch(event["pitch"].replace("b", "-"))
        node = ET.SubElement(note, "pitch")
        ET.SubElement(node, "step").text = pitch.step
        if pitch.accidental is not None:
            ET.SubElement(node, "alter").text = str(int(pitch.accidental.alter))
        ET.SubElement(node, "octave").text = str(pitch.octave)
    ET.SubElement(note, "duration").text = str(int(duration * divisions))
    kind, dots, tuplet = _duration_type(duration)
    ET.SubElement(note, "type").text = kind
    for _ in range(dots):
        ET.SubElement(note, "dot")
    if tuplet:
        modification = ET.SubElement(note, "time-modification")
        ET.SubElement(modification, "actual-notes").text = "3"
        ET.SubElement(modification, "normal-notes").text = "2"
    if event is not None and not event["rest"] and event["string"] is not None:
        if event["tie"] is not None:
            ET.SubElement(note, "tie", type=event["tie"])
        notations = ET.SubElement(note, "notations")
        if event["tie"] is not None:
            ET.SubElement(notations, "tied", type=event["tie"])
        technical = ET.SubElement(notations, "technical")
        ET.SubElement(technical, "string").text = str(event["string"])
        ET.SubElement(technical, "fret").text = str(event["fret"])


def _duration_type(duration):
    mapping = {Fraction(4): "whole", Fraction(2): "half", Fraction(1): "quarter",
               Fraction(1, 2): "eighth", Fraction(1, 4): "16th", Fraction(1, 8): "32nd",
               Fraction(1, 16): "64th", Fraction(1, 32): "128th"}
    if duration in mapping:
        return mapping[duration], 0, False
    for base, name in ((Fraction(4), "whole"), (Fraction(2), "half"), (Fraction(1), "quarter"),
                       (Fraction(1, 2), "eighth"), (Fraction(1, 4), "16th"), (Fraction(1, 8), "32nd")):
        if duration == base * Fraction(3, 2):
            return name, 1, False
        triplet = base * Fraction(2, 3)
        if duration == triplet:
            return name, 0, True
    raise ValueError(f"音価{duration}はMusicXMLへ出力できません。")
