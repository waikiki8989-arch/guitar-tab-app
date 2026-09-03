from app import app


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