"""Read notation changes without altering the score's exact musical timeline."""
from fractions import Fraction

from music21.duration import convertTypeToQuarterLength
from music21.musicxml.xmlToM21 import MeasureParser


def read_changes(element, cursor, divisions):
    changes, warnings = [], []
    if element.tag == "attributes":
        for child in element:
            try:
                if child.tag == "time":
                    value = MeasureParser().xmlToTimeSignature(child).ratioString
                    if "+" in value:
                        raise ValueError
                    changes.append({"kind": "time", "value": value, "start": cursor})
                elif child.tag == "key":
                    value = int(child.findtext("fifths"))
                    if not -7 <= value <= 7:
                        raise ValueError
                    changes.append({"kind": "key", "value": value, "start": cursor})
            except Exception:
                warnings.append("特殊な拍子・調号は未対応です。直前の設定で表示します。")
    elif element.tag in ("direction", "sound"):
        try:
            offset = element.findtext("offset")
            start = cursor + (Fraction(offset) / divisions if offset is not None else 0)
            sound = element if element.tag == "sound" else element.find("sound")
            bpm = sound.get("tempo") if sound is not None else None
            if bpm is None:
                metronome = element.find("direction-type/metronome")
                if metronome is not None:
                    base = Fraction(str(convertTypeToQuarterLength(metronome.findtext("beat-unit"))))
                    dots = len(metronome.findall("beat-unit-dot"))
                    bpm = Fraction(metronome.findtext("per-minute")) * base * sum(Fraction(1, 2**i) for i in range(dots + 1))
            if bpm is not None:
                bpm = Fraction(bpm)
                if not 0 < bpm <= 1000:
                    raise ValueError
                changes.append({"kind": "tempo", "value": str(bpm), "start": start})
        except Exception:
            warnings.append("読み取れないテンポ指定があります。直前のテンポを使用します。")
    return changes, warnings
