from types import SimpleNamespace

import pytest

from string_fret_candidates import StringFretError, assign_chord, candidates, pitch_to_midi


def test_g3_has_standard_tuning_candidates():
    assert [(item.string, item.fret) for item in candidates("G3")] == [(3, 0), (4, 5), (5, 10), (6, 15)]


def test_open_e2_is_sixth_string_zero_fret():
    assert [(item.string, item.fret) for item in candidates("E2")] == [(6, 0)]


def test_max_fret_and_open_string_policy_filter_candidates():
    assert [(item.string, item.fret) for item in candidates("G3", max_fret=5)] == [(3, 0), (4, 5)]
    assert [(item.string, item.fret) for item in candidates("G3", open_strings="exclude")] == [(4, 5), (5, 10), (6, 15)]


def test_chord_assigns_distinct_strings():
    result = assign_chord([{"pitch": "C3"}, {"pitch": "E3"}, {"pitch": "G3"}])
    assert result["playable"] is True
    assert {(note["string"], note["fret"]) for note in result["notes"]} == {(5, 3), (4, 2), (3, 0)}
    assert len({note["string"] for note in result["notes"]}) == 3


def test_impossible_chord_reports_warning():
    result = assign_chord([{"pitch": "E6"}, {"pitch": "F6"}], max_fret=20)
    assert result["playable"] is False
    assert result["warnings"]


def test_candidate_data_preserves_timing():
    result = assign_chord([SimpleNamespace(pitch="G3", start="1/2", duration="1", measure="2")])
    assert result["notes"][0]["start"] == "1/2"
    assert result["notes"][0]["measure"] == "2"


@pytest.mark.parametrize("pitch", ["H3", "", "C#"])
def test_invalid_pitch_is_rejected(pitch):
    with pytest.raises(StringFretError):
        pitch_to_midi(pitch)
