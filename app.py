from pathlib import Path
from io import BytesIO
import os
import secrets

from flask import Flask, render_template, request, send_file, jsonify
from itsdangerous import BadData, URLSafeTimedSerializer

from chord_progression import ChordProgression, KIND_LABELS, SOURCE_LABELS
from chord_estimation import DEFAULT_SETTINGS, estimate_sections
from melody import FILTER_FIELDS, MelodySelection, filter_notes, filter_options
from musicxml_parser import MAX_XML_BYTES, parse_score
from score_preview import build_preview
from fingering import reconcile, apply_action
from tab_score import editor_state, edit, build_tab_preview
import arrangement
from arrangement_score import build_arrangement_preview
import project
import export_settings
import score_export

from tab_generator import CHORDS, generate_chord_tab, generate_tab


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config["MAX_FORM_MEMORY_SIZE"] = 12 * 1024 * 1024
app.config["SECRET_KEY"] = os.environ.get("GUITAR_TAB_SECRET_KEY") or secrets.token_hex(32)


def state_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="melody-selection-v1")


def render_page(*, selection=None, progression=None, notation=None, fingering_state=None, tab_state=None, arrangement_state=None, estimate_state=None, project_state=None, output_settings=None, filename="", filters=None, **context):
    output_settings = export_settings.normalize(output_settings)
    filters = filters or {}
    fingering_rows, fingering_error = (), None
    arrangement_warnings, arrangement_spans, arrangement_rows = [], [], []
    arrangement_form = {"arr_note_id": "", "role": "bass", "measure_id": "", "offset": "0", "duration": "1", "string": 6, "fret": 0}
    if selection is not None:
        fingering_state, fingering_rows, fingering_error = reconcile(selection, fingering_state)
        tab_state = editor_state(selection, fingering_state, tab_state)
        if arrangement_state is not None and progression is not None:
            arrangement_state = arrangement.sync(arrangement_state, selection, fingering_state, progression, fingering_rows)
            arrangement_warnings, arrangement_spans = arrangement.validate(arrangement_state, arrangement.melody_notes(fingering_rows), progression, fingering_state['max_fret'])
            arrangement_rows = arrangement.extra_notes(arrangement_state)
            selected = next((n for n in arrangement_rows if n.note_id == arrangement_state.get('selected')), None)
            if selected:
                measure = progression.measure_at(selected.start)
                arrangement_form = {'arr_note_id': selected.note_id, 'role': selected.role, 'measure_id': measure.measure_id,
                                    'offset': str(selected.start - measure.start), 'duration': str(selected.duration),
                                    'string': selected.string, 'fret': selected.fret}
        project_data = project.snapshot(selection, progression, notation or {'changes': [], 'warnings': []},
                                        fingering_state, arrangement_state, filename, output_settings)
        project_state = project.track(project_data, project_state)
        context.update(
            notes=selection.notes,
            candidates=filter_notes(selection.notes, filters),
            options={field: filter_options(selection.notes, field) for field in FILTER_FIELDS},
            state_token=state_serializer().dumps({
                "selection": selection.to_data(), "filename": filename, "filters": filters,
                "progression": progression.to_data() if progression is not None else None,
                "estimate_state": estimate_state,
                "notation": notation or {},
                "fingering": fingering_state,
                "tab_editor": tab_state,
                "arrangement": arrangement_state,
                "project": project_state,
                "export_settings": output_settings,
            }),
        )
    else:
        context["notes"] = None
    results = estimate_sections(selection.notes, progression.measures, estimate_state["settings"]) if estimate_state and selection is not None and progression is not None else ()
    if context.get("estimate_form") is None:
        context["estimate_form"] = estimate_state["settings"] if estimate_state else DEFAULT_SETTINGS
    existing = {i for i, result in enumerate(results)
                if any(e.symbol.kind != "unset" for _, _, events in progression.affected_segments(result.start, result.end) for e in events)}
    preview, preview_error = None, None
    if selection is not None and progression is not None:
        try:
            if fingering_rows and arrangement_state is not None:
                preview = build_arrangement_preview(selection, progression, notation, fingering_state, fingering_rows, arrangement_state, filename)
            elif fingering_rows:
                preview = build_tab_preview(selection, progression, notation, fingering_state, fingering_rows, filename)
            else:
                preview = build_preview(selection, progression, notation, filename or 'メロディーとコード')
        except ValueError as exc:
            preview_error = str(exc)
    return render_template("index.html", chords=CHORDS.keys(), selection=selection,
                           progression=progression, kind_labels=KIND_LABELS,
                           source_labels=SOURCE_LABELS, estimate_state=estimate_state,
                           estimate_results=results, estimate_existing=existing,
                           preview=preview, preview_error=preview_error,
                           arrangement_state=arrangement_state, arrangement_rows=arrangement_rows,
                           arrangement_warnings=arrangement_warnings, arrangement_spans=arrangement_spans,
                           arrangement_form=arrangement_form, arrangement_roles=arrangement.ROLES,
                           project_state=project_state, output_settings=output_settings,
                           project_dirty=bool(project_state and project_state['current_digest'] != project_state['saved_digest']),
                           tab_state=tab_state,
                           tab_selected=next((r for r in fingering_rows if tab_state and r["note"].note_id == tab_state["selected"]), None),
                           fingering_state=fingering_state, fingering_rows=fingering_rows, fingering_error=fingering_error,
                           filename=filename, filters=filters, **context)


@app.errorhandler(413)
def upload_too_large(error):
    return render_page(error="アップロードが大きすぎます。MusicXMLは2 MiB以下、プロジェクトは8 MiB以下にしてください。"), 413


@app.post('/project/download')
def download_project():
    try:
        state = state_serializer().loads(request.form.get('score_state', ''), max_age=8 * 60 * 60)
        selection = MelodySelection.from_data(state['selection'])
        progression = ChordProgression.from_data(state['progression'])
        data = project.snapshot(selection, progression, state['notation'], state['fingering'],
                                state['arrangement'], state['filename'], state.get('export_settings'))
        metadata = project.track(data, state.get('project'))
        content = project.dumps(data, metadata)
        # This records the downloaded snapshot, not confirmation that the OS saved it.
        metadata['saved_digest'] = metadata['current_digest']
        state['project'] = metadata
        if request.accept_mimetypes.best == 'application/json':
            response = jsonify(content=content.decode('utf-8'), filename=project.filename(metadata['name']),
                               state_token=state_serializer().dumps(state))
        else:
            response = send_file(BytesIO(content), mimetype='application/json', as_attachment=True,
                                 download_name=project.filename(metadata['name']))
        response.headers['Cache-Control'] = 'no-store'
        return response
    except (BadData, KeyError, ValueError) as exc:
        message = ('作業情報が無効または期限切れです。元の画面に戻り、プロジェクトを開き直してください。'
                   if isinstance(exc, (BadData, KeyError)) else str(exc))
        if request.accept_mimetypes.best == 'application/json':
            return jsonify(error=message), 400
        return render_template('project_download_error.html', error=message), 400


@app.post('/score/<action>')
def export_score(action):
    if action not in ('print', 'musicxml'):
        return 'Not found', 404
    token = request.form.get('score_state', '')
    try:
        state = state_serializer().loads(token, max_age=8 * 60 * 60)
        result = score_export.prepare(
            MelodySelection.from_data(state['selection']), ChordProgression.from_data(state['progression']),
            state['notation'], state['fingering'], state['arrangement'], state.get('export_settings'),
            state.get('project', {}).get('name') or 'ギター譜')
        acknowledged = request.form.get('acknowledge') == 'yes'
        if result['blockers'] or (result['warnings'] and not acknowledged):
            response = app.make_response((render_template('export_check.html', result=result, action=action,
                                                        state_token=token), 422 if result['blockers'] else 200))
        elif action == 'musicxml':
            name = project.filename(result['settings']['title']).removesuffix('.guitar-tab.json') + '.musicxml'
            response = send_file(BytesIO(result['xml'].encode('utf-8')), mimetype='application/vnd.recordare.musicxml+xml',
                                 as_attachment=True, download_name=name)
        else:
            response = app.make_response(render_template('score_print.html', result=result))
        response.headers['Cache-Control'] = 'no-store'
        return response
    except (BadData, KeyError, ValueError) as exc:
        error = str(exc) if isinstance(exc, ValueError) else '作業情報が無効または期限切れです。元の画面からやり直してください。'
        return render_template('project_download_error.html', error=error), 400


def restore_project(document):
    data = document['data']
    selection = MelodySelection.from_data(data['selection'])
    progression = ChordProgression.from_data(data['progression'])
    fingering, rows, _ = reconcile(selection, data['fingering'])
    extra = data['arrangement']
    if extra is not None:
        extra = {**arrangement.create(selection, fingering, progression, rows), **extra}
    metadata = {**document['project'], 'saved_digest': project.digest(data, document['project']['name'])}
    metadata['current_digest'] = metadata['saved_digest']
    return selection, progression, fingering, extra, metadata


@app.route("/", methods=["GET", "POST"])
@app.post('/project/open', endpoint='open_project')
def index():
    tab = None
    error = None
    frets = ""
    selected_chord = ""
    selection = None
    progression = None
    notation = {}
    fingering_state = None
    tab_state = None
    arrangement_state = None
    project_state = None
    output_settings = None
    estimate_state = None
    estimate_form = None
    chord_form = {"event_id": "", "symbol_name": "", "measure_id": "", "offset": "0"}
    filters = {}
    filename = ""
    message = None
    response_status = 200

    if request.method == "POST":
        mode = 'project_open' if request.path == '/project/open' else request.form.get("mode", "frets")

        try:
            token = request.form.get("score_state")
            if token:
                try:
                    state = state_serializer().loads(token, max_age=8 * 60 * 60)
                except BadData as exc:
                    if mode not in ("musicxml", "project_open"):
                        raise ValueError("作業情報が無効または期限切れです。楽譜を再度読み込んでください。") from exc
                else:
                    selection = MelodySelection.from_data(state["selection"])
                    filename, filters = state["filename"], state["filters"]
                    if state.get("progression") is not None:
                        progression = ChordProgression.from_data(state["progression"])
                    estimate_state = state.get("estimate_state")
                    notation = state.get("notation", {})
                    fingering_state = state.get("fingering")
                    tab_state = state.get("tab_editor")
                    arrangement_state = state.get("arrangement")
                    project_state = state.get("project")
                    output_settings = state.get("export_settings")
            if mode == "musicxml":
                uploaded = request.files.get("musicxml")
                if uploaded is None or not uploaded.filename:
                    raise ValueError("MusicXMLファイルを選択してください。")
                if Path(uploaded.filename).suffix.lower() not in (".musicxml", ".xml"):
                    raise ValueError(".musicxml または .xml を選択してください。.mxl・PDF・画像は未対応です。")
                raw = uploaded.read(MAX_XML_BYTES + 64 * 1024 + 1)
                if len(raw) > MAX_XML_BYTES + 64 * 1024:
                    response_status = 413
                    raise ValueError('アップロードが大きすぎます。MusicXMLは2 MiB以下にしてください。')
                imported = parse_score(raw)
                new_progression = ChordProgression.from_import(imported.measures, imported.harmonies)
                if selection is not None and request.form.get('replace_current') != 'yes':
                    raise ValueError('現在の作業を置き換える確認欄にチェックしてください。')
                selection = MelodySelection(imported.notes)
                progression = new_progression
                notation = imported.notation
                fingering_state = None
                tab_state = None
                arrangement_state = None
                estimate_state = None
                project_state = None
                output_settings = None
                filename, filters = uploaded.filename, {}
            elif mode == 'project_open':
                uploaded = request.files.get('project_file')
                if uploaded is None or not uploaded.filename:
                    raise ValueError('プロジェクトファイルを選択してください。')
                document = project.loads(uploaded.read(project.MAX_PROJECT_BYTES + 1))
                restored = restore_project(document)
                if selection is not None and request.form.get('replace_current') != 'yes':
                    raise ValueError('現在の作業を置き換える確認欄にチェックしてください。')
                selection, progression, fingering_state, arrangement_state, project_state = restored
                output_settings = document['data']['export_settings']
                notation, filename = document['data']['notation'], document['data']['filename']
                filters, tab_state, estimate_state = {}, None, None
                message = 'プロジェクトを開きました。演奏上の注意と未変換音を確認してください。'
            elif mode == 'export_settings':
                if selection is None:
                    raise ValueError('先に楽譜を開いてください。')
                output_settings = export_settings.normalize({key: request.form.get(key, '') for key in export_settings.DEFAULTS})
                message = '出力設定を反映しました。プロジェクトを保存すると、この設定も保存されます。'
            elif mode == 'project_name':
                if selection is None:
                    raise ValueError('先に楽譜またはプロジェクトを開いてください。')
                new_name = project.name(request.form.get('project_name', ''))
                project_state = {**project_state, 'name': new_name}
                message = 'プロジェクト名を変更しました。ファイルに残すにはプロジェクトを保存してください。'
            elif mode == "arrangement":
                if selection is None or progression is None:
                    raise ValueError("先にMusicXMLファイルを読み込んでください。")
                fingering_state, rows, problem = reconcile(selection, fingering_state)
                if problem:
                    raise ValueError(problem)
                arrangement_state = arrangement.act(arrangement_state, selection, fingering_state, progression, rows,
                                                     request.form.get('arr_action', ''), request.form)
                if tab_state:
                    tab_state['selected'] = None
                message = "編曲の操作を反映しました。演奏上の注意を確認してください。"
            elif mode == "tab_editor":
                if selection is None:
                    raise ValueError("先にMusicXMLファイルを読み込んでください。")
                fingering_state, tab_state = edit(selection, fingering_state, tab_state, request.form.get("tab_action", ""), request.form)
                if arrangement_state:
                    arrangement_state["selected"] = None
                message = "TAB譜の選択・運指を更新しました。"
            elif mode == "fingering":
                if selection is None:
                    raise ValueError("先にMusicXMLファイルを読み込んでください。")
                fingering_state = apply_action(selection, fingering_state, request.form.get("fingering_action", ""), request.form)
                tab_state = None
                message = "ギター用の運指を更新しました。タイでつながる音は同じ運指にそろえています。"
            elif mode == "estimate":
                if selection is None or progression is None:
                    raise ValueError("先にMusicXMLファイルを読み込んでください。")
                action = request.form.get("estimate_action", "")
                if action == "run":
                    estimate_form = {key: request.form.get(key, "").strip() for key in DEFAULT_SETTINGS}
                    estimate_sections(selection.notes, progression.measures, estimate_form)
                    estimate_state = {"settings": estimate_form, "skipped": []}
                    message = "コード候補を推定しました。コード進行は変更していません。"
                else:
                    if estimate_state is None:
                        raise ValueError("先にコード候補を推定してください。")
                    results = estimate_sections(selection.notes, progression.measures, estimate_state["settings"])
                    parts = action.split(":")
                    if not ((len(parts) == 2 and parts[0] == "skip") or (len(parts) == 3 and parts[0] == "adopt")):
                        raise ValueError("表示された区間と候補を選んでください。")
                    try:
                        numbers = [int(part) for part in parts[1:]]
                    except ValueError as exc:
                        raise ValueError("表示された区間と候補を選んでください。") from exc
                    try:
                        index = numbers[0]
                        if not 0 <= index < len(results):
                            raise ValueError
                        result = results[index]
                        if parts[0] == "skip" and len(parts) == 2:
                            estimate_state = {**estimate_state, "skipped": sorted(set(estimate_state["skipped"]) | {index})}
                            message = "この区間の候補を見送りました。コード進行は変更していません。"
                        elif parts[0] == "adopt" and len(parts) == 3:
                            rank = numbers[1]
                            if index in estimate_state["skipped"] or not 0 <= rank < len(result.candidates):
                                raise ValueError
                            progression = progression.apply_interval(
                                result.candidates[rank].symbol, result.start, result.end,
                                replace_existing=request.form.get(f"replace_{index}") == "yes")
                            message = "候補をこの区間に採用しました。区間外のコード進行は維持しています。"
                        else:
                            raise ValueError
                    except (IndexError, ValueError) as exc:
                        if str(exc):
                            raise
                        raise ValueError("表示された区間と候補を選んでください。") from exc
            elif mode == "progression":
                if progression is None:
                    raise ValueError("先にMusicXMLファイルを読み込んでください。")
                action = request.form.get("progression_action", "")
                if action == "save":
                    chord_form = {key: request.form.get(key, "") for key in chord_form}
                    updated = progression.save(chord_form["symbol_name"], chord_form["measure_id"],
                                               chord_form["offset"], chord_form["event_id"])
                    progression = updated
                    chord_form = {"event_id": "", "symbol_name": "", "measure_id": "", "offset": "0"}
                    message = "コード進行を更新しました。同じ位置の同じコードは1つにまとめています。"
                elif action.startswith("edit:"):
                    event = next((e for e in progression.events if e.event_id == action[5:]), None)
                    if event is None:
                        raise ValueError("変更するコードが見つかりません。")
                    measure = progression.measure_at(event.start)
                    chord_form = {"event_id": event.event_id, "symbol_name": event.symbol.name,
                                  "measure_id": measure.measure_id, "offset": str(event.start - measure.start)}
                elif action.startswith("delete:"):
                    progression = progression.delete(action[7:])
                    message = "コードを削除しました。前のコードの有効範囲は次のコードまで延びます。"
                elif action != "cancel":
                    raise ValueError("コード進行の操作が不正です。")
            elif mode == "melody":
                if selection is None:
                    raise ValueError("先にMusicXMLファイルを読み込んでください。")
                new_filters = {field: request.form.get(field, "") for field in FILTER_FIELDS}
                candidates = filter_notes(selection.notes, new_filters)
                action = request.form.get("action", "apply")
                if action not in {"filter", "apply", "bulk", "confirm"} and not action.startswith("remove:"):
                    raise ValueError("メロディーの操作が不正です。")
                visible_ids = {note.note_id for note in filter_notes(selection.notes, filters)}
                selection = selection.update_visible(visible_ids, request.form.getlist("selected_ids"))
                filters = new_filters
                if action == "bulk":
                    selection = selection.select(selection.selected_ids | {note.note_id for note in candidates})
                    message = f"表示条件に一致する{len(candidates)}音を選択に追加しました。"
                elif action.startswith("remove:"):
                    note_id = action.removeprefix("remove:")
                    if note_id not in {note.note_id for note in selection.notes}:
                        raise ValueError("除外する音符が見つかりません。")
                    selection = selection.select(selection.selected_ids - {note_id})
                    message = "音符を選択から除外しました。"
                elif action == "confirm":
                    selection = selection.confirm()
                    message = "メロディーを確定しました。"
                else:
                    message = "選択と表示条件を反映しました。"
            elif mode == "chord":
                selected_chord = request.form.get("chord", "")
                tab = generate_chord_tab(selected_chord)
            else:
                frets = request.form.get("frets", "").strip()
                tab = generate_tab(frets)
        except ValueError as exc:
            error = str(exc)

    return render_page(
        tab=tab,
        error=error,
        frets=frets,
        selected_chord=selected_chord,
        selection=selection,
        progression=progression,
        notation=notation,
        fingering_state=fingering_state,
        tab_state=tab_state,
        arrangement_state=arrangement_state,
        project_state=project_state,
        output_settings=output_settings,
        estimate_state=estimate_state,
        estimate_form=estimate_form,
        chord_form=chord_form,
        filename=filename,
        filters=filters,
        message=message,
    ), response_status


if __name__ == "__main__":
    app.run(debug=True)
