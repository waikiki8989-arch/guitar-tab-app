import pytest

from capo_optimization import CapoOptimizationError, capo_from_key, optimize, parse_chord, shape_for


PROGRESSION = [{"measure": i + 1, "beat": 1, "chord": chord}
               for i, chord in enumerate(["Ab", "Bbsus4", "Eb", "Cm"])]


def test_capo_one_keeps_sounding_chords_and_generates_shapes():
    result = optimize(PROGRESSION, min_capo=1, max_capo=1)
    assert result["recommended"]["capo"] == 1
    assert [item["shapeChord"] for item in result["recommended"]["chords"]] == ["G", "Asus4", "D", "Bm"]
    assert [item["soundingChord"] for item in result["recommended"]["chords"]] == ["Ab", "Bbsus4", "Eb", "Cm"]


def test_capos_are_ranked_and_multiple_candidates_returned():
    result = optimize(PROGRESSION, min_capo=0, max_capo=7, top=3)
    assert len(result["candidates"]) == 3
    assert result["candidates"][0]["rank"] == 1
    assert result["recommended"] == result["candidates"][0]


def test_unhandled_chord_is_retained_with_warning():
    result = optimize([{"measure": 1, "beat": 1, "chord": "Dadd9/F#"}], min_capo=0, max_capo=0)
    chord = result["recommended"]["chords"][0]
    assert chord["soundingChord"] == "Dadd9/F#"
    assert chord["fingering"] is None
    assert chord["warning"]


def test_chord_parser_normalizes_aliases():
    assert parse_chord("BbM7")[:2] == ("Bb", "maj7")
    form, shape, sounding = shape_for("Ab", 1)
    assert form.name == "G"
    assert shape == "G"
    assert sounding == "Ab"


def test_key_signature_selects_capo_for_open_chord_root():
    # A-flat major is -4 fifths; capo 1 moves the shape to G.
    assert capo_from_key(-4) == 1
    result = optimize(PROGRESSION, key_fifths=-4)
    assert result["recommended"]["capo"] == 1
    assert result["recommended"]["keyPreferred"] is True


@pytest.mark.parametrize("kwargs", [{"min_capo": 8, "max_capo": 7}, {"min_capo": -1}, {"top": 0}])
def test_invalid_settings_are_rejected(kwargs):
    with pytest.raises(CapoOptimizationError):
        optimize(PROGRESSION, **kwargs)
