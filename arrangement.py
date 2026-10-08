"""Independent solo-guitar arrangement, deterministic proposals and playability checks."""
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from itertools import combinations

from music21.pitch import Pitch

from chord_progression import CHORD_DEFINITIONS
from fingering import TUNING

ROLES = {'melody': 'メロディー', 'bass': 'ベース', 'accompaniment': '伴奏'}
COLORS = {'melody': '#222222', 'bass': '#1565C0', 'accompaniment': '#8E247A'}
HISTORY_LIMIT = 20


@dataclass(frozen=True)
class ArrangementNote:
    note_id: str
    role: str
    pitch: str
    midi: Fraction
    start: Fraction
    duration: Fraction
    string: int | None
    fret: int | None
    source_note_id: str | None = None
    tie: str | None = None

    @property
    def end(self):
        return self.start + self.duration

    def to_data(self):
        return {**asdict(self), 'midi': str(self.midi), 'start': str(self.start), 'duration': str(self.duration)}

    @classmethod
    def from_data(cls, data):
        return cls(**{**data, **{key: Fraction(data[key]) for key in ('midi', 'start', 'duration')}})


def melody_notes(rows):
    return tuple(ArrangementNote('melody-' + r['note'].note_id, 'melody', r['guitar_pitch'],
        Fraction(r['midi']), r['note'].start, r['note'].duration,
        r['position'][0] if r['position'] else None, r['position'][1] if r['position'] else None,
        r['note'].note_id, r['note'].tie) for r in rows)


def extra_notes(state):
    return tuple(ArrangementNote.from_data(n) for n in state['notes'])


def source_key(selection, fingering, progression, rows):
    return [selection.to_data(), fingering, progression.to_data(), [n.to_data() for n in melody_notes(rows)]]


def sync(state, selection, fingering, progression, rows):
    if state is None:
        return None
    state = deepcopy(state)
    key = source_key(selection, fingering, progression, rows)
    if state['source_key'] != key:
        # Retain every added note; old proposals/history cannot overwrite new upstream data.
        state.update(source_key=key, candidate=None, undo=[], redo=[])
    return state


def create(selection, fingering, progression, rows):
    return {'notes': [], 'next_id': 1, 'span_limit': 4, 'candidate': None, 'undo': [], 'redo': [],
            'baseline': [n.to_data() for n in melody_notes(rows)],
            'source_key': source_key(selection, fingering, progression, rows)}


def number(value, label):
    try:
        return Fraction(value)
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        raise ValueError(f'{label}は数値または分数で指定してください。') from exc


def integer(value, low, high, label):
    try:
        result = int(str(value))
    except (ValueError, TypeError) as exc:
        raise ValueError(f'{label}は{low}〜{high}の整数で指定してください。') from exc
    if not low <= result <= high:
        raise ValueError(f'{label}は{low}〜{high}の整数で指定してください。')
    return result


def positioned_note(note_id, role, string, fret, start, duration):
    midi = TUNING[string] + fret
    return ArrangementNote(note_id, role, Pitch(midi).nameWithOctave.replace('-', 'b'), Fraction(midi),
                           start, duration, string, fret)


def overlap(a, b):
    return a.start < b.end and b.start < a.end


def chord_pcs(symbol):
    if not symbol.supported or symbol.kind not in CHORD_DEFINITIONS:
        return None
    root = Pitch(symbol.root.replace('b', '-')).pitchClass
    bass = Pitch((symbol.bass or symbol.root).replace('b', '-')).pitchClass
    return {(root + interval) % 12 for interval in CHORD_DEFINITIONS[symbol.kind][1]}, bass


def validate(state, melody, progression, max_fret):
    notes = (*melody, *extra_notes(state))
    warnings, spans = [], []
    def warn(start, end, ids, reason):
        measure = next((m.number for m in progression.measures if m.start <= start < m.start + m.duration), '範囲外')
        warnings.append({'start': str(start), 'end': str(end), 'measure': measure, 'ids': list(ids), 'reason': reason})
    current = {n.source_note_id: n for n in melody}
    for data in state['baseline']:
        old = ArrangementNote.from_data(data)
        new = current.get(old.source_note_id)
        if new is None:
            warn(old.start, old.end, [old.note_id], '編曲開始時のメロディーとの対応が失われています。追加音を見直してください。')
        elif (old.midi, old.start, old.duration, old.string, old.fret) != (new.midi, new.start, new.duration, new.string, new.fret):
            warn(new.start, new.end, [new.note_id], 'メロディーまたは運指が変更されています。追加音との組み合わせを見直してください。')
    for n in notes:
        if n.string is None:
            warn(n.start, n.end, [n.note_id], 'メロディーが未変換です。この音は再生しません。')
        elif n.fret > max_fret:
            warn(n.start, n.end, [n.note_id], f'{n.fret}フレットは現在の上限{max_fret}を超えています。')
        if n.start < 0 or n.end > progression.end:
            warn(n.start, n.end, [n.note_id], '音が曲の範囲を超えています。')
        if n.role != 'melody':
            for start, end, group in progression.affected_segments(n.start, n.end):
                info = chord_pcs(group[0].symbol) if len(group) == 1 else None
                if info is None:
                    warn(start, end, [n.note_id], 'コード未設定・N.C.・未対応・競合の区間です。追加音を確認してください。')
                elif int(n.midi) % 12 not in info[0] | {info[1]}:
                    warn(start, end, [n.note_id], f'{n.pitch}は現在の{group[0].symbol.name}の構成音・指定ベース音ではありません。')
    for a, b in combinations(notes, 2):
        if a.string is not None and a.string == b.string and overlap(a, b):
            warn(max(a.start, b.start), min(a.end, b.end), [a.note_id, b.note_id], f'{a.string}弦で発音時間が重なっています。')
    boundaries = sorted({t for n in notes for t in (n.start, n.end)})
    for start, end in zip(boundaries, boundaries[1:]):
        sounding = [n for n in notes if n.start <= start < n.end and n.string is not None]
        fretted = [n.fret for n in sounding if n.fret > 0]
        spread = max(fretted) - min(fretted) if fretted else 0
        if sounding:
            spans.append({'start': str(start), 'end': str(end), 'span': spread, 'ids': [n.note_id for n in sounding]})
        if spread > state['span_limit']:
            warn(start, end, [n.note_id for n in sounding], f'押弦の広がり{spread}が目安{state["span_limit"]}を超えています（開放弦は除外）。')
    return warnings, spans


def generate(state, melody, progression, max_fret, form):
    measure = next((m for m in progression.measures if m.measure_id == form.get('measure_id')), None)
    if measure is None:
        raise ValueError('候補を作る小節を選択してください。')
    a = number(form.get('start_offset') or '0', '区間の開始')
    b = number(form.get('end_offset') or str(measure.duration), '区間の終了')
    if not 0 <= a < b <= measure.duration:
        raise ValueError('候補の区間は選択した小節内で、開始より終了を後にしてください。')
    a, b = measure.start + a, measure.start + b
    pattern = form.get('pattern', 'bass')
    if pattern not in ('bass', 'full'):
        raise ValueError('ベースのみ／ベースと伴奏を選択してください。')
    existing = [*melody, *extra_notes(state)]
    proposal, reasons = [], []
    for start, end, group in progression.affected_segments(a, b):
        info = chord_pcs(group[0].symbol) if len(group) == 1 else None
        if info is None:
            reasons.append(f'開始{start}〜{end}：コード未設定・N.C.・未対応・競合のため候補を作りません。')
            continue
        pcs, bass = info
        if any(n.string is None and n.start < end and start < n.end for n in melody):
            reasons.append(f'開始{start}〜{end}：未変換のメロディーがあるため、使用弦を確認できません。')
            continue
        ceiling = min((n.midi for n in melody if n.start <= start < n.end), default=89)
        chosen = []
        for role in (['bass'] if pattern == 'bass' else ['bass', 'accompaniment', 'accompaniment']):
            options = []
            for string, opened in TUNING.items():
                if any(n.string == string and n.start <= start < n.end for n in existing + proposal + chosen):
                    continue
                finish = min([end, *(n.start for n in existing if n.string == string and start < n.start < end)])
                for fret in range(max_fret + 1):
                    midi = opened + fret
                    if midi % 12 not in ({bass} if role == 'bass' else pcs):
                        continue
                    if role != 'bass' and any(n.midi % 12 == midi % 12 for n in chosen):
                        continue
                    frets = [n.fret for n in existing + chosen if n.start <= start < n.end and n.fret]
                    if fret:
                        frets.append(fret)
                    spread = max(frets) - min(frets) if frets else 0
                    options.append(((midi >= ceiling, spread > state['span_limit'], midi if role == 'bass' else spread,
                                     fret, string), positioned_note('', role, string, fret, start, finish - start)))
            if not options:
                reasons.append(f'開始{start}：衝突しない{ROLES[role]}の候補がありません。')
                if role == 'bass':
                    break
                continue
            chosen.append(min(options, key=lambda item: item[0])[1])
        proposal.extend(chosen)
    return {'notes': [replace(n, note_id=f'candidate-{i}').to_data() for i, n in enumerate(proposal, 1)],
            'reasons': reasons, 'start': str(a), 'end': str(b), 'pattern': pattern}


def act(state, selection, fingering, progression, rows, action, form):
    selection.get_confirmed_melody()
    if not rows:
        raise ValueError('単旋律のメロディーと運指を確認してください。')
    state = sync(state, selection, fingering, progression, rows) or create(selection, fingering, progression, rows)
    if action == 'start':
        return state
    if action == 'edit':
        note_id = form.get('arr_note_id')
        if note_id not in {n['note_id'] for n in state['notes']}:
            raise ValueError('編集する追加音が見つかりません。')
        state['selected'] = note_id
        return state
    if action == 'cancel':
        state['selected'] = None
        return state
    if action == 'span':
        state['span_limit'] = integer(form.get('span_limit'), 0, 24, '押弦の広がりの目安')
        state['candidate'] = None
        return state
    if action == 'review':
        state['baseline'] = [n.to_data() for n in melody_notes(rows)]
        return state
    if action == 'generate':
        state['candidate'] = generate(state, melody_notes(rows), progression, fingering['max_fret'], form)
        proposed = {**state, 'notes': state['notes'] + state['candidate']['notes']}
        warnings, _ = validate(proposed, melody_notes(rows), progression, fingering['max_fret'])
        state['candidate']['warnings'] = [w for w in warnings if any(i.startswith('candidate-') for i in w['ids'])]
        return state
    if action == 'skip':
        state['candidate'] = None
        return state
    if action in ('undo', 'redo'):
        other = 'redo' if action == 'undo' else 'undo'
        if not state[action]:
            raise ValueError('戻せる編曲編集がありません。')
        state[other] = (state[other] + [deepcopy(state['notes'])])[-HISTORY_LIMIT:]
        state['notes'] = state[action].pop()
        state['candidate'] = None
        state['selected'] = None
        return state
    previous = deepcopy(state['notes'])
    if action == 'save':
        role = form.get('role')
        if role not in ('bass', 'accompaniment'):
            raise ValueError('追加音はベースまたは伴奏を選択してください。')
        note_id = form.get('arr_note_id', '')
        if note_id and note_id not in {n['note_id'] for n in state['notes']}:
            raise ValueError('編集する追加音が見つかりません。')
        start = progression.position(form.get('measure_id'), form.get('offset', ''))
        duration = number(form.get('duration', ''), '音の長さ')
        if not 0 < duration <= progression.end - start:
            raise ValueError('音の長さは0より大きく、曲末を超えない値を指定してください。')
        string = integer(form.get('string'), 1, 6, '弦')
        fret = integer(form.get('fret'), 0, 24, 'フレット')
        if not note_id:
            note_id = f'arr-{state["next_id"]}'
            state['next_id'] += 1
        note = positioned_note(note_id, role, string, fret, start, duration)
        state['notes'] = [n for n in state['notes'] if n['note_id'] != note_id] + [note.to_data()]
        state['selected'] = note_id
    elif action == 'delete':
        note_id = form.get('arr_note_id')
        if note_id not in {n['note_id'] for n in state['notes']}:
            raise ValueError('削除する追加音が見つかりません。')
        state['notes'] = [n for n in state['notes'] if n['note_id'] != note_id]
        state['selected'] = None
    elif action == 'adopt':
        if not state['candidate'] or not state['candidate']['notes']:
            raise ValueError('採用する候補がありません。現在の設定で生成してください。')
        for data in state['candidate']['notes']:
            state['notes'].append({**data, 'note_id': f'arr-{state["next_id"]}'})
            state['next_id'] += 1
    else:
        raise ValueError('編曲の操作が不正です。')
    if previous != state['notes']:
        state['undo'] = (state['undo'] + [previous])[-HISTORY_LIMIT:]
        state['redo'] = []
    state['candidate'] = None
    return state
