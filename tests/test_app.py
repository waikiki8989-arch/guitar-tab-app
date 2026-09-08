from app import app
from io import BytesIO
from pathlib import Path

import pytest


def test_index_page():
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert "Guitar Tab App" in response.text


def test_generate_tab_from_form():
    client = app.test_client()

    response = client.post(
        "/",
        data={"frets": "0 2 2 1 0 0"},
    )

    assert response.status_code == 200
    assert "e|--0--|" in response.text


def test_show_validation_error():
    client = app.test_client()

    response = client.post(
        "/",
        data={"frets": "0 2 2"},
    )

    assert response.status_code == 200
    assert "フレット番号を6個入力してください。" in response.text

def test_generate_tab_from_chord():
    client = app.test_client()

    response = client.post(
        "/",
        data={
            "mode": "chord",
            "chord": "E",
        },
    )

    assert response.status_code == 200
    assert "e|--0--|" in response.text
    assert "E|--0--|" in response.text


def test_chord_options_are_displayed():
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert '<option' in response.text
    assert 'value="C"' in response.text
    assert 'value="Em"' in response.text


@pytest.mark.parametrize("filename", ["piano.musicxml", "piano.xml", "piano.XML"])
def test_upload_musicxml(filename):
    data = (Path(__file__).parent / "fixtures" / "simple_piano.musicxml").read_bytes()
    response = app.test_client().post('/', data={
        'mode': 'musicxml', 'musicxml': (BytesIO(data), filename),
    })
    assert response.status_code == 200
    assert '10音' in response.text
    assert '<td>Bb3</td>' in response.text
    assert '<td>16/3</td>' in response.text
    assert '<td>Piano</td>' in response.text


@pytest.mark.parametrize("file, message", [
    (None, 'ファイルを選択'),
    ((b'', 'empty.xml'), 'ファイルが空'),
    ((b'hello', 'invalid.xml'), 'MusicXMLファイルではありません'),
    ((b'hello', 'piano.pdf'), '.musicxml または .xml'),
    ((b'PK\x03\x04', 'piano.mxl'), '.musicxml または .xml'),
    ((b'x' * (2 * 1024 * 1024 + 1), 'large.xml'), '2 MiB以下'),
])
def test_upload_errors(file, message):
    data = {'mode': 'musicxml'}
    if file:
        data['musicxml'] = (BytesIO(file[0]), file[1])
    response = app.test_client().post('/', data=data)
    assert response.status_code == 200
    assert message in response.text
    assert 'role="alert"' in response.text


def test_request_limit():
    response = app.test_client().post('/', data={
        'mode': 'musicxml', 'musicxml': (BytesIO(b'x' * (3 * 1024 * 1024)), 'huge.xml'),
    })
    assert response.status_code == 413
    assert '2 MiB以下' in response.text


def test_upload_content_is_escaped():
    xml = (Path(__file__).parent / 'fixtures' / 'simple_piano.musicxml').read_bytes()
    xml = xml.replace(b'Piano', b'&lt;script&gt;alert(1)&lt;/script&gt;')
    response = app.test_client().post('/', data={
        'mode': 'musicxml', 'musicxml': (BytesIO(xml), 'piano.xml'),
    })
    assert '<script>' not in response.text
    assert '&lt;script&gt;' in response.text
