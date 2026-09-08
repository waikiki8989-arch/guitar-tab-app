from dataclasses import dataclass, replace
from fractions import Fraction

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from music21.musicxml.xmlToM21 import MeasureParser

from chord_progression import ChordEvent, ScoreMeasure, parse_harmony


MAX_XML_BYTES = 2 * 1024 * 1024


class MusicXMLParseError(ValueError):
    """An invalid or unsupported score that can be explained to the user."""


@dataclass(frozen=True)
class ScoreNote:
    pitch: str
    start: Fraction
    duration: Fraction
    part_id: str
    part_name: str | None
    staff: str | None
    voice: str | None
    measure: str
    note_id: str = ""


@dataclass(frozen=True)
class ParsedScore:
    notes: tuple[ScoreNote, ...]
    measures: tuple[ScoreMeasure, ...]
    harmonies: tuple[ChordEvent, ...]


def _text(element, path):
    value = element.findtext(path)
    return value.strip() if value and value.strip() else None


def _positive(value, label):
    try:
        result = Fraction(value)
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        raise MusicXMLParseError(f"{label}が不正です。") from exc
    if result <= 0:
        raise MusicXMLParseError(f"{label}は0より大きい値が必要です。")
    return result


def _parse_measure(measure, divisions, part_id, part_name):
    """Keep exact XML timing and original metadata; let music21 decode notes."""
    number = measure.get("number")
    if not number:
        raise MusicXMLParseError("小節番号がありません。")
    cursor = Fraction(0)
    extent = Fraction(0)
    previous = None
    notes = []
    harmonies = []
    parser = MeasureParser()
    for element in measure:
        if element.tag == "attributes":
            if element.find("transpose") is not None:
                raise MusicXMLParseError("移調楽器の楽譜は未対応です。ピアノ譜を使用してください。")
            value = _text(element, "divisions")
            if value is not None:
                divisions = _positive(value, "divisions")
        elif element.tag == "harmony":
            offset = _text(element, "offset")
            try:
                shift = Fraction(offset) / divisions if offset is not None and divisions is not None else Fraction(0)
            except (ValueError, ZeroDivisionError) as exc:
                raise MusicXMLParseError("コード記号のoffsetが不正です。") from exc
            if offset is not None and divisions is None:
                raise MusicXMLParseError("コード記号の時間単位（divisions）がありません。")
            harmonies.append(ChordEvent("", parse_harmony(element), cursor + shift, part_id=part_id))
        elif element.tag in ("backup", "forward"):
            if divisions is None:
                raise MusicXMLParseError("音の時間単位（divisions）がありません。")
            shift = _positive(_text(element, "duration"), "移動時間") / divisions
            cursor += -shift if element.tag == "backup" else shift
            if cursor < 0:
                raise MusicXMLParseError("小節の先頭より前に戻るbackupがあります。")
            extent = max(extent, cursor)
            previous = None
        elif element.tag == "note":
            if divisions is None:
                raise MusicXMLParseError("音の時間単位（divisions）がありません。")
            if element.find("grace") is not None or element.find("unpitched") is not None:
                raise MusicXMLParseError("装飾音・音高のない打楽器音符は未対応です。")
            duration = _positive(_text(element, "duration"), "音の長さ") / divisions
            staff, voice = _text(element, "staff"), _text(element, "voice")
            is_rest = element.find("rest") is not None
            is_chord = element.find("chord") is not None
            if is_chord:
                if previous is None or is_rest:
                    raise MusicXMLParseError("和音の先頭となる音符がありません。")
                onset, base_duration, base_voice = previous
                if duration > base_duration or (voice is not None and base_voice is not None and voice != base_voice):
                    raise MusicXMLParseError("和音の長さまたは声部が不正です。")
            else:
                onset = cursor
                cursor += duration
                previous = None if is_rest else (onset, duration, voice)
            extent = max(extent, onset + duration)
            if is_rest:
                continue
            if _text(element, "pitch/step") not in "A B C D E F G".split() or _text(element, "pitch/octave") is None:
                raise MusicXMLParseError("音名またはオクターブがありません。")
            parser.divisions = float(divisions)
            try:
                parsed = parser.xmlToSimpleNote(element)
            except Exception as exc:
                # The third-party importer raises several exception types for malformed notes.
                raise MusicXMLParseError("音符の音高または記譜情報を読み取れません。") from exc
            pitch = parsed.pitch.nameWithOctave.replace("-", "b")
            notes.append(ScoreNote(pitch, onset, duration, part_id, part_name,
                                   staff, voice, number))
    if extent == 0:
        raise MusicXMLParseError("長さを判定できない空の小節があります。休符を含む楽譜を使用してください。")
    return notes, extent, divisions, harmonies


def parse_score(data: bytes) -> ParsedScore:
    """Read uncompressed score-partwise XML without writing uploaded data to disk.

    Missing optional metadata stays None. Rests advance time but are not rows.
    Measures are aligned across parts by order, using their maximum extent.
    Repeats are not unfolded; tied notes remain separate written notes.
    """
    if not data.strip():
        raise MusicXMLParseError("ファイルが空です。")
    if len(data) > MAX_XML_BYTES:
        raise MusicXMLParseError("ファイルは2 MiB以下にしてください。")
    if data.startswith(b"PK"):
        raise MusicXMLParseError("圧縮MusicXML（.mxl）は未対応です。")
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, DefusedXmlException, ValueError) as exc:
        raise MusicXMLParseError("安全に読み取れるMusicXMLファイルではありません。") from exc

    # Also accept namespaced MusicXML while preserving the original attribute values.
    for element in root.iter():
        if element.tag.startswith("{"):
            element.tag = element.tag.split("}", 1)[1]
    if root.tag != "score-partwise":
        raise MusicXMLParseError("score-partwise形式のMusicXMLを使用してください。")
    names = {}
    for part in root.findall("part-list/score-part"):
        part_id = part.get("id")
        if not part_id or part_id in names:
            raise MusicXMLParseError("パートIDがないか、重複しています。")
        names[part_id] = _text(part, "part-name")
    parts = root.findall("part")
    if not parts:
        raise MusicXMLParseError("楽譜にパートがありません。")

    parsed_parts = []
    seen = set()
    for part in parts:
        part_id = part.get("id")
        if part_id not in names or part_id in seen:
            raise MusicXMLParseError("パートIDが未定義か、重複しています。")
        seen.add(part_id)
        measures = part.findall("measure")
        if not measures:
            raise MusicXMLParseError("楽譜に小節がありません。")
        divisions = None
        parsed_measures = []
        for measure in measures:
            notes, extent, divisions, harmonies = _parse_measure(measure, divisions, part_id, names[part_id])
            parsed_measures.append((notes, extent, harmonies, measure.get("number")))
        parsed_parts.append(parsed_measures)
    if len({len(part) for part in parsed_parts}) != 1:
        raise MusicXMLParseError("パート間の小節数が一致しません。")

    result = []
    result_harmonies = []
    result_measures = []
    start = Fraction(0)
    for index, measure_group in enumerate(zip(*parsed_parts), 1):
        duration = max(extent for _, extent, _, _ in measure_group)
        result_measures.append(ScoreMeasure(f"measure-{index}", measure_group[0][3], start, duration))
        for notes, _, harmonies, _ in measure_group:
            for note in notes:
                result.append(ScoreNote(note.pitch, start + note.start, note.duration,
                                        note.part_id, note.part_name, note.staff,
                                        note.voice, note.measure))
            result_harmonies.extend(replace(event, start=start + event.start) for event in harmonies)
        start += duration
    if any(not 0 <= event.start < start for event in result_harmonies):
        raise MusicXMLParseError("コード記号の位置が曲の範囲外です。")
    return ParsedScore(
        tuple(replace(note, note_id=f"note-{index}")
              for index, note in enumerate(sorted(result, key=lambda note: note.start), 1)),
        tuple(result_measures),
        tuple(replace(event, event_id=f"chord-{index}")
              for index, event in enumerate(sorted(result_harmonies, key=lambda event: event.start), 1)),
    )


def parse_musicxml(data: bytes) -> list[ScoreNote]:
    """Compatibility entry point for consumers that only need the original notes."""
    return list(parse_score(data).notes)
