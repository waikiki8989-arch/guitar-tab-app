from fractions import Fraction
from pathlib import Path

import pytest

from musicxml_parser import MAX_XML_BYTES, MusicXMLParseError, parse_musicxml


SAMPLE = Path(__file__).parent / "fixtures" / "simple_piano.musicxml"


def score(body, attributes="<divisions>1</divisions>"):
    return (
        '<score-partwise><part-list><score-part id="P1"/></part-list>'
        '<part id="P1"><measure number="0a"><attributes>'
        f'{attributes}</attributes>{body}</measure></part></score-partwise>'
    ).encode()


def note(step="C", duration="1", extra=""):
    return (f'<note>{extra}<pitch><step>{step}</step><octave>4</octave></pitch>'
            f'<duration>{duration}</duration></note>')


def test_piano_timing_chords_rests_and_metadata():
    notes = parse_musicxml(SAMPLE.read_bytes())
    assert [(n.pitch, n.start, n.duration, n.staff, n.voice, n.measure) for n in notes] == [
        ("C4", 0, 1, "1", "1", "1"),
        ("C3", 0, 4, "2", "2", "1"),
        ("E4", 2, 2, "1", "1", "1"),
        ("C4", 4, 1, "1", "1", "2"),
        ("E4", 4, 1, "1", "1", "2"),
        ("G4", 4, 1, "1", "1", "2"),
        ("C3", 4, 4, "2", "2", "2"),
        ("F#4", 5, Fraction(1, 3), "1", "1", "2"),
        ("Bb3", Fraction(16, 3), Fraction(1, 3), "1", "1", "2"),
        ("G4", Fraction(17, 3), Fraction(1, 3), "1", "1", "2"),
    ]
    assert all(n.part_id == "P1" and n.part_name == "Piano" for n in notes)
    assert all(isinstance(n.start, Fraction) and isinstance(n.duration, Fraction) for n in notes)


def test_omitted_metadata_and_measure_label():
    parsed = parse_musicxml(score(note()))[0]
    assert (parsed.part_name, parsed.staff, parsed.voice) == (None, None, None)
    assert parsed.measure == "0a"


def test_forward_and_fractional_divisions():
    parsed = parse_musicxml(score('<forward><duration>1</duration></forward>' + note(),
                                  '<divisions>3</divisions>'))[0]
    assert parsed.start == parsed.duration == Fraction(1, 3)


def test_parts_align_after_pickup_and_unequal_written_lengths():
    xml = ('<score-partwise><part-list><score-part id="P1"/>'
           '<score-part id="P2"/></part-list>')
    for part_id, duration in [("P1", "1"), ("P2", "2")]:
        xml += (f'<part id="{part_id}"><measure number="0" implicit="yes">'
                '<attributes><divisions>1</divisions></attributes>' + note(duration=duration)
                + '</measure><measure number="1">' + note() + '</measure></part>')
    notes = parse_musicxml((xml + '</score-partwise>').encode())
    assert [(n.part_id, n.start) for n in notes] == [("P1", 0), ("P2", 0), ("P1", 2), ("P2", 2)]


def test_rest_only_score():
    assert parse_musicxml(score('<note><rest/><duration>4</duration></note>')) == []


def test_namespace_utf16_and_standard_doctype():
    xml = score(note()).decode().replace('<score-partwise>', '<score-partwise xmlns="http://www.musicxml.org/ns/musicxml">')
    xml = '<!DOCTYPE score-partwise SYSTEM "http://example.invalid/score.dtd">' + xml
    assert parse_musicxml(xml.encode('utf-16'))[0].pitch == "C4"


@pytest.mark.parametrize("data", [
    b"", b"not xml", b"<score-partwise>", b"<html/>", b"<score-timewise/>",
    b"PK\x03\x04compressed", b"<score-partwise/>", b"x" * (MAX_XML_BYTES + 1),
    score(note(), ""), score(note(), "<divisions>0</divisions>"),
    score(note(duration="-1")), score(note(duration="nan")),
    score('<backup><duration>1</duration></backup>' + note()),
    score(note(extra="<chord/>")), score(note(extra="<grace/>")),
    score('<note><pitch><step>Z</step><octave>4</octave></pitch><duration>1</duration></note>'),
    score(note(), '<divisions>1</divisions><transpose><chromatic>2</chromatic></transpose>'),
    score(''),
    b'<!DOCTYPE score-partwise [<!ENTITY x "test">]><score-partwise>&x;</score-partwise>',
])
def test_invalid_or_unsupported_xml(data):
    with pytest.raises(MusicXMLParseError):
        parse_musicxml(data)


def test_multiple_voices_on_same_staff():
    xml = score(note(extra='<voice>top</voice><staff>1</staff>')
                + '<backup><duration>1</duration></backup>'
                + note('E', extra='<voice>bottom</voice><staff>1</staff>'))
    notes = parse_musicxml(xml)
    assert [(n.start, n.staff, n.voice) for n in notes] == [(0, '1', 'top'), (0, '1', 'bottom')]


def test_chord_with_omitted_voice_preserves_missing_metadata():
    xml = score(note(extra='<voice>1</voice>') + note('E', extra='<chord/>'))
    notes = parse_musicxml(xml)
    assert [(n.start, n.voice) for n in notes] == [(0, '1'), (0, None)]
