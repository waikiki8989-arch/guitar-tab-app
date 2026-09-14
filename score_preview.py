"""Build notation and playback from one exact, immutable musical timeline."""
from fractions import Fraction
from math import lcm
from xml.etree.ElementTree import Element, SubElement, tostring

from music21.duration import Duration
from music21.pitch import Pitch

from chord_estimation import sustained_sounds
from chord_progression import CHORD_DEFINITIONS


def child(parent, tag, text=None, **attributes):
    element = SubElement(parent, tag, attributes)
    if text is not None:
        element.text = str(text)
    return element


def duration_pieces(length, position=Fraction(0)):
    duration = Duration(length)
    if duration.tuplets and (duration.tuplets[0].numberNotesActual, duration.tuplets[0].numberNotesNormal) != (3, 2):
        # A gap after two triplets should be a triplet rest plus ordinary rests,
        # not an invented 12:7 whole-note tuplet spanning the remainder of the bar.
        until_beat = Fraction(position.numerator // position.denominator + 1) - position
        if until_beat < length:
            return duration_pieces(until_beat, position) + duration_pieces(length - until_beat, position + until_beat)
    if duration.type == 'complex':
        if duration.tuplets:
            raise ValueError('複合的な連符の表記は未対応です。')
        return [(Fraction(str(c.quarterLength)), Duration(c.quarterLength)) for c in duration.components]
    return [(length, duration)]


def build_preview(selection, progression, notation=None, title='メロディーとコード'):
    if not selection.selected_notes:
        return None
    notes = selection.selected_notes
    if any(a.start + a.duration > b.start for a, b in zip(notes, notes[1:])):
        raise ValueError('選択した音の発音時間が重なっています。五線譜表示・再生には単旋律になるよう音を選び直してください。')
    notation = notation or {}
    warnings = list(notation.get('warnings', []))
    end = progression.end
    changes = {('time', Fraction(0)): '4/4', ('key', Fraction(0)): 0, ('tempo', Fraction(0)): '100'}
    for change in notation.get('changes', []):
        start = Fraction(change['start'])
        if 0 <= start < end:
            changes[(change['kind'], start)] = change['value']
        else:
            warnings.append('曲の範囲外の拍子・調号・テンポ指定は表示・再生から除外しました。')
    sounds = sustained_sounds(notes)
    if any(sound.midi.denominator != 1 for sound in sounds):
        raise ValueError('微分音を含むメロディーの五線譜表示・再生は未対応です。')

    events = [{'start': float(s.start), 'end': float(s.end), 'midi': int(s.midi), 'channel': 'melody'} for s in sounds]
    chord_labels = {}
    for start, finish, group in progression.segments:
        if not group:
            continue
        chord_labels[start] = ' / '.join(event.symbol.name for event in group)
        if len(group) > 1:
            warnings.append(f'開始位置{start}のコードは競合しているため、伴奏を再生しません。')
            continue
        symbol = group[0].symbol
        if symbol.kind in ('none', 'unset'):
            continue
        if not symbol.supported or symbol.kind not in CHORD_DEFINITIONS:
            warnings.append(f'{symbol.name}は未対応のため、記号だけ表示し伴奏は再生しません。')
            continue
        root = Pitch(symbol.root.replace('b', '-')).pitchClass
        bass = Pitch((symbol.bass or symbol.root).replace('b', '-')).pitchClass
        midis = [36 + bass, *(60 + root + interval for interval in CHORD_DEFINITIONS[symbol.kind][1])]
        events.extend({'start': float(start), 'end': float(finish), 'midi': midi, 'channel': 'chord'} for midi in midis)

    xml = Element('score-partwise', version='4.0')
    child(child(xml, 'work'), 'work-title', title)
    part_list = child(xml, 'part-list')
    child(child(part_list, 'score-part', id='melody'), 'part-name', 'Melody')
    part = child(xml, 'part', id='melody')
    time_signature = '4/4'
    for measure in progression.measures:
        a, b = measure.start, measure.start + measure.duration
        local_notes = [note for note in notes if note.start < b and note.start + note.duration > a]
        points = {a, b}
        points.update(max(a, note.start) for note in local_notes)
        points.update(min(b, note.start + note.duration) for note in local_notes)
        points.update(start for _, start in changes if a <= start < b)
        points.update(start for start in chord_labels if a <= start < b)
        points = sorted(points)
        divisions = lcm(*(p.denominator for p in points))
        time_signature = changes.get(('time', a), time_signature)
        numerator, denominator = map(int, time_signature.split('/'))
        mx_measure = child(part, 'measure', number=measure.number,
                           implicit='yes' if measure.duration != Fraction(numerator * 4, denominator) else 'no')
        attrs = child(mx_measure, 'attributes')
        child(attrs, 'divisions', divisions)
        triplet_notes = []
        for left, right in zip(points, points[1:]):
            for kind in ('key', 'time'):
                if (kind, left) not in changes:
                    continue
                target = attrs if left == a else child(mx_measure, 'attributes')
                value = changes[(kind, left)]
                if kind == 'key':
                    child(child(target, 'key'), 'fifths', value)
                else:
                    time_signature = value
                    beats, beat_type = value.split('/')
                    time = child(target, 'time')
                    child(time, 'beats', beats)
                    child(time, 'beat-type', beat_type)
            if left == 0:
                clef = child(attrs, 'clef')
                child(clef, 'sign', 'G')
                child(clef, 'line', '2')
            if ('tempo', left) in changes:
                bpm = Fraction(changes[('tempo', left)])
                direction = child(mx_measure, 'direction', placement='above')
                metronome = child(child(direction, 'direction-type'), 'metronome')
                child(metronome, 'beat-unit', 'quarter')
                child(metronome, 'per-minute', float(bpm))
                child(direction, 'sound', tempo=str(float(bpm)))
            if left in chord_labels:
                # Keep chord labels below the staff to avoid collisions with tempo marks.
                direction = child(mx_measure, 'direction', placement='below')
                child(child(direction, 'direction-type'), 'words', chord_labels[left])
            sound = next((s for s in sounds if s.start <= left < s.end), None)
            source = next((n for n in local_notes if n.start <= left < n.start + n.duration), None)
            position = left
            for length, duration in duration_pieces(right - left, left):
                mx_note = child(mx_measure, 'note')
                if sound is None:
                    child(mx_note, 'rest')
                else:
                    pitch = Pitch(sound.pitch.replace('b', '-'))
                    mx_pitch = child(mx_note, 'pitch')
                    child(mx_pitch, 'step', pitch.step)
                    if pitch.accidental is not None:
                        child(mx_pitch, 'alter', int(pitch.accidental.alter))
                    child(mx_pitch, 'octave', pitch.octave)
                child(mx_note, 'duration', int(length * divisions))
                ties = [] if sound is None else (['stop'] if position > sound.start else []) + (['start'] if position + length < sound.end else [])
                for tie in ties:
                    child(mx_note, 'tie', type=tie)
                child(mx_note, 'type', duration.type)
                for _ in range(duration.dots):
                    child(mx_note, 'dot')
                if source and position == source.start and source.accidental:
                    child(mx_note, 'accidental', source.accidental)
                if len(duration.tuplets) > 1:
                    raise ValueError('入れ子の連符は未対応です。')
                if duration.tuplets:
                    tuplet = duration.tuplets[0]
                    modification = child(mx_note, 'time-modification')
                    child(modification, 'actual-notes', tuplet.numberNotesActual)
                    child(modification, 'normal-notes', tuplet.numberNotesNormal)
                    if (tuplet.numberNotesActual, tuplet.numberNotesNormal) != (3, 2):
                        warnings.append('三連符以外の連符は簡略表記です。')
                    signature = (tuplet.numberNotesActual, tuplet.numberNotesNormal, duration.type)
                    if triplet_notes and triplet_notes[-1][1:] != signature:
                        _mark_tuplets(triplet_notes)
                        triplet_notes = []
                    triplet_notes.append((mx_note, *signature))
                elif triplet_notes:
                    _mark_tuplets(triplet_notes)
                    triplet_notes = []
                if ties:
                    notations = child(mx_note, 'notations')
                    for tie in ties:
                        child(notations, 'tied', type=tie)
                position += length
        _mark_tuplets(triplet_notes)
    tempos = [{'start': float(start), 'bpm': float(Fraction(value))}
              for (kind, start), value in sorted(changes.items(), key=lambda item: item[0][1]) if kind == 'tempo']
    return {'xml': tostring(xml, encoding='unicode', xml_declaration=True), 'events': sorted(events, key=lambda event: event['start']),
            'tempos': tempos, 'end': float(end), 'warnings': list(dict.fromkeys(warnings)),
            'measures': [{'number': m.number, 'start': float(m.start), 'end': float(m.start + m.duration)} for m in progression.measures]}


def _mark_tuplets(notes):
    index = 0
    while index < len(notes):
        group = notes[index:index + notes[index][1]]
        for mx_note, kind in [(group[0][0], 'start'), (group[-1][0], 'stop')]:
            notations = mx_note.find('notations')
            if notations is None:
                notations = child(mx_note, 'notations')
            child(notations, 'tuplet', type=kind, number='1')
        index += len(group)
