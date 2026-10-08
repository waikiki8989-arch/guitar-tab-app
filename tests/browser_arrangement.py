"""Optional Chromium check for polyphonic selection, editing and candidate audition."""
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
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
            page.goto(f'http://127.0.0.1:{server.server_port}')
            page.locator('#musicxml').set_input_files(Path(__file__).parent / 'fixtures/score_playback.musicxml')
            page.get_by_role('button', name='音符を読み込む', exact=True).click()
            page.get_by_role('button', name='条件に一致する音をまとめて追加', exact=True).click()
            page.get_by_role('button', name='メロディーを確定', exact=True).click()
            page.get_by_role('button', name='ソロギター編曲を始める', exact=True).click()
            page.wait_for_function('window.tabEditor')
            melody = page.evaluate('scorePlayer.data.events')
            page.locator('#arr-measure').select_option('measure-2')
            page.locator('#arr-pattern').select_option('full')
            page.get_by_role('button', name='編曲候補を作る', exact=True).click()
            page.wait_for_function('window.tabEditor')
            assert page.locator('[data-arr-note]').count() == 0
            candidate_count = page.locator('#arr-candidate > ul:not(.melody-warning) > li').count()
            assert candidate_count > 0
            page.locator('#play-arr-candidate').click()
            page.wait_for_function('scorePlayer.state === "playing" && scorePlayer.audition')
            assert page.evaluate('scorePlayer.queue.some(e=>e.channel === "bass")')
            assert page.evaluate('scorePlayer.offset') > 0
            page.locator('#pause-score').click()
            assert page.evaluate('scorePlayer.audition')
            page.locator('#stop-score').click()
            assert not page.evaluate('scorePlayer.audition')
            page.get_by_role('button', name='候補を採用', exact=True).click()
            page.wait_for_function('window.tabEditor')
            assert page.locator('[data-arr-note]').count() == candidate_count
            assert page.evaluate('scorePlayer.data.events.filter(e=>e.channel === "melody")') == melody
            for role in ('melody', 'bass', 'accompaniment'):
                for staff in ('0', '1'):
                    assert page.locator(f'.tab-hit[data-role="{role}"][data-staff="{staff}"]').count() > 0
            bass = page.locator('.tab-hit[data-role="bass"][data-staff="1"]').first
            note_id = bass.get_attribute('data-tab-note')
            bass.click()
            page.wait_for_function('window.tabEditor')
            assert page.locator('#arr-edit-form input[name=arr_note_id]').input_value() == note_id
            assert page.locator('.tab-selected[data-staff="0"]').count() > 0
            assert page.locator('.tab-selected[data-staff="1"]').count() > 0
            page.locator('#arr-fret').fill('1')
            page.get_by_role('button', name='追加音の変更を反映', exact=True).click()
            page.get_by_role('button', name='編曲を元に戻す', exact=True).click()
            page.get_by_role('button', name='編曲をやり直す', exact=True).click()
            page.wait_for_function('window.tabEditor')
            assert page.locator(f'[data-arr-note="{note_id}"]').count() == 1
            accompaniment = page.locator('.tab-hit[data-role="accompaniment"][data-staff="0"]').first
            accompaniment_id = accompaniment.get_attribute('data-tab-note')
            accompaniment.focus()
            page.keyboard.press('Enter')
            page.wait_for_function('window.tabEditor')
            assert page.locator('#arr-edit-form input[name=arr_note_id]').input_value() == accompaniment_id
            page.locator('.tab-hit[data-role="melody"][data-staff="1"]').first.click()
            page.locator('#tab-edit-form').wait_for()
            page.wait_for_function('window.tabEditor')
            assert page.locator('#arr-edit-form input[name=arr_note_id]').input_value() == ''
            count = page.locator('.tab-hit').count()
            page.set_viewport_size({'width': 760, 'height': 900})
            page.wait_for_timeout(600)
            assert page.locator('.tab-hit').count() == count
            page.locator('#score-sheet').screenshot(path='/tmp/guitar-arrangement.png')
            page.locator('#playback-mode').select_option('all')
            page.locator('#play-score').click()
            page.wait_for_function('scorePlayer.state === "playing"')
            assert page.evaluate('scorePlayer.queue.some(e=>e.channel === "bass")')
            page.locator('#arr-fret').fill('2')
            assert page.evaluate('scorePlayer.state') == 'stopped'
            assert not errors, errors
            print('PASS: polyphonic notation/TAB selection, audition, adoption, edit, undo/redo, resize and playback')
            browser.close()
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
