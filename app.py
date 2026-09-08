from pathlib import Path
import os
import secrets

from flask import Flask, render_template, request
from itsdangerous import BadData, URLSafeTimedSerializer

from chord_progression import ChordProgression, KIND_LABELS, SOURCE_LABELS
from chord_estimation import DEFAULT_SETTINGS, estimate_sections
from melody import FILTER_FIELDS, MelodySelection, filter_notes, filter_options
from musicxml_parser import MAX_XML_BYTES, parse_score

from tab_generator import CHORDS, generate_chord_tab, generate_tab


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_XML_BYTES + 64 * 1024
app.config["SECRET_KEY"] = os.environ.get("GUITAR_TAB_SECRET_KEY") or secrets.token_hex(32)


def state_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="melody-selection-v1")


def render_page(*, selection=None, progression=None, estimate_state=None, filename="", filters=None, **context):
    filters = filters or {}
    if selection is not None:
        context.update(
            notes=selection.notes,
            candidates=filter_notes(selection.notes, filters),
            options={field: filter_options(selection.notes, field) for field in FILTER_FIELDS},
            state_token=state_serializer().dumps({
                "selection": selection.to_data(), "filename": filename, "filters": filters,
                "progression": progression.to_data() if progression is not None else None,
                "estimate_state": estimate_state,
            }),
        )
    else:
        context["notes"] = None
    results = estimate_sections(selection.notes, progression.measures, estimate_state["settings"]) if estimate_state and selection is not None and progression is not None else ()
    if context.get("estimate_form") is None:
        context["estimate_form"] = estimate_state["settings"] if estimate_state else DEFAULT_SETTINGS
    existing = {i for i, result in enumerate(results)
                if any(e.symbol.kind != "unset" for _, _, events in progression.affected_segments(result.start, result.end) for e in events)}
    return render_template("index.html", chords=CHORDS.keys(), selection=selection,
                           progression=progression, kind_labels=KIND_LABELS,
                           source_labels=SOURCE_LABELS, estimate_state=estimate_state,
                           estimate_results=results, estimate_existing=existing,
                           filename=filename, filters=filters, **context)


@app.errorhandler(413)
def upload_too_large(error):
    return render_page(error="アップロードが大きすぎます。ファイルは2 MiB以下にしてください。"), 413


@app.route("/", methods=["GET", "POST"])
def index():
    tab = None
    error = None
    frets = ""
    selected_chord = ""
    selection = None
    progression = None
    estimate_state = None
    estimate_form = None
    chord_form = {"event_id": "", "symbol_name": "", "measure_id": "", "offset": "0"}
    filters = {}
    filename = ""
    message = None

    if request.method == "POST":
        mode = request.form.get("mode", "frets")

        try:
            token = request.form.get("score_state")
            if token:
                try:
                    state = state_serializer().loads(token, max_age=8 * 60 * 60)
                except BadData as exc:
                    if mode != "musicxml":
                        raise ValueError("作業情報が無効または期限切れです。楽譜を再度読み込んでください。") from exc
                else:
                    selection = MelodySelection.from_data(state["selection"])
                    filename, filters = state["filename"], state["filters"]
                    if state.get("progression") is not None:
                        progression = ChordProgression.from_data(state["progression"])
                    estimate_state = state.get("estimate_state")
            if mode == "musicxml":
                uploaded = request.files.get("musicxml")
                if uploaded is None or not uploaded.filename:
                    raise ValueError("MusicXMLファイルを選択してください。")
                if Path(uploaded.filename).suffix.lower() not in (".musicxml", ".xml"):
                    raise ValueError(".musicxml または .xml を選択してください。.mxl・PDF・画像は未対応です。")
                imported = parse_score(uploaded.read(MAX_XML_BYTES + 1))
                new_progression = ChordProgression.from_import(imported.measures, imported.harmonies)
                selection = MelodySelection(imported.notes)
                progression = new_progression
                estimate_state = None
                filename, filters = uploaded.filename, {}
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
        estimate_state=estimate_state,
        estimate_form=estimate_form,
        chord_form=chord_form,
        filename=filename,
        filters=filters,
        message=message,
    )


if __name__ == "__main__":
    app.run(debug=True)
