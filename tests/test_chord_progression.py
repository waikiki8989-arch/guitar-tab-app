from fractions import Fraction
from pathlib import Path
from xml.etree.ElementTree import fromstring

import pytest

from chord_progression import ChordProgression, ScoreMeasure, parse_chord_name, parse_harmony
from musicxml_parser import MusicXMLParseError, parse_score


SAMPLE = Path(__file__).parent / 'fixtures' / 'chord_progression.musicxml'


@pytest.fixture
def progression():
    score = parse_score(SAMPLE.read_bytes())
    return ChordProgression.from_import(score.measures, score.harmonies)


@pytest.mark.parametrize('name, canonical, kind, root, bass', [
    ('C', 'C', 'major', 'C', None), ('Am', 'Am', 'minor', 'A', None),
    ('G7', 'G7', 'dominant', 'G', None), ('CM7', 'CM7', 'major-seventh', 'C', None),
    ('Cmaj7', 'CM7', 'major-seventh', 'C', None), ('Dm7/G', 'Dm7/G', 'minor-seventh', 'D', 'G'),
    ('Bdim', 'Bdim', 'diminished', 'B', None), ('Caug', 'Caug', 'augmented', 'C', None),
    ('Dsus4', 'Dsus4', 'suspended-fourth', 'D', None), ('C/E', 'C/E', 'major', 'C', 'E'),
    ('F#m7/C#', 'F#m7/C#', 'minor-seventh', 'F#', 'C#'),
    (' B♭M7/F ', 'BbM7/F', 'major-seventh', 'Bb', 'F'),
    ('N.C.', 'N.C.', 'none', None, None),
])
def test_chord_names(name, canonical, kind, root, bass):
    symbol = parse_chord_name(name)
    assert (symbol.name, symbol.kind, symbol.root, symbol.bass) == (canonical, kind, root, bass)


@pytest.mark.parametrize('name', ['', 'H', 'Cmaj9', 'Cm9', 'C/', '/G', 'C/G7', '<script>', 'C7 junk'])
def test_invalid_names(name):
    with pytest.raises(ValueError, match='コード名'):
        parse_chord_name(name)


def test_import_positions_duplicates_conflicts_and_unknown(progression):
    assert [(m.number, m.start, m.duration) for m in progression.measures] == [('1', 0, 4), ('2', 4, 3), ('3', 7, 4)]
    assert len(progression.original) == 10
    assert [(e.symbol.name, e.start) for e in progression.events] == [
        ('C', 0), ('G7/B', 2), ('Am', 2), ('CM7', Fraction(13, 3)),
        ('Dm7/G', 5), ('N.C.', 7), ('D [maj9]', 9),
    ]
    assert progression.events[1].symbol.bass == 'B'
    assert set(progression.conflicts) == {Fraction(2)}
    assert not progression.events[-1].symbol.supported
    assert '<kind text="maj9">major-ninth</kind>' in progression.events[-1].symbol.raw_xml
    assert progression.segments[-1][1] == 11


def test_manual_edit_delete_and_original_is_immutable(progression):
    original = progression.original
    chord = next(e for e in progression.events if e.symbol.name == 'Am')
    edited = progression.save('Am', 'measure-2', '1/2', chord.event_id)
    assert not edited.conflicts
    moved = next(e for e in edited.events if e.event_id == chord.event_id)
    assert moved.start == Fraction(9, 2) and moved.source == 'manual'
    edited = edited.delete(chord.event_id)
    assert edited.original == original == progression.original
    assert any(e.event_id == chord.event_id for e in progression.events)
    assert not any(e.event_id == chord.event_id for e in edited.events)


def test_duplicate_manual_chords_normalize_aliases(progression):
    edited = progression.save('Cmaj7', 'measure-2', '1/3')
    assert len(edited.events) == len(progression.events)
    assert sum(e.symbol.name == 'CM7' for e in edited.events) == 1


def test_unset_no_chord_and_last_duration():
    progression = ChordProgression((ScoreMeasure('m', '0', Fraction(0), Fraction(4)),))
    assert progression.segments == ((0, 4, ()),)
    progression = progression.save('C', 'm', '1').save('N.C.', 'm', '2')
    assert [(start, end, [e.symbol.name for e in events]) for start, end, events in progression.segments] == [
        (0, 1, []), (1, 2, ['C']), (2, 4, ['N.C.']),
    ]
    progression = progression.delete(progression.events[-1].event_id)
    assert progression.segments[-1][0:2] == (1, 4)


@pytest.mark.parametrize('measure, offset', [('measure-1', '-1'), ('measure-1', '4'), ('measure-2', '3'),
                                           ('measure-3', '4'), ('wrong', '0'), ('measure-1', '1/0'),
                                           ('measure-1', 'nan'), ('measure-1', 'abc')])
def test_invalid_positions(progression, measure, offset):
    before = progression.to_data()
    with pytest.raises(ValueError):
        progression.save('C', measure, offset)
    assert progression.to_data() == before


def test_roundtrip_and_unknown_reposition(progression):
    unknown = progression.events[-1]
    moved = progression.save(unknown.symbol.name, 'measure-3', '3', unknown.event_id)
    assert moved.events[-1].symbol == unknown.symbol
    assert ChordProgression.from_data(moved.to_data()) == moved


@pytest.mark.parametrize('extra', ['<degree><degree-value>9</degree-value><degree-alter>0</degree-alter><degree-type>add</degree-type></degree>',
                                  '<inversion>1</inversion>', '<root><root-step>G</root-step></root><kind>minor</kind>'])
def test_complex_harmony_is_preserved_as_unsupported(extra):
    xml = '<harmony><root><root-step>C</root-step></root><kind>major</kind>' + extra + '</harmony>'
    symbol = parse_harmony(fromstring(xml))
    assert not symbol.supported and extra in symbol.raw_xml


def test_altered_root_and_bass():
    symbol = parse_harmony(fromstring('<harmony><root><root-step>B</root-step><root-alter>-1</root-alter></root><kind>minor-seventh</kind><bass><bass-step>F</bass-step><bass-alter>1</bass-alter></bass></harmony>'))
    assert symbol.name == 'Bbm7/F#'
    assert symbol.supported


def test_explicit_bass_with_inversion():
    symbol = parse_harmony(fromstring('<harmony><root><root-step>C</root-step></root><kind>major</kind><inversion>1</inversion><bass><bass-step>E</bass-step></bass></harmony>'))
    assert symbol.name == 'C/E' and symbol.supported


def test_offsets_backup_forward_and_no_note_time_changes():
    xml = b'''<score-partwise><part-list><score-part id="P1"/></part-list><part id="P1">
    <measure number="0"><attributes><divisions>3</divisions></attributes>
    <note><pitch><step>C</step><octave>4</octave></pitch><duration>3</duration></note>
    <harmony><root><root-step>C</root-step></root><kind>major</kind><offset>-2</offset></harmony>
    <backup><duration>3</duration></backup><forward><duration>2</duration></forward>
    <harmony><root><root-step>G</root-step></root><kind>dominant</kind></harmony>
    <note><rest/><duration>1</duration></note></measure></part></score-partwise>'''
    score = parse_score(xml)
    assert [(n.start, n.duration) for n in score.notes] == [(0, 1)]
    assert [e.start for e in score.harmonies] == [Fraction(1, 3), Fraction(2, 3)]
    assert score.measures[0].duration == 1
    with pytest.raises(MusicXMLParseError, match='範囲外'):
        parse_score(xml.replace(b'<offset>-2</offset>', b'<offset>-9</offset>'))
    with pytest.raises(MusicXMLParseError, match='offset'):
        parse_score(xml.replace(b'<offset>-2</offset>', b'<offset>bad</offset>'))


def test_missing_chord_ids_are_not_silently_edited(progression):
    with pytest.raises(ValueError, match='見つかりません'):
        progression.delete('missing')
    with pytest.raises(ValueError, match='見つかりません'):
        progression.save('C', 'measure-1', '0', 'missing')
