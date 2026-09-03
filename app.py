from flask import Flask, render_template
#Flask は、Webアプリ本体を作るために使います。
#render_template は、HTMLファイルを読み込んでブラウザに表示するために使います。

app = Flask(__name__)
#Flask(__name__) でFlaskアプリを作成して、それを app という変数に入れています。

@app.route("/")
def index():
    return render_template("index.html")
    #index.html をブラウザに返す


if __name__ == "__main__":
    app.run(debug=True)