from app import app
from test_melody_app import state, token, submit, upload
from test_fingering_app import confirmed, finger


def tab(client, response, action, **fields):
    return client.post('/', data={'mode': 'tab_editor', 'tab_action': action,
                                  'score_state': token(response), **fields})


def test_web_edit_undo_redo_and_select_leave_score_unchanged():
    client = app.test_client()
    response = confirmed(client)
    original = state(response)
    note_id = original['selection']['notes'][1]['note_id']
    response = tab(client, response, 'select', note_id=note_id)
    assert 'id="tab-edit-form"' in response.text
    assert not state(response)['tab_editor']['undo']
    response = tab(client, response, 'choose', note_id=note_id, position='3:9')
    chosen = state(response)['fingering']
    assert chosen['locks'][note_id]['position'] == [3, 9]
    response = tab(client, response, 'undo')
    assert state(response)['fingering'] == original['fingering']
    response = tab(client, response, 'redo')
    assert state(response)['fingering'] == chosen
    response = tab(client, response, 'unlock', note_id=note_id)
    assert note_id not in state(response)['fingering']['locks']
    response = tab(client, response, 'undo')
    assert state(response)['fingering'] == chosen
    for key in ('selection', 'notation', 'progression'):
        assert state(response)[key] == original[key]


def test_history_resets_on_other_fingering_edits_melody_settings_and_upload():
    client = app.test_client()
    response = confirmed(client)
    note_id = state(response)['selection']['notes'][1]['note_id']
    response = tab(client, response, 'choose', note_id=note_id, position='3:9')
    edited = response
    response = finger(client, edited, 'calculate', max_fret='12', octave='0')
    assert not state(response)['tab_editor']['undo']
    response = finger(client, edited, 'unlock', note_id=note_id)
    assert not state(response)['tab_editor']['undo']
    response = submit(client, edited, f'remove:{note_id}')
    assert not state(response)['tab_editor']['undo']
    response = upload(client, 'score_playback.musicxml', score_state=token(edited))
    assert not state(response)['tab_editor']['undo']
    # Chord editing does not invalidate fingering history.
    response = client.post('/', data={'mode': 'progression', 'progression_action': 'save',
        'symbol_name': 'Am', 'measure_id': 'measure-1', 'offset': '0', 'score_state': token(edited)})
    assert state(response)['tab_editor']['undo'] == state(edited)['tab_editor']['undo']


def test_invalid_actions_are_atomic_and_unconfirmed_input_is_rejected():
    client = app.test_client()
    response = confirmed(client)
    before = state(response)
    result = tab(client, response, 'choose', note_id=before['selection']['notes'][0]['note_id'], position='1:0')
    assert '表示された弦・フレット' in result.text
    assert state(result) == before
    response = upload(client, 'score_playback.musicxml')
    result = tab(client, response, 'undo')
    assert 'メロディーを確定' in result.text
    assert state(result) == state(response)
    assert '先にMusicXML' in client.post('/', data={'mode': 'tab_editor'}).text
