from flask import Flask, render_template, request
#Flask は、Webアプリ本体を作るために使います。
#render_template は、HTMLファイルを読み込んでブラウザに表示するために使います。
from tab_generator import generate_tab

app = Flask(__name__)
#Flask(__name__) でFlaskアプリを作成して、それを app という変数に入れています。

@app.route("/", methods=["GET", "POST"])
def index():
    tab = None
    error = None
    frets = ""
    if request.method == "POST":
        frets = request.form.get("frets", "").strip()

        try:
            tab = generate_tab(frets)
        except ValueError as exc:
            error = str(exc)

    return render_template(
        "index.html",
        tab=tab,
        error=error,
        frets=frets,
    )


if __name__ == "__main__":
    app.run(debug=True)