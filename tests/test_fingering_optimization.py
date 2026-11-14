import pytest

from fingering_optimization import FingeringOptimizationError, optimize


def note(pitch, start, choices, measure=1):
    return {"pitch": pitch, "start": start, "duration": "1", "measure": measure,
            "candidates": [{"string": string, "fret": fret} for string, fret in choices]}


def test_prefers_nearby_fret_over_large_position_jump():
    result = optimize([note("C4", "1", [(1, 5), (2, 3)]), note("D4", "2", [(1, 15), (2, 5)])])
    assert result["notes"][1]["fret"] == 5
    assert abs(result["notes"][1]["fret"] - result["notes"][0]["fret"]) < 10
    assert not result["warnings"]


def test_open_string_is_preferred_for_beginner_mode():
    result = optimize([note("G3", "1", [(3, 0), (4, 5)])], mode="beginner")
    assert result["notes"][0]["string"] == 3
    assert result["notes"][0]["fret"] == 0


def test_melody_prefers_fret_one_or_two_over_a_higher_position():
    result = optimize([{"pitch": "F#4", "role": "melody", "start": "1", "duration": "1",
                        "candidates": [{"string": 1, "fret": 5}, {"string": 2, "fret": 1}]}])
    assert result["notes"][0]["string"] == 2
    assert result["notes"][0]["fret"] == 1


def test_chord_never_uses_same_string_twice():
    result = optimize([note("C3", "1", [(5, 3), (4, 8)]), note("E3", "1", [(5, 7), (4, 2)])])
    assert len({item["string"] for item in result["notes"]}) == 2


def test_large_move_is_warned_and_alternatives_are_saved():
    result = optimize([note("C4", "1", [(2, 5), (1, 4)]), note("D4", "2", [(2, 16), (1, 15)])], alternatives=3)
    assert result["warnings"]
    assert isinstance(result["alternatives"], list)


@pytest.mark.parametrize("kwargs", [{"mode": "bad"}, {"capo": 13}, {"alternatives": 0}])
def test_invalid_settings_are_rejected(kwargs):
    with pytest.raises(FingeringOptimizationError):
        optimize([note("C4", "1", [(2, 5)])], **kwargs)
