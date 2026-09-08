from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from chord_estimation import DEFAULT_SETTINGS, analysis_intervals, estimate_sections, sustained_sounds
from chord_progression import CHORD_DEFINITIONS, CHORD_SUFFIXES, ChordProgression, ScoreMeasure, parse_chord_name
from musicxml_parser import ScoreNote, parse_score


SAMPLE = Path(__file__).parent / 'fixtures' / 'chord_estimation.musicxml'


def note(pitch, start=0, duration=4, **extra):
    return ScoreNote(pitch, Fraction(start), Fraction(duration), 'P1', 'Piano', '1', '1', '1', **extra)


@pytest.fixture
def score():
    return parse_score(SAMPLE.read_bytes())


def test_common_chords_arpeggio_inversion_and_insufficient_evidence(score):
    results = estimate_sections(score.notes, score.measures, DEFAULT_SETTINGS)
    assert [r.candidates[0].symbol.name for r in results[:3]] == ['C', 'Am', 'G7/B']
    assert all(len(r.candidates) <= 3 for r in results)
    assert results[5].candidates == () and '音がない' in results[5].reason
    assert results[6].candidates == () and '不足' in results[6].reason
    assert dict(results[0].observed) == {'C4': 1, 'E4': 1, 'G4': 1, 'C5': 1}
    assert results[2].bass == 'B2'
    assert results == estimate_sections(score.notes, score.measures, DEFAULT_SETTINGS)
    assert CHORD_SUFFIXES == {kind: definition[0] for kind, definition in CHORD_DEFINITIONS.items()}


def test_split_measure_and_exact_time(score):
    settings = {**DEFAULT_SETTINGS, 'measure_id': 'measure-4', 'split_points': '2'}
    results = estimate_sections(score.notes, score.measures, settings)
    assert [(r.start, r.end, r.candidates[0].symbol.name) for r in results] == [(12, 14, 'C'), (14, 16, 'Am')]
    intervals = analysis_intervals(score.measures, {**settings, 'start_offset': '1/3', 'end_offset': '3', 'split_points': '2/3,2'})
    assert [(a, b) for _, a, b in intervals] == [(Fraction(37, 3), Fraction(38, 3)), (Fraction(38, 3), 14), (14, 15)]


def test_tied_notes_merge_without_modifying_source(score):
    tied = [n for n in score.notes if n.tie]
    assert [(n.start, n.duration, n.tie) for n in tied] == [(14, 2, 'start'), (16, 4, 'stop')]
    sounds = sustained_sounds(score.notes)
    assert any(s.pitch == 'E4' and s.start == 14 and s.end == 20 for s in sounds)
    result = estimate_sections(score.notes, score.measures, {**DEFAULT_SETTINGS, 'measure_id': 'measure-5'})[0]
    assert dict(result.observed)['E4'] == 4
    assert score == parse_score(SAMPLE.read_bytes())


def test_held_notes_are_clipped_and_other_parts_are_not_tied():
    notes = (note('C3', duration=1, tie='start'), note('C3', start=1, duration=2, tie='stop'),
             note('E3', start=1, duration=1), note('G3', start=1, duration=1))
    measures = (ScoreMeasure('m', '1', Fraction(0), Fraction(4)),)
    result = estimate_sections(notes, measures, {**DEFAULT_SETTINGS, 'measure_id': 'm', 'start_offset': '1', 'end_offset': '2'})[0]
    assert dict(result.observed) == {'C3': 1, 'E3': 1, 'G3': 1}
    assert result.candidates[0].symbol.name == 'C'
    different = (notes[0], replace(notes[1], part_id='P2'))
    assert len(sustained_sounds(different)) == 2


def test_filtering_and_duration_weighted_passing_note():
    notes = (note('C3'), note('E3'), note('G3'), note('Db4', duration=Fraction(1, 8)),
             replace(note('F#2'), part_id='P2', staff=None))
    measures = (ScoreMeasure('m', '1', Fraction(0), Fraction(4)),)
    result = estimate_sections(notes, measures, {**DEFAULT_SETTINGS, 'part_id': '"P1"', 'staff': '"1"'})[0]
    assert result.candidates[0].symbol.name == 'C'
    assert result.candidates[0].outside == ('Db4',)
    assert result.candidates[0].matched == ('C3', 'E3', 'G3')
    result = estimate_sections(notes, measures, {**DEFAULT_SETTINGS, 'part_id': '"P2"', 'staff': 'null'})[0]
    assert dict(result.observed) == {'F#2': 4} and not result.candidates


@pytest.mark.parametrize('settings', [
    {'split_points': '2'}, {'measure_id': 'missing'},
    {'measure_id': 'measure-1', 'start_offset': '2', 'end_offset': '1'},
    {'measure_id': 'measure-1', 'start_offset': '-1'},
    {'measure_id': 'measure-1', 'end_offset': '5'},
    {'measure_id': 'measure-1', 'split_points': '0'},
    {'measure_id': 'measure-1', 'split_points': '4'},
    {'measure_id': 'measure-1', 'split_points': '1/0'},
    {'measure_id': 'measure-1', 'start_offset': 'NaN'},
    {'part_id': '"missing"'},
])
def test_invalid_settings(score, settings):
    with pytest.raises(ValueError):
        estimate_sections(score.notes, score.measures, {**DEFAULT_SETTINGS, **settings})


def active(progression, position):
    return tuple(e.symbol.name for a, b, events in progression.segments if a <= position < b
                 for e in events if e.symbol.kind != 'unset')


def test_adoption_restores_existing_code_and_preserves_original():
    progression = ChordProgression((ScoreMeasure('m', '1', Fraction(0), Fraction(8)),))
    progression = progression.save('C', 'm', '0').save('G7', 'm', '4').save('Am', 'm', '6')
    before = progression.to_data()
    with pytest.raises(ValueError, match='チェック'):
        progression.apply_interval(parse_chord_name('Dm'), Fraction(1), Fraction(3))
    updated = progression.apply_interval(parse_chord_name('Dm'), Fraction(1), Fraction(3), replace_existing=True)
    assert active(updated, 2) == ('Dm',)
    for position in (0, Fraction(1, 2), 3, Fraction(7, 2), 4, 5, 6, 7):
        assert active(updated, position) == active(progression, position)
    assert progression.to_data() == before
    assert updated.original == progression.original
    assert next(e for e in updated.events if e.start == 1).source == 'estimated'
    assert next(e for e in updated.events if e.start == 3).restored
    assert ChordProgression.from_data(updated.to_data()) == updated


def test_adoption_restores_unset_and_manual_add_replaces_unset_boundary():
    empty = ChordProgression((ScoreMeasure('m', '1', Fraction(0), Fraction(4)),))
    updated = empty.apply_interval(parse_chord_name('C'), Fraction(1), Fraction(2))
    assert active(updated, 0) == active(updated, 2) == active(updated, 3) == ()
    assert active(updated, 1) == ('C',)
    updated = updated.save('G7', 'm', '2')
    assert not updated.conflicts
    assert active(updated, 2) == ('G7',)


def test_adoption_at_exact_boundary_and_to_song_end():
    original = ChordProgression((ScoreMeasure('m', '1', Fraction(0), Fraction(4)),)).save('N.C.', 'm', '0').save('G7', 'm', '2')
    updated = original.apply_interval(parse_chord_name('C'), Fraction(0), Fraction(2), replace_existing=True)
    assert updated.events[-1] == original.events[-1]
    updated = updated.apply_interval(parse_chord_name('Am'), Fraction(2), Fraction(4), replace_existing=True)
    assert all(e.start < 4 for e in updated.events)
    assert active(updated, 3) == ('Am',)


def test_conflicting_and_unsupported_states_are_restored():
    score = parse_score((SAMPLE.parent / 'chord_progression.musicxml').read_bytes())
    original = ChordProgression.from_import(score.measures, score.harmonies)
    updated = original.apply_interval(parse_chord_name('C'), Fraction(1), Fraction(3), replace_existing=True)
    assert active(updated, 3) == active(original, 3) == ('G7/B', 'Am')
    updated = original.apply_interval(parse_chord_name('C'), Fraction(9), Fraction(10), replace_existing=True)
    assert active(updated, 10) == active(original, 10)
    assert not updated.events[-1].symbol.supported
    assert updated.original == original.original
