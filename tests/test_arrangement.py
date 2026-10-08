from copy import deepcopy
from fractions import Fraction
from xml.etree import ElementTree as ET

import pytest

import arrangement as arr
from arrangement_score import build_arrangement_preview
from fingering import apply_action, reconcile
from musicxml_parser import parse_score
from test_tab_score import setup


def prepared():
    score, selection, fingering, rows, progression = setup()
    progression = progression.save('C/E', 'measure-1', '0')
    return score, selection, fingering, rows, progression, arr.create(selection, fingering, progression, rows)


def action(state, selection, fingering, progression, rows, name, **form):
    return arr.act(state, selection, fingering, progression, rows, name, form)


def test_candidates_respect_slash_bass_strings_and_leave_source_intact():
    _, selection, fingering, rows, progression, state = prepared()
    before = deepcopy((selection.to_data(), fingering, progression.to_data(), state))
    candidate = arr.generate(state, arr.melody_notes(rows), progression, 24,
                             {'measure_id': 'measure-1', 'pattern': 'full'})
    notes = [arr.ArrangementNote.from_data(n) for n in candidate['notes']]
    assert len(notes) == 3
    assert notes[0].role == 'bass' and notes[0].midi % 12 == 4  # C/E
    assert len({n.string for n in notes + list(arr.melody_notes(rows)[:1])}) == 4
    assert all(n.start == 0 and n.duration > 0 for n in notes)
    assert candidate == arr.generate(state, arr.melody_notes(rows), progression, 24,
                                     {'measure_id': 'measure-1', 'pattern': 'full'})
    assert before == (selection.to_data(), fingering, progression.to_data(), state)


def test_save_edit_delete_undo_redo_are_atomic_and_ids_are_never_reused():
    _, s, f, rows, p, state = prepared()
    state = action(state, s, f, p, rows, 'save', role='bass', measure_id='measure-1',
                   offset='0', duration='1/3', string='6', fret='0')
    first = deepcopy(state['notes'])
    state = action(state, s, f, p, rows, 'save', arr_note_id='arr-1', role='accompaniment',
                   measure_id='measure-1', offset='1/3', duration='2/3', string='5', fret='3')
    assert state['notes'][0]['midi'] == '48'
    edited = deepcopy(state['notes'])
    state = action(state, s, f, p, rows, 'undo')
    assert state['notes'] == first
    state = action(state, s, f, p, rows, 'redo')
    assert state['notes'] == edited
    state = action(state, s, f, p, rows, 'delete', arr_note_id='arr-1')
    assert not state['notes']
    state = action(state, s, f, p, rows, 'undo')
    assert state['notes'] == edited
    state = action(state, s, f, p, rows, 'save', role='bass', measure_id='measure-1',
                   offset='0', duration='1', string='6', fret='0')
    assert state['notes'][-1]['note_id'] == 'arr-2' and not state['redo']


@pytest.mark.parametrize('fields', [dict(duration='0'), dict(duration='100'), dict(duration='1/0'),
    dict(string='7'), dict(fret='25'), dict(fret='1.5'), dict(role='melody'), dict(offset='-1'),
    dict(arr_note_id='missing'), dict(measure_id='missing')])
def test_invalid_manual_edits_do_not_modify_state(fields):
    _, s, f, rows, p, state = prepared()
    before = deepcopy(state)
    form = dict(role='bass', measure_id='measure-1', offset='0', duration='1', string='6', fret='0')
    with pytest.raises(ValueError):
        action(state, s, f, p, rows, 'save', **(form | fields))
    assert state == before


def test_warnings_include_sustained_string_collisions_span_and_chord_changes():
    _, s, f, rows, p, state = prepared()
    melody = arr.melody_notes(rows)
    # Two sustained notes on one string and widely separated fretted notes.
    state['notes'] = [arr.positioned_note('a', 'bass', 6, 1, Fraction(0), Fraction(4)).to_data(),
                      arr.positioned_note('b', 'bass', 6, 0, Fraction(1), Fraction(1)).to_data(),
                      arr.positioned_note('c', 'accompaniment', 5, 12, Fraction(0), Fraction(1)).to_data()]
    warnings, spans = arr.validate(state, melody, p, 5)
    reasons = ' '.join(w['reason'] for w in warnings)
    assert '6弦で発音時間が重なって' in reasons
    assert '広がり11' in reasons and '上限5' in reasons and '構成音' in reasons
    assert any(w['start'] == '1' and w['end'] == '2' and w['ids'] == ['a', 'b'] for w in warnings)
    assert spans[0]['span'] == 11


def test_proposal_adoption_skip_history_and_upstream_changes():
    _, s, f, rows, p, state = prepared()
    candidate = action(state, s, f, p, rows, 'generate', measure_id='measure-1', pattern='full')
    assert candidate['candidate']['notes'] and not candidate['notes']
    assert not action(candidate, s, f, p, rows, 'skip')['candidate']
    adopted = action(candidate, s, f, p, rows, 'adopt')
    assert len(adopted['notes']) == 3 and adopted['undo'] == [[]]
    assert not action(adopted, s, f, p, rows, 'undo')['notes']
    changed = p.save('Am', 'measure-1', '0')
    synced = arr.sync(adopted, s, f, changed, rows)
    assert synced['notes'] == adopted['notes'] and not synced['undo'] and synced['candidate'] is None
    # A stale proposal cannot be adopted after a chord change.
    with pytest.raises(ValueError, match='採用する候補'):
        action(candidate, s, f, changed, rows, 'adopt')
    new_f = apply_action(s, f, 'calculate', {'max_fret': '24', 'octave': '-1'})
    new_f, new_rows, _ = reconcile(s, new_f)
    synced = arr.sync(adopted, s, new_f, p, new_rows)
    warnings, _ = arr.validate(synced, arr.melody_notes(new_rows), p, 24)
    assert any('メロディーまたは運指が変更' in w['reason'] for w in warnings)


def test_generation_skips_unknown_chords_and_unconverted_melody():
    _, s, f, rows, p, state = prepared()
    for symbol in ('N.C.',):
        changed = p.save(symbol, 'measure-1', '0')
        candidate = arr.generate(state, arr.melody_notes(rows), changed, 24, {'measure_id': 'measure-1'})
        assert not candidate['notes'] and candidate['reasons']
    missing = [dict(rows[0], position=None), *rows[1:]]
    candidate = arr.generate(state, arr.melody_notes(missing), p, 24, {'measure_id': 'measure-1'})
    assert not candidate['notes'] and '未変換' in candidate['reasons'][0]


def test_polyphonic_preview_preserves_fractional_timing_ties_and_voice_mapping():
    score, s, f, rows, p, state = prepared()
    state = action(state, s, f, p, rows, 'save', role='bass', measure_id='measure-1',
                   offset='0', duration='4/3', string='6', fret='0')
    state = action(state, s, f, p, rows, 'save', role='accompaniment', measure_id='measure-1',
                   offset='1/3', duration='2/3', string='5', fret='3')
    state = action(state, s, f, p, rows, 'generate', measure_id='measure-2', pattern='full')
    preview = build_arrangement_preview(s, p, score.notation, f, rows, state)
    parsed = parse_score(preview['xml'].encode())
    upper = [n for n in parsed.notes if n.part_id == 'melody']
    bass = [n for n in upper if n.voice == '2']
    assert bass[0].start == 0 and sum(n.duration for n in bass) == Fraction(4, 3)
    assert bass[0].pitch == 'E3'  # written one octave above E2
    assert {n['role'] for n in preview['note_map']} == {'melody', 'bass', 'accompaniment'}
    assert {e['channel'] for e in preview['events']} == {'melody', 'bass', 'accompaniment'}
    assert {'start': 0, 'end': float(Fraction(4, 3)), 'midi': 40, 'channel': 'bass'} in preview['events']
    assert sum(e['midi'] == 79 for e in preview['events'] if e['channel'] == 'melody') == 1  # tied G5
    candidate = preview['candidate_preview']
    assert all(candidate['start'] <= e['start'] < e['end'] <= candidate['end'] for e in candidate['events'])
    root = ET.fromstring(preview['xml'])
    assert len(root.findall('part')) == 2
    assert {n.attrib['color'] for n in root.findall('part/measure/note')} == set(arr.COLORS.values())
