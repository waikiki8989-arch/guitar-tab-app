"""Real Chromium: export settings, preflight, immutable downloads, A4 pagination/PDF."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
import os
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from pypdf import PdfReader
from werkzeug.serving import make_server
from app import app, state_serializer
from browser_project import QuietHandler


def long_score():
    root = ET.parse(Path(__file__).parent / 'fixtures/score_playback.musicxml').getroot()
    part = root.find('part')
    measures = list(part)
    part.clear()
    part.set('id', 'P1')
    for _ in range(8):
        for measure in measures:
            copy = deepcopy(measure)
            copy.set('number', str(len(part) + 1))
            part.append(copy)
    return ET.tostring(root, encoding='utf-8')


def main():
    server = make_server('127.0.0.1', 0, app, request_handler=QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p, TemporaryDirectory(prefix='guitar-export-') as temp:
            folder = Path(os.environ.get('EXPORT_ARTIFACTS', temp))
            folder.mkdir(parents=True, exist_ok=True)
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            page.locator('#musicxml').set_input_files({'name': 'long.musicxml', 'mimeType': 'application/xml', 'buffer': long_score()})
            page.get_by_role('button', name='音符を読み込む', exact=True).click()
            with page.expect_popup() as popup:
                page.get_by_role('button', name='印刷プレビューを開く', exact=True).click()
            blocked = popup.value
            assert 'メロディー' in blocked.locator('main').inner_text()
            assert blocked.locator('#print-button').count() == 0
            blocked.close()
            page.get_by_role('button', name='条件に一致する音をまとめて追加', exact=True).click()
            page.get_by_role('button', name='メロディーを確定', exact=True).click()
            page.get_by_role('button', name='ソロギター編曲を始める', exact=True).click()
            # A long bass crosses a barline under the triplet melody.
            page.locator('#arr-note-measure').select_option('measure-1')
            page.locator('#arr-duration').fill('6')
            page.locator('#arr-string').select_option('6')
            page.locator('#arr-fret').fill('0')
            page.get_by_role('button', name='音を追加', exact=True).click()
            page.locator('#export-title').fill('印刷検証の練習曲')
            page.get_by_role('button', name='印刷プレビューを開く', exact=True).click()
            assert '反映' in page.locator('#project-transfer-status').inner_text()
            page.locator('#export-composer').fill('作曲テスト')
            page.locator('#export-arranger').fill('編曲テスト')
            for layout in ('both', 'tab'):
                page.locator('#export-layout').select_option(layout)
                page.get_by_role('button', name='出力設定を反映', exact=True).click()
                before = page.locator('input[name=score_state]').first.input_value()
                with page.expect_popup() as popup:
                    page.get_by_role('button', name='印刷プレビューを開く', exact=True).click()
                printed = popup.value
                printed.on('pageerror', lambda error: errors.append(str(error)))
                printed.locator('input[name=acknowledge]').check()
                printed.get_by_role('button', name='印刷プレビューへ進む', exact=True).click()
                printed.wait_for_function("document.body.dataset.printReady || document.getElementById('print-status')?.textContent.includes('できません')")
                assert printed.locator('body').get_attribute('data-print-ready'), printed.locator('#print-status').inner_text()
                count = printed.locator('.print-page').count()
                assert count > 1
                assert printed.locator('.tab-hit, .tab-selected, #play-score').count() == 0
                assert printed.locator('.print-page header').inner_text().count('作曲テスト') == 1
                assert '休符' in printed.locator('#engraving').inner_text()
                assert '4分·' in printed.locator('#engraving').inner_text()
                assert '8分³' in printed.locator('#engraving').inner_text()
                # Every source measure belongs to exactly one unsplit system.
                numbers = printed.locator('.print-system').evaluate_all("els => els.flatMap(e => e.dataset.measures.split(',').filter(Boolean).map(Number))")
                assert sorted(numbers) == list(range(1, 33)), numbers
                assert printed.evaluate('''() => [...document.querySelectorAll('.print-page')].every(page => {
                    const box = page.getBoundingClientRect(), footer = page.querySelector('footer').getBoundingClientRect();
                    return [...page.querySelectorAll('.print-system')].every(svg => {
                        const r = svg.getBoundingClientRect();
                        return r.left >= box.left + 50 && r.right <= box.right - 50 && r.bottom < footer.top;
                    });
                })''')
                printed.screenshot(path=str(folder / f'print-{layout}.png'), full_page=True)
                printed.locator('.print-page').first.screenshot(path=str(folder / f'first-{layout}.png'))
                (folder / f'rhythms-{layout}.txt').write_text(printed.locator('#engraving').inner_text())
                printed.emulate_media(media='print')
                assert not printed.locator('.print-controls').is_visible()
                pdf = folder / f'print-{layout}.pdf'
                printed.pdf(path=str(pdf), prefer_css_page_size=True, print_background=True)
                pages = PdfReader(pdf).pages
                assert len(pages) == count
                for index, pdf_page in enumerate(pages):
                    assert abs(float(pdf_page.mediabox.width) - 595.28) < 2
                    assert abs(float(pdf_page.mediabox.height) - 841.89) < 2
                    assert f'{index + 1} / {count}' in pdf_page.extract_text()
                printed.close()
                assert page.locator('input[name=score_state]').first.input_value() == before
                assert page.evaluate("() => !window.dispatchEvent(new Event('beforeunload', {cancelable:true}))")
                # Warning acknowledgment is also required for MusicXML downloads.
                with page.expect_popup() as popup:
                    page.get_by_role('button', name='MusicXMLをダウンロード', exact=True).click()
                check = popup.value
                check.locator('[name=acknowledge]').check()
                with check.expect_download() as pending:
                    check.get_by_role('button', name='MusicXMLをダウンロード', exact=True).click()
                pending.value.save_as(folder / f'export-{layout}.musicxml')
                check.close()
            assert not errors, errors
            print(f'Chromium {browser.version}: export forms, diagnostics, both/TAB-only multipage A4 PDF passed')
            browser.close()
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
