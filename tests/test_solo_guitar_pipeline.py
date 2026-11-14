from fractions import Fraction
from io import BytesIO
import re
from types import SimpleNamespace
from pathlib import Path

from app import app
from capo_optimization import capo_for_key
from solo_guitar_pipeline import build


def note(pitch, start="0", duration="1", measure="1", tie=None, note_id=""):
    return SimpleNamespace(pitch=pitch, start=Fraction(start), duration=Fraction(duration),
                           measure=measure, part_id="P1", part_name="Piano", staff="1",
                           voice="1", tie=tie, note_id=note_id)


def test_key_signature_uses_c_form_or_cm_form_capo():
    assert capo_for_key(-3) == 3  # E-flat major
    assert capo_for_key(0, key_mode="minor") == 0  # C minor


def test_pipeline_separates_source_written_and_sounding_pitches():
    result = build([note("C5", note_id="source-1")], key_fifths=0, capo=0)
    converted = result["notes"][0]
    assert converted["sourcePitch"] == "C5"
    assert converted["guitarSoundingPitch"] == "C4"
    assert converted["guitarWrittenPitch"] == "C5"
    assert converted["start"] == "0"
    assert Fraction(result["candidates"][0]["start"]) == Fraction(0)


def test_solo_mode_classifies_outer_voices_and_prefers_role_strings():
    result = build([note("C4"), note("E4"), note("G5")], mode="solo", max_voices=3)
    by_role = {item["role"]: item for item in result["notes"]}
    assert by_role["bass"]["string"] in (5, 6)
    assert by_role["melody"]["string"] in (1, 2, 3, 4)
    assert by_role["inner"]["string"] is not None
    assert len({item["string"] for item in result["notes"]}) == 3


def test_melody_does_not_use_above_fifth_fret_on_strings_two_to_six():
    result = build([note("A5")], mode="melody", capo=0)
    melody = result["notes"][0]
    assert melody["string"] == 1
    assert all(candidate["string"] == 1 or candidate["fret"] <= 4
               for candidate in melody["candidates"])


def test_bass_uses_lower_string_to_stay_near_melody_fret():
    result = build([note("G4"), note("C5")], mode="solo", max_voices=2, capo=0)
    by_role = {item["role"]: item for item in result["notes"]}
    assert by_role["melody"]["fret"] == 1
    assert by_role["bass"]["string"] == 3
    assert abs(by_role["bass"]["fret"] - by_role["melody"]["fret"]) <= 2
    assert any("4・3弦" in warning["reason"] for warning in result["warnings"])


def test_bass_infers_chord_and_adds_missing_chord_tone_below_melody():
    result = build([note("C4"), note("G5")], mode="solo", max_voices=3, capo=0)
    assert result["chords"] == [{"start": "0", "name": "C"}]
    assert any(item["sourcePitch"] == "E5" for item in result["notes"])
    assert result["tabLayout"][0]["columns"][0]["chord"] == "C"


def test_chord_tone_below_melody_has_top_inner_voice_priority():
    result = build([note("C3"), note("E3"), note("G3"), note("D4"), note("B4")],
                   mode="solo", max_voices=3, capo=0)
    selected = {item["sourcePitch"]: item for item in result["notes"]}
    assert selected["G4"]["generated"] is True
    assert selected["G4"]["innerPriority"] is True
    assert "E3" not in selected and "G3" not in selected and "D4" not in selected


def test_capo_candidates_use_tab_fret_and_keep_physical_fret():
    result = build([note("F#5")], capo=2, max_fret=20)
    candidate = next(item for item in result["candidates"][0]["candidates"] if item["string"] == 1)
    assert candidate["fret"] == 0
    assert candidate["physicalFret"] == 2


def test_tied_notes_share_one_fingering_without_changing_source_pitch():
    result = build([note("G4", tie="start", note_id="a"),
                    note("G4", start="1", tie="stop", note_id="b")], capo=0)
    assert len({(item["string"], item["fret"]) for item in result["notes"]}) == 1
    assert all(item["sourcePitch"] == "G4" for item in result["notes"])


def test_unplayable_pitch_is_kept_as_x_with_warning():
    result = build([note("C2")], capo=0)
    item = result["notes"][0]
    assert item["sourcePitch"] == "C2"
    assert item["guitarSoundingPitch"] == "C1"
    assert item["missing"] is True
    assert any("未変換" in warning["reason"] for warning in result["warnings"])
    assert "×" in result["tabText"]


def test_musicxml_screen_can_run_the_shared_pipeline():
    xml = Path(__file__).parent.joinpath("fixtures", "simple_piano.musicxml").read_bytes()
    client = app.test_client()
    loaded = client.post('/', data={"mode": "musicxml", "musicxml": (BytesIO(xml), "score.musicxml")})
    token = re.search(r'name="score_state" value="([^"]+)"', loaded.text).group(1)
    response = client.post('/', data={"mode": "solo_tab", "score_state": token,
                                      "solo_mode": "solo", "max_voices": "3",
                                      "max_fret": "20", "capo": "auto"})
    assert response.status_code == 200
    assert "ソロギターTAB譜（カポ" in response.text
    assert "ギター実音" in response.text
