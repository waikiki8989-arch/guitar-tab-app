from dataclasses import replace
from fractions import Fraction
from itertools import product
from pathlib import Path

import pytest

from fingering import TUNING, apply_action, best_path, movement, pitch_info, positions, reconcile
from melody import MelodySelection
from musicxml_parser import parse_score


def selection():
    notes = parse_score((Path(__file__).parent / 'fixtures/guitar_fingering.musicxml').read_bytes()).notes
    return MelodySelection(notes).select(n.note_id for n in notes).confirm()


def test_known_pitches_and_all_candidates_are_exact_and_within_limit():
    assert positions(40) == ((6, 0),)
    assert (1, 0) in positions(64)
    assert len(positions(64)) == 6
    assert positions(64, 0) == ((1, 0),)
    for limit in (0, 5, 12, 24):
        for midi in range(20, 100):
            actual = set(positions(midi, limit))
            expected = {(s, fret) for s, opened in TUNING.items() for fret in range(limit + 1) if opened + fret == midi}
            assert actual == expected
    assert not positions(Fraction(129, 2))
    assert pitch_info('Bb3', 1) == ('Bb4', Fraction(70))


def test_dp_matches_exhaustive_optimum_and_is_deterministic():
    options = [positions(midi, 12) for midi in (64, 67, 65, 59)]
    def score(path):
        return sum(movement(a, b) for a, b in zip(path, path[1:])), sum(p[1] for p in path)
    result = best_path(options)
    assert score(result) == min(score(path) for path in product(*options))
    assert best_path(options) == result
    assert movement((1, 0), (1, 12)) == 0
    assert movement((1, 2), (2, 8)) == 13


def test_original_timing_gaps_ties_and_unplayable_notes_are_preserved():
    melody = selection()
    before = melody.to_data()
    state, rows, error = reconcile(melody)
    assert error is None
    assert [r['note'].start for r in rows] == [0, 1, 3, 4, 5, 6]
    assert rows[0]['position'] == (6, 0)
    assert rows[2]['position'] == rows[3]['position']
    assert rows[-1]['position'] is None and rows[-2]['position'] is None
    assert all('音域外' in r['reason'] for r in rows[-2:])
    assert melody.to_data() == before
    assert reconcile(melody, state)[0] == state


def test_manual_choice_lock_recalculation_and_tie_group_changes():
    melody = selection()
    state, rows, _ = reconcile(melody)
    note_id = rows[2]['note'].note_id
    state = apply_action(melody, state, 'choose', {'note_id': note_id, 'position': '2:8', 'lock': 'yes'})
    assert state['choices'][note_id] == [2, 8]
    state = apply_action(melody, state, 'calculate', {'max_fret': '12', 'octave': '0'})
    for row in reconcile(melody, state)[1][2:4]:
        assert row['position'] == (2, 8) and row['locked']
    state = apply_action(melody, state, 'unlock', {'note_id': rows[3]['note'].note_id})
    assert not state['locks']
    assert all(not r['locked'] for r in reconcile(melody, state)[1])


@pytest.mark.parametrize('limit,octave', [(5, 0), (24, 1), (24, -1)])
def test_invalid_lock_is_not_silently_replaced_and_can_be_reselected(limit, octave):
    melody = selection()
    state, rows, _ = reconcile(melody)
    note_id = rows[1]['note'].note_id
    state = apply_action(melody, state, 'choose', {'note_id': note_id, 'position': '3:9', 'lock': 'yes'})
    before = state.copy()
    changed = apply_action(melody, state, 'calculate', {'max_fret': str(limit), 'octave': str(octave)})
    row = reconcile(melody, changed)[1][1]
    assert row['position'] is None and row['locked'] and '再選択' in row['reason']
    assert changed['locks'] == state['locks']
    assert state == before
    p = row['candidates'][0]
    changed = apply_action(melody, changed, 'choose', {'note_id': note_id, 'position': f'{p[0]}:{p[1]}', 'lock': 'yes'})
    row = reconcile(melody, changed)[1][1]
    assert row['position'] == p and not row['reason']


def test_octave_changes_candidates_without_changing_original_notes():
    melody = selection()
    state = apply_action(melody, None, 'calculate', {'max_fret': '24', 'octave': '-1'})
    rows = reconcile(melody, state)[1]
    assert rows[-2]['guitar_pitch'] == 'F#5' and rows[-2]['position'] is not None
    assert rows[1]['candidates'] == positions(52)
    assert rows[-2]['note'].pitch == 'F#6'


def test_selection_changes_prune_removed_notes_and_compute_added_notes_after_confirmation():
    melody = selection()
    state, rows, _ = reconcile(melody)
    note_id = rows[1]['note'].note_id
    state = apply_action(melody, state, 'choose', {'note_id': note_id, 'position': '1:0', 'lock': 'yes'})
    changed = melody.select(melody.selected_ids - {note_id})
    state, rows, _ = reconcile(changed, state)
    assert not rows and note_id not in state['choices'] and note_id not in state['locks']
    changed = changed.select(melody.selected_ids).confirm()
    state, rows, _ = reconcile(changed, state)
    assert note_id in state['choices'] and len(rows) == 6


def test_unconfirmed_and_overlapping_melodies_cannot_be_converted():
    melody = selection()
    with pytest.raises(ValueError, match='確定'):
        apply_action(replace(melody, confirmed=False), None, 'calculate', {'max_fret': '24', 'octave': '0'})
    notes = (replace(melody.notes[0], duration=Fraction(2)), *melody.notes[1:])
    _, rows, error = reconcile(replace(melody, notes=notes))
    assert not rows and '重なって' in error


@pytest.mark.parametrize('action,form', [
    ('calculate', {'max_fret': '25', 'octave': '0'}),
    ('calculate', {'max_fret': '-1', 'octave': '0'}),
    ('calculate', {'max_fret': '1.5', 'octave': '0'}),
    ('calculate', {'max_fret': '12', 'octave': '2'}),
    ('calculate', {'max_fret': '', 'octave': '0'}),
    ('choose', {'note_id': 'missing', 'position': '1:0'}),
    ('choose', {'note_id': 'note-1', 'position': 'wrong'}),
    ('unknown', {}),
])
def test_invalid_input_does_not_mutate_state(action, form):
    melody = selection()
    state, _, _ = reconcile(melody)
    from copy import deepcopy
    before = deepcopy(state)
    with pytest.raises(ValueError):
        apply_action(melody, state, action, form)
    assert state == before


def test_recalculation_releases_only_unlocked_manual_choices():
    source = selection()
    melody = source.select([source.notes[1].note_id]).confirm()
    note_id = melody.selected_notes[0].note_id
    state = apply_action(melody, None, 'choose', {'note_id': note_id, 'position': '3:9'})
    assert state['choices'][note_id] == [3, 9]
    state = apply_action(melody, state, 'calculate', {'max_fret': '24', 'octave': '0'})
    assert state['choices'][note_id] == [1, 0]


def test_future_locked_position_constrains_global_path():
    options = [positions(65, 12), positions(66, 12), ((3, 12),)]
    result = best_path(options)
    assert result[-1] == (3, 12)
    assert result[0] == (3, 10) and result[1] == (3, 11)
