from flask import Flask, render_template, request

from tab_generator import CHORDS, generate_chord_tab, generate_tab


app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def index():
    tab = None
    error = None
    frets = ""
    selected_chord = ""

    if request.method == "POST":
        mode = request.form.get("mode", "frets")

        try:
            if mode == "chord":
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
    )


if __name__ == "__main__":
    app.run(debug=True)