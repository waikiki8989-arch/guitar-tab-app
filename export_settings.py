"""Persistent notation output preferences, independent of project serialization."""
DEFAULTS = {'title': '', 'composer': '', 'arranger': '', 'layout': 'both'}


def normalize(value=None):
    if value is None:
        return dict(DEFAULTS)
    if type(value) is not dict or set(value) != set(DEFAULTS):
        raise ValueError('出力設定の項目が不正です。')
    result = {}
    for key in ('title', 'composer', 'arranger'):
        text = value[key]
        if type(text) is not str or len(text) > 120 or any(ord(c) < 32 for c in text):
            raise ValueError('曲名・作曲者・編曲者は120文字以内の1行で入力してください。')
        try:
            text.encode('utf-8')
        except UnicodeError as exc:
            raise ValueError('曲名・作曲者・編曲者に無効な文字が含まれています。') from exc
        if any(c in text for c in ('\ufffe', '\uffff')):
            raise ValueError('曲名・作曲者・編曲者に無効な文字が含まれています。')
        result[key] = text.strip()
    if value['layout'] not in ('both', 'tab'):
        raise ValueError('出力する譜面を選んでください。')
    result['layout'] = value['layout']
    return result
