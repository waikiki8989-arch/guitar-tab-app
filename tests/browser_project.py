"""Real Chromium checks for portable projects, atomic replacement and unload notices."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server, WSGIRequestHandler
from app import app, state_serializer


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


def main():
    server = make_server('127.0.0.1', 0, app, request_handler=QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p, TemporaryDirectory(prefix='guitar-project-') as folder:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900}, accept_downloads=True)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            base = f'http://127.0.0.1:{server.server_port}'
            page.goto(base)

            def work():
                return state_serializer().loads(page.locator('input[name=score_state]').first.input_value())

            def beforeunload_blocked():
                return page.evaluate('''() => !window.dispatchEvent(new Event('beforeunload', {cancelable: true}))''')

            page.locator('#musicxml').set_input_files(Path(__file__).parent / 'fixtures/score_playback.musicxml')
            page.get_by_role('button', name='音符を読み込む', exact=True).click()
            page.get_by_role('button', name='条件に一致する音をまとめて追加', exact=True).click()
            page.get_by_role('button', name='メロディーを確定', exact=True).click()
            page.get_by_role('button', name='ソロギター編曲を始める', exact=True).click()
            page.locator('#arr-measure').select_option('measure-2')
            page.locator('#arr-pattern').select_option('full')
            page.get_by_role('button', name='編曲候補を作る', exact=True).click()
            page.get_by_role('button', name='候補を採用', exact=True).click()
            page.locator('#project-name').fill('練習曲')
            # Unsubmitted input cannot accidentally be reported as part of the saved snapshot.
            page.locator('#project-save-button').click()
            assert '反映ボタン' in page.locator('#project-transfer-status').inner_text()
            page.get_by_role('button', name='プロジェクト名を反映', exact=True).click()
            page.wait_for_function('window.scorePlayer && window.tabEditor')
            original = work()
            audio = page.evaluate('scorePlayer.data.events')
            assert beforeunload_blocked()
            with page.expect_download() as pending:
                page.locator('#project-save-button').click()
            download = pending.value
            assert download.suggested_filename == '練習曲.guitar-tab.json'
            saved = Path(folder) / download.suggested_filename
            download.save_as(saved)
            page.wait_for_function('document.getElementById("project-panel").dataset.dirty === "false"')
            assert not beforeunload_blocked()
            assert '保存先を確認' in page.locator('#project-transfer-status').inner_text()
            doc = json.loads(saved.read_text())
            assert doc['data']['selection'] == original['selection']
            assert 'undo' not in doc['data']['arrangement']

            page.locator('#arr-fret').fill('1')
            assert beforeunload_blocked()
            page.get_by_role('button', name='音を追加', exact=True).click()
            edited = work()
            assert '保存していない変更' in page.locator('#project-change-status').inner_text()

            # Canceling replacement leaves both applied work and the current page intact.
            page.locator('#project-file').set_input_files(saved)
            dialogs = []
            def cancel(dialog):
                dialogs.append(dialog.type)
                dialog.dismiss()
            page.once('dialog', cancel)
            page.get_by_role('button', name='プロジェクトを開く', exact=True).click()
            assert dialogs == ['confirm'] and work() == edited
            page.locator('#project-file').set_input_files({'name': 'bad.json', 'mimeType': 'application/json', 'buffer': b'broken'})
            page.once('dialog', lambda d: d.accept())
            page.get_by_role('button', name='プロジェクトを開く', exact=True).click()
            assert '読み取れません' in page.locator('[role=alert]').inner_text()
            assert work() == edited

            # Reload the actual downloaded file while audio is playing.
            page.wait_for_function('window.scorePlayer')
            page.locator('#play-score').click()
            page.wait_for_function('scorePlayer.state === "playing"')
            page.locator('#project-file').set_input_files(saved)
            page.once('dialog', lambda d: d.accept())
            page.get_by_role('button', name='プロジェクトを開く', exact=True).click()
            page.wait_for_function('window.scorePlayer && window.tabEditor')
            assert page.evaluate('scorePlayer.state') == 'stopped'
            assert page.evaluate('scorePlayer.data.events') == audio
            restored = work()
            for key in ('selection', 'progression', 'notation', 'fingering'):
                assert restored[key] == original[key]
            assert restored['arrangement']['undo'] == [] and restored['tab_editor']['undo'] == []
            assert page.locator('#score-sheet svg').count() > 0
            # Test further edits from /project/open: every form must post to the proper route.
            page.locator('.tab-hit[data-role="bass"][data-staff="1"]').first.click()
            page.wait_for_function('window.tabEditor')
            page.locator('#arr-fret').fill('1')
            page.get_by_role('button', name='追加音の変更を反映', exact=True).click()
            page.wait_for_function('window.tabEditor')
            page.locator('#project-panel').screenshot(path='/tmp/guitar-project-panel.png')

            # A failed download must not reset the dirty marker or discard the current token.
            before_failed_download = work()
            page.route('**/project/download', lambda route: route.fulfill(status=400,
                content_type='application/json', body='{"error":"テスト用エラー"}'))
            page.locator('#project-save-button').click()
            page.wait_for_function('document.getElementById("project-transfer-status").textContent.includes("テスト用エラー")')
            assert work() == before_failed_download and beforeunload_blocked()
            page.unroute('**/project/download')
            with page.expect_download() as again:
                page.locator('#project-save-button').click()
            again.value.save_as(Path(folder) / 'edited.guitar-tab.json')
            page.wait_for_function('document.getElementById("project-panel").dataset.dirty === "false"')

            # Actual browser reload confirmation, rather than only a synthetic event.
            page.locator('#project-name').fill('未反映')
            page.once('dialog', cancel)
            page.evaluate('setTimeout(() => location.reload(), 0)')
            page.wait_for_timeout(300)
            assert dialogs[-1] == 'beforeunload'
            assert page.locator('#project-name').input_value() == '未反映'

            # No JavaScript: opening, continued editing and direct attachment download still work.
            context = browser.new_context(java_script_enabled=False, accept_downloads=True)
            plain = context.new_page()
            plain.goto(base)
            plain.locator('#project-file').set_input_files(saved)
            plain.get_by_role('button', name='プロジェクトを開く', exact=True).click()
            assert plain.locator('#project-name').input_value() == '練習曲'
            plain.locator('#project-name').fill('別の名前')
            plain.get_by_role('button', name='プロジェクト名を反映', exact=True).click()
            with plain.expect_download() as direct:
                plain.locator('#project-save-button').click()
            assert direct.value.suggested_filename == '別の名前.guitar-tab.json'
            assert plain.locator('#project-name').input_value() == '別の名前'
            assert not errors, errors
            print('PASS: download/open, continued editing, audio reset, dirty tracking, cancel/error preservation, unload confirmation and no-JavaScript fallback')
            browser.close()
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
