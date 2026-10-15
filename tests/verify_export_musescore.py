"""Optional integration check using a real MuseScore executable, no server required.

Example: python tests/verify_export_musescore.py /path/to/AppRun --offscreen
Requires MuseScore 4.7.5. Artifacts are kept in --output (default: a temp directory).
"""
import argparse
from collections import Counter
from fractions import Fraction
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arrangement import positioned_note
from export_settings import DEFAULTS
from score_export import prepare
from test_score_export import setup, xml_notes


LENGTHS = {'whole': 4, 'half': 2, 'quarter': 1, 'eighth': Fraction(1, 2), '16th': Fraction(1, 4)}


def read_muse(staff, measures, tablature):
    result = Counter()
    for measure, original in zip(staff.findall('Measure'), measures):
        for voice in measure.findall('voice'):
            cursor, ratio = original.start, Fraction(1)
            for element in voice:
                if element.tag == 'Tuplet':
                    ratio = Fraction(int(element.findtext('normalNotes')), int(element.findtext('actualNotes')))
                elif element.tag == 'endTuplet':
                    ratio = Fraction(1)
                elif element.tag in ('Chord', 'Rest'):
                    length = (original.duration if element.findtext('durationType') == 'measure'
                              else Fraction(LENGTHS[element.findtext('durationType')]) *
                              (2 - Fraction(1, 2 ** int(element.findtext('dots', '0')))) * ratio)
                    for note in element.findall('Note'):
                        position = (int(note.findtext('string')) + 1, int(note.findtext('fret'))) if tablature else ()
                        ties = tuple(kind for kind, tag in [('stop', 'prev'), ('start', 'next')]
                                     if note.find(f'Spanner[@type="Tie"]/{tag}') is not None)
                        result[(cursor, length, int(note.findtext('pitch')), position, ties)] += 1
                    cursor += length
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('musescore')
    parser.add_argument('--offscreen', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--schema', type=Path, help='MusicXML 4.0 XSD; xml.xsd and xlink.xsd must be in the same directory')
    args = parser.parse_args()
    output = args.output or Path(tempfile.mkdtemp(prefix='guitar-musescore-'))
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'XDG_CONFIG_HOME': str(output / 'config'), 'XDG_DATA_HOME': str(output / 'data')}
    if args.offscreen:
        env.pop('DISPLAY', None)
        env.pop('WAYLAND_DISPLAY', None)
        env['XDG_CURRENT_DESKTOP'] = 'NONE'
    selection, progression, notation, fingering, arrangement = setup()
    arrangement['notes'] = [positioned_note('arr-1', 'bass', 6, 0, Fraction(0), Fraction(6)).to_data(),
                            positioned_note('arr-2', 'accompaniment', 5, 0, Fraction(1), Fraction(2, 3)).to_data()]
    notation['changes'].append({'kind': 'tempo', 'start': '4/3', 'value': '96'})
    for layout in ('both', 'tab'):
        result = prepare(selection, progression, notation, fingering, arrangement, {**DEFAULTS, 'layout': layout})
        assert not result['blockers'], result['blockers']
        source, destination = output / f'{layout}.musicxml', output / f'{layout}.mscx'
        source.write_text(result['xml'])
        if args.schema:
            from lxml import etree
            schema = etree.parse(str(args.schema))
            for imported_schema in schema.findall('{http://www.w3.org/2001/XMLSchema}import'):
                filename = imported_schema.get('schemaLocation').rsplit('/', 1)[-1]
                imported_schema.set('schemaLocation', str(args.schema.resolve().parent / filename))
            etree.XMLSchema(schema).assertValid(etree.parse(str(source)))
        command = [args.musescore, str(source)]
        if args.offscreen:
            # MuseScore's CLI also sees Qt flags; put -o last so its parser keeps the output path.
            command += ['-platform', 'offscreen', '-platformtheme', 'basic']
        subprocess.run([*command, '-o', str(destination)], env=env, check=True, capture_output=True, timeout=60)
        imported = ET.parse(destination).getroot()
        xml = ET.fromstring(result['xml'])
        staves = imported.findall('Score/Staff')
        assert len(staves) == (2 if layout == 'both' else 1)
        for index, staff in enumerate(staves, 1):
            tablature = layout == 'tab' or index == 2
            expected = Counter()
            for start, length, pitch, note in xml_notes(xml, str(index)):
                position = (int(note.findtext('notations/technical/string')), int(note.findtext('notations/technical/fret'))) if tablature else ()
                ties = tuple(kind for kind in ('stop', 'start') if note.find(f'notations/tied[@type="{kind}"]') is not None)
                expected[(start, length, pitch, position, ties)] += 1
            actual = read_muse(staff, progression.measures, tablature)
            assert actual == expected, (layout, index, actual - expected, expected - actual)
            assert all(note.findtext('play', '1') == ('0' if layout == 'both' and tablature else '1')
                       for note in staff.findall('.//Chord/Note'))
        assert imported.findall('.//Tuplet')
        assert len(imported.findall('.//Tempo')) == len(xml.findall('.//direction/sound'))
        assert imported.findall('.//Harmony')
        assert 'E [maj9]' in destination.read_text()
    print(f'MuseScore import: exact pitches, onsets, durations, ties, fingerings and silent alternate TAB passed. Artifacts: {output}')


if __name__ == '__main__':
    main()
