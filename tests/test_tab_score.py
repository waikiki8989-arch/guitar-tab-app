from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from chord_progression import ChordProgression
from fingering import reconcile, apply_action
from melody import MelodySelection
from musicxml_parser import parse_score
from tab_score import HISTORY_LIMIT, build_tab_preview, edit, editor_state


def setup(name='score_playback.musicxml'):
    score = parse_score((Path(__file__).parent / 'fixtures' / name).read_bytes())
    selection = MelodySelection(score.notes).select(n.note_id for n in score.notes).confirm()
    fingering, rows, _ = reconcile(selection)
    progression = ChordProgression.from_import(score.measures, score.harmonies)
    return score, selection, fingering, rows, progression


def test_paired_parts_share_measures_rhythm_chords_and_fingerings():
    score, selection, fingering, rows, progression = setup()
    before = selection.to_data(), deepcopy(fingering)
    preview = build_tab_preview(selection, progression, score.notation, fingering, rows)
    root = ET.fromstring(preview['xml'])
    upper, tab = root.findall('part')
    assert tab.findtext('measure/attributes/clef/sign') == 'TAB'
    assert tab.findtext('measure/attributes/staff-details/staff-lines') == '6'
    assert len(upper.findall('measure')) == len(tab.findall('measure')) == 4
    for a, b in zip(upper.findall('measure'), tab.findall('measure')):
        assert a.attrib == b.attrib
        for n, t in zip(a.findall('note'), b.findall('note')):
            for tag in ('duration', 'type'):
                assert n.findtext(tag) == t.findtext(tag)
            for tag in ('dot', 'rest', 'tie', 'time-modification'):
                assert [ET.tostring(e) for e in n.findall(tag)] == [ET.tostring(e) for e in t.findall(tag)]
    assert [e.text for e in upper.findall('.//words')] == ['C/E', 'Dm7/G', 'N.C.', 'E [maj9]', 'G7']
    assert not tab.findall('.//direction')
    assert len(tab.findall('.//time-modification')) == 3
    assert len(tab.findall('.//dot')) == 1
    assert len(tab.findall('.//notations/tied')) == 2
    assert {(int(n.findtext('notations/technical/string')), int(n.findtext('notations/technical/fret')))
            for n in tab.findall('.//note') if n.find('pitch') is not None} == {r['position'] for r in rows}
    assert before == (selection.to_data(), fingering)


def test_written_pitch_is_one_octave_above_guitar_audio_and_same_fret_edit_keeps_audio():
    score, selection, fingering, _, progression = setup()
    fingering = apply_action(selection, fingering, 'calculate', {'max_fret': '24', 'octave': '-1'})
    fingering, rows, _ = reconcile(selection, fingering)
    preview = build_tab_preview(selection, progression, score.notation, fingering, rows)
    notes = parse_score(preview['xml'].encode()).notes
    upper = [n for n in notes if n.part_id == 'melody']
    assert [(n.pitch, n.start, n.duration) for n in upper] == [(n.pitch, n.start, n.duration) for n in selection.selected_notes]
    events = [e for e in preview['events'] if e['channel'] == 'melody']
    assert events[0]['midi'] == 55  # original G4 -> guitar G3, written G4
    assert sum(e['midi'] == 67 for e in events) == 1  # tied G4, one attack
    changed, _ = edit(selection, fingering, None, 'choose', {'note_id': rows[0]['note'].note_id, 'position': '4:5'})
    changed, changed_rows, _ = reconcile(selection, changed)
    after = build_tab_preview(selection, progression, score.notation, changed, changed_rows)
    assert after['events'] == preview['events']


def test_unconverted_notes_have_cross_and_position_and_are_silent():
    score, selection, fingering, rows, progression = setup('guitar_fingering.musicxml')
    preview = build_tab_preview(selection, progression, score.notation, fingering, rows)
    assert [n['start'] for n in preview['missing']] == ['5', '6']
    assert len(ET.fromstring(preview['xml']).findall("part[@id='tab']//notehead")) == 2
    assert sum(n['missing'] for n in preview['note_map']) == 2
    assert not any(e['channel'] == 'melody' and e['start'] >= 5 for e in preview['events'])


def test_split_notes_map_to_original_id_and_keep_fraction_timing():
    score, selection, fingering, rows, progression = setup()
    progression = progression.save('C', 'measure-2', '2')
    preview = build_tab_preview(selection, progression, score.notation, fingering, rows)
    source = next(n for n in selection.selected_notes if n.pitch == 'F#5')
    assert [n['start'] for n in preview['note_map'] if n['note_id'] == source.note_id] == [2, 3]
    assert any(n['start'] == float(Fraction(4, 3)) for n in preview['note_map'])


def test_tied_manual_edits_unlock_undo_redo_restore_exact_state():
    _, selection, fingering, rows, _ = setup('guitar_fingering.musicxml')
    initial = deepcopy(fingering)
    chosen, history = edit(selection, fingering, None, 'choose', {'note_id': rows[2]['note'].note_id, 'position': '2:8'})
    assert all(chosen['choices'][r['note'].note_id] == [2, 8] for r in rows[2:4])
    assert all(r['note'].note_id in chosen['locks'] for r in rows[2:4])
    unlocked, history = edit(selection, chosen, history, 'unlock', {'note_id': rows[3]['note'].note_id})
    assert not unlocked['locks']
    restored, history = edit(selection, unlocked, history, 'undo', {})
    assert restored == chosen
    restored, history = edit(selection, restored, history, 'undo', {})
    assert restored == initial
    restored, history = edit(selection, restored, history, 'redo', {})
    assert restored == chosen
    assert fingering == initial
    _, history = edit(selection, restored, history, 'choose', {'note_id': rows[2]['note'].note_id, 'position': '1:3'})
    assert not history['redo']


def test_history_bound_and_reset_on_configuration_or_selection():
    _, selection, fingering, rows, _ = setup('guitar_fingering.musicxml')
    history = None
    for i in range(HISTORY_LIMIT + 3):
        fingering, history = edit(selection, fingering, history, 'choose',
                                  {'note_id': rows[1]['note'].note_id, 'position': '1:0' if i % 2 else '2:5'})
    assert len(history['undo']) == HISTORY_LIMIT
    changed = {**fingering, 'octave': -1}
    assert not editor_state(selection, changed, history)['undo']
    assert not editor_state(replace(selection, confirmed=False), fingering, history)['undo']
    assert not editor_state(selection.select(selection.selected_ids - {rows[0]['note'].note_id}), fingering, history)['undo']


@pytest.mark.parametrize('action,form', [('undo', {}), ('redo', {}), ('choose', {'note_id': 'no'}),
    ('choose', {'note_id': 'note-1', 'position': '6:25'}), ('unknown', {})])
def test_invalid_edits_do_not_mutate_inputs(action, form):
    _, selection, fingering, _, _ = setup()
    history = editor_state(selection, fingering)
    before = deepcopy((fingering, history))
    with pytest.raises(ValueError):
        edit(selection, fingering, history, action, form)
    assert before == (fingering, history)
