"""Versioned, portable project files. Never deserialize an uploaded working token."""
from copy import deepcopy
from dataclasses import fields
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import re

from defusedxml import ElementTree as SafeET
from music21.pitch import Pitch

from arrangement import ArrangementNote
from chord_progression import ChordEvent, ChordSymbol, ScoreMeasure, SOURCE_LABELS, parse_chord_name
from fingering import TUNING
from musicxml_parser import ScoreNote

FORMAT = 'guitar-tab-project'
VERSION = 1
MAX_PROJECT_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 10000
ARRANGEMENT_FIELDS = ('notes', 'next_id', 'span_limit', 'baseline')


def now():
    return datetime.now(timezone.utc).isoformat()


def name(value):
    text(value, 'プロジェクト名', 120)
    if not value.strip():
        fail('プロジェクト名', '空欄にはできません')
    return value.strip()


def filename(value):
    # Keep Japanese names, but remove path separators, control characters and OS-reserved punctuation.
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', value).strip(' .') or 'project'
    return stem + '.guitar-tab.json'


def snapshot(selection, progression, notation, fingering, arrangement, source_filename):
    return deepcopy({'selection': selection.to_data(), 'progression': progression.to_data(),
        'notation': notation, 'fingering': fingering, 'filename': source_filename,
        'tuning': {str(k): v for k, v in TUNING.items()},
        'arrangement': {k: arrangement[k] for k in ARRANGEMENT_FIELDS} if arrangement is not None else None})


def digest(data, project_name):
    encoded = json.dumps([project_name, data], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def track(data, metadata=None):
    """Track changes in persistent content, excluding display choices and edit history."""
    metadata = deepcopy(metadata) if metadata else {
        'name': name(data['filename'].rsplit('.', 1)[0][:120] or '無題'),
        'created_at': now(), 'saved_digest': None,
    }
    current = digest(data, metadata['name'])
    if current != metadata.get('current_digest'):
        timestamp = max(datetime.fromisoformat(now()), datetime.fromisoformat(metadata.get('updated_at', metadata['created_at'])))
        metadata.update(current_digest=current, updated_at=timestamp.isoformat())
    return metadata


def dumps(data, metadata):
    document = {'format': FORMAT, 'version': VERSION,
                'project': {k: metadata[k] for k in ('name', 'created_at', 'updated_at')}, 'data': data}
    validate(document)
    result = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8') + b'\n'
    if len(result) > MAX_PROJECT_BYTES:
        fail('ファイルサイズ', 'プロジェクトは8 MiB以下にしてください')
    return result


def fail(path, reason='値・形式が不正です'):
    raise ValueError(f'プロジェクトの{path}：{reason}。')


def obj(value, keys, path):
    if type(value) is not dict or set(value) != set(keys):
        fail(path, '必須項目がないか、未対応の項目があります')
    return value


def array(value, path, limit=MAX_ITEMS):
    if type(value) is not list or len(value) > limit:
        fail(path, f'{limit}件以下の配列が必要です')
    return value


def text(value, path, limit=1024, nullable=False):
    if nullable and value is None:
        return value
    if type(value) is not str or len(value) > limit or any(ord(c) < 32 and c not in '\n\t\r' for c in value):
        fail(path, '文字列が不正です')
    try:
        value.encode('utf-8')
    except UnicodeError:
        fail(path, '文字列が不正です')
    return value


def integer(value, path, low, high):
    if type(value) is not int or not low <= value <= high:
        fail(path, f'{low}〜{high}の整数が必要です')
    return value


def boolean(value, path):
    if type(value) is not bool:
        fail(path, '真偽値が必要です')


def rational(value, path):
    # JSON floats would lose exact timing. Bound textual input before constructing integers.
    if type(value) is not str or not re.fullmatch(r'-?\d{1,16}(?:/\d{1,16})?', value):
        fail(path, '整数または分数を文字列で指定してください')
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError):
        fail(path, '分数が不正です')
    if abs(result) > 1000000:
        fail(path, '数値が大きすぎます')
    return result


def pitch(value, path):
    text(value, path, 32)
    try:
        p = Pitch(value.replace('b', '-'))
        if p.octave is None or not -128 <= p.ps <= 255:
            fail(path, '音高またはオクターブが不正です')
        return Fraction(str(p.ps))
    except Exception as exc:
        raise ValueError(f'プロジェクトの{path}：音高が不正です。') from exc


def identifier(value, path, seen=None):
    text(value, path, 200)
    if not value or (seen is not None and value in seen):
        fail(path, 'IDが空か重複しています')
    if seen is not None:
        seen.add(value)


def position(value, path):
    if type(value) is not list or len(value) != 2:
        fail(path, '弦とフレットの組が必要です')
    integer(value[0], path + '.弦', 1, 6)
    integer(value[1], path + '.フレット', 0, 24)
    return TUNING[value[0]] + value[1]


def timing(note, end, path):
    start = rational(note['start'], path + '.開始位置')
    duration = rational(note['duration'], path + '.長さ')
    if not 0 <= start < end or not 0 < duration <= end - start:
        fail(path, '開始位置・長さが曲の範囲外です')


def validate_progression(data):
    obj(data, ('measures', 'original', 'events'), 'コード進行')
    seen, end = set(), Fraction(0)
    measures = array(data['measures'], '小節', 2000)
    if not measures:
        fail('小節', '小節がありません')
    for m in measures:
        obj(m, (f.name for f in fields(ScoreMeasure)), '小節')
        identifier(m['measure_id'], '小節ID', seen)
        text(m['number'], '小節番号')
        start, duration = rational(m['start'], '小節の開始'), rational(m['duration'], '小節の長さ')
        if start != end or not 0 < duration <= 1024:
            fail('小節', '小節の時間が連続していないか、長さが不正です')
        end += duration
    for key in ('original', 'events'):
        ids = set()
        for event in array(data[key], 'コード一覧'):
            obj(event, (f.name for f in fields(ChordEvent)), 'コード')
            identifier(event['event_id'], 'コードID', ids)
            if not 0 <= rational(event['start'], 'コード位置') < end:
                fail('コード位置', '曲の範囲外です')
            if event['source'] not in SOURCE_LABELS:
                fail('コードの入力元')
            text(event['part_id'], 'コードのパートID', nullable=True)
            boolean(event['restored'], 'コードの復元状態')
            symbol = obj(event['symbol'], (f.name for f in fields(ChordSymbol)), 'コード記号')
            for field in ('name', 'root', 'kind', 'bass'):
                text(symbol[field], 'コード記号.' + field, nullable=field != 'name')
            boolean(symbol['supported'], 'コードの対応状態')
            raw = text(symbol['raw_xml'], '元コードXML', 65536)
            if raw:
                try:
                    if SafeET.fromstring(raw).tag != 'harmony':
                        fail('元コードXML')
                except Exception as exc:
                    raise ValueError('プロジェクトの元コードXMLが不正です。') from exc
            if not symbol['supported']:
                if not raw:
                    fail('未対応コード', '元のXMLが必要です')
            elif symbol['kind'] == 'unset':
                if (symbol['name'], symbol['root'], symbol['bass']) != ('コード未設定', None, None):
                    fail('未設定コード')
            else:
                expected = parse_chord_name(symbol['name'])
                if any(symbol[k] != getattr(expected, k) for k in ('name', 'root', 'kind', 'bass')):
                    fail('コード記号', '名前と構成情報が一致しません')
    return end


def validate_selection(selection, end):
    obj(selection, ('notes', 'selected_ids', 'confirmed'), 'メロディー')
    ids, notes = set(), {}
    for note in array(selection['notes'], '音符一覧'):
        obj(note, (f.name for f in fields(ScoreNote)), '音符')
        identifier(note['note_id'], '音符ID', ids)
        notes[note['note_id']] = note
        pitch(note['pitch'], '音符の実音')
        timing(note, end, '音符')
        for key in ('part_id', 'part_name', 'staff', 'voice', 'measure', 'note_type', 'accidental'):
            text(note[key], '音符.' + key, nullable=key not in ('part_id', 'measure'))
        if note['tie'] not in (None, 'start', 'stop', 'continue'):
            fail('タイ')
        integer(note['dots'], '付点', 0, 8)
        for key in ('tuplet_actual', 'tuplet_normal'):
            if note[key] is not None:
                integer(note[key], '連符', 1, 1024)
        if (note['tuplet_actual'] is None) != (note['tuplet_normal'] is None):
            fail('連符', '実際の音数と基準の音数が必要です')
    selected = set()
    for note_id in array(selection['selected_ids'], '選択音符ID'):
        identifier(note_id, '選択音符ID', selected)
        if note_id not in ids:
            fail('選択音符ID', '存在しない音符を参照しています')
    boolean(selection['confirmed'], 'メロディーの確定状態')
    if selection['confirmed'] and (not selected or len({Fraction(notes[i]['start']) for i in selected}) != len(selected)):
        fail('メロディーの確定状態', '未選択または同時開始の複数音があります')
    return notes, selected


def validate_notation(data):
    obj(data, ('changes', 'warnings'), '記譜情報')
    for message in array(data['warnings'], '記譜上の注意'):
        text(message, '記譜上の注意')
    for change in array(data['changes'], '拍子・調号・テンポ'):
        obj(change, ('kind', 'start', 'value'), '記譜の変更')
        rational(change['start'], '記譜の変更位置')  # Out-of-score metadata is retained with the existing preview warning.
        kind, value = change['kind'], change['value']
        if kind == 'key':
            integer(value, '調号', -7, 7)
        elif kind == 'tempo':
            if not 0 < rational(value, 'テンポ') <= 1000:
                fail('テンポ')
        elif kind == 'time':
            if type(value) is not str or not re.fullmatch(r'[1-9]\d{0,2}/[1-9]\d{0,2}', value):
                fail('拍子')
            beats, unit = map(int, value.split('/'))
            if beats > 128 or unit not in (1, 2, 4, 8, 16, 32, 64, 128):
                fail('拍子', '未対応の拍子です')
        else:
            fail('記譜の種類')


def validate_fingering(data, notes, selected):
    obj(data, ('max_fret', 'octave', 'choices', 'locks'), '運指')
    integer(data['max_fret'], 'フレット上限', 0, 24)
    integer(data['octave'], 'オクターブ移動', -1, 1)
    for field in ('choices', 'locks'):
        mapping = data[field]
        if type(mapping) is not dict or not set(mapping).issubset(selected):
            fail('運指の音符ID', '選択済み音符にないIDがあります')
        for note_id, value in mapping.items():
            if field == 'locks':
                obj(value, ('position', 'midi'), '固定運指')
                midi = rational(value['midi'], '固定時の実音')
                if midi != position(value['position'], '固定運指'):
                    fail('固定運指', '固定時の実音と弦・フレットが一致しません')
                # A valid old lock may disagree with the CURRENT octave, maximum fret or tie.
            else:
                midi = pitch(notes[note_id]['pitch'], '運指の実音') + data['octave'] * 12
                if position(value, '運指') != midi:
                    fail('運指', '実音と弦・フレットが一致しません')


def validate_arrangement(data, notes, end):
    if data is None:
        return
    obj(data, ARRANGEMENT_FIELDS, '編曲')
    integer(data['span_limit'], '押弦の広がり', 0, 24)
    integer(data['next_id'], '編曲の次のID', 1, 1000000000)
    for field in ('notes', 'baseline'):
        seen = set()
        for n in array(data[field], '編曲音符', 2000):
            obj(n, (f.name for f in fields(ArrangementNote)), '編曲音符')
            identifier(n['note_id'], '編曲音符ID', seen)
            midi = rational(n['midi'], '編曲音の実音')
            if pitch(n['pitch'], '編曲音名') != midi:
                fail('編曲音', '音名と実音が一致しません')
            timing(n, end, '編曲音')
            if n['tie'] not in (None, 'start', 'stop', 'continue'):
                fail('編曲音のタイ')
            if field == 'notes':
                match = re.fullmatch(r'arr-([1-9]\d{0,8})', n['note_id'])
                if not match or int(match[1]) >= data['next_id'] or n['note_id'] in notes:
                    fail('編曲音符ID', '追加音のIDまたは採番情報が不正です')
                if n['role'] not in ('bass', 'accompaniment') or n['source_note_id'] is not None or n['tie'] is not None:
                    fail('追加音の役割・参照')
            else:
                if type(n['source_note_id']) is not str or n['source_note_id'] not in notes:
                    fail('編曲の元音符ID', '存在しない音符を参照しています')
                if n['note_id'] != 'melody-' + n['source_note_id'] or n['role'] != 'melody':
                    fail('編曲のメロディー対応')
                if n['string'] is None and n['fret'] is None:
                    continue  # Unconverted melody is valid project data.
            if position([n['string'], n['fret']], '編曲の運指') != midi:
                fail('編曲の運指', '実音と弦・フレットが一致しません')


def validate(document):
    obj(document, ('format', 'version', 'project', 'data'), 'ファイル構造')
    if document['format'] != FORMAT:
        fail('形式', 'Guitar Tab Appのプロジェクトではありません')
    if type(document['version']) is not int or document['version'] != VERSION:
        fail('形式バージョン', '未対応のバージョンです（対応：1）')
    metadata = obj(document['project'], ('name', 'created_at', 'updated_at'), 'プロジェクト情報')
    name(metadata['name'])
    dates = []
    for key in ('created_at', 'updated_at'):
        text(metadata[key], key, 64)
        try:
            date = datetime.fromisoformat(metadata[key])
            if date.tzinfo is None:
                fail(key, 'タイムゾーンが必要です')
            dates.append(date)
        except ValueError as exc:
            raise ValueError(f'プロジェクトの{key}：日時が不正です。') from exc
    if dates[1] < dates[0]:
        fail('更新日時', '作成日時より前です')
    data = obj(document['data'], ('selection', 'progression', 'notation', 'fingering', 'filename', 'tuning', 'arrangement'), '保存内容')
    text(data['filename'], '元ファイル名')
    obj(data['tuning'], (str(k) for k in TUNING), 'チューニング')
    if any(type(data['tuning'][str(k)]) is not int or data['tuning'][str(k)] != v for k, v in TUNING.items()):
        fail('チューニング', '標準チューニング以外は未対応です')
    end = validate_progression(data['progression'])
    notes, selected = validate_selection(data['selection'], end)
    validate_notation(data['notation'])
    validate_fingering(data['fingering'], notes, selected)
    validate_arrangement(data['arrangement'], notes, end)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail('JSON', '同じ項目が重複しています')
        result[key] = value
    return result


def loads(raw):
    if len(raw) > MAX_PROJECT_BYTES:
        fail('ファイルサイズ', 'プロジェクトは8 MiB以下にしてください')
    try:
        document = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                              parse_constant=lambda _: fail('JSON', '有限の数値が必要です'))
        validate(document)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('プロジェクトを読み取れません。UTF-8のJSONファイルを選択してください。') from exc
    except (TypeError, KeyError, OverflowError) as exc:
        raise ValueError('プロジェクトの項目・データ型が不正です。') from exc
    return document
