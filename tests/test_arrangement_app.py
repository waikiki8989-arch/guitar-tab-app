import json
import re

from app import app
from test_melody_app import state, token, submit, upload


def arrangement(client, response, action, **fields):
    result = client.post('/', data={'mode': 'arrangement', 'arr_action': action,
        'score_state': token(response), **fields})
    assert result.status_code == 200
    return result


def ready(client):
    response = submit(client, upload(client, 'score_playback.musicxml'), 'bulk')
    response = submit(client, response, 'confirm')
    response = client.post('/', data={'mode': 'progression', 'progression_action': 'save',
        'symbol_name': 'C/E', 'measure_id': 'measure-1', 'offset': '0', 'score_state': token(response)})
    return arrangement(client, response, 'start')


def test_full_web_flow_retains_sources_and_renders_arrangement_preview():
    client = app.test_client()
    response = ready(client)
    initial = state(response)
    response = arrangement(client, response, 'generate', measure_id='measure-1', pattern='full')
    assert '候補を採用' in response.text and 'play-arr-candidate' in response.text
    assert not state(response)['arrangement']['notes']
    response = arrangement(client, response, 'adopt')
    assert len(state(response)['arrangement']['notes']) == 3
    preview = json.loads(re.search(r'<script id="score-preview-data" type="application/json">(.*?)</script>', response.text, re.S)[1])
    assert preview['arrangement'] and {e['channel'] for e in preview['events']} == {'melody', 'bass', 'accompaniment'}
    response = arrangement(client, response, 'edit', arr_note_id='arr-1')
    assert state(response)['arrangement']['selected'] == 'arr-1'
    response = arrangement(client, response, 'save', arr_note_id='arr-1', role='bass',
        measure_id='measure-1', offset='0', duration='1', string='6', fret='1')
    assert '構成音' in response.text
    response = arrangement(client, response, 'delete', arr_note_id='arr-1')
    assert len(state(response)['arrangement']['notes']) == 2
    response = arrangement(client, response, 'undo')
    assert len(state(response)['arrangement']['notes']) == 3
    response = arrangement(client, response, 'redo')
    assert len(state(response)['arrangement']['notes']) == 2
    for key in ('selection', 'progression', 'notation', 'fingering'):
        assert state(response)[key] == initial[key]


def test_invalid_edits_unconfirmed_melody_and_upload_reset():
    client = app.test_client()
    response = ready(client)
    before = state(response)
    result = arrangement(client, response, 'save', role='bass', measure_id='measure-1',
        offset='0', duration='0', string='6', fret='0')
    assert '音の長さ' in result.text and state(result) == before
    response = arrangement(client, response, 'generate', measure_id='measure-1')
    response = arrangement(client, response, 'adopt')
    notes = state(response)['arrangement']['notes']
    removed = submit(client, response, 'remove:' + before['selection']['notes'][0]['note_id'])
    assert '追加音は保持' in removed.text
    assert state(removed)['arrangement']['notes'] == notes
    result = arrangement(client, removed, 'undo')
    assert 'メロディーを確定してください' in result.text
    result = upload(client, 'score_playback.musicxml', score_state=token(response))
    assert state(result)['arrangement'] is None
    assert '先にMusicXML' in client.post('/', data={'mode': 'arrangement'}).text
