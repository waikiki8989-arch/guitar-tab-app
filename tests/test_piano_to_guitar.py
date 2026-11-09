from fractions import Fraction
from types import SimpleNamespace

import pytest

from piano_to_guitar import PianoConversionError, convert


def note(pitch, start="1", staff="1", measure="1"):
    return SimpleNamespace(pitch=pitch, start=Fraction(start), duration=Fraction(1),
                           staff=staff, voice="1", measure=measure)


def test_hand_selection_and_rhythm_are_preserved():
    result = convert([note("C4", staff="1"), note("C3", staff="2", start="2")], hand="right")
    assert [item["pitch"] for item in result["convertedNotes"]] == ["C4"]
    assert result["convertedNotes"][0]["start"] == "1"


def test_same_onset_is_grouped_but_arpeggio_is_not():
    result = convert([note("C4"), note("E4"), note("G4"), note("C5", start="5/4")], max_voices=3)
    assert [item["pitch"] for item in result["convertedNotes"]] == ["G4", "E4", "C4", "C5"]
    assert result["convertedNotes"][-1]["start"] == "5/4"


def test_duplicate_pitch_classes_are_removed():
    result = convert([note("C3"), note("C4"), note("G3")], mode="bass", max_voices=3)
    assert [item["pitch"] for item in result["convertedNotes"]] == ["C3", "G3"]
    assert result["removedNotes"] == [{"pitch": "C4", "reason": "同じ音名の重複"}]


def test_out_of_range_note_is_moved_and_recorded():
    result = convert([note("C2")], out_of_range="up")
    assert result["convertedNotes"][0]["pitch"] == "C3"
    assert result["movedNotes"][0]["originalPitch"] == "C2"


def test_out_of_range_note_can_be_deleted():
    result = convert([note("C2")], out_of_range="delete")
    assert result["convertedNotes"] == []
    assert result["removedNotes"][0]["reason"] == "ギターの音域外のため削除"


@pytest.mark.parametrize("kwargs", [{"hand": "invalid"}, {"mode": "invalid"}, {"max_voices": 7}])
def test_invalid_settings_are_rejected(kwargs):
    with pytest.raises(PianoConversionError):
        convert([note("C4")], **kwargs)
