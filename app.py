from pathlib import Path

from flask import Flask, render_template, request

from musicxml_parser import MAX_XML_BYTES, parse_musicxml

from tab_generator import CHORDS, generate_chord_tab, generate_tab


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_XML_BYTES + 64 * 1024


@app.errorhandler(413)
def upload_too_large(error):
    return render_template(
        "index.html", error="アップロードが大きすぎます。ファイルは2 MiB以下にしてください。",
        chords=CHORDS.keys(), notes=None,
    ), 413


@app.route("/", methods=["GET", "POST"])
def index():
    tab = None
    error = None
    frets = ""
    selected_chord = ""
    notes = None
    filename = ""

    if request.method == "POST":
        mode = request.form.get("mode", "frets")

        try:
            if mode == "musicxml":
                uploaded = request.files.get("musicxml")
                if uploaded is None or not uploaded.filename:
                    raise ValueError("MusicXMLファイルを選択してください。")
                filename = uploaded.filename
                if Path(filename).suffix.lower() not in (".musicxml", ".xml"):
                    raise ValueError(".musicxml または .xml を選択してください。.mxl・PDF・画像は未対応です。")
                notes = parse_musicxml(uploaded.read(MAX_XML_BYTES + 1))
            elif mode == "chord":
                selected_chord = request.form.get("chord", "")
                tab = generate_chord_tab(selected_chord)
            else:
                frets = request.form.get("frets", "").strip()
                tab = generate_tab(frets)
        except ValueError as exc:
            error = str(exc)

    return render_template(
        "index.html",
        tab=tab,
        error=error,
        frets=frets,
        chords=CHORDS.keys(),
        selected_chord=selected_chord,
        notes=notes,
        filename=filename,
    )


if __name__ == "__main__":
    app.run(debug=True)
