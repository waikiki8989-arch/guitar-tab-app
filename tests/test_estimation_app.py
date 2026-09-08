from fractions import Fraction
from html import unescape
from io import BytesIO
from pathlib import Path
import re

import pytest

from app import app, state_serializer
from chord_progression import ChordProgression


FIXTURES = Path(__file__).parent / 'fixtures'


def upload(client, filename='chord_estimation.musicxml', **fields):
    return client.post('/', data={'mode': 'musicxml',
                                 'musicxml': (BytesIO((FIXTURES / filename).read_bytes()), filename), **fields})


def token(response):
    return unescape(re.search(r'name="score_state" value="([^"]+)"', response.text)[1])


def state(response):
    return state_serializer().loads(token(response))


def estimate(client, response, action='run', **fields):
    return client.post('/', data={'mode': 'estimate', 'score_state': token(response),
                                 'estimate_action': action, **fields})


def active(response, position):
    progression = ChordProgression.from_data(state(response)['progression'])
    return tuple(e.symbol.name for a, b, events in progression.segments if a <= position < b
                 for e in events if e.symbol.kind != 'unset')


def test_default_estimation_displays_evidence_without_mutations():
    client = app.test_client()
    response = upload(client)
    before = state(response)
    response = estimate(client, response)
    assert response.status_code == 200
    assert 'G7/B' in response.text and 'Am' in response.text
    assert '一致する音' in response.text and 'コード外の音' in response.text
    assert '音がない区間' in response.text and '根拠が不足' in response.text
    assert state(response)['progression'] == before['progression']
    assert state(response)['selection'] == before['selection']
    assert state(response)['estimate_state']['settings']['measure_id'] == ''


def test_split_and_adopt_both_candidates_preserving_outside_and_melody():
    client = app.test_client()
    response = upload(client)
    first = state(response)['selection']['notes'][0]['note_id']
    response = client.post('/', data={'mode': 'melody', 'action': 'confirm', 'score_state': token(response), 'selected_ids': [first]})
    melody = state(response)['selection']
    response = estimate(client, response, measure_id='measure-4', split_points='2')
    assert '小節内0〜2' in response.text and '小節内2〜4' in response.text
    response = estimate(client, response, 'adopt:0:0')
    assert active(response, 12) == ('C',)
    assert active(response, 11) == active(response, 14) == active(response, 17) == ()
    response = estimate(client, response, 'adopt:1:0')
    assert active(response, 14) == ('Am',)
    assert active(response, 16) == ()
    assert state(response)['selection'] == melody
    assert all(e['source'] == 'estimated' for e in state(response)['progression']['events'] if e['symbol']['kind'] != 'unset')
    assert '推定候補から採用' in response.text
    before = state(response)['progression']
    response = estimate(client, response, measure_id='measure-4', split_points='2')
    assert state(response)['progression'] == before


def test_replacement_requires_checkbox_and_restores_prior_chord():
    client = app.test_client()
    response = upload(client)
    response = client.post('/', data={'mode': 'progression', 'score_state': token(response), 'progression_action': 'save',
                                      'symbol_name': 'Dm', 'measure_id': 'measure-1', 'offset': '0'})
    before = state(response)['progression']
    response = estimate(client, response, measure_id='measure-2', start_offset='1', end_offset='3')
    assert 'Dm' in response.text and '既存コードを、選んだ候補に置き換える' in response.text
    response = estimate(client, response, 'adopt:0:0')
    assert '置き換えのチェック' in response.text
    assert state(response)['progression'] == before
    response = estimate(client, response, 'adopt:0:0', replace_0='yes')
    assert active(response, 5) == ('Am',)
    assert active(response, 4) == active(response, 7) == active(response, 8) == ('Dm',)


def test_skip_and_manual_edit_then_reestimate():
    client = app.test_client()
    response = estimate(client, upload(client), measure_id='measure-2')
    original = state(response)['progression']
    response = estimate(client, response, 'skip:0')
    assert '見送り済み' in response.text
    assert state(response)['progression'] == original
    response = estimate(client, response, 'adopt:0:0')
    assert state(response)['progression'] == original
    assert '表示された区間と候補' in response.text
    response = estimate(client, response, measure_id='measure-2')
    response = estimate(client, response, 'adopt:0:0')
    event = next(e for e in state(response)['progression']['events'] if e['source'] == 'estimated')
    response = client.post('/', data={'mode': 'progression', 'score_state': token(response), 'progression_action': 'save',
                                      'symbol_name': 'Am7', 'event_id': event['event_id'], 'measure_id': 'measure-2', 'offset': '0'})
    before = state(response)['progression']
    response = estimate(client, response, measure_id='measure-2')
    assert state(response)['progression'] == before
    assert next(e for e in before['events'] if e['event_id'] == event['event_id'])['source'] == 'manual'


@pytest.mark.parametrize('action', ['adopt:-1:0', 'adopt:99:0', 'adopt:0:99', 'adopt:0:-1', 'adopt:bad:0', 'skip:99', 'unknown'])
def test_invalid_adoption_is_atomic(action):
    client = app.test_client()
    response = estimate(client, upload(client))
    before = state(response)
    response = estimate(client, response, action)
    assert '表示された区間と候補' in response.text
    assert state(response) == before


def test_filter_and_invalid_run_preserves_previous_results():
    client = app.test_client()
    response = estimate(client, upload(client), part_id='"P1"', staff='null', measure_id='measure-1')
    before = state(response)
    response = estimate(client, response, measure_id='measure-1', start_offset='4', end_offset='1')
    assert '開始より終了を後に' in response.text
    assert state(response) == before
    assert 'name="start_offset" value="4"' in response.text


def test_upload_reset_and_missing_source():
    client = app.test_client()
    response = client.post('/', data={'mode': 'estimate', 'estimate_action': 'run'})
    assert '先にMusicXML' in response.text
    response = upload(client)
    assert '先にコード候補' in estimate(client, response, 'adopt:0:0').text
    response = estimate(client, response)
    response = upload(client, 'simple_piano.musicxml', score_state=token(response))
    assert state(response)['estimate_state'] is None
