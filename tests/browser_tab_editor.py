"""Optional Chromium integration check for paired notation/TAB editing."""
from pathlib import Path
import sys
import threading
from copy import deepcopy
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from app import app


def main():
    server = make_server('127.0.0.1', 0, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
            def upload(name=None, payload=None):
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.locator('#musicxml').set_input_files(payload or Path(__file__).parent / 'fixtures' / name)
                page.get_by_role('button', name='音符を読み込む', exact=True).click()
                page.get_by_role('button', name='条件に一致する音をまとめて追加', exact=True).click()
                page.get_by_role('button', name='メロディーを確定', exact=True).click()
                page.wait_for_function('window.tabEditor && document.querySelectorAll(".tab-hit").length > 0')
            upload('score_playback.musicxml')
            assert page.locator('.tab-rest-copy').count() == 1
            assert any('·' in t for t in page.locator('.tab-rhythm').all_text_contents())
            assert page.evaluate('scorePlayer.osmd.GraphicSheet.MeasureList.every(ms => ms[1].stave.getNumLines() === 6)')
            assert page.evaluate('''() => scorePlayer.osmd.GraphicSheet.MeasureList.every(ms => ms[0].staffEntries.every((entry, i) => {
                if (entry.graphicalVoiceEntries[0].notes[0].sourceNote.isRest()) return true;
                const a = entry.graphicalVoiceEntries[0].notes[0].vfnote[0];
                const b = ms[1].staffEntries[i].graphicalVoiceEntries[0].notes[0].vfnote[0];
                return Math.abs(a.getAbsoluteX() - b.getAbsoluteX()) < 2;
            }))''')
            first = page.locator('.tab-hit[data-staff="1"]').first
            note_id = first.get_attribute('data-tab-note')
            first.click()
            page.locator('#tab-edit-form').wait_for()
            page.wait_for_function('document.querySelectorAll(".tab-selected").length === 2')
            old_position = page.locator('#tab-position').input_value()
            alternative = next(value for value in page.locator('#tab-position option').evaluate_all('(els) => els.map(e=>e.value)') if value != old_position)
            before_audio = page.evaluate('scorePlayer.data.events')
            page.locator('#tab-position').select_option(alternative)
            page.get_by_role('button', name='TABの運指を変更・固定', exact=True).click()
            page.wait_for_function('window.tabEditor')
            assert '固定あり' in page.locator('#tab-edit-form').inner_text()
            assert page.locator('#tab-position').input_value() == alternative
            assert page.evaluate('scorePlayer.data.events') == before_audio
            page.get_by_role('button', name='元に戻す', exact=True).click()
            assert page.locator('#tab-position').input_value() == old_position
            page.get_by_role('button', name='やり直す', exact=True).click()
            assert page.locator('#tab-position').input_value() == alternative
            page.get_by_role('button', name='TABの固定を解除', exact=True).click()
            assert '固定なし' in page.locator('#tab-edit-form').inner_text()
            page.get_by_role('button', name='元に戻す', exact=True).click()
            assert '固定あり' in page.locator('#tab-edit-form').inner_text()
            page.wait_for_function('window.tabEditor')
            page.locator('#score-sheet').screenshot(path='/tmp/guitar-tab-editor.png')
            page.locator('#play-score').click()
            page.wait_for_function('scorePlayer.state === "playing" && scorePlayer.position > 0.1')
            assert page.evaluate('scorePlayer.osmd.cursor.cursorElement.getBoundingClientRect().height > 100')
            page.locator('#tab-position').select_option(old_position)
            assert page.evaluate('scorePlayer.state') == 'stopped'
            page.locator('#guitar-octave').select_option('-1')
            page.get_by_role('button', name='設定を反映・運指を再計算', exact=True).click()
            page.wait_for_function('window.tabEditor')
            assert page.get_by_role('button', name='元に戻す', exact=True).is_disabled()
            assert page.evaluate('scorePlayer.data.events.filter(e=>e.channel === "melody")[0].midi') == 60  # first G becomes invalid locked, next C5 -> C4
            upload('guitar_fingering.musicxml')
            assert page.locator('.tab-missing[data-staff="1"]').count() == 2
            page.locator('#play-score').click()
            assert '確認欄' in page.locator('#playback-status').inner_text()
            assert page.evaluate('scorePlayer.state') == 'stopped'
            page.locator('#allow-missing-playback').check()
            page.locator('#play-score').click()
            page.wait_for_function('scorePlayer.state === "playing"')
            page.locator('#stop-score').click()
            page.locator('.tab-missing[data-staff="1"]').first.focus()
            page.keyboard.press('Enter')
            page.locator('#tab-edit-form').wait_for()
            assert '未変換' in page.locator('#tab-edit-form').inner_text()
            # Several systems plus resize exercise selection overlays after OSMD re-rendering.
            root = ET.parse(Path(__file__).parent / 'fixtures/score_playback.musicxml').getroot()
            part = root.find('part')
            original = list(part)
            for _ in range(5):
                for measure in original:
                    part.append(deepcopy(measure))
            upload(payload={'name': 'long.musicxml', 'mimeType': 'application/xml', 'buffer': ET.tostring(root)})
            count = page.locator('.tab-hit').count()
            page.set_viewport_size({'width': 760, 'height': 900})
            page.wait_for_timeout(600)
            assert page.locator('.tab-hit').count() == count
            last = page.locator('.tab-hit[data-staff="1"]').last
            last.click()
            page.locator('#tab-edit-form').wait_for()
            page.wait_for_function('document.querySelectorAll(".tab-selected").length === 2')
            assert not errors, errors
            print('PASS: paired rhythm/alignment, click/keyboard selection, edit, undo/redo, audio, missing notes, long score and resize')
            browser.close()
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
