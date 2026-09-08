"""Chord symbols and editable progression, independent of score notes."""

from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import re
from uuid import uuid4
from xml.etree import ElementTree as ET


CHORD_DEFINITIONS = {
    "major": ("", (0, 4, 7)), "minor": ("m", (0, 3, 7)),
    "dominant": ("7", (0, 4, 7, 10)), "major-seventh": ("M7", (0, 4, 7, 11)),
    "minor-seventh": ("m7", (0, 3, 7, 10)), "diminished": ("dim", (0, 3, 6)),
    "augmented": ("aug", (0, 4, 8)), "suspended-fourth": ("sus4", (0, 5, 7)),
}
CHORD_SUFFIXES = {kind: definition[0] for kind, definition in CHORD_DEFINITIONS.items()}
KIND_LABELS = {
    "major": "メジャー", "minor": "マイナー", "dominant": "セブンス",
    "major-seventh": "メジャーセブンス", "minor-seventh": "マイナーセブンス",
    "diminished": "ディミニッシュ", "augmented": "オーギュメント",
    "suspended-fourth": "サスペンデッド4", "none": "和音なし", "unset": "コード未設定",
}
SOURCE_LABELS = {"imported": "楽譜から読み込み", "manual": "手入力",
                 "estimated": "推定候補から採用", "boundary": "区間後の未設定を復元"}


@dataclass(frozen=True)
class ScoreMeasure:
    measure_id: str
    number: str
    start: Fraction
    duration: Fraction


@dataclass(frozen=True)
class ChordSymbol:
    name: str
    root: str | None
    kind: str | None
    bass: str | None
    supported: bool = True
    raw_xml: str = ""


@dataclass(frozen=True)
class ChordEvent:
    event_id: str
    symbol: ChordSymbol
    start: Fraction
    source: str = "imported"
    part_id: str | None = None
    restored: bool = False


def parse_chord_name(name):
    name = name.strip().replace("♯", "#").replace("♭", "b")
    if name in ("N.C.", "NC"):
        return ChordSymbol("N.C.", None, "none", None)
    match = re.fullmatch(r"([A-G][#b]?)(maj7|M7|m7|m|7|dim|aug|sus4)?(?:/([A-G][#b]?))?", name)
    if match is None:
        raise ValueError("対応するコード名を入力してください（例：C、Am、G7、CM7、Dm7/G、N.C.）。")
    root, suffix, bass = match.groups()
    suffix = "M7" if suffix == "maj7" else suffix or ""
    kind = next(kind for kind, value in CHORD_SUFFIXES.items() if value == suffix)
    return ChordSymbol(root + suffix + (f"/{bass}" if bass else ""), root, kind, bass)


def _xml_pitch(element, tag):
    parent = element.find(tag)
    if parent is None:
        return None
    step = (parent.findtext(f"{tag}-step") or "").strip()
    alter_text = (parent.findtext(f"{tag}-alter") or "0").strip()
    try:
        alter = Fraction(alter_text)
    except (ValueError, ZeroDivisionError):
        return f"{step or '?'}(alter={alter_text})"
    accidental = {Fraction(-2): "bb", Fraction(-1): "b", Fraction(0): "",
                  Fraction(1): "#", Fraction(2): "##"}.get(alter, f"(alter={alter_text})")
    return (step or "?") + accidental


def parse_harmony(element):
    """Preserve unsupported analysis instead of silently simplifying it."""
    raw = ET.tostring(element, encoding="unicode")
    root, bass = _xml_pitch(element, "root"), _xml_pitch(element, "bass")
    kind = (element.findtext("kind") or "").strip() or None
    plain = (len(element.findall("kind")) == 1
             and len(element.findall("root")) <= 1
             and element.find("degree") is None
             and element.find("numeral") is None and element.find("function") is None
             and ((element.findtext("inversion") or "0").strip() == "0" or bass is not None))
    valid_pitch = lambda value: value is not None and re.fullmatch(r"[A-G][#b]?", value)
    if plain and kind == "none":
        return ChordSymbol("N.C.", None, "none", None, raw_xml=raw)
    if plain and kind in CHORD_SUFFIXES and valid_pitch(root) and (bass is None or valid_pitch(bass)):
        name = root + CHORD_SUFFIXES[kind] + (f"/{bass}" if bass else "")
        return ChordSymbol(name, root, kind, bass, raw_xml=raw)
    kind_element = element.find("kind")
    label = kind_element.get("text") if kind_element is not None else None
    name = f"{root or '?'} [{label or kind or '種別不明'}]" + (f"/{bass}" if bass else "")
    return ChordSymbol(name, root, kind, bass, False, raw)


def _identity(event):
    symbol = event.symbol
    if symbol.supported:
        identity = (symbol.root, symbol.kind, symbol.bass)
    else:
        identity = (ET.canonicalize(symbol.raw_xml, strip_text=True),)
    return event.start, symbol.supported, identity


def _deduplicate(events):
    seen, result = set(), []
    for event in sorted(events, key=lambda event: event.start):
        key = _identity(event)
        if key not in seen:
            seen.add(key)
            result.append(event)
    return tuple(result)


@dataclass(frozen=True)
class ChordProgression:
    measures: tuple[ScoreMeasure, ...]
    original: tuple[ChordEvent, ...] = ()
    events: tuple[ChordEvent, ...] = ()

    @classmethod
    def from_import(cls, measures, events):
        instance = cls(tuple(measures), tuple(events), _deduplicate(events))
        for event in events:
            instance.measure_at(event.start)
        return instance

    @property
    def end(self):
        return self.measures[-1].start + self.measures[-1].duration

    def measure_at(self, start):
        for measure in self.measures:
            if measure.start <= start < measure.start + measure.duration:
                return measure
        raise ValueError("コードの位置が曲の範囲外です。")

    def position(self, measure_id, offset):
        measure = next((m for m in self.measures if m.measure_id == measure_id), None)
        if measure is None:
            raise ValueError("配置する小節を選択してください。")
        try:
            offset = Fraction(offset)
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            raise ValueError("小節内位置は数値または分数で入力してください（例：0、1.5、1/3）。") from exc
        if not 0 <= offset < measure.duration:
            raise ValueError(f"小節内位置は0以上、{measure.duration}未満にしてください。小節の終わりは次の小節の0を指定します。")
        return measure.start + offset

    def save(self, name, measure_id, offset, event_id=""):
        previous = next((event for event in self.events if event.event_id == event_id), None)
        if event_id and previous is None:
            raise ValueError("変更するコードが見つかりません。")
        symbol = previous.symbol if previous and name.strip() == previous.symbol.name else parse_chord_name(name)
        start = self.position(measure_id, offset)
        event = ChordEvent(event_id or f"manual-{uuid4().hex}", symbol, start, "manual")
        events = [e for e in self.events if e.event_id != event_id
                  and not (e.start == start and e.symbol.kind == "unset")] + [event]
        return replace(self, events=_deduplicate(events))

    def affected_segments(self, start, end):
        return tuple((max(a, start), min(b, end), events) for a, b, events in self.segments
                     if a < end and b > start)

    def apply_interval(self, symbol, start, end, *, replace_existing=False):
        if not 0 <= start < end <= self.end:
            raise ValueError("採用する区間が曲の範囲外です。")
        if not symbol.supported or symbol.kind not in CHORD_DEFINITIONS:
            raise ValueError("対応する推定候補を選んでください。")
        affected = self.affected_segments(start, end)
        if any(e.symbol.kind != "unset" for _, _, events in affected for e in events) and not replace_existing:
            raise ValueError("この区間の既存コードを確認し、置き換えのチェックを入れてください。")
        after = next((events for a, b, events in self.segments if a <= end < b), ())
        events = [event for event in self.events if not start <= event.start < end]
        events.append(ChordEvent(f"estimated-{uuid4().hex}", symbol, start, "estimated"))
        # Restore even an unset or conflicting state; a local adoption must not leak beyond end.
        if end < self.end and not any(event.start == end for event in self.events):
            if after:
                events.extend(replace(event, event_id=f"restore-{uuid4().hex}", start=end, restored=True)
                              for event in after)
            else:
                events.append(ChordEvent(f"boundary-{uuid4().hex}",
                                         ChordSymbol("コード未設定", None, "unset", None), end, "boundary"))
        return replace(self, events=_deduplicate(events))

    def delete(self, event_id):
        if not any(event.event_id == event_id for event in self.events):
            raise ValueError("削除するコードが見つかりません。")
        return replace(self, events=tuple(e for e in self.events if e.event_id != event_id))

    @property
    def conflicts(self):
        return {start: events for start, _, events in self.segments if len(events) > 1}

    @property
    def segments(self):
        groups = {Fraction(0): []}
        for event in self.events:
            groups.setdefault(event.start, []).append(event)
        starts = sorted(groups)
        return tuple((start, starts[i + 1] if i + 1 < len(starts) else self.end,
                      tuple(groups[start])) for i, start in enumerate(starts))

    def to_data(self):
        def event_data(event):
            return {**asdict(event), "start": str(event.start)}
        return {
            "measures": [{**asdict(m), "start": str(m.start), "duration": str(m.duration)} for m in self.measures],
            "original": [event_data(e) for e in self.original],
            "events": [event_data(e) for e in self.events],
        }

    @classmethod
    def from_data(cls, data):
        def event_from_data(event):
            return ChordEvent(**{**event, "start": Fraction(event["start"]),
                                 "symbol": ChordSymbol(**event["symbol"])})
        return cls(tuple(ScoreMeasure(**{**m, "start": Fraction(m["start"]),
                                        "duration": Fraction(m["duration"])}) for m in data["measures"]),
                   tuple(event_from_data(e) for e in data["original"]),
                   tuple(event_from_data(e) for e in data["events"]))
