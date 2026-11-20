(() => {
    const root = document.getElementById('dedicated-tab-editor');
    if (!root) return;
    const stateEl = document.getElementById('tab-document-data');
    let documentData = JSON.parse(stateEl.textContent);
    let stateToken = root.dataset.scoreState;
    let scoreXml = JSON.parse(document.getElementById('tab-score-xml')?.textContent || 'null');
    let scoreOsmd = null;
    let selected = null;
    let audioContext = null;
    let timers = [];

    const $ = id => document.getElementById(id);
    const status = message => { $('tab-document-status').textContent = message; };
    const eventById = id => documentData.events.find(event => event.event_id === id);

    function render() {
        root.querySelectorAll('.dedicated-tab-event').forEach(button => {
            button.classList.toggle('selected', button.dataset.eventId === selected);
        });
        const event = eventById(selected);
        $('tab-selected-label').textContent = event ? `${event.event_id}・開始 ${event.start}・${event.pitch || '休符'}` : '未選択';
        const disabled = !event || event.rest;
        $('tab-document-string').disabled = disabled;
        $('tab-document-fret').disabled = disabled;
        $('tab-document-apply').disabled = !event;
        $('tab-document-delete').disabled = !event;
        if (event) {
            $('tab-document-string').value = String(event.string || 1);
            $('tab-document-fret').value = event.fret == null ? 0 : event.fret;
            $('tab-document-duration').value = event.duration;
        }
        renderFretboard(event);
        decorateScoreSelection();
    }

    async function renderScore() {
        const sheet = $('dedicated-score-sheet');
        if (!sheet || !scoreXml || !window.opensheetmusicdisplay) return;
        sheet.innerHTML = '';
        scoreOsmd = new opensheetmusicdisplay.OpenSheetMusicDisplay('dedicated-score-sheet', {
            autoResize: true,
            backend: 'svg',
            drawTitle: false,
            drawPartNames: false,
            drawMeasureNumbers: true,
            followCursor: false,
        });
        await scoreOsmd.load(scoreXml);
        scoreOsmd.render();
        decorateScore();
    }

    function decorateScore() {
        if (!scoreOsmd?.GraphicSheet?.MeasureList) return;
        document.querySelectorAll('.dedicated-score-hit').forEach(element => element.remove());
        const byStart = new Map();
        documentData.events.filter(event => !event.rest).forEach(event => {
            const key = Fraction(event.start).toFixed(7);
            if (!byStart.has(key)) byStart.set(key, []);
            byStart.get(key).push(event);
        });
        for (const measures of scoreOsmd.GraphicSheet.MeasureList) {
            for (const measure of measures) {
                if (!measure) continue;
                for (const entry of measure.staffEntries || []) {
                    for (const voice of entry.graphicalVoiceEntries || []) {
                        for (const note of voice.notes || []) {
                            if (note.sourceNote?.isRest?.()) continue;
                            const key = (note.sourceNote.getAbsoluteTimestamp().RealValue * 4).toFixed(7);
                            const mapped = byStart.get(key)?.shift();
                            const element = note.getSVGGElement?.();
                            if (!mapped || !element) continue;
                            let box;
                            try { box = element.getBBox(); } catch (_) { continue; }
                            const svg = element.ownerSVGElement;
                            if (!svg) continue;
                            const hit = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                            hit.setAttribute('x', box.x - 5); hit.setAttribute('y', box.y - 5);
                            hit.setAttribute('width', box.width + 10); hit.setAttribute('height', box.height + 10);
                            hit.setAttribute('rx', '4'); hit.dataset.eventId = mapped.event_id;
                            hit.setAttribute('tabindex', '0'); hit.setAttribute('role', 'button');
                            hit.setAttribute('aria-label', `${mapped.event_id}・${mapped.pitch || '未変換'}を選択`);
                            hit.classList.add('dedicated-score-hit');
                            hit.addEventListener('click', () => select(mapped.event_id));
                            hit.addEventListener('keydown', event => {
                                if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(mapped.event_id); }
                            });
                            svg.appendChild(hit);
                        }
                    }
                }
            }
        }
        decorateScoreSelection();
    }

    function decorateScoreSelection() {
        document.querySelectorAll('.dedicated-score-hit').forEach(element => {
            element.classList.toggle('selected', element.dataset.eventId === selected);
            element.setAttribute('aria-pressed', String(element.dataset.eventId === selected));
        });
    }

    function renderFretboard(event) {
        const panel = $('guitar-fretboard-panel');
        const board = $('guitar-fretboard');
        if (!event) {
            panel.hidden = true;
            board.innerHTML = '';
            return;
        }
        panel.hidden = false;
        const targetMidi = event.pitch ? midi(event.pitch) : null;
        $('guitar-fretboard-pitch').textContent = event.rest ? '休符' : (event.pitch || '未変換');
        const labels = {1: '1弦 E4', 2: '2弦 B3', 3: '3弦 G3', 4: '4弦 D3', 5: '5弦 A2', 6: '6弦 E2'};
        const capo = Number(documentData.capo || 0);
        const openMidi = {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40};
        board.innerHTML = '';

        const header = document.createElement('div');
        header.className = 'fretboard-row fretboard-header';
        const empty = document.createElement('span');
        empty.className = 'fretboard-string-label';
        header.appendChild(empty);
        for (let fret = 0; fret <= 24; fret += 1) {
            const label = document.createElement('span');
            label.className = `fretboard-fret-label${fret === 0 ? ' open' : ''}`;
            label.textContent = fret === 0 ? '開放' : fret;
            header.appendChild(label);
        }
        board.appendChild(header);

        for (let string = 1; string <= 6; string += 1) {
            const row = document.createElement('div');
            row.className = 'fretboard-row';
            const label = document.createElement('span');
            label.className = 'fretboard-string-label';
            label.textContent = labels[string];
            row.appendChild(label);
            for (let fret = 0; fret <= 24; fret += 1) {
                const button = document.createElement('button');
                const noteMidi = openMidi[string] + fret + capo;
                button.type = 'button';
                button.className = `fretboard-fret${fret === 0 ? ' open' : ''}`;
                if (targetMidi != null && noteMidi === targetMidi) button.classList.add('same-pitch');
                if (!event.rest && event.string === string && event.fret === fret) button.classList.add('selected');
                button.textContent = pitchName(noteMidi);
                button.title = `${string}弦 ${fret === 0 ? '開放' : `${fret}フレット`}・${pitchName(noteMidi)}`;
                button.setAttribute('aria-label', button.title);
                button.addEventListener('click', () => safe(async () => {
                    await command({type: 'set_position', event_id: selected, string, fret});
                }));
                row.appendChild(button);
            }
            board.appendChild(row);
        }
    }

    function drawGrid() {
        const labels = {1: 'e', 2: 'B', 3: 'G', 4: 'D', 5: 'A', 6: 'E'};
        root.querySelectorAll('.dedicated-measure').forEach(measure => {
            const number = measure.dataset.measure;
            const target = measure.querySelector('.dedicated-tab-lines');
            const source = documentData.measures.find(item => item.number === number);
            const events = documentData.events.filter(event => source &&
                Fraction(event.start) >= Fraction(source.start) && Fraction(event.start) < Fraction(source.start) + Fraction(source.duration));
            const starts = [...new Set(events.map(event => event.start))].sort((a, b) => Fraction(a) - Fraction(b));
            target.innerHTML = '';
            for (let string = 1; string <= 6; string += 1) {
                const line = document.createElement('div');
                line.className = 'dedicated-tab-line';
                line.innerHTML = `<span class="dedicated-string-label">${labels[string]}|</span>`;
                for (const start of starts) {
                    const cell = document.createElement('span');
                    cell.className = 'dedicated-tab-cell';
                    const same = events.filter(event => event.start === start && (event.string === string || (event.rest && string === 1) || (!event.rest && event.string == null && string === 1)));
                    if (same.length) {
                        const event = same[0];
                        const button = document.createElement('button');
                        button.type = 'button';
                        button.className = `dedicated-tab-event${event.event_id === selected ? ' selected' : ''}${event.rest ? ' rest' : ''}${!event.rest && event.string == null ? ' missing' : ''}`;
                        button.dataset.eventId = event.event_id;
                        button.textContent = event.rest ? '𝄽' : (event.string == null ? '×' : event.fret);
                        button.setAttribute('aria-label', `${event.event_id}・${string}弦・${event.fret ?? (event.rest ? '休符' : '未変換')}`);
                        button.addEventListener('click', () => select(event.event_id));
                        cell.appendChild(button);
                    } else cell.textContent = '-';
                    line.appendChild(cell);
                }
                target.appendChild(line);
            }
        });
        render();
    }

    // Exact rational comparison for the bounded editor view.
    function Fraction(value) {
        const [a, b = '1'] = String(value).split('/');
        return Number(a) / Number(b);
    }

    async function command(command) {
        const response = await fetch('/api/tab/command', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({score_state: stateToken, command}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'TAB編集に失敗しました。');
        documentData = result.document;
        stateToken = result.state_token;
        scoreXml = result.musicxml;
        selected = result.selected || selected;
        await renderScore();
        drawGrid();
        status('変更を反映しました。');
    }

    function select(eventId) {
        stop();
        selected = eventId;
        render();
        status(`${eventId}を選択中です。`);
    }

    async function safe(action) {
        try { await action(); } catch (error) { status(error.message); }
    }

    $('tab-document-apply').addEventListener('click', () => safe(async () => {
        const event = eventById(selected);
        if (!event) return;
        await command({type: 'set_position', event_id: selected,
            string: Number($('tab-document-string').value), fret: Number($('tab-document-fret').value)});
        await command({type: 'set_duration', event_id: selected, duration: $('tab-document-duration').value,
            dots: $('tab-document-duration').value.startsWith('3/') ? 1 : 0});
    }));
    $('tab-document-delete').addEventListener('click', () => safe(async () => {
        if (!selected) return;
        await command({type: 'delete_event', event_id: selected});
        selected = null;
        render();
    }));
    $('tab-document-rest').addEventListener('click', () => safe(async () => {
        if (!selected) return;
        const event = eventById(selected);
        if (!event) return;
        await command({type: 'set_rest', event_id: selected, rest: !event.rest,
            string: Number($('tab-document-string').value), fret: Number($('tab-document-fret').value)});
    }));
    $('tab-document-add').addEventListener('click', () => safe(async () => {
        await command({type: 'add_event', event: {
            event_id: null, source_note_id: null, role: 'melody',
            start: $('tab-document-add-start').value, duration: $('tab-document-add-duration').value,
            pitch: null, string: Number($('tab-document-add-string').value),
            fret: Number($('tab-document-add-fret').value), tie: null, dots: 0, rest: false,
        }});
        selected = documentData.events[documentData.events.length - 1].event_id;
        drawGrid();
    }));
    $('tab-document-add-rest').addEventListener('click', () => safe(async () => {
        await command({type: 'add_event', event: {
            event_id: null, source_note_id: null, role: 'melody',
            start: $('tab-document-add-start').value, duration: $('tab-document-add-duration').value,
            pitch: null, string: null, fret: null, tie: null, dots: 0, rest: true,
        }});
        selected = documentData.events[documentData.events.length - 1].event_id;
        drawGrid();
    }));
    $('tab-document-undo').addEventListener('click', () => safe(() => command({type: 'undo'})));
    $('tab-document-redo').addEventListener('click', () => safe(() => command({type: 'redo'})));

    $('tab-document-save').addEventListener('click', () => safe(async () => {
        const response = await fetch('/tab-editor/project', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({score_state: stateToken})});
        if (!response.ok) throw new Error((await response.json()).error || 'プロジェクト保存に失敗しました。');
        const blob = await response.blob();
        const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'guitar-tab.guitar-tab.json'; link.click();
        status('プロジェクトを保存しました。');
    }));
    $('tab-document-xml').addEventListener('click', () => safe(async () => {
        const response = await fetch('/tab-editor/musicxml', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({score_state: stateToken})});
        if (!response.ok) throw new Error((await response.json()).error || 'MusicXML出力に失敗しました。');
        const blob = await response.blob();
        const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'guitar-tab.musicxml'; link.click();
        status('MusicXMLを出力しました。');
    }));
    $('tab-document-print').addEventListener('click', () => window.print());

    function stop() { timers.forEach(timer => clearTimeout(timer)); timers = []; $('tab-document-stop').disabled = true; }
    $('tab-document-stop').addEventListener('click', stop);
    $('tab-document-play').addEventListener('click', () => {
        stop();
        audioContext ||= new (window.AudioContext || window.webkitAudioContext)();
        const start = audioContext.currentTime;
        const beat = 60 / 100;
        for (const event of documentData.events.filter(event => !event.rest && event.pitch).sort((a, b) => Fraction(a.start) - Fraction(b.start))) {
            const oscillator = audioContext.createOscillator();
            const gain = audioContext.createGain();
            oscillator.frequency.value = 440 * Math.pow(2, (midi(event.pitch) - 69) / 12);
            gain.gain.setValueAtTime(.0001, start + Fraction(event.start) * beat);
            gain.gain.exponentialRampToValueAtTime(.12, start + Fraction(event.start) * beat + .01);
            gain.gain.exponentialRampToValueAtTime(.0001, start + (Fraction(event.start) + Fraction(event.duration)) * beat);
            oscillator.connect(gain).connect(audioContext.destination);
            oscillator.start(start + Fraction(event.start) * beat); oscillator.stop(start + (Fraction(event.start) + Fraction(event.duration)) * beat + .02);
        }
        $('tab-document-stop').disabled = false;
        status('再生中です。');
        timers.push(setTimeout(() => { $('tab-document-stop').disabled = true; status('再生が終わりました。'); }, Fraction(documentData.end) * beat * 1000 + 100));
    });

    function midi(pitch) {
        const match = /^([A-G])([#b]?)(-?\d+)$/.exec(pitch);
        const pcs = {C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11};
        return (Number(match[3]) + 1) * 12 + pcs[match[1]] + (match[2] === '#' ? 1 : match[2] === 'b' ? -1 : 0);
    }

    function pitchName(value) {
        const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
        return `${names[((value % 12) + 12) % 12]}${Math.floor(value / 12) - 1}`;
    }

    document.addEventListener('keydown', event => {
        if (!selected || event.target.matches('input, select, textarea')) return;
        const input = $('tab-document-fret');
        if (/^\d$/.test(event.key)) { input.value = `${input.value === '0' ? '' : input.value}${event.key}`.slice(-2); event.preventDefault(); }
        else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') { input.value = Math.max(0, Math.min(24, Number(input.value || 0) + (event.key === 'ArrowUp' ? 1 : -1))); event.preventDefault(); }
        else if (event.key === 'Enter') { $('tab-document-apply').click(); event.preventDefault(); }
        else if (event.key === 'Escape') render();
        else if (event.key === 'Delete') $('tab-document-delete').click();
    });
    root.querySelectorAll('.dedicated-tab-event').forEach(button => button.addEventListener('click', () => select(button.dataset.eventId)));
    renderScore().catch(error => {
        $('dedicated-score-status').textContent = `TAB譜を表示できませんでした：${error.message}`;
    });
    render();
})();
