from fractions import Fraction
from html import unescape
from io import BytesIO
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from music21 import converter
import pytest

from app import app, state_serializer
from chord_progression import ChordProgression
from melody import MelodySelection
from musicxml_parser import parse_score
from score_preview import build_preview


SAMPLE = Path(__file__).parent / 'fixtures' / 'score_playback.musicxml'


def setup_preview():
    score = parse_score(SAMPLE.read_bytes())
    selection = MelodySelection(score.notes).select(n.note_id for n in score.notes)
    progression = ChordProgression.from_import(score.measures, score.harmonies)
    return score, selection, progression


def test_metadata_preserves_pickup_changes_and_notation():
    score, _, _ = setup_preview()
    assert [(m.number, m.start, m.duration) for m in score.measures] == [('0', 0, 1), ('1', 1, 4), ('2', 5, 3), ('3', 8, 3)]
    tempos = [c for c in score.notation['changes'] if c['kind'] == 'tempo']
    assert [(c['start'], c['value']) for c in tempos] == [('0', '120'), ('5', '60'), ('9', '120')]
    assert [c['value'] for c in score.notation['changes'] if c['kind'] == 'time'] == ['4/4', '3/4', '6/8']
    assert [c['value'] for c in score.notation['changes'] if c['kind'] == 'key'] == [0, 1, -1]
    triplet = next(n for n in score.notes if n.pitch == 'D5')
    assert (triplet.note_type, triplet.tuplet_actual, triplet.tuplet_normal) == ('eighth', 3, 2)
    dotted = next(n for n in score.notes if n.pitch == 'F#5')
    assert dotted.dots == 1 and dotted.accidental == 'sharp'


def test_notation_roundtrip_and_playback_share_the_same_timeline():
    score, selection, progression = setup_preview()
    before = selection.to_data(), progression.to_data(), score.notation.copy()
    preview = build_preview(selection, progression, score.notation)
    assert preview['xml'].startswith('<?xml ')  # OSMD requires a declaration for string input.
    parsed = parse_score(preview['xml'].encode())
    assert [(n.pitch, n.start, n.duration) for n in parsed.notes] == [(n.pitch, n.start, n.duration) for n in selection.selected_notes]
    # Also validate with the full music21 importer, independent of our parser.
    full = converter.parseData(preview['xml'], format='musicxml')
    assert [(n.pitch.nameWithOctave.replace('-', 'b'), Fraction(n.getOffsetInHierarchy(full)), Fraction(n.quarterLength)) for n in full.recurse().notes] == [(n.pitch, n.start, n.duration) for n in selection.selected_notes]
    root = ET.fromstring(preview['xml'])
    assert root.find('part/measure').get('implicit') == 'yes'
    assert len(root.findall('.//time-modification')) == 3
    assert len(root.findall('.//dot')) == 1
    assert root.findall('.//rest')
    assert root.find('.//repeat') is None
    assert any(e['midi'] == 79 and e['start'] == 4 and e['end'] == 6 for e in preview['events'])
    assert sum(e['channel'] == 'melody' and e['midi'] == 79 for e in preview['events']) == 1
    assert before == (selection.to_data(), progression.to_data(), score.notation)


def test_bass_unset_nc_and_unsupported_chords():
    score, selection, progression = setup_preview()
    preview = build_preview(selection, progression, score.notation)
    chords = [e for e in preview['events'] if e['channel'] == 'chord']
    assert min(e['midi'] for e in chords if e['start'] == 1) == 40  # E2, C/E bass
    assert not any(e['start'] < 1 or 5 <= e['start'] < 9 for e in chords)
    assert any('未対応' in w for w in preview['warnings'])
    root = ET.fromstring(preview['xml'])
    assert [w.text for w in root.findall('.//words')] == ['C/E', 'Dm7/G', 'N.C.', 'E [maj9]', 'G7']
    assert max(e['end'] for e in chords) == preview['end'] == 11


def test_triplet_gap_is_triplet_rest_then_half_rest():
    score = parse_score((SAMPLE.parent / 'simple_piano.musicxml').read_bytes())
    selection = MelodySelection(score.notes).select(n.note_id for n in score.notes if n.staff == '1' and n.pitch not in ('E4', 'G4'))
    preview = build_preview(selection, ChordProgression.from_import(score.measures, score.harmonies), score.notation)
    root = ET.fromstring(preview['xml'])
    actual = [e.text for e in root.findall('.//actual-notes')]
    assert actual == ['3', '3', '3']
    last = root.findall('part/measure')[-1].findall('note')
    assert last[-2].find('rest') is not None and last[-2].findtext('duration') == '1'
    assert last[-1].find('rest') is not None and last[-1].findtext('type') == 'half'


def test_chord_change_inside_sustained_note_splits_notation_but_not_audio():
    score, selection, progression = setup_preview()
    progression = progression.save('C', 'measure-2', '2')
    preview = build_preview(selection, progression, score.notation)
    parsed = parse_score(preview['xml'].encode())
    dotted_pieces = [n for n in parsed.notes if n.pitch == 'F#5']
    assert len(dotted_pieces) == 2 and sum(n.duration for n in dotted_pieces) == Fraction(3, 2)
    assert [n.tie for n in dotted_pieces] == ['start', 'stop']
    assert sum(e['channel'] == 'melody' and e['midi'] == 78 for e in preview['events']) == 1


def test_empty_overlap_and_default_tempo():
    score, selection, progression = setup_preview()
    assert build_preview(MelodySelection(score.notes), progression) is None
    default = build_preview(selection, progression)
    assert default['tempos'] == [{'start': 0, 'bpm': 100}]
    source = parse_score((SAMPLE.parent / 'simple_piano.musicxml').read_bytes())
    with pytest.raises(ValueError, match='重なっています'):
        build_preview(MelodySelection(source.notes).select(n.note_id for n in source.notes), ChordProgression.from_import(source.measures, source.harmonies))


def test_web_preview_updates_after_selection_and_chord_edits():
    client = app.test_client()
    response = client.post('/', data={'mode': 'musicxml', 'musicxml': (BytesIO(SAMPLE.read_bytes()), 'score.musicxml')})
    def token(response):
        return unescape(re.search(r'name="score_state" value="([^"]+)"', response.text)[1])
    state = state_serializer().loads(token(response))
    assert 'メロディーが未選択' in response.text
    response = client.post('/', data={'mode': 'melody', 'action': 'apply', 'score_state': token(response),
                                      'selected_ids': [n['note_id'] for n in state['selection']['notes']]})
    assert 'id="score-preview-data"' in response.text
    data = json.loads(re.search(r'<script id="score-preview-data" type="application/json">(.*?)</script>', response.text, re.S)[1])
    assert data['end'] == 11 and data['tempos'][0]['bpm'] == 120
    response = client.post('/', data={'mode': 'progression', 'progression_action': 'save', 'score_state': token(response),
                                      'symbol_name': 'Am', 'measure_id': 'measure-1', 'offset': '0'})
    data = json.loads(re.search(r'<script id="score-preview-data" type="application/json">(.*?)</script>', response.text, re.S)[1])
    assert '<words>Am</words>' in data['xml']
    assert state_serializer().loads(token(response))['notation'] == state['notation']


def test_title_is_safe_in_json_script():
    score, selection, progression = setup_preview()
    with app.test_request_context():
        from app import render_page
        html = render_page(selection=selection, progression=progression, notation=score.notation,
                           filename='</script><script>alert(1)</script>', chord_form={})
        assert '</script><script>alert(1)' not in html
