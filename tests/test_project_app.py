from copy import deepcopy
from io import BytesIO
import json
import re

import pytest

from app import app, state_serializer
from test_melody_app import upload, token, state, submit
from test_fingering_app import confirmed, finger
from test_arrangement_app import ready, arrangement
from test_tab_editor_app import tab
import project


def download(client, response, ajax=False):
    result = client.post('/project/download', data={'score_state': token(response)},
                         headers={'Accept': 'application/json'} if ajax else {})
    assert result.status_code == 200, result.text
    return result


def open_project(client, content, response=None, confirm=True):
    return client.post('/project/open', data={
        'project_file': (BytesIO(content), 'test.guitar-tab.json'),
        **({'score_state': token(response)} if response else {}),
        **({'replace_current': 'yes'} if confirm else {}),
    })


def preview(response):
    return json.loads(re.search(r'<script id="score-preview-data" type="application/json">(.*?)</script>', response.text, re.S)[1])


def test_full_arrangement_restores_without_original_file_and_can_edit_again(monkeypatch):
    client = app.test_client()
    response = ready(client)
    response = arrangement(client, response, 'generate', measure_id='measure-1', pattern='full')
    response = arrangement(client, response, 'adopt')
    response = arrangement(client, response, 'save', role='bass', measure_id='measure-2',
                           offset='1/3', duration='2/3', string='6', fret='0')
    response = tab(client, response, 'choose', note_id='note-1', position='1:3')
    response = arrangement(client, response, 'generate', measure_id='measure-2', pattern='bass')
    response = client.post('/', data={'mode': 'project_name', 'score_state': token(response), 'project_name': '練習曲'})
    before, before_preview = state(response), preview(response)
    exported = download(client, response)
    assert exported.mimetype == 'application/json'
    assert 'attachment;' in exported.headers['Content-Disposition']
    assert '.guitar-tab.json' in exported.headers['Content-Disposition']
    monkeypatch.setitem(app.config, 'SECRET_KEY', 'another-process-secret')
    restored = open_project(app.test_client(), exported.data)
    assert restored.status_code == 200
    after = state(restored)
    for key in ('selection', 'progression', 'notation', 'fingering', 'filename'):
        assert after[key] == before[key]
    for key in project.ARRANGEMENT_FIELDS:
        assert after['arrangement'][key] == before['arrangement'][key]
    assert after['arrangement']['candidate'] is None
    assert after['arrangement']['undo'] == after['arrangement']['redo'] == []
    assert not after['tab_editor']['undo'] and after['tab_editor']['selected'] is None
    assert after['estimate_state'] is None and after['filters'] == {}
    assert after['project']['name'] == '練習曲'
    assert after['project']['created_at'] == before['project']['created_at']
    assert after['project']['current_digest'] == after['project']['saved_digest']
    assert preview(restored)['events'] == before_preview['events']
    assert preview(restored)['xml'] == before_preview['xml']
    changed = arrangement(client, restored, 'delete', arr_note_id='arr-1')
    assert state(changed)['project']['current_digest'] != state(changed)['project']['saved_digest']
    assert len(json.loads(download(client, changed).data)['data']['arrangement']['notes']) == 3


@pytest.mark.parametrize('phase', ['imported', 'unconfirmed', 'confirmed', 'missing', 'invalid_lock', 'removed'])
def test_save_restore_at_each_stage_including_unconverted_and_invalid_locks(phase):
    client = app.test_client()
    response = upload(client, 'guitar_fingering.musicxml')
    if phase != 'imported':
        response = submit(client, response, 'bulk')
    if phase not in ('imported', 'unconfirmed'):
        response = submit(client, response, 'confirm')
    if phase in ('invalid_lock', 'removed'):
        response = finger(client, response, 'choose', note_id='note-2', position='3:9', lock='yes')
        response = arrangement(client, response, 'start')
        response = finger(client, response, 'calculate', max_fret='5', octave='0')
    if phase == 'removed':
        response = submit(client, response, 'remove:note-2')
    before = state(response)
    restored = open_project(client, download(client, response).data)
    after = state(restored)
    assert after['selection'] == before['selection']
    assert after['fingering'] == before['fingering']
    if phase == 'invalid_lock':
        assert '再選択または固定解除' in restored.text
        assert after['fingering']['locks']['note-2']['position'] == [3, 9]
    if phase == 'removed':
        assert '追加音は保持' in restored.text


def test_dirty_tracking_ignores_view_selection_candidates_and_download_does_not_claim_saved():
    client = app.test_client()
    response = ready(client)
    result = download(client, response, ajax=True).json
    downloaded = state_serializer().loads(result['state_token'])
    assert downloaded['project']['saved_digest'] == downloaded['project']['current_digest']
    response = client.post('/', data={'mode': 'tab_editor', 'tab_action': 'select',
        'note_id': 'note-1', 'score_state': result['state_token']})
    assert '開いた／ダウンロードした内容から変更はありません' in response.text
    before = state(response)['project']
    response = arrangement(client, response, 'generate', measure_id='measure-1')
    assert state(response)['project'] == before
    response = arrangement(client, response, 'adopt')
    assert state(response)['project']['current_digest'] != before['saved_digest']
    assert 'ファイルに保存していない変更' in response.text
    assert '保存完了は確認できない' in response.text


@pytest.mark.parametrize('kind', ['broken', 'version', 'id', 'oversize', 'xml', 'missing'])
def test_failed_load_retains_all_current_work(kind):
    client = app.test_client()
    response = ready(client)
    response = arrangement(client, response, 'generate', measure_id='measure-1')
    before = deepcopy(state(response))
    doc = json.loads(download(client, response).data)
    if kind == 'version':
        doc['version'] = 999
    elif kind == 'id':
        doc['data']['selection']['selected_ids'] = ['unknown']
    raw = json.dumps(doc).encode()
    if kind == 'broken':
        raw = b'broken'
    elif kind == 'oversize':
        raw = b'x' * (project.MAX_PROJECT_BYTES + 1)
    elif kind == 'xml':
        raw = b'<score-partwise/>'
    if kind == 'missing':
        result = client.post('/project/open', data={'score_state': token(response)})
    else:
        result = open_project(client, raw, response)
    assert result.status_code == 200
    assert 'role="alert"' in result.text
    assert state(result) == before


def test_replacement_requires_confirmation_and_large_musicxml_retains_project():
    client = app.test_client()
    response = ready(client)
    before = state(response)
    result = open_project(client, download(client, response).data, response, confirm=False)
    assert '確認欄' in result.text and state(result) == before
    result = upload(client, 'simple_piano.musicxml', score_state=token(response), replace_current='')
    assert '確認欄' in result.text and state(result) == before
    result = client.post('/', data={'mode': 'musicxml', 'score_state': token(response),
        'musicxml': (BytesIO(b'x' * 3 * 1024 * 1024), 'large.xml')})
    assert result.status_code == 413 and state(result) == before


def test_bad_download_token_and_invalid_name_do_not_replace_state():
    client = app.test_client()
    assert client.post('/project/download', data={'score_state': 'bad'}).status_code == 400
    assert client.post('/project/download', headers={'Accept': 'application/json'}).json['error']
    response = ready(client)
    before = state(response)
    for invalid in ('', ' ' * 3, 'x' * 121):
        result = client.post('/', data={'mode': 'project_name', 'score_state': token(response), 'project_name': invalid})
        assert state(result) == before
