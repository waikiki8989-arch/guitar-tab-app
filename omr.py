from __future__ import annotations

from pathlib import Path
import base64
import os
import shlex
import subprocess
import tempfile


class OMRExecutionError(ValueError):
    pass


def command():
    configured = os.environ.get("GUITAR_TAB_OMR_COMMAND")
    if configured:
        return shlex.split(configured)
    default = Path(__file__).resolve().parent / ".venv-omr" / "bin" / "homr"
    return [str(default)]


def recognize_png(png: bytes, *, timeout: int = 300) -> bytes:
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise OMRExecutionError("認識対象のページ画像がPNGではありません。")
    with tempfile.TemporaryDirectory(prefix="guitar-tab-omr-") as directory:
        input_path = Path(directory) / "page.png"
        input_path.write_bytes(png)
        try:
            result = subprocess.run(
                [*command(), str(input_path)], capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except FileNotFoundError as exc:
            raise OMRExecutionError("OMRエンジンが見つかりません。.venv-omrを作成するか、GUITAR_TAB_OMR_COMMANDを設定してください。") from exc
        except subprocess.TimeoutExpired as exc:
            raise OMRExecutionError("楽譜認識がタイムアウトしました。") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
            raise OMRExecutionError("楽譜認識に失敗しました。" + (f" {detail[0]}" if detail else ""))
        output_path = input_path.with_suffix(".musicxml")
        if not output_path.exists():
            raise OMRExecutionError("OMRエンジンがMusicXMLを生成しませんでした。")
        return output_path.read_bytes()


def png_from_data_url(data_url: str) -> bytes:
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        raise OMRExecutionError("認識対象の画像形式が不正です。")
    try:
        return base64.b64decode(data_url.removeprefix(prefix), validate=True)
    except ValueError as exc:
        raise OMRExecutionError("認識対象の画像を読み込めません。") from exc
