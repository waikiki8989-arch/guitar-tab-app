from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path

import pytest

import arrangement
from chord_progression import ChordProgression, parse_chord_name
from fingering import reconcile, apply_action
from melody import MelodySelection
from musicxml_parser import parse_score
import project

FIXTURES = Path(__file__).parent / 'fixtures'


def document(filename='score_playback.musicxml', confirmed=False, extra=False):
    score = parse_score((FIXTURES / filename).read_bytes())
    selection = MelodySelection(score.notes)
    if confirmed:
        selection = selection.select(n.note_id for n in score.notes).confirm()
    progression = ChordProgression.from_import(score.measures, score.harmonies)
    fingering, rows, _ = reconcile(selection)
    arr = arrangement.create(selection, fingering, progression, rows) if extra else None
    data = project.snapshot(selection, progression, score.notation, fingering, arr, filename)
    return json.loads(project.dumps(data, project.track(data)))


@pytest.mark.parametrize('filename', [p.name for p in FIXTURES.glob('*.musicxml')])
def test_imported_score_roundtrip_is_exact_without_melody_or_chords(filename):
    before = document(filename)
    after = project.loads(json.dumps(before, ensure_ascii=False).encode())
    assert after == before
    assert after['data']['selection']['selected_ids'] == []
    assert after['data']['arrangement'] is None


def test_edited_project_preserves_fractional_time_sources_locks_and_arrangement():
    score = parse_score((FIXTURES / 'score_playback.musicxml').read_bytes())
    selection = MelodySelection(score.notes).select(n.note_id for n in score.notes).confirm()
    p = ChordProgression.from_import(score.measures, score.harmonies)
    p = p.apply_interval(parse_chord_name('Am'), Fraction(0), Fraction(1, 3))
    f, rows, _ = reconcile(selection)
    f = apply_action(selection, f, 'choose', {'note_id': rows[0]['note'].note_id, 'position': '1:3', 'lock': 'yes'})
    f, rows, _ = reconcile(selection, f)
    arr = arrangement.act(None, selection, f, p, rows, 'save', {
        'role': 'bass', 'measure_id': 'measure-2', 'offset': '1/3', 'duration': '2/3', 'string': '6', 'fret': '0'})
    arr = arrangement.act(arr, selection, f, p, rows, 'generate', {'measure_id': 'measure-2', 'pattern': 'full'})
    original = deepcopy((f, arr, selection.to_data(), p.to_data()))
    data = project.snapshot(selection, p, score.notation, f, arr, 'sample.musicxml')
    raw = project.dumps(data, project.track(data))
    restored = project.loads(raw)
    assert restored['data'] == data
    assert restored['data']['arrangement']['notes'][0]['start'] == '4/3'
    assert restored['data']['arrangement']['notes'][0]['duration'] == '2/3'
    assert set(restored['data']['arrangement']) == set(project.ARRANGEMENT_FIELDS)
    assert not any(key in raw.decode() for key in ('source_key', 'candidate', 'undo', 'redo', 'score_state'))
    assert (f, arr, selection.to_data(), p.to_data()) == original


@pytest.mark.parametrize('path,value', [
    (('format',), 'other'), (('version',), 2), (('version',), True),
    (('project', 'name'), ''), (('project', 'created_at'), 'yesterday'),
    (('project', 'updated_at'), '2000-01-01T00:00:00Z'),
    (('data', 'selection', 'selected_ids'), ['unknown']),
    (('data', 'selection', 'selected_ids'), ['note-1', 'note-1']),
    (('data', 'selection', 'confirmed'), 'true'),
    (('data', 'selection', 'notes', 0, 'duration'), '0'),
    (('data', 'selection', 'notes', 0, 'duration'), '1/0'),
    (('data', 'selection', 'notes', 0, 'duration'), 0.333333),
    (('data', 'selection', 'notes', 0, 'start'), '-1'),
    (('data', 'selection', 'notes', 0, 'pitch'), 'bad-pitch'),
    (('data', 'selection', 'notes', 0, 'pitch'), 'C'),
    (('data', 'selection', 'notes', 0, 'dots'), True),
    (('data', 'selection', 'notes', 0, 'tuplet_actual'), 3),
    (('data', 'progression', 'measures', 0, 'start'), '1'),
    (('data', 'progression', 'measures', 0, 'duration'), '-1'),
    (('data', 'progression', 'events', 0, 'start'), '999'),
    (('data', 'progression', 'events', 0, 'symbol', 'root'), 'D'),
    (('data', 'progression', 'events', 0, 'source'), 'invented'),
    (('data', 'tuning', '1'), 63),
    (('data', 'fingering', 'max_fret'), 25),
    (('data', 'fingering', 'octave'), 2),
    (('data', 'fingering', 'choices'), {'unknown': [1, 0]}),
    (('data', 'fingering', 'choices'), {'note-1': [7, 0]}),
    (('data', 'fingering', 'choices'), {'note-1': [1, 0]}),
    (('data', 'fingering', 'locks'), {'note-1': {'position': [1, 3], 'midi': '40'}}),
    (('data', 'arrangement', 'span_limit'), -1),
    (('data', 'arrangement', 'baseline', 0, 'source_note_id'), 'unknown'),
    (('data', 'notation', 'changes', 0, 'kind'), 'unknown'),
    (('data', 'notation', 'changes'), [{'kind': 'time', 'start': '0', 'value': '4/0'}]),
    (('data', 'notation', 'changes'), [{'kind': 'tempo', 'start': '0', 'value': '0'}]),
])
def test_invalid_values_and_references_are_rejected(path, value):
    doc = document(confirmed=True, extra=True)
    target = doc
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        project.loads(json.dumps(doc).encode())


@pytest.mark.parametrize('value', [None, [], 4, True, 'text'])
def test_bad_nested_types_do_not_escape_as_server_errors(value):
    for path in [('data',), ('data', 'fingering'), ('data', 'selection'), ('data', 'notation'),
                 ('data', 'progression'), ('project',)]:
        doc = document()
        target = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(ValueError):
            project.loads(json.dumps(doc).encode())


def test_duplicate_ids_duplicate_keys_unknown_fields_unsafe_xml_and_limits():
    doc = document()
    doc['data']['selection']['notes'][1]['note_id'] = 'note-1'
    with pytest.raises(ValueError, match='重複'):
        project.loads(json.dumps(doc).encode())
    doc = document()
    doc['data']['progression']['events'][0]['symbol']['raw_xml'] = '<!DOCTYPE harmony [<!ENTITY x "boom">]><harmony>&x;</harmony>'
    with pytest.raises(ValueError, match='XML'):
        project.loads(json.dumps(doc).encode())
    doc = document()
    del doc['data']['notation']
    with pytest.raises(ValueError, match='必須項目'):
        project.loads(json.dumps(doc).encode())
    for raw in [b'{"format":1,"format":2}', b'{"x":NaN}', b'\xff', b'{}', b'[' * 2000]:
        with pytest.raises(ValueError):
            project.loads(raw)
    with pytest.raises(ValueError, match='8 MiB'):
        project.loads(b' ' * (project.MAX_PROJECT_BYTES + 1))


def test_invalid_current_lock_is_valid_project_data():
    doc = document('guitar_fingering.musicxml', confirmed=True, extra=True)
    f = doc['data']['fingering']
    f['locks']['note-2'] = {'position': [3, 9], 'midi': '64'}
    f['max_fret'] = 5
    f['choices'].pop('note-2')
    assert project.loads(json.dumps(doc).encode()) == doc


def test_filename_does_not_escape_download_directory():
    assert project.filename('練習曲') == '練習曲.guitar-tab.json'
    assert '/' not in project.filename('../../song\r\n')
    assert '\n' not in project.filename('../../song\r\n')
