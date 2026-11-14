from fractions import Fraction
from types import SimpleNamespace

from pdf_to_tab_pipeline import build
from chord_progression import ChordEvent, ChordSymbol


def note(pitch, start="1", staff="1"):
    return SimpleNamespace(pitch=pitch, start=Fraction(start), duration=Fraction(1),
                           staff=staff, voice="1", measure="1")


def test_pipeline_returns_converted_fingering_and_tab_data():
    result = build([note("G3"), note("C4", start="2")], hand="right", max_fret=20, key_fifths=-4)
    assert len(result["converted"]["convertedNotes"]) == 2
    assert all("candidates" in item for item in result["candidates"])
    assert len(result["fingering"]["notes"]) == 2
    assert "e|" in result["tabText"]
    assert "E|" in result["tabText"]
    assert "小節 1" in result["tabText"]
    assert result["settings"]["capo"] == 1


def test_pipeline_keeps_removed_note_reasons():
    result = build([note("C3"), note("C4"), note("E4")], max_voices=2)
    assert result["converted"]["removedNotes"]
    assert any("重複" in item["reason"] for item in result["converted"]["removedNotes"])


def test_chord_aware_selection_keeps_bass_chord_tones_and_top_melody():
    notes = [note("C3"), note("E3"), note("G3"), note("B4")]
    chords = (ChordEvent("c1", ChordSymbol("C", "C", "major", None), Fraction(0)),)
    result = build(notes, chords=chords, max_voices=3)
    pitches = [item["originalPitch"] for item in result["converted"]["convertedNotes"]]
    assert "C3" in pitches
    assert "B4" in pitches
    assert "E3" in pitches or "G3" in pitches
    assert {"C3", "E3", "G3", "B4"}.issuperset(pitches)
