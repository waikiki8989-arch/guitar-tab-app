"""Paired guitar notation/TAB and bounded, server-validated fingering history."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from xml.etree import ElementTree as ET

from fingering import apply_action, pitch_info, reconcile
from melody import MelodySelection
from score_preview import build_preview, child

HISTORY_LIMIT = 20


def editor_state(selection, fingering, state=None):
    fingerprint = [sorted(selection.selected_ids), selection.confirmed, fingering['max_fret'], fingering['octave']]
    if not state or state.get('fingerprint') != fingerprint:
        return {'fingerprint': fingerprint, 'selected': None, 'undo': [], 'redo': []}
    return deepcopy(state)


def edit(selection, fingering, state, action, form):
    selection.get_confirmed_melody()
    fingering, rows, error = reconcile(selection, fingering)
    if error:
        raise ValueError(error)
    state = editor_state(selection, fingering, state)
    if action in ('undo', 'redo'):
        source, destination = (action, 'redo' if action == 'undo' else 'undo')
        if not state[source]:
            raise ValueError('戻せる運指編集がありません。')
        state[destination] = (state[destination] + [deepcopy(fingering)])[-HISTORY_LIMIT:]
        return state[source].pop(), state
    if action not in ('select', 'choose', 'unlock'):
        raise ValueError('TAB編集の操作が不正です。')
    note_id = form.get('note_id', state['selected'])
    if note_id not in {row['note'].note_id for row in rows}:
        raise ValueError('TAB譜の音符を選択してください。')
    state['selected'] = note_id
    if action == 'select':
        return fingering, state
    updated = apply_action(selection, fingering, action, {**dict(form), 'note_id': note_id, 'lock': 'yes'})
    if updated != fingering:
        state['undo'] = (state['undo'] + [deepcopy(fingering)])[-HISTORY_LIMIT:]
        state['redo'] = []
    return updated, state


def build_tab_preview(selection, progression, notation, fingering, rows, title='ギター譜'):
    """Both parts share every rhythmic boundary, including inserted rests/split ties."""
    written = tuple(replace(row['note'], pitch=pitch_info(row['guitar_pitch'], 1)[0]) for row in rows)
    guitar = MelodySelection(written, frozenset(n.note_id for n in written), True)
    preview = build_preview(guitar, progression, notation, title)
    if not preview:
        return None
    for event in preview['events']:
        if event['channel'] == 'melody':
            event['midi'] -= 12
    preview['events'] = [e for e in preview['events'] if e['channel'] != 'melody' or not any(
        r['position'] is None and float(r['note'].start) < e['end']
        and float(r['note'].start + r['note'].duration) > e['start'] for r in rows)]
    root = ET.fromstring(preview['xml'])
    root.find('part-list/score-part/part-name').text = 'Guitar (written +8va)'
    child(child(root.find('part-list'), 'score-part', id='tab'), 'part-name', 'TAB')
    source_part = root.find('part')
    tab_part = child(root, 'part', id='tab')
    note_map = []
    missing = []
    for row in rows:
        if row['position'] is None:
            missing.append({'note_id': row['note'].note_id, 'measure': row['note'].measure,
                            'start': str(row['note'].start), 'reason': row['reason']})
    for source_measure, measure in zip(source_part.findall('measure'), progression.measures):
        tab_measure = deepcopy(source_measure)
        tab_part.append(tab_measure)
        # Only the upper part carries tempo/chord labels; shared layout aligns their positions.
        for direction in list(tab_measure.findall('direction')):
            tab_measure.remove(direction)
        for attrs in tab_measure.findall('attributes'):
            for element in list(attrs):
                if element.tag in ('key', 'clef'):
                    attrs.remove(element)
        if measure == progression.measures[0]:
            attrs = tab_measure.find('attributes')
            child(child(attrs, 'clef'), 'sign', 'TAB')
            child(child(attrs, 'staff-details'), 'staff-lines', 6)
        cursor = measure.start
        divisions = Fraction(source_measure.findtext('attributes/divisions'))
        for original, tab_note in zip(source_measure.findall('note'), tab_measure.findall('note')):
            row = next((r for r in rows if r['note'].start <= cursor < r['note'].start + r['note'].duration), None)
            if row and original.find('pitch') is not None:
                # OSMD uses the source timestamp to map split graphical notes back to original IDs.
                note_map.append({'start': float(cursor), 'note_id': row['note'].note_id,
                                 'missing': row['position'] is None, 'type': original.findtext('type'),
                                 'dots': len(original.findall('dot'))})
                notations = tab_note.find('notations')
                if notations is None:
                    notations = child(tab_note, 'notations')
                technical = child(notations, 'technical')
                if row['position']:
                    string, fret = row['position']
                    child(technical, 'string', string)
                    child(technical, 'fret', fret)
                else:
                    # A cross marks an unconverted note; never invent a playable fret.
                    child(technical, 'string', 1)
                    child(technical, 'fret', 0)
                    head = ET.Element('notehead')
                    head.text = 'x'
                    tab_note.insert(list(tab_note).index(notations), head)
            cursor += Fraction(original.findtext('duration')) / divisions
    preview.update(xml=ET.tostring(root, encoding='unicode', xml_declaration=True),
                   guitar=True, note_map=note_map, missing=missing)
    if missing:
        preview['warnings'].append('未変換の音はTAB上の×で示します。再生前に下の対象箇所を確認してください。')
    return preview
