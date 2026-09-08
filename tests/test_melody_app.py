from html import unescape
from io import BytesIO
from pathlib import Path
import re

import pytest

from app import app, state_serializer
from melody import MelodySelection, filter_notes


FIXTURES = Path(__file__).parent / 'fixtures'


def upload(client, filename='melody_selection.musicxml', **extra):
    return client.post('/', data={
        'mode': 'musicxml', 'musicxml': (BytesIO((FIXTURES / filename).read_bytes()), filename), **extra,
    })


def token(response):
    return unescape(re.search(r'name="score_state" value="([^"]+)"', response.text)[1])


def state(response):
    return state_serializer().loads(token(response))


def submit(client, response, action='apply', **changes):
    previous = state(response)
    selection = MelodySelection.from_data(previous['selection'])
    visible = filter_notes(selection.notes, previous['filters'])
    data = {'mode': 'melody', 'score_state': token(response), 'action': action,
            'selected_ids': [n.note_id for n in visible if n.note_id in selection.selected_ids],
            **previous['filters'], **changes}
    return client.post('/', data=data)


def test_full_melody_workflow():
    client = app.test_client()
    response = upload(client)
    assert response.status_code == 200
    assert 'メロディーは未選択' in response.text
    original = state(response)['selection']['notes']
    response = submit(client, response, 'filter', part_id='"P1"', staff='"1"', voice='"1"')
    assert '候補 4音' in response.text
    response = submit(client, response, 'bulk')
    assert '同じ開始位置で複数' in response.text
    response = submit(client, response, 'confirm')
    assert not state(response)['selection']['confirmed']
    e5 = next(n['note_id'] for n in original if n['pitch'] == 'E5')
    response = submit(client, response, f'remove:{e5}')
    assert '同じ開始位置で複数' not in response.text
    response = submit(client, response, 'filter', staff='"2"', voice='"4"')
    assert len(state(response)['selection']['selected_ids']) == 3
    response = submit(client, response, 'bulk')
    response = submit(client, response, 'filter', part_id='"P2"', staff='null', voice='null')
    response = submit(client, response, 'bulk')
    response = submit(client, response, 'confirm')
    selection = MelodySelection.from_data(state(response)['selection'])
    assert [n.pitch for n in selection.get_confirmed_melody()] == ['C4', 'E4', 'C5', 'D4', 'G4']
    assert '確定済み：5音' in response.text
    assert state(response)['selection']['notes'] == original
    # A display-only change retains confirmation; removing a hidden selected note invalidates it.
    response = submit(client, response, 'filter', part_id='"P1"', staff='"1"', voice='"2"')
    assert state(response)['selection']['confirmed']
    response = submit(client, response, f'remove:{selection.selected_notes[0].note_id}')
    assert not state(response)['selection']['confirmed']
    assert len(state(response)['selection']['selected_ids']) == 4


def test_checkboxes_apply_before_filter_change_and_unchecking_keeps_hidden_selection():
    client = app.test_client()
    response = upload(client)
    notes = state(response)['selection']['notes']
    first, last = notes[0]['note_id'], notes[-1]['note_id']
    response = submit(client, response, 'filter', selected_ids=[first, last], part_id='"P1"')
    assert set(state(response)['selection']['selected_ids']) == {first, last}
    response = submit(client, response, selected_ids=[])
    assert state(response)['selection']['selected_ids'] == [last]


def test_invalid_upload_retains_work_and_new_upload_resets_it():
    client = app.test_client()
    response = upload(client)
    first = state(response)['selection']['notes'][0]['note_id']
    response = submit(client, response, 'confirm', selected_ids=[first])
    before = state(response)
    response = client.post('/', data={'mode': 'musicxml', 'score_state': token(response),
                                      'musicxml': (BytesIO(b'broken'), 'bad.xml')})
    assert state(response) == before
    response = upload(client, 'simple_piano.musicxml', score_state=token(response))
    assert len(state(response)['selection']['notes']) == 10
    assert state(response)['selection']['selected_ids'] == []
    assert not state(response)['selection']['confirmed']


def test_tab_generation_keeps_melody_and_clients_do_not_share_state():
    client = app.test_client()
    response = upload(client)
    first = state(response)['selection']['notes'][0]['note_id']
    response = submit(client, response, 'confirm', selected_ids=[first])
    before = state(response)
    response = client.post('/', data={'mode': 'chord', 'chord': 'E', 'score_state': token(response)})
    assert 'e|--0--|' in response.text
    assert state(response) == before
    assert state(upload(app.test_client()))['selection']['selected_ids'] == []
    assert 'メロディーを選ぶ' not in app.test_client().get('/').text


def test_forged_state_and_expired_state_are_rejected(monkeypatch):
    client = app.test_client()
    response = upload(client)
    result = client.post('/', data={'mode': 'melody', 'score_state': token(response) + 'x'})
    assert '作業情報が無効' in result.text
    with monkeypatch.context() as patch:
        patch.setattr('itsdangerous.timed.time.time', lambda: 1)
        expired = state_serializer().dumps(state(response))
    result = client.post('/', data={'mode': 'melody', 'score_state': expired})
    assert '期限切れ' in result.text
    result = upload(client, score_state=expired)
    assert result.status_code == 200 and 'メロディーを選ぶ' in result.text


@pytest.mark.parametrize('changes, expected', [
    ({'part_id': '"unknown"'}, '絞り込み条件が不正'),
    ({'selected_ids': ['unknown']}, '表示中の候補にない'),
    ({'action': 'unknown'}, '操作が不正'),
    ({'action': 'confirm'}, 'メロディーに使う音符を選択'),
])
def test_invalid_operations_preserve_score(changes, expected):
    client = app.test_client()
    response = upload(client)
    original = state(response)['selection']['notes']
    response = submit(client, response, **changes)
    assert response.status_code == 200
    assert expected in response.text
    assert state(response)['selection']['notes'] == original


def test_no_score_and_rest_only_score():
    client = app.test_client()
    assert '先にMusicXML' in client.post('/', data={'mode': 'melody'}).text
    xml = b'<score-partwise><part-list><score-part id="P1"/></part-list><part id="P1"><measure number="1"><attributes><divisions>1</divisions></attributes><note><rest/><duration>4</duration></note></measure></part></score-partwise>'
    response = client.post('/', data={'mode': 'musicxml', 'musicxml': (BytesIO(xml), 'rests.xml')})
    assert '条件に一致する音符がありません' in response.text
    response = submit(client, response, 'confirm')
    assert 'メロディーに使う音符を選択' in response.text
