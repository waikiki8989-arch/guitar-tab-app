/* Standalone, immutable print rendering. Whole systems are placed on A4 pages. */
(() => {
    const ns = 'http://www.w3.org/2000/svg';
    const data = JSON.parse(document.getElementById('print-data').textContent);
    const status = document.getElementById('print-status');
    const button = document.getElementById('print-button');
    const converter = opensheetmusicdisplay.VexFlowConverter;
    const original = converter.CreateTabNote;
    converter.CreateTabNote = function (...args) {
        const note = original.apply(this, args);
        note.render_options.draw_stem = true;
        note.render_options.draw_dots = true;
        return note;
    };
    const names = {whole: '全', half: '2分', quarter: '4分', eighth: '8分', '16th': '16分', '32nd': '32分', '64th': '64分'};
    function rhythmMap(xml) {
        const map = new Map();
        const part = [...xml.querySelectorAll('part')].at(-1);
        let base = 0;
        for (const measure of part.children) {
            const divisions = Number(measure.querySelector('divisions').textContent);
            let cursor = 0, end = 0;
            for (const element of measure.children) {
                const duration = Number(element.querySelector('duration')?.textContent || 0) / divisions;
                if (element.tagName === 'backup') cursor -= duration;
                else if (element.tagName === 'forward') cursor += duration;
                else if (element.tagName === 'note') {
                    const voice = element.querySelector('voice')?.textContent || '1';
                    map.set(`${voice}:${(base + cursor).toFixed(7)}`, {
                        type: element.querySelector('type').textContent,
                        dots: element.querySelectorAll('dot').length,
                        tuplet: !!element.querySelector('time-modification'),
                    });
                    cursor += duration;
                    end = Math.max(end, cursor);
                }
            }
            base += end;
        }
        return map;
    }
    function rhythms(osmd, svg, metadata) {
        // TAB rests are not drawn by OSMD 2.1.2. Add explicit rhythmic labels
        // for every voice, including rests, without editor hit areas or cursors.
        for (const measures of osmd.GraphicSheet.MeasureList) {
            const measure = measures[data.layout === 'both' ? 1 : 0];
            if (!measure) continue;
            for (const entry of measure.staffEntries) {
                for (const voice of entry.graphicalVoiceEntries) {
                    for (const note of voice.notes) {
                        const source = note.sourceNote;
                        if (!source.PrintObject) continue;
                        const voiceId = source.ParentVoiceEntry.ParentVoice.VoiceId;
                        const rhythm = metadata.get(`${voiceId}:${(source.getAbsoluteTimestamp().RealValue * 4).toFixed(7)}`);
                        if (!rhythm) throw new Error('TABの音価と表示位置を照合できませんでした。');
                        const vf = note.vfnote?.[0];
                        if (!vf && !source.isRest()) continue;
                        const label = document.createElementNS(ns, 'text');
                        label.setAttribute('x', vf ? vf.getAbsoluteX() : entry.PositionAndShape.AbsolutePosition.x * 10);
                        label.setAttribute('y', measure.stave.getYForLine(5) + 42 + (voiceId - 1) * 24);
                        label.setAttribute('text-anchor', 'middle');
                        label.setAttribute('font-size', '10');
                        label.setAttribute('fill', '#111');
                        label.classList.add('print-rhythm');
                        label.textContent = (names[rhythm.type] || rhythm.type) + '·'.repeat(rhythm.dots) + (rhythm.tuplet ? '³' : '');
                        if (source.isRest()) {
                            const rest = document.createElementNS(ns, 'tspan');
                            rest.setAttribute('x', label.getAttribute('x'));
                            rest.setAttribute('dy', '11');
                            rest.textContent = '休符';
                            label.appendChild(rest);
                        }
                        svg.appendChild(label);
                    }
                }
            }
        }
    }
    async function render() {
        const osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay('engraving', {
            autoResize: false, backend: 'svg', drawTitle: false, drawComposer: false,
            drawPartNames: false, drawPartAbbreviations: false, drawMeasureNumbers: true,
        });
        const xml = new DOMParser().parseFromString(data.xml, 'application/xml');
        // OSMD's metronome renderer places only one mark per measure and may
        // overlap high guitar notes. Words participate in its collision layout
        // and retain every change on the exact note boundary from the XML.
        for (const metronome of xml.querySelectorAll('metronome')) {
            const words = xml.createElement('words');
            words.textContent = `♩ = ${metronome.querySelector('per-minute').textContent}`;
            words.setAttribute('font-size', '12');
            const direction = metronome.parentNode.parentNode;
            metronome.replaceWith(words);
            direction.querySelector('sound')?.remove();
        }
        // Combine simultaneous textual marks so tempo and chord names cannot
        // occupy the same anchor. No time or note data is changed.
        for (const measure of xml.querySelectorAll('part > measure')) {
            let previous = null;
            for (const element of [...measure.children]) {
                const words = element.querySelector('direction-type > words');
                if (element.tagName === 'direction' && words) {
                    if (previous) {
                        previous.textContent += `　${words.textContent}`;
                        element.remove();
                    } else previous = words;
                } else if (['note', 'backup', 'forward'].includes(element.tagName)) previous = null;
            }
        }
        const voices = Math.max(1, ...[...xml.querySelectorAll('note > voice')].map(v => Number(v.textContent)));
        const metadata = rhythmMap(xml);
        xml.querySelectorAll('[color]').forEach(element => element.removeAttribute('color'));
        osmd.EngravingRules.MinimumDistanceBetweenSystems = 16 + voices * 2.4;
        osmd.EngravingRules.RenderXMeasuresPerLineAkaSystem = 3;
        osmd.EngravingRules.RenderMeasureNumbersForImplicitMeasures = true;
        await osmd.load(new XMLSerializer().serializeToString(xml));
        osmd.render();
        const svg = document.querySelector('#engraving svg');
        if (!svg) throw new Error('楽譜を描画できませんでした。');
        rhythms(osmd, svg, metadata);
        const systems = osmd.GraphicSheet.MusicPages.flatMap(page => page.MusicSystems);
        const width = Number(svg.getAttribute('width'));
        const box = svg.getBBox();
        if (box.x < -1 || box.x + box.width > width + 1) throw new Error('記号が印刷幅を超えています。長いコード名や音の密度を確認してください。');
        const height = Math.max(Number(svg.getAttribute('height')), box.y + box.height + 12);
        const boundaries = systems.map(system => (system.PositionAndShape.AbsolutePosition.y + system.PositionAndShape.BorderTop) * 10 - 12);
        boundaries[0] = Math.max(0, box.y - 12);
        boundaries.push(height);
        for (const label of svg.querySelectorAll('.print-rhythm')) {
            const box = label.getBBox();
            if (boundaries.some(y => box.y < y && y < box.y + box.height)) {
                throw new Error('音価の表示が段の境界に重なっています。音の密度を確認してください。');
            }
        }
        const container = document.getElementById('print-pages');
        const pxPerMm = 96 / 25.4;
        const scale = 180 * pxPerMm / width;
        let page, used = 0;
        function addPage() {
            page = document.createElement('article');
            page.className = 'print-page';
            if (!container.children.length) {
                page.appendChild(document.getElementById('page-heading').content.cloneNode(true));
            } else used = 0;
            container.appendChild(page);
            used = page.querySelector('header')?.getBoundingClientRect().height || 0;
        }
        addPage();
        systems.forEach((system, index) => {
            const y = boundaries[index];
            const slice = boundaries[index + 1] - y;
            const renderedHeight = slice * scale;
            if (!(slice > 0) || renderedHeight > 267 * pxPerMm) throw new Error('1段がA4の印刷範囲を超えています。譜面を確認してください。');
            if (used + renderedHeight > 267 * pxPerMm) addPage();
            const copy = svg.cloneNode(true);
            copy.removeAttribute('id');
            copy.querySelectorAll('[id]').forEach(element => element.removeAttribute('id'));
            copy.setAttribute('viewBox', `0 ${y} ${width} ${slice}`);
            copy.setAttribute('height', renderedHeight);
            copy.removeAttribute('width');
            copy.classList.add('print-system');
            copy.dataset.measures = system.GraphicalMeasures?.map(m => m[0]?.parentSourceMeasure.getPrintedMeasureNumber()).join(',') || '';
            page.appendChild(copy);
            used += renderedHeight;
        });
        [...container.children].forEach((page, index) => {
            const footer = document.createElement('footer');
            footer.textContent = `${index + 1} / ${container.children.length}`;
            page.appendChild(footer);
        });
        document.body.dataset.printReady = 'true';
        button.disabled = false;
        status.textContent = `${container.children.length}ページの印刷準備ができました。`;
        window.printScore = osmd;
    }
    button.addEventListener('click', () => window.print());
    render().catch(error => {
        document.getElementById('print-pages').replaceChildren();
        status.textContent = `印刷プレビューを作成できませんでした。${error.message}`;
    });
})();
