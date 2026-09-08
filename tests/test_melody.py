from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from pathlib import Path

import pytest

from melody import MelodySelection, filter_notes, filter_options
from musicxml_parser import parse_musicxml


@pytest.fixture
def notes():
    return tuple(parse_musicxml((Path(__file__).parent / 'fixtures' / 'melody_selection.musicxml').read_bytes()))


def test_note_ids_are_unique_and_repeatable(notes):
    assert len({note.note_id for note in notes}) == len(notes) == 8
    again = parse_musicxml((Path(__file__).parent / 'fixtures' / 'melody_selection.musicxml').read_bytes())
    assert list(notes) == again


def test_filters_and_missing_metadata(notes):
    assert [n.pitch for n in filter_notes(notes, {'part_id': '"P1"', 'staff': '"1"', 'voice': '"1"'})] == ['C4', 'E4', 'C5', 'E5']
    assert [n.pitch for n in filter_notes(notes, {'staff': 'null', 'voice': 'null'})] == ['G4']
    assert filter_notes(notes, {}) == notes
    assert ('null', None) in filter_options(notes, 'staff')
    assert filter_notes(notes, {'part_id': '"P2"', 'staff': '"2"'}) == ()
    with pytest.raises(ValueError, match='絞り込み'):
        filter_notes(notes, {'part_id': '"Unknown"'})


def test_selection_across_voices_keeps_source_and_gaps(notes):
    original = tuple(notes)
    melody = MelodySelection(notes)
    upper = filter_notes(notes, {'voice': '"1"'})
    melody = melody.select(n.note_id for n in upper)
    assert list(melody.conflicts) == [Fraction(4)]
    melody = melody.select(n.note_id for n in melody.selected_notes if n.pitch != 'E5')
    lower = filter_notes(notes, {'voice': '"4"'})
    melody = melody.update_visible([n.note_id for n in lower], [n.note_id for n in lower])
    other = filter_notes(notes, {'part_id': '"P2"'})
    melody = melody.update_visible([n.note_id for n in other], [n.note_id for n in other]).confirm()
    assert [(n.pitch, n.start, n.duration) for n in melody.get_confirmed_melody()] == [
        ('C4', 0, 1), ('E4', 2, 2), ('C5', 4, 2), ('D4', 6, 2), ('G4', 8, 4),
    ]
    assert notes == original
    with pytest.raises(FrozenInstanceError):
        notes[0].start = Fraction(99)


def test_confirmation_and_reediting(notes):
    melody = MelodySelection(notes)
    with pytest.raises(ValueError, match='選択してください'):
        melody.confirm()
    with pytest.raises(ValueError, match='複数'):
        melody.select(n.note_id for n in notes).confirm()
    melody = melody.select([notes[0].note_id]).confirm()
    assert melody.select(melody.selected_ids).confirmed
    melody = melody.select([])
    assert not melody.confirmed
    with pytest.raises(ValueError, match='確定してください'):
        melody.get_confirmed_melody()


def test_roundtrip_retains_exact_time_and_confirmation():
    notes = tuple(parse_musicxml((Path(__file__).parent / 'fixtures' / 'simple_piano.musicxml').read_bytes()))
    melody = MelodySelection(notes).select([notes[-1].note_id]).confirm()
    restored = MelodySelection.from_data(melody.to_data())
    assert restored == melody
    assert restored.get_confirmed_melody()[0].start == Fraction(17, 3)


def test_invalid_ids_and_same_pitch_chord(notes):
    with pytest.raises(ValueError, match='楽譜にありません'):
        MelodySelection(notes).select(['unknown'])
    with pytest.raises(ValueError, match='表示中'):
        MelodySelection(notes).update_visible([notes[0].note_id], [notes[1].note_id])
    with pytest.raises(ValueError, match='重複'):
        MelodySelection((notes[0], notes[0]))
    unison = (notes[0], replace(notes[0], note_id='other-string'))
    assert len(MelodySelection(unison).select(n.note_id for n in unison).conflicts[0]) == 2
