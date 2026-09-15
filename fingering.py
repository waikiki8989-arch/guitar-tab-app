"""Standard-tuning fingering, separate from the original, exact melody."""
from copy import deepcopy
from fractions import Fraction

from music21.pitch import Pitch

# String numbering and pitches are sounding pitches, not guitar notation.
TUNING = {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40}


def settings(max_fret=24, octave=0):
    try:
        fret, shift = int(str(max_fret)), int(str(octave))
    except (ValueError, TypeError) as exc:
        raise ValueError('フレット上限は0〜24、オクターブ移動は-1・0・1を指定してください。') from exc
    if not 0 <= fret <= 24 or shift not in (-1, 0, 1):
        raise ValueError('フレット上限は0〜24、オクターブ移動は-1・0・1を指定してください。')
    return fret, shift


def pitch_info(name, octave=0):
    pitch = Pitch(name.replace('b', '-'))
    pitch.octave += octave
    return pitch.nameWithOctave.replace('-', 'b'), Fraction(str(pitch.ps))


def positions(midi, max_fret=24):
    max_fret, _ = settings(max_fret)
    midi = Fraction(midi)
    if midi.denominator != 1:
        return ()
    return tuple(sorted(((string, int(midi) - opened) for string, opened in TUNING.items()
                         if 0 <= midi - opened <= max_fret), key=lambda p: (p[1], p[0])))


def tied_groups(notes):
    """A single melodic line can only tie into its immediately preceding note."""
    groups = []
    for note in notes:
        previous = groups[-1][-1] if groups else None
        if (previous and previous.tie in ('start', 'continue') and note.tie in ('stop', 'continue')
                and previous.start + previous.duration == note.start
                and (previous.part_id, previous.staff, previous.voice, previous.pitch)
                == (note.part_id, note.staff, note.voice, note.pitch)):
            groups[-1].append(note)
        else:
            groups.append([note])
    return groups


def movement(a, b):
    # An open string does not require moving the fretting hand to fret zero.
    frets = abs(a[1] - b[1]) if a[1] and b[1] else 0
    return 2 * frets + abs(a[0] - b[0])


def best_path(options):
    """Dynamic programming; minimize motion, then fret sum. Stable input breaks ties.

    Back pointers keep memory linear in melody length. Each group has <= 6 positions.
    """
    if not options:
        return []
    scores = [(0, p[1]) for p in options[0]]
    parents = []
    for previous, current in zip(options, options[1:]):
        new_scores, links = [], []
        for p in current:
            score, index = min(((s[0] + movement(q, p), s[1] + p[1]), i)
                               for i, (q, s) in enumerate(zip(previous, scores)))
            new_scores.append(score)
            links.append(index)
        parents.append(links)
        scores = new_scores
    index = min(range(len(scores)), key=lambda i: (scores[i], i))
    path = [options[-1][index]]
    for level in range(len(parents) - 1, -1, -1):
        index = parents[level][index]
        path.append(options[level][index])
    return list(reversed(path))


def reconcile(selection, state=None, *, recalculate=False):
    state = deepcopy(state) if state is not None else {'max_fret': 24, 'octave': 0, 'choices': {}, 'locks': {}}
    maximum, octave = settings(state['max_fret'], state['octave'])
    ids = selection.selected_ids
    for field in ('choices', 'locks'):
        state[field] = {key: value for key, value in state[field].items() if key in ids}
    if not selection.confirmed:
        return state, (), None
    notes = selection.get_confirmed_melody()
    if any(a.start + a.duration > b.start for a, b in zip(notes, notes[1:])):
        return state, (), '発音時間が重なっています。単旋律になるようメロディーを選び直してください。'
    groups = tied_groups(notes)
    rows, group_options = [], []
    for group in groups:
        name, midi = pitch_info(group[0].pitch, octave)
        candidates = positions(midi, maximum)
        locks = [state['locks'][n.note_id] for n in group if n.note_id in state['locks']]
        reason = ''
        allowed = candidates
        if locks:
            locked_positions = {tuple(lock['position']) for lock in locks}
            if (len(locked_positions) != 1 or any(Fraction(lock['midi']) != midi for lock in locks)
                    or not locked_positions.issubset(candidates)):
                reason = '固定運指が現在の設定またはタイと一致しません。再選択または固定解除が必要です。'
                allowed = ()
            else:
                allowed = (tuple(locks[0]['position']),)
        elif not recalculate:
            previous = {tuple(state['choices'][n.note_id]) for n in group if n.note_id in state['choices']}
            if len(previous) == 1 and previous.issubset(candidates):
                allowed = tuple(previous)
        if not candidates and not reason:
            reason = ('微分音は標準フレットでは変換できません。' if midi.denominator != 1
                      else '音域外またはフレット上限内に候補がありません。上限やオクターブ移動を変更してください。')
        group_options.append(allowed)
        rows.append([{'note': n, 'guitar_pitch': name, 'midi': str(midi), 'candidates': candidates,
                      'position': None, 'locked': bool(locks), 'reason': reason,
                      'saved_lock': locks[0]['position'] if locks else None,
                      'tie_ids': [item.note_id for item in group]} for n in group])
    # An unconvertible group breaks a path but never removes the note from the result.
    choices, start = {}, 0
    while start < len(groups):
        if not group_options[start]:
            start += 1
            continue
        end = start
        while end < len(groups) and group_options[end]:
            end += 1
        for group_rows, position in zip(rows[start:end], best_path(group_options[start:end])):
            for row in group_rows:
                row['position'] = position
                choices[row['note'].note_id] = list(position)
        start = end
    state['choices'] = choices
    return state, tuple(row for group in rows for row in group), None


def apply_action(selection, state, action, form):
    selection.get_confirmed_melody()
    state, rows, error = reconcile(selection, state)
    if error:
        raise ValueError(error)
    if action == 'calculate':
        state['max_fret'], state['octave'] = settings(form.get('max_fret', ''), form.get('octave', ''))
        return reconcile(selection, state, recalculate=True)[0]
    if action not in ('choose', 'unlock'):
        raise ValueError('運指の操作が不正です。')
    row = next((row for row in rows if row['note'].note_id == form.get('note_id')), None)
    if row is None:
        raise ValueError('変更する音符が選択済みメロディーにありません。')
    if action == 'choose':
        try:
            position = tuple(int(v) for v in form.get('position', '').split(':'))
        except ValueError as exc:
            raise ValueError('表示された弦・フレット候補を選択してください。') from exc
        if position not in row['candidates']:
            raise ValueError('表示された弦・フレット候補を選択してください。')
        for note_id in row['tie_ids']:
            state['choices'][note_id] = list(position)
            if form.get('lock') == 'yes':
                state['locks'][note_id] = {'position': list(position), 'midi': row['midi']}
            else:
                state['locks'].pop(note_id, None)
    else:
        for note_id in row['tie_ids']:
            state['locks'].pop(note_id, None)
    return reconcile(selection, state)[0]
