"""Optional real-browser smoke check: python tests/browser_fingering.py.

Requires Playwright and its Chromium installation; not part of pytest's unit suite.
"""
from pathlib import Path
import sys
import threading

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
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            page.locator('#musicxml').set_input_files(Path(__file__).parent / 'fixtures/guitar_fingering.musicxml')
            page.get_by_role('button', name='音符を読み込む', exact=True).click()
            page.get_by_role('button', name='条件に一致する音をまとめて追加', exact=True).click()
            page.get_by_role('button', name='メロディーを確定', exact=True).click()
            rows = page.locator('[data-fingering-note]')
            assert rows.count() == 6
            e4 = rows.nth(1)
            assert e4.locator('option').count() == 6
            e4.locator('select').select_option('3:9')
            e4.locator('input[name=lock]').check()
            e4.get_by_role('button', name='運指を反映', exact=True).click()
            assert '3弦 9フレット' in e4.locator('td').nth(5).inner_text()
            page.locator('#max-fret').fill('5')
            page.get_by_role('button', name='設定を反映・運指を再計算', exact=True).click()
            assert '未変換' in e4.locator('td').nth(5).inner_text()
            assert '再選択' in e4.inner_text()
            e4.get_by_role('button', name='固定を解除', exact=True).click()
            assert '未変換' not in e4.locator('td').nth(5).inner_text()
            page.locator('#max-fret').fill('24')
            page.get_by_role('button', name='設定を反映・運指を再計算', exact=True).click()
            tie = rows.nth(2)
            tie.locator('select').select_option('2:8')
            tie.locator('input[name=lock]').check()
            tie.get_by_role('button', name='運指を反映', exact=True).click()
            assert '2弦 8フレット' in rows.nth(3).locator('td').nth(5).inner_text()
            page.locator('#guitar-octave').select_option('-1')
            page.get_by_role('button', name='設定を反映・運指を再計算', exact=True).click()
            assert 'F#5' in rows.nth(4).locator('td').nth(2).inner_text()
            assert '未変換' not in rows.nth(4).locator('td').nth(5).inner_text()
            page.wait_for_function('window.scorePlayer && !document.getElementById("play-score").disabled')
            assert page.locator('#score-sheet svg').count() > 0
            page.locator('#play-score').click()
            page.wait_for_function('scorePlayer.state === "playing"')
            page.locator('#max-fret').fill('12')
            assert page.evaluate('scorePlayer.state') == 'stopped'
            assert not errors, errors
            print('PASS: upload, confirmation, choices, locks, invalidation, ties, octave, score and edit-stop')
            browser.close()
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
