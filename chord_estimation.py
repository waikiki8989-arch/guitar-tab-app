"""Deterministic, duration-weighted chord suggestions; never edits the score."""

from dataclasses import dataclass, replace
from fractions import Fraction
import re

from music21.pitch import Pitch

from chord_progression import CHORD_DEFINITIONS, ChordSymbol, ScoreMeasure, parse_chord_name
from melody import filter_notes


DEFAULT_SETTINGS = {"part_id": "", "staff": "", "measure_id": "",
                    "start_offset": "", "end_offset": "", "split_points": ""}
PITCH_NAMES = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")


@dataclass(frozen=True)
class Sound:
    pitch: str
    midi: Fraction
    start: Fraction
    end: Fraction


@dataclass(frozen=True)
class ChordCandidate:
    symbol: ChordSymbol
    matched: tuple[str, ...]
    outside: tuple[str, ...]
    missing: tuple[str, ...]
    score: Fraction


@dataclass(frozen=True)
class EstimateResult:
    measure: ScoreMeasure
    start: Fraction
    end: Fraction
    observed: tuple[tuple[str, Fraction], ...]
    bass: str | None
    candidates: tuple[ChordCandidate, ...]
    reason: str = ""


def sustained_sounds(notes):
    """Join contiguous tied notes only within the same part/staff/voice/pitch."""
    sounds, open_ties = [], {}
    for note in sorted(notes, key=lambda note: note.start):
        pitch = Pitch(note.pitch.replace("b", "-"))
        sound = Sound(note.pitch, Fraction(str(pitch.ps)), note.start, note.start + note.duration)
        key = (note.part_id, note.staff, note.voice, note.pitch)
        pending = open_ties.setdefault(key, [])
        previous = next((index for index in pending if sounds[index].end == note.start), None)
        if previous is not None and note.tie in ("stop", "continue"):
            sounds[previous] = replace(sounds[previous], end=sound.end)
            if note.tie == "stop":
                pending.remove(previous)
        else:
            sounds.append(sound)
            if note.tie in ("start", "continue"):
                pending.append(len(sounds) - 1)
    return tuple(sounds)


def _number(text):
    try:
        return Fraction(text)
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        raise ValueError("区間の位置は数値または分数で入力してください（例：2、1/3）。") from exc


def analysis_intervals(measures, settings):
    measure_id = settings.get("measure_id", "")
    selected = [measure for measure in measures if not measure_id or measure.measure_id == measure_id]
    if not selected:
        raise ValueError("解析する小節を選択してください。")
    if not measure_id and any(settings.get(field) for field in ("start_offset", "end_offset", "split_points")):
        raise ValueError("区間や分割位置を指定するときは、対象の小節を1つ選んでください。")
    result = []
    for measure in selected:
        start = _number(settings["start_offset"]) if settings.get("start_offset") else Fraction(0)
        end = _number(settings["end_offset"]) if settings.get("end_offset") else measure.duration
        if not 0 <= start < end <= measure.duration:
            raise ValueError(f"解析区間は小節内の0〜{measure.duration}で、開始より終了を後にしてください。")
        text = settings.get("split_points", "").strip()
        cuts = sorted(set(_number(value.strip()) for value in text.replace("、", ",").split(","))) if text else []
        if len(cuts) > 32 or any(not start < cut < end for cut in cuts):
            raise ValueError("分割位置は区間の開始と終了の間に、32個以下で指定してください。")
        points = [start, *cuts, end]
        result.extend((measure, measure.start + a, measure.start + b) for a, b in zip(points, points[1:]))
    return tuple(result)


def estimate_interval(sounds, measure, start, end):
    sounding = [sound for sound in sounds if sound.start < end and sound.end > start]
    weights, pitches = {}, {}
    for sound in sounding:
        weights[sound.pitch] = weights.get(sound.pitch, Fraction(0)) + min(end, sound.end) - max(start, sound.start)
        pitches[sound.pitch] = sound.midi
    observed = tuple(sorted(weights.items(), key=lambda pair: (pitches[pair[0]], pair[0])))
    if not sounding:
        return EstimateResult(measure, start, end, (), None, (), "音がない区間です。候補はありません。")
    lowest = min(sounding, key=lambda sound: (sound.midi, sound.pitch))
    if any(sound.midi.denominator != 1 for sound in sounding):
        return EstimateResult(measure, start, end, observed, lowest.pitch, (), "微分音を含むため、この区間の推定は未対応です。")
    pc_weights, spellings = {}, {}
    for name, duration in observed:
        pc = int(pitches[name]) % 12
        pc_weights[pc] = pc_weights.get(pc, Fraction(0)) + duration
        spelling = re.sub(r"-?\d+$", "", name)
        if re.fullmatch(r"[A-G][#b]?", spelling):
            spellings.setdefault(pc, spelling)
    if len(pc_weights) < 3:
        return EstimateResult(measure, start, end, observed, lowest.pitch, (), "異なる高さの音が3種類未満のため、根拠が不足しています（オクターブ違いは同じ種類）。")
    total = sum(pc_weights.values())
    bass_pc = int(lowest.midi) % 12
    candidates = []
    for root in range(12):
        for kind, (suffix, intervals) in CHORD_DEFINITIONS.items():
            tones = {(root + interval) % 12 for interval in intervals}
            matched_pcs = tones & pc_weights.keys()
            matched_weight = sum(pc_weights[pc] for pc in matched_pcs)
            if len(matched_pcs) < 3 or matched_weight * 3 < total * 2:
                continue
            missing = tones - pc_weights.keys()
            # Exact arithmetic and stable iteration break ties reproducibly; this is not a probability.
            score = (2 * matched_weight - total) / total - Fraction(len(missing), 5)
            if root == bass_pc:
                score += Fraction(1, 20)
            name = spellings.get(root, PITCH_NAMES[root]) + suffix
            if root != bass_pc:
                name += "/" + spellings.get(bass_pc, PITCH_NAMES[bass_pc])
            candidates.append(ChordCandidate(
                parse_chord_name(name),
                tuple(name for name, _ in observed if int(pitches[name]) % 12 in tones),
                tuple(name for name, _ in observed if int(pitches[name]) % 12 not in tones),
                tuple(PITCH_NAMES[pc] for pc in sorted(missing)), score,
            ))
    candidates.sort(key=lambda candidate: -candidate.score)
    reason = "構成音の一致が不足しています。区間を短くするか、コードを手入力してください。" if not candidates else ""
    return EstimateResult(measure, start, end, observed, lowest.pitch, tuple(candidates[:3]), reason)


def estimate_sections(notes, measures, settings):
    filtered = filter_notes(notes, {field: settings.get(field, "") for field in ("part_id", "staff")})
    sounds = sustained_sounds(filtered)
    return tuple(estimate_interval(sounds, measure, start, end)
                 for measure, start, end in analysis_intervals(measures, settings))
