"""Render independent polyphonic voices on one paired guitar score/TAB timeline."""
from copy import deepcopy
from fractions import Fraction
from math import lcm
from xml.etree import ElementTree as ET

from arrangement import COLORS, ROLES, extra_notes, melody_notes, ArrangementNote
from melody import MelodySelection
from musicxml_parser import ScoreNote
from score_preview import child
from tab_score import build_tab_preview


def lanes(notes):
    result = []
    for role in ROLES:
        tracks = []
        for note in sorted((n for n in notes if n.role == role), key=lambda n: (n.start, n.note_id)):
            track = next((t for t in tracks if t[-1].end <= note.start), None)
            if track is None:
                track = []
                tracks.append(track)
            track.append(note)
        result.extend(tracks)
    return result


def build_arrangement_preview(selection, progression, notation, fingering, rows, state, title='ソロギター編曲'):
    all_notes = (*melody_notes(rows), *extra_notes(state))
    original = {n.note_id: n for n in selection.notes}
    previews, roles = [], []
    for lane in lanes(all_notes):
        lane_rows = []
        for n in lane:
            source = original.get(n.source_note_id)
            if source is None:
                source = ScoreNote(n.pitch, n.start, n.duration, 'arrangement', ROLES[n.role], '1', '1',
                                   progression.measure_at(n.start).number, n.note_id)
            lane_rows.append({'note': source, 'guitar_pitch': n.pitch, 'midi': str(n.midi),
                              'position': (n.string, n.fret) if n.string is not None else None,
                              'reason': 'メロディーの運指が未変換です。'})
        notes = tuple(r['note'] for r in lane_rows)
        lane_selection = MelodySelection(notes, frozenset(n.note_id for n in notes), True)
        previews.append(build_tab_preview(lane_selection, progression, notation, fingering, lane_rows, title))
        roles.append(lane[0].role)
    if not previews:
        return None
    roots = [ET.fromstring(p['xml']) for p in previews]
    root = roots[0]
    mapping, events, missing, warnings = [], [], [], []
    for voice, (preview, role) in enumerate(zip(previews, roles), 1):
        mapping.extend({**n, 'voice': voice, 'role': role} for n in preview['note_map'])
        events.extend({**e, 'channel': role} for e in preview['events'] if e['channel'] == 'melody')
        missing.extend(preview['missing'])
        # Chord audio is deliberately excluded; chord labels remain on the melody part.
        warnings.extend(w for w in preview['warnings'] if '伴奏' not in w)
    for part_index in (0, 1):
        target_part = root.findall('part')[part_index]
        source_measures = [r.findall('part')[part_index].findall('measure') for r in roots]
        for i, measure in enumerate(progression.measures):
            copies = [deepcopy(ms[i]) for ms in source_measures]
            divisions = lcm(*(int(m.findtext('attributes/divisions')) for m in copies))
            target = target_part.findall('measure')[i]
            for element in list(target):
                target.remove(element)
            for voice, (copy, role) in enumerate(zip(copies, roles), 1):
                old_divisions = int(copy.findtext('attributes/divisions'))
                if voice > 1:
                    child(child(target, 'backup'), 'duration', int(measure.duration * divisions))
                for element in list(copy):
                    if element.tag == 'attributes':
                        if voice > 1:
                            continue
                        if element.find('divisions') is not None:
                            element.find('divisions').text = str(divisions)
                    if element.tag == 'direction' and voice > 1:
                        continue
                    if element.tag == 'note':
                        duration = element.find('duration')
                        duration.text = str(int(Fraction(duration.text) * divisions / old_divisions))
                        v = ET.Element('voice')
                        v.text = str(voice)
                        element.insert(list(element).index(element.find('type')), v)
                        element.set('color', COLORS[role])
                        if element.find('pitch') is not None:
                            stem = ET.Element('stem')
                            stem.text = 'up' if role == 'melody' else 'down'
                            notations = element.find('notations')
                            element.insert(list(element).index(notations) if notations is not None else len(element), stem)
                        if element.find('rest') is not None and voice > 1:
                            element.set('print-object', 'no')
                    target.append(element)
    result = {**previews[0], 'xml': ET.tostring(root, encoding='unicode', xml_declaration=True),
              'arrangement': True, 'note_map': mapping, 'events': sorted(events, key=lambda e: e['start']),
              'missing': missing, 'warnings': list(dict.fromkeys(warnings)), 'candidate_preview': None}
    if state['candidate'] and state['candidate']['notes']:
        a, b = float(Fraction(state['candidate']['start'])), float(Fraction(state['candidate']['end']))
        candidate_events = [{'start': float(n.start), 'end': float(n.end), 'midi': int(n.midi), 'channel': n.role}
                            for n in (ArrangementNote.from_data(d) for d in state['candidate']['notes'])]
        result['candidate_preview'] = {'start': a, 'end': b, 'events': [
            {**e, 'start': max(a, e['start']), 'end': min(b, e['end'])}
            for e in sorted(events + candidate_events, key=lambda e: e['start']) if e['start'] < b and a < e['end']]}
    return result
