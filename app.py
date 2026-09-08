from pathlib import Path
import os
import secrets

from flask import Flask, render_template, request
from itsdangerous import BadData, URLSafeTimedSerializer

from melody import FILTER_FIELDS, MelodySelection, filter_notes, filter_options
from musicxml_parser import MAX_XML_BYTES, parse_musicxml

from tab_generator import CHORDS, generate_chord_tab, generate_tab


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_XML_BYTES + 64 * 1024
app.config["SECRET_KEY"] = os.environ.get("GUITAR_TAB_SECRET_KEY") or secrets.token_hex(32)


def state_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="melody-selection-v1")


def render_page(*, selection=None, filename="", filters=None, **context):
    filters = filters or {}
    if selection is not None:
        context.update(
            notes=selection.notes,
            candidates=filter_notes(selection.notes, filters),
            options={field: filter_options(selection.notes, field) for field in FILTER_FIELDS},
            state_token=state_serializer().dumps({
                "selection": selection.to_data(), "filename": filename, "filters": filters,
            }),
        )
    else:
        context["notes"] = None
    return render_template("index.html", chords=CHORDS.keys(), selection=selection,
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
            if mode == "musicxml":
                uploaded = request.files.get("musicxml")
                if uploaded is None or not uploaded.filename:
                    raise ValueError("MusicXMLファイルを選択してください。")
                if Path(uploaded.filename).suffix.lower() not in (".musicxml", ".xml"):
                    raise ValueError(".musicxml または .xml を選択してください。.mxl・PDF・画像は未対応です。")
                imported = parse_musicxml(uploaded.read(MAX_XML_BYTES + 1))
                selection = MelodySelection(tuple(imported))
                filename, filters = uploaded.filename, {}
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
        filename=filename,
        filters=filters,
        message=message,
    )


if __name__ == "__main__":
    app.run(debug=True)
