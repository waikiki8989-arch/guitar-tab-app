STRING_NAMES = ["E", "A", "D", "G", "B", "e"]

CHORDS = {
    "C": "x 3 2 0 1 0",
    "D": "x x 0 2 3 2",
    "E": "0 2 2 1 0 0",
    "G": "3 2 0 0 0 3",
    "A": "x 0 2 2 2 0",
    "Am": "x 0 2 2 1 0",
    "Em": "0 2 2 0 0 0",
}


def generate_chord_tab(chord_name):
    if chord_name not in CHORDS:
        raise ValueError("対応していないコードです。")

    return generate_tab(CHORDS[chord_name])


def generate_tab(frets_text):
    frets = frets_text.split()

    if len(frets) != 6:
        raise ValueError("フレット番号を6個入力してください。")

    normalized_frets = []

    for fret in frets:
        if fret.lower() == "x":
            normalized_frets.append("x")
            continue

        if not fret.isdigit():
            raise ValueError("フレット番号には0〜24またはxを入力してください。")

        fret_number = int(fret)

        if fret_number < 0 or fret_number > 24:
            raise ValueError("フレット番号は0〜24で入力してください。")

        normalized_frets.append(str(fret_number))

    lines = []

    # 入力は6弦から1弦、表示は1弦から6弦の順にする
    for string_name, fret in zip(
        reversed(STRING_NAMES),
        reversed(normalized_frets),
    ):
        lines.append(f"{string_name}|--{fret}--|")

    return "\n".join(lines)