from io import BytesIO

from app import app
from test_melody_app import upload, submit, state, token


def confirmed(client):
    response = upload(client, 'guitar_fingering.musicxml')
    response = submit(client, response, 'bulk')
    return submit(client, response, 'confirm')


def finger(client, response, action, **data):
    return client.post('/', data={'mode': 'fingering', 'score_state': token(response),
                                 'fingering_action': action, **data})


def test_web_settings_choice_lock_and_invalid_settings_preserve_other_work():
    client = app.test_client()
    response = confirmed(client)
    original = state(response)
    note_id = original['selection']['notes'][1]['note_id']
    assert '未変換' in response.text and 'F#6' in response.text
    response = finger(client, response, 'choose', note_id=note_id, position='3:9', lock='yes')
    assert state(response)['fingering']['choices'][note_id] == [3, 9]
    response = finger(client, response, 'calculate', max_fret='12', octave='0')
    assert state(response)['fingering']['choices'][note_id] == [3, 9]
    before = state(response)
    response = finger(client, response, 'calculate', max_fret='30', octave='0')
    assert 'フレット上限は0〜24' in response.text and state(response) == before
    response = finger(client, response, 'calculate', max_fret='5', octave='0')
    assert '再選択または固定解除' in response.text
    assert note_id not in state(response)['fingering']['choices']
    response = finger(client, response, 'unlock', note_id=note_id)
    assert note_id in state(response)['fingering']['choices']
    assert not state(response)['fingering']['locks']
    for key in ('selection', 'notation', 'progression'):
        assert state(response)[key] == original[key]


def test_unrelated_forms_retain_manual_choices_and_upload_resets_state():
    client = app.test_client()
    response = confirmed(client)
    note_id = state(response)['selection']['notes'][1]['note_id']
    response = finger(client, response, 'choose', note_id=note_id, position='3:9')
    before = state(response)['fingering']
    response = client.post('/', data={'mode': 'chord', 'chord': 'E', 'score_state': token(response)})
    assert state(response)['fingering'] == before
    response = client.post('/', data={'mode': 'progression', 'progression_action': 'save',
        'symbol_name': 'Am', 'measure_id': 'measure-1', 'offset': '0', 'score_state': token(response)})
    assert state(response)['fingering'] == before
    response = client.post('/', data={'mode': 'musicxml', 'score_state': token(response),
                                      'musicxml': (BytesIO(b'broken'), 'bad.xml')})
    assert state(response)['fingering'] == before
    response = upload(client, 'guitar_fingering.musicxml', score_state=token(response))
    assert state(response)['fingering'] == {'max_fret': 24, 'octave': 0, 'choices': {}, 'locks': {}}


def test_requires_confirmation_and_prunes_removed_notes():
    client = app.test_client()
    response = upload(client, 'guitar_fingering.musicxml')
    response = finger(client, response, 'calculate', max_fret='12', octave='0')
    assert 'メロディーを確定してください' in response.text
    response = submit(client, response, 'bulk')
    response = submit(client, response, 'confirm')
    note_id = state(response)['selection']['notes'][1]['note_id']
    response = finger(client, response, 'choose', note_id=note_id, position='3:9', lock='yes')
    response = submit(client, response, f'remove:{note_id}')
    assert 'data-fingering-note=' not in response.text
    assert note_id not in state(response)['fingering']['locks']
    response = submit(client, response, 'confirm')
    assert 'data-fingering-note=' in response.text
    assert note_id not in state(response)['fingering']['choices']


def test_forged_choice_and_no_score_are_rejected():
    client = app.test_client()
    assert '先にMusicXML' in client.post('/', data={'mode': 'fingering'}).text
    response = confirmed(client)
    before = state(response)
    note_id = before['selection']['notes'][0]['note_id']
    response = finger(client, response, 'choose', note_id=note_id, position='1:0')
    assert '表示された弦・フレット候補' in response.text
    assert state(response) == before
    response = client.post('/', data={'mode': 'fingering', 'score_state': token(response) + 'x',
                                      'fingering_action': 'calculate', 'max_fret': '24', 'octave': '0'})
    assert '作業情報が無効' in response.text
