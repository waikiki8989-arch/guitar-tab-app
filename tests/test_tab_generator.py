import pytest

from tab_generator import generate_chord_tab, generate_tab


def test_generate_tab():
    result = generate_tab("0 2 2 1 0 0")

    assert result == "\n".join([
        "e|--0--|",
        "B|--0--|",
        "G|--1--|",
        "D|--2--|",
        "A|--2--|",
        "E|--0--|",
    ])


def test_generate_tab_with_muted_string():
    result = generate_tab("x 3 2 0 1 0")

    assert "E|--x--|" in result


def test_rejects_wrong_number_of_frets():
    with pytest.raises(
        ValueError,
        match="フレット番号を6個入力してください。",
    ):
        generate_tab("0 2 2")


def test_rejects_invalid_fret():
    with pytest.raises(ValueError):
        generate_tab("0 2 abc 1 0 0")


def test_rejects_fret_over_24():
    with pytest.raises(ValueError):
        generate_tab("0 2 25 1 0 0")

def test_generate_chord_tab():
    result = generate_chord_tab("E")

    assert result == "\n".join([
        "e|--0--|",
        "B|--0--|",
        "G|--1--|",
        "D|--2--|",
        "A|--2--|",
        "E|--0--|",
    ])


def test_rejects_unknown_chord():
    with pytest.raises(
        ValueError,
        match="対応していないコードです。",
    ):
        generate_chord_tab("Unknown")