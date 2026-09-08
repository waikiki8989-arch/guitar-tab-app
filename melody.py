"""Melody selection is independent of the immutable imported score."""

from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import json

from musicxml_parser import ScoreNote


FILTER_FIELDS = ("part_id", "staff", "voice")


def filter_options(notes, field):
    values = dict.fromkeys(getattr(note, field) for note in notes)
    return [(json.dumps(value, ensure_ascii=False), value) for value in values]


def filter_notes(notes, filters):
    """An empty filter means all; JSON null means explicitly missing metadata."""
    for field in FILTER_FIELDS:
        value = filters.get(field, "")
        if value and value not in {key for key, _ in filter_options(notes, field)}:
            raise ValueError("絞り込み条件が不正です。条件を選び直してください。")
    return tuple(note for note in notes if all(
        not filters.get(field) or getattr(note, field) == json.loads(filters[field])
        for field in FILTER_FIELDS
    ))


@dataclass(frozen=True)
class MelodySelection:
    notes: tuple[ScoreNote, ...]
    selected_ids: frozenset[str] = frozenset()
    confirmed: bool = False

    def __post_init__(self):
        ids = [note.note_id for note in self.notes]
        if any(not note_id for note_id in ids) or len(ids) != len(set(ids)):
            raise ValueError("音符IDがないか重複しています。")
        if not self.selected_ids.issubset(ids):
            raise ValueError("選択された音符が楽譜にありません。")

    @property
    def selected_notes(self):
        return tuple(sorted((note for note in self.notes if note.note_id in self.selected_ids),
                            key=lambda note: note.start))

    @property
    def conflicts(self):
        groups = {}
        for note in self.selected_notes:
            groups.setdefault(note.start, []).append(note)
        return {start: notes for start, notes in groups.items() if len(notes) > 1}

    def select(self, ids):
        ids = frozenset(ids)
        return replace(self, selected_ids=ids,
                       confirmed=self.confirmed and ids == self.selected_ids)

    def update_visible(self, visible_ids, checked_ids):
        visible_ids, checked_ids = set(visible_ids), set(checked_ids)
        if not checked_ids.issubset(visible_ids):
            raise ValueError("表示中の候補にない音符が指定されています。")
        return self.select((self.selected_ids - visible_ids) | checked_ids)

    def confirm(self):
        if not self.selected_ids:
            raise ValueError("メロディーに使う音符を選択してください。")
        if self.conflicts:
            raise ValueError("同じ開始位置の音が複数選ばれています。各位置で1音に絞ってください。")
        return replace(self, confirmed=True)

    def get_confirmed_melody(self):
        """Downstream notation/TAB processing receives the original exact note data."""
        if not self.confirmed:
            raise ValueError("メロディーを確定してください。")
        return self.confirm().selected_notes

    def to_data(self):
        return {
            "notes": [{**asdict(note), "start": str(note.start), "duration": str(note.duration)}
                      for note in self.notes],
            "selected_ids": sorted(self.selected_ids),
            "confirmed": self.confirmed,
        }

    @classmethod
    def from_data(cls, data):
        notes = tuple(ScoreNote(**{**note, "start": Fraction(note["start"]),
                                  "duration": Fraction(note["duration"])})
                      for note in data["notes"])
        return cls(notes, frozenset(data["selected_ids"]), data["confirmed"])
