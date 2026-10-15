"""Export the adopted arrangement as one guitar performance (exact rational time)."""
from copy import deepcopy
from fractions import Fraction
from itertools import combinations
from xml.etree import ElementTree as ET

from music21.pitch import Pitch

import arrangement
from arrangement_score import build_arrangement_preview
from chord_progression import CHORD_DEFINITIONS
from export_settings import normalize
from fingering import TUNING, reconcile
from score_preview import child


class NotationError(ValueError):
    def __init__(self, start, message):
        super().__init__(message)
        self.start = start


def prepare(selection, progression, notation, fingering, state, settings=None, title='ギター譜'):
    """Return diagnostics and immutable export data; never adopt proposals or edit input."""
    settings = normalize(settings)
    settings['title'] = settings['title'] or title
    result = {'settings': settings, 'blockers': [], 'warnings': [], 'xml': None}

    def report(target, start, ids, reason):
        measure = next((m.number for m in progression.measures if m.start <= start < m.start + m.duration), '範囲外')
        result[target].append({'measure': measure, 'start': str(start), 'ids': list(ids), 'reason': reason})

    fingering, rows, problem = reconcile(selection, fingering)
    if not selection.confirmed or problem or not rows:
        report('blockers', Fraction(0), [], problem or '先にメロディーを選択して確定してください。')
        return result
    state = deepcopy(state) if state is not None else arrangement.create(selection, fingering, progression, rows)
    state['candidate'] = None
    notes = (*arrangement.melody_notes(rows), *arrangement.extra_notes(state))
    for n in notes:
        if n.string not in TUNING or n.fret is None:
            report('blockers', n.start, [n.note_id], '運指が未変換です。TABを完成させてください。')
        elif not 0 <= n.fret <= fingering['max_fret'] or TUNING[n.string] + n.fret != n.midi:
            report('blockers', n.start, [n.note_id], '弦・フレットが音高と一致しないか、フレット上限を超えています。')
        if n.midi.denominator != 1 or Fraction(str(Pitch(n.pitch.replace('b', '-')).ps)) != n.midi:
            report('blockers', n.start, [n.note_id], '音高が不正または微分音のため出力できません。')
        if n.start < 0 or n.duration <= 0 or n.end > progression.end:
            report('blockers', n.start, [n.note_id], '音符の開始位置・長さが曲の範囲外です。')
    for a, b in combinations(notes, 2):
        if a.string is not None and a.string == b.string and arrangement.overlap(a, b):
            report('blockers', max(a.start, b.start), [a.note_id, b.note_id], f'{a.string}弦で音の長さが重なっています。')
    for n in selection.selected_notes:
        if n.tuplet_actual is not None and (n.tuplet_actual, n.tuplet_normal) != (3, 2):
            report('blockers', n.start, [n.note_id], '三連符以外の連符は出力未対応です。')
    for change in notation.get('changes', []):
        if not 0 <= Fraction(change['start']) < progression.end:
            report('blockers', Fraction(change['start']), [], '拍子・調号・テンポの変更位置が曲の範囲外です。')
    for message in notation.get('warnings', []):
        report('warnings', Fraction(0), [], message)
    warnings, _ = arrangement.validate(state, arrangement.melody_notes(rows), progression, fingering['max_fret'])
    result['warnings'].extend(warnings)
    for event in progression.events:
        if not event.symbol.supported or event.symbol.kind not in (*CHORD_DEFINITIONS, 'none'):
            report('warnings', event.start, [event.event_id], f'コード「{event.symbol.name}」は文字として出力します。')
    if result['blockers']:
        return result
    try:
        preview = build_arrangement_preview(selection, progression, notation, fingering, rows, state, settings['title'])
        preview['xml'] = group_tuplets(preview['xml'], progression)
        result['xml'] = convert(preview['xml'], progression, settings)
        # Rendering uses ordinary notes on two visual parts; it has no playback controls.
        result['preview'] = preview
    except (ValueError, OverflowError) as exc:
        report('blockers', getattr(exc, 'start', Fraction(0)), [], f'記譜を出力できません：{exc}')
    return result


def group_tuplets(xml, progression):
    """Group by elapsed duration, including mixed quarter/eighth triplet notes.

    Ending a bracket on each note type makes readers pad partial tuplets with
    invented rests. Explicit normal-type keeps differently sized notes together.
    """
    root = ET.fromstring(xml)
    normal_types = {Fraction(8): 'whole', Fraction(4): 'half', Fraction(2): 'quarter',
                    Fraction(1): 'eighth', Fraction(1, 2): '16th', Fraction(1, 4): '32nd',
                    Fraction(1, 8): '64th', Fraction(1, 16): '128th'}
    for part in root.findall('part'):
        for measure, source in zip(part, progression.measures):
            divisions = Fraction(measure.findtext('attributes/divisions'))
            cursor, duration, group = source.start, Fraction(0), []
            for element in measure:
                if element.tag in ('backup', 'forward') or (element.tag == 'note' and element.find('time-modification') is None):
                    if group:
                        raise NotationError(cursor - duration, '三連符のまとまりが完結しないリズムは出力未対応です。音価と休符を見直してください。')
                if element.tag in ('backup', 'forward'):
                    cursor += Fraction(element.findtext('duration')) / divisions * (-1 if element.tag == 'backup' else 1)
                if element.tag != 'note':
                    continue
                length = Fraction(element.findtext('duration')) / divisions
                _check_rhythm(element, divisions, source.number, cursor)
                cursor += length
                modification = element.find('time-modification')
                if modification is None:
                    continue
                notations = element.find('notations')
                if notations is None:
                    notations = child(element, 'notations')
                for tuplet in list(notations.findall('tuplet')):
                    notations.remove(tuplet)
                group.append(element)
                duration += length
                if duration in normal_types:
                    for note in group:
                        child(note.find('time-modification'), 'normal-type', normal_types[duration])
                    start = child(group[0].find('notations'), 'tuplet', type='start', number='1')
                    for name, count in [('actual', 3), ('normal', 2)]:
                        info = child(start, 'tuplet-' + name)
                        child(info, 'tuplet-number', count)
                        child(info, 'tuplet-type', normal_types[duration])
                    child(group[-1].find('notations'), 'tuplet', type='stop', number='1')
                    duration, group = Fraction(0), []
            if group:
                raise NotationError(cursor - duration, '小節末で完結しない三連符は出力未対応です。音価と休符を見直してください。')
    return ET.tostring(root, encoding='unicode', xml_declaration=True)


def _staff(element, number):
    staff = ET.Element('staff')
    staff.text = str(number)
    # MusicXML order: stem / notehead / staff / beam / notations.
    following = next((i for i, e in enumerate(element) if e.tag in ('beam', 'notations', 'lyric', 'play', 'listen')), len(element))
    element.insert(following, staff)


def _harmony(symbol, staff):
    harmony = ET.Element('harmony')
    if symbol.kind == 'none':
        child(child(harmony, 'root'), 'root-step', 'C')
        child(harmony, 'kind', 'none').set('text', 'N.C.')
    else:
        for tag, value in [('root', symbol.root), ('bass', symbol.bass)]:
            if tag == 'bass':
                child(harmony, 'kind', symbol.kind)
            if value:
                pitch = Pitch(value.replace('b', '-'))
                node = child(harmony, tag)
                child(node, tag + '-step', pitch.step)
                if pitch.accidental:
                    child(node, tag + '-alter', int(pitch.accidental.alter))
    child(harmony, 'staff', staff)
    return harmony


def _check_rhythm(note, divisions, number, start):
    # Verify every output duration against the displayed type; int() must never truncate.
    lengths = {'maxima': 32, 'long': 16, 'breve': 8, 'whole': 4, 'half': 2, 'quarter': 1,
               'eighth': Fraction(1, 2), '16th': Fraction(1, 4), '32nd': Fraction(1, 8),
               '64th': Fraction(1, 16), '128th': Fraction(1, 32), '256th': Fraction(1, 64),
               '512th': Fraction(1, 128), '1024th': Fraction(1, 256)}
    length = Fraction(lengths.get(note.findtext('type'), 0))
    dots = len(note.findall('dot'))
    length *= 2 - Fraction(1, 2 ** dots)
    tm = note.find('time-modification')
    if tm is not None:
        if (tm.findtext('actual-notes'), tm.findtext('normal-notes')) != ('3', '2'):
            raise NotationError(start, f'小節{number}：三連符以外の連符は未対応です。')
        length *= Fraction(2, 3)
    if length <= 0 or length != Fraction(note.findtext('duration')) / divisions:
        raise NotationError(start, f'小節{number}：音符・休符の長さを正確に記譜できません。')


def convert(xml, progression, settings):
    source = ET.fromstring(xml)
    root = ET.Element('score-partwise', version='4.0')
    child(child(root, 'work'), 'work-title', settings['title'])
    identity = child(root, 'identification')
    for role in ('composer', 'arranger'):
        if settings[role]:
            child(identity, 'creator', settings[role], type=role)
    child(child(identity, 'encoding'), 'software', 'Guitar Tab App')
    defaults = child(root, 'defaults')
    scaling = child(defaults, 'scaling')
    child(scaling, 'millimeters', 7)
    child(scaling, 'tenths', 40)
    page = child(defaults, 'page-layout')
    child(page, 'page-height', str(float(Fraction(297 * 40, 7))))
    child(page, 'page-width', '1200')
    margins = child(page, 'page-margins', type='both')
    for edge in ('left', 'right', 'top', 'bottom'):
        child(margins, edge + '-margin', '85.714286')
    score_part = child(child(root, 'part-list'), 'score-part', id='guitar')
    child(score_part, 'part-name', 'Guitar')
    child(child(score_part, 'score-instrument', id='guitar-sound'), 'instrument-name', 'Acoustic Guitar')
    midi = child(score_part, 'midi-instrument', id='guitar-sound')
    child(midi, 'midi-channel', 1)
    child(midi, 'midi-program', 25)
    part = child(root, 'part', id='guitar')
    both = settings['layout'] == 'both'
    upper, tab = source.findall('part')
    voices = max(int(n.findtext('voice', '1')) for n in upper.findall('.//note'))
    for index, (m, u, t) in enumerate(zip(progression.measures, upper, tab)):
        target = child(part, 'measure', **u.attrib)
        divisions = int(u.findtext('attributes/divisions'))
        attrs = deepcopy(u.find('attributes'))
        for clef in list(attrs.findall('clef')):
            attrs.remove(clef)
        if index == 0:
            if both:
                child(attrs, 'staves', 2)
                child(child(attrs, 'clef', number='1'), 'sign', 'G')
                child(attrs.findall('clef')[-1], 'line', 2)
                # Encode sounding pitches with an octave-down guitar clef.
                # A part transpose also transposes TAB fretting in MuseScore.
                child(attrs.findall('clef')[-1], 'clef-octave-change', -1)
            clef = child(attrs, 'clef', number='2' if both else '1')
            child(clef, 'sign', 'TAB')
            child(clef, 'line', 5)
            details = child(attrs, 'staff-details', number='2' if both else '1')
            if both:
                child(details, 'staff-type', 'alternate')
            child(details, 'staff-lines', 6)
            for line, string in enumerate(range(6, 0, -1), 1):
                pitch = Pitch(TUNING[string])
                tuning = child(details, 'staff-tuning', line=str(line))
                child(tuning, 'tuning-step', pitch.step)
                child(tuning, 'tuning-octave', pitch.octave)
        target.append(attrs)
        sources = [(u, 1, False), (t, 2, True)] if both else [(u, 1, True)]
        # The upper stream has chord/tempo changes; TAB-only uses it with TAB technicals.
        techniques = [n.find('notations/technical') for n in t.findall('note')]
        for stream, staff, is_tab in sources:
            if both and is_tab:
                child(child(target, 'backup'), 'duration', int(m.duration * divisions))
            cursor, note_index = m.start, 0
            for original in stream:
                element = deepcopy(original)
                if original is stream.find('attributes'):
                    continue
                if element.tag == 'attributes':
                    if not (both and is_tab):
                        target.append(element)
                    continue
                if element.tag in ('backup', 'forward'):
                    cursor += Fraction(element.findtext('duration')) / divisions * (-1 if element.tag == 'backup' else 1)
                if element.tag == 'direction':
                    if element.find('direction-type/words') is not None:
                        events = next((group for start, _, group in progression.segments if start == cursor), ())
                        for event in events:
                            if event.symbol.supported and event.symbol.kind in (*CHORD_DEFINITIONS, 'none'):
                                target.append(_harmony(event.symbol, staff))
                            else:
                                direction = child(target, 'direction', placement='above')
                                child(child(direction, 'direction-type'), 'words', event.symbol.name)
                                child(direction, 'staff', staff)
                        continue
                    sound = element.find('sound')
                    position = list(element).index(sound) if sound is not None else len(element)
                    node = ET.Element('staff')
                    node.text = str(staff)
                    element.insert(position, node)
                if element.tag == 'note':
                    _check_rhythm(element, divisions, m.number, cursor)
                    element.attrib.pop('color', None)
                    technical = techniques[note_index]
                    note_index += 1
                    if technical is not None and element.find('notations/technical') is None:
                        notations = element.find('notations')
                        if notations is None:
                            notations = child(element, 'notations')
                        notations.append(deepcopy(technical))
                    if element.find('pitch') is not None:
                        octave = element.find('pitch/octave')
                        octave.text = str(int(octave.text) - 1)
                    if both and is_tab:
                        # alternate alone is ignored by some readers. Full-sized cue notes
                        # keep this alternate staff silent, including on those readers.
                        element.insert(0, ET.Element('cue'))
                        for tie in list(element.findall('tie')):
                            element.remove(tie)
                        element.find('type').set('size', 'full')
                        element.find('voice').text = str(int(element.findtext('voice')) + voices)
                    _staff(element, staff)
                    cursor += Fraction(element.findtext('duration')) / divisions
                target.append(element)
            if cursor != m.start + m.duration:
                raise NotationError(m.start, f'小節{m.number}：声部の長さが一致しません。')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True).decode('utf-8')
