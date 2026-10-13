/* Portable downloads and unsaved-work notices; no browser storage or autosave. */
(() => {
    const panel = document.getElementById('project-panel');
    if (!panel) return;
    const status = document.getElementById('project-change-status');
    const transfer = document.getElementById('project-transfer-status');
    const hasProject = panel.dataset.hasProject === 'true';
    let dirty = panel.dataset.dirty === 'true';
    let navigating = false;
    let revision = 0;
    const pendingForms = new Set();
    const saveForm = document.getElementById('project-save-form');
    const saveButton = document.getElementById('project-save-button');

    document.addEventListener('input', event => {
        const form = event.target.closest('form');
        if (!hasProject || !form || form.hasAttribute('data-replaces-project') || event.target.type === 'hidden') return;
        dirty = true;
        revision++;
        pendingForms.add(form);
        status.textContent = '入力中の変更があります。各欄の反映ボタンで確定してから保存してください。';
    });

    window.addEventListener('beforeunload', event => {
        const formNavigation = navigating;
        navigating = false;
        if (dirty && !formNavigation) {
            event.preventDefault();
            event.returnValue = '';
        }
    });
    window.addEventListener('pageshow', () => { navigating = false; });

    // Capture phase runs before the playback submit handler; canceled replacements keep playing state intact.
    document.addEventListener('submit', event => {
        const form = event.target;
        if (form === saveForm) return;
        if (form.hasAttribute('data-replaces-project')) {
            const fileInput = form.querySelector('input[type=file]');
            const file = fileInput?.files[0];
            if (file && file.size > Number(fileInput.dataset.maxBytes)) {
                event.preventDefault();
                event.stopImmediatePropagation();
                transfer.textContent = 'ファイルが大きすぎます。MusicXMLは2 MiB以下、プロジェクトは8 MiB以下にしてください。現在の作業は保持しています。';
                return;
            }
            const confirmation = form.querySelector('[name=replace_current]');
            if (hasProject && confirmation && !confirmation.checked) {
                if (!window.confirm('現在の作業を置き換えます。ファイルに保存していない変更は失われます。続けますか？')) {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    return;
                }
                confirmation.checked = true;
            }
        } else if ([...pendingForms].some(pending => pending !== form)) {
            if (!window.confirm('ほかの入力欄に未反映の変更があります。この操作で入力中の内容が失われます。続けますか？')) {
                event.preventDefault();
                event.stopImmediatePropagation();
                return;
            }
        }
        navigating = true; // Ordinary form submissions carry the signed state to the next page.
    }, true);

    if (!saveForm) return;
    saveForm.addEventListener('submit', async event => {
        event.preventDefault();
        if (pendingForms.size) {
            transfer.textContent = '入力中の変更があります。各欄の反映ボタンを押してから保存してください。';
            return;
        }
        const startedRevision = revision;
        saveButton.disabled = true;
        transfer.textContent = 'ダウンロード用のファイルを準備しています…';
        try {
            const response = await fetch(saveForm.action, {
                method: 'POST', body: new FormData(saveForm), headers: {Accept: 'application/json'},
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'ダウンロード用のファイルを作成できませんでした。');
            const blob = new Blob([result.content], {type: 'application/json;charset=utf-8'});
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = result.filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 60000);
            if (revision === startedRevision) {
                document.querySelectorAll('input[name=score_state]').forEach(input => { input.value = result.state_token; });
                dirty = false;
                panel.dataset.dirty = 'false';
                status.textContent = 'ダウンロード用に作成した内容から変更はありません。端末への保存完了は保存先で確認してください。';
            }
            transfer.textContent = 'ダウンロードを開始しました。端末の保存先を確認してください。既存ファイルは自動更新されません。';
        } catch (error) {
            transfer.textContent = `保存を開始できませんでした。現在の作業は保持しています。${error.message}`;
        } finally {
            saveButton.disabled = false;
        }
    });
})();
