from copy import deepcopy
from fractions import Fraction
import json
from xml.etree import ElementTree as ET

import pytest

import arrangement
import export_settings
import project
from app import app, restore_project
from score_export import prepare
from test_project import document
from test_project_app import download, open_project
from test_melody_app import token, state, upload, submit


def setup(filename='score_playback.musicxml'):
    doc = document(filename, confirmed=True, extra=True)
    s, p, f, a, _ = restore_project(doc)
    return s, p, doc['data']['notation'], f, a


def xml_notes(root, staff='1'):
    """Independent timeline reader: preserve backup/forward, chord, and tie semantics."""
    result = []
    base = Fraction(0)
    for measure in root.findall('part/measure'):
        cursor = Fraction(0)
        end = Fraction(0)
        divisions = Fraction(measure.findtext('attributes/divisions'))
        for e in measure:
            if e.tag in ('backup', 'forward'):
                cursor += Fraction(e.findtext('duration')) / divisions * (-1 if e.tag == 'backup' else 1)
            elif e.tag == 'note':
                length = Fraction(e.findtext('duration')) / divisions
                if e.findtext('staff') == staff and e.find('pitch') is not None:
                    step = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}[e.findtext('pitch/step')]
                    midi = (int(e.findtext('pitch/octave')) + 1) * 12 + step + int(e.findtext('pitch/alter', '0'))
                    result.append((base + cursor, length, midi, e))
                cursor += length
                end = max(end, cursor)
        base += end
    return result


@pytest.mark.parametrize('layout', ['both', 'tab'])
def test_exact_timing_ties_tuplets_metadata_fingering_and_single_performance(layout):
    args = setup()
    settings = {**export_settings.DEFAULTS, 'title': '練習 <A>', 'composer': '作曲者', 'arranger': '編曲者', 'layout': layout}
    before = deepcopy(args)
    result = prepare(*args, settings)
    assert not result['blockers']
    root = ET.fromstring(result['xml'])
    assert len(root.findall('part')) == 1
    assert root.findtext('work/work-title') == '練習 <A>'
    assert root.findtext('identification/creator[@type="arranger"]') == '編曲者'
    assert root.findtext('.//staff-tuning[@line="1"]/tuning-octave') == '2'
    assert root.findtext('.//staff-tuning[@line="6"]/tuning-octave') == '4'
    assert root.findtext('.//midi-program') == '25'
    notes = xml_notes(root)
    assert notes[0][:3] == (0, 1, 67)
    assert notes[1][:3] == (1, Fraction(1, 3), 72)
    assert notes[4][1] == Fraction(3, 2)
    assert notes[4][3].find('dot') is not None
    assert notes[5][3].find('tie[@type="start"]') is not None
    assert notes[6][3].find('tie[@type="stop"]') is not None
    assert notes[1][3].findtext('time-modification/actual-notes') == '3'
    assert root.findall('.//note/rest')
    assert [n.text for n in root.findall('.//key/fifths')] == ['0', '1', '-1']
    assert [n.attrib['tempo'] for n in root.findall('.//direction/sound')] == ['120.0', '60.0', '120.0']
    assert root.findtext('.//harmony/bass/bass-step') == 'E'
    assert root.find('.//words[.="E [maj9]"]') is not None
    assert any('文字' in w['reason'] for w in result['warnings'])
    if layout == 'both':
        assert root.findtext('.//clef[@number="1"]/clef-octave-change') == '-1'
        assert root.find('.//transpose') is None
        assert root.findtext('.//staff-type') == 'alternate'
        alternate = xml_notes(root, '2')
        assert [n[:3] for n in notes] == [n[:3] for n in alternate]
        assert all(n[3].find('cue') is not None and n[3].find('tie') is None for n in alternate)
    else:
        assert root.find('.//cue') is None and root.find('.//transpose') is None
    for _, _, midi, n in notes:
        string = int(n.findtext('notations/technical/string'))
        fret = int(n.findtext('notations/technical/fret'))
        assert arrangement.TUNING[string] + fret == midi
    assert args == before


def test_polyphony_spans_barlines_preserves_all_roles_and_ignores_candidate():
    s, p, n, f, a = setup()
    a['notes'] = [arrangement.positioned_note('arr-1', 'bass', 6, 0, Fraction(0), Fraction(6)).to_data(),
                  arrangement.positioned_note('arr-2', 'accompaniment', 5, 0, Fraction(1), Fraction(2, 3)).to_data()]
    a['candidate'] = {'notes': [{'invalid': 'must never be rendered'}]}
    before = deepcopy(a)
    result = prepare(s, p, n, f, a)
    assert not result['blockers']
    notes = xml_notes(ET.fromstring(result['xml']))
    bass = [n for n in notes if n[2] == 40]
    assert sum(n[1] for n in bass) == 6
    assert all(n[3].find('tie') is not None for n in bass)
    assert sum(n[1] for n in notes if n[2] == 45) == Fraction(2, 3)
    assert len({n[3].findtext('voice') for n in notes}) == 3
    assert a == before


def test_missing_invalid_fingering_overlap_and_unsupported_rhythm_block():
    s, p, n, f, a = setup()
    f['max_fret'] = 0
    missing = prepare(s, p, n, f, a)
    assert missing['blockers'] and missing['xml'] is None
    s, p, n, f, a = setup()
    from fingering import reconcile
    _, rows, _ = reconcile(s, f)
    string, fret = rows[0]['position']
    a['notes'] = [arrangement.positioned_note('arr-1', 'bass', string, fret, Fraction(0), Fraction(1)).to_data()]
    result = prepare(s, p, n, f, a)
    assert any('重な' in w['reason'] and w['measure'] == '0' for w in result['blockers'])
    a['notes'][0]['start'], a['notes'][0]['duration'] = '7/5', '1/5'
    result = prepare(s, p, n, f, a)
    assert result['blockers'] and result['xml'] is None


def test_project_v1_migrates_and_output_preferences_roundtrip():
    doc = document(confirmed=True)
    doc['version'] = 1
    del doc['data']['export_settings']
    migrated = project.loads(json.dumps(doc).encode())
    assert migrated['version'] == 2
    assert migrated['data']['export_settings'] == export_settings.DEFAULTS
    with pytest.raises(ValueError):
        project.loads(json.dumps({**doc, 'version': 999}).encode())


@pytest.mark.parametrize('settings', [
    {}, {**export_settings.DEFAULTS, 'layout': 'pdf'},
    {**export_settings.DEFAULTS, 'title': 'a' * 121},
    {**export_settings.DEFAULTS, 'composer': '\ud800'},
    {**export_settings.DEFAULTS, 'arranger': '\ufffe'},
])
def test_invalid_output_preferences_are_rejected_on_project_load(settings):
    doc = document()
    doc['data']['export_settings'] = settings
    with pytest.raises(ValueError):
        project.loads(json.dumps(doc).encode())


def test_mixed_triplet_values_share_one_complete_group_with_correct_location():
    s, p, n, f, a = setup()
    a['notes'] = [arrangement.positioned_note('arr-1', 'bass', 6, 0, Fraction(0), Fraction(6)).to_data()]
    n['changes'].append({'kind': 'tempo', 'start': '4/3', 'value': '96'})
    result = prepare(s, p, n, f, a)
    assert not result['blockers']
    notes = xml_notes(ET.fromstring(result['xml']))
    mixed = [note for start, _, pitch, note in notes if pitch == 40 and 1 <= start < 2]
    assert [note.findtext('type') for note in mixed] == ['eighth', 'quarter']
    assert all(note.findtext('time-modification/normal-type') == 'eighth' for note in mixed)
    assert mixed[0].find('notations/tuplet[@type="start"]') is not None
    assert mixed[0].find('notations/tuplet[@type="stop"]') is None
    assert mixed[1].find('notations/tuplet[@type="stop"]') is not None
    a['notes'] = [arrangement.positioned_note('arr-1', 'bass', 6, 0, Fraction(4, 3), Fraction(1, 2)).to_data()]
    result = prepare(s, p, n, f, a)
    assert result['xml'] is None
    assert result['blockers'][0]['measure'] == '1'


def ready(client):
    response = upload(client, 'score_playback.musicxml')
    response = submit(client, response, 'bulk')
    return submit(client, response, 'confirm')


def test_output_routes_require_warning_ack_and_do_not_mutate_restored_project():
    c = app.test_client()
    response = ready(c)
    settings = {**export_settings.DEFAULTS, 'title': '練習曲', 'composer': 'A', 'layout': 'tab'}
    response = c.post('/', data={'mode': 'export_settings', 'score_state': token(response), **settings})
    original = state(response)
    assert original['export_settings'] == settings
    for action in ('print', 'musicxml'):
        check = c.post('/score/' + action, data={'score_state': token(response)})
        assert '出力前確認' in check.text
        assert 'Content-Disposition' not in check.headers
    def output(response):
        return c.post('/score/musicxml', data={'score_state': token(response), 'acknowledge': 'yes'})
    exported = output(response)
    assert exported.status_code == 200 and '.musicxml' in exported.headers['Content-Disposition']
    restored = open_project(c, download(c, response).data)
    assert state(restored)['export_settings'] == settings
    assert output(restored).data == exported.data
    assert state(response) == original
    printed = c.post('/score/print', data={'score_state': token(restored), 'acknowledge': 'yes'})
    assert printed.status_code == 200 and 'page-heading' in printed.text
    assert 'tab-select-form' not in printed.text and 'play-score' not in printed.text


@pytest.mark.parametrize('action', ['print', 'musicxml'])
def test_blocking_problems_cannot_be_acknowledged_away(action):
    c = app.test_client()
    response = upload(c, 'score_playback.musicxml')
    blocked = c.post('/score/' + action, data={'score_state': token(response), 'acknowledge': 'yes'})
    assert blocked.status_code == 422 and '確定' in blocked.text
    invalid = c.post('/score/' + action, data={'score_state': 'bad'})
    assert invalid.status_code == 400
