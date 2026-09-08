from html import unescape
from io import BytesIO
from pathlib import Path
import re

import pytest

from app import app, state_serializer
from chord_progression import ChordProgression


FIXTURES = Path(__file__).parent / 'fixtures'


def upload(client, filename='chord_progression.musicxml', **extra):
    return client.post('/', data={'mode': 'musicxml',
                                 'musicxml': (BytesIO((FIXTURES / filename).read_bytes()), filename), **extra})


def token(response):
    return unescape(re.search(r'name="score_state" value="([^"]+)"', response.text)[1])


def state(response):
    return state_serializer().loads(token(response))


def submit(client, response, action='save', **fields):
    return client.post('/', data={'mode': 'progression', 'score_state': token(response),
                                 'progression_action': action, **fields})


def test_imported_chord_table_and_original_information():
    response = upload(app.test_client())
    assert response.status_code == 200
    assert '編集後のコード進行 — 7件' in response.text
    assert '元のコード記号（10件）' in response.text
    assert '<td>G7/B' in response.text
    assert '<td>1/3</td><td>13/3</td>' in response.text
    assert '同じ位置に異なるコード' in response.text
    assert 'N.C.' in response.text and '未対応' in response.text
    assert '<kind text="maj9">major-ninth</kind>' in unescape(response.text)


def test_full_edit_workflow_keeps_confirmed_melody_and_original_chords():
    client = app.test_client()
    response = upload(client)
    first = state(response)['selection']['notes'][0]['note_id']
    response = client.post('/', data={'mode': 'melody', 'score_state': token(response),
                                      'action': 'confirm', 'selected_ids': [first]})
    original_selection = state(response)['selection']
    original_chords = state(response)['progression']['original']
    conflict = next(e for e in state(response)['progression']['events'] if e['symbol']['name'] == 'Am')
    response = submit(client, response, f"edit:{conflict['event_id']}")
    assert '選択したコードを変更' in response.text
    assert 'name="symbol_name" value="Am"' in response.text
    response = submit(client, response, event_id=conflict['event_id'], symbol_name='BbM7/F',
                      measure_id='measure-2', offset='1/2')
    assert '同じ位置に異なるコード' not in response.text
    progression = ChordProgression.from_data(state(response)['progression'])
    event = next(e for e in progression.events if e.event_id == conflict['event_id'])
    assert str(event.start) == '9/2' and event.symbol.name == 'BbM7/F' and event.source == 'manual'
    response = submit(client, response, symbol_name='G7', measure_id='measure-3', offset='3')
    added = next(e for e in state(response)['progression']['events'] if e['symbol']['name'] == 'G7')
    response = submit(client, response, f"delete:{added['event_id']}")
    assert not any(e['event_id'] == added['event_id'] for e in state(response)['progression']['events'])
    assert state(response)['selection'] == original_selection
    assert state(response)['progression']['original'] == original_chords


def test_no_chords_manual_entry_nc_and_duplicate_alias():
    client = app.test_client()
    response = upload(client, 'simple_piano.musicxml')
    assert '楽譜にコード記号がありません' in response.text
    assert 'コード未設定' in response.text
    response = submit(client, response, symbol_name='Cmaj7', measure_id='measure-1', offset='1')
    response = submit(client, response, symbol_name='CM7', measure_id='measure-1', offset='1')
    assert len(state(response)['progression']['events']) == 1
    response = submit(client, response, symbol_name='N.C.', measure_id='measure-2', offset='0')
    progression = ChordProgression.from_data(state(response)['progression'])
    assert [(a, b, [e.symbol.name for e in group]) for a, b, group in progression.segments] == [
        (0, 1, []), (1, 4, ['CM7']), (4, 8, ['N.C.']),
    ]


@pytest.mark.parametrize('fields, expected', [
    ({'symbol_name': 'H7', 'offset': '0', 'measure_id': 'measure-1'}, '対応するコード名'),
    ({'symbol_name': 'C', 'offset': '4', 'measure_id': 'measure-1'}, '4未満'),
    ({'symbol_name': 'C', 'offset': '0', 'measure_id': 'unknown'}, '小節を選択'),
    ({'symbol_name': 'C', 'offset': 'NaN', 'measure_id': 'measure-1'}, '数値または分数'),
    ({'symbol_name': 'C', 'offset': '0', 'measure_id': 'measure-1', 'event_id': 'missing'}, '見つかりません'),
])
def test_invalid_edit_is_atomic_and_keeps_entered_values(fields, expected):
    client = app.test_client()
    response = upload(client)
    before = state(response)
    response = submit(client, response, **fields)
    assert response.status_code == 200 and expected in response.text
    assert state(response) == before
    assert f'name="symbol_name" value="{fields["symbol_name"]}"' in response.text
    assert f'name="offset" value="{fields["offset"]}"' in response.text


def test_chords_survive_melody_tab_and_invalid_upload_then_reset():
    client = app.test_client()
    response = upload(client)
    before = state(response)['progression']
    response = client.post('/', data={'mode': 'melody', 'action': 'apply', 'score_state': token(response)})
    assert state(response)['progression'] == before
    response = client.post('/', data={'mode': 'chord', 'chord': 'C', 'score_state': token(response)})
    assert state(response)['progression'] == before
    response = client.post('/', data={'mode': 'musicxml', 'score_state': token(response),
                                      'musicxml': (BytesIO(b'bad'), 'bad.xml')})
    assert state(response)['progression'] == before
    response = upload(client, 'simple_piano.musicxml', score_state=token(response))
    assert state(response)['progression']['events'] == state(response)['progression']['original'] == []


def test_missing_state_and_unknown_action():
    client = app.test_client()
    assert '先にMusicXML' in client.post('/', data={'mode': 'progression'}).text
    response = upload(client)
    for action in ['edit:missing', 'delete:missing', 'bad-action']:
        result = submit(client, response, action)
        assert result.status_code == 200 and 'role="alert"' in result.text
        assert state(result) == state(response)


def test_unknown_chord_raw_text_is_escaped():
    xml = (FIXTURES / 'chord_progression.musicxml').read_bytes().replace(b'text="maj9"', b'text="&lt;script&gt;alert(1)&lt;/script&gt;"')
    response = app.test_client().post('/', data={'mode': 'musicxml', 'musicxml': (BytesIO(xml), 'score.xml')})
    assert response.status_code == 200
    assert '<script>' not in response.text
    assert '&lt;script&gt;' in response.text


def test_rest_only_measures_still_offer_chord_positions():
    xml = b'<score-partwise><part-list><score-part id="P1"/></part-list><part id="P1"><measure number="0"><attributes><divisions>1</divisions></attributes><note><rest/><duration>4</duration></note></measure></part></score-partwise>'
    client = app.test_client()
    response = client.post('/', data={'mode': 'musicxml', 'musicxml': (BytesIO(xml), 'rests.xml')})
    response = submit(client, response, symbol_name='Am', measure_id='measure-1', offset='3')
    assert response.status_code == 200
    assert state(response)['progression']['events'][0]['start'] == '3'
    assert state(response)['selection']['notes'] == []
