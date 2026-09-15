/* OSMD 2.1.2 adapter: rhythmic TAB and original-note selection across both parts. */
const tabConverter = opensheetmusicdisplay.VexFlowConverter;
const createTabNote = tabConverter.CreateTabNote;
tabConverter.CreateTabNote = function (...args) {
    const note = createTabNote.apply(this, args);
    note.render_options.draw_stem = true;
    note.render_options.draw_dots = true;
    note.setStemDirection(-1);
    return note;
};

class TabEditor {
    constructor(data, osmd) {
        this.data = data;
        this.osmd = osmd;
        this.byTime = new Map(data.note_map.map(note => [note.start.toFixed(7), note]));
        this.selected = document.querySelector('#tab-edit-form input[name=note_id]')?.value;
        const render = osmd.render.bind(osmd);
        osmd.render = (...args) => { render(...args); this.decorate(); };
        this.decorate();
    }

    select(noteId) {
        window.scorePlayer?.stop();
        document.getElementById('tab-note-id').value = noteId;
        document.getElementById('tab-select-form').requestSubmit(document.getElementById('tab-select-submit'));
    }

    decorate() {
        const sheet = document.getElementById('score-sheet');
        sheet.querySelectorAll('.tab-hit, .tab-rest-copy, .tab-rhythm').forEach(el => el.remove());
        for (const measures of this.osmd.GraphicSheet.MeasureList) {
            const upperRests = new Map();
            const upperGraphics = new Map();
            for (const [staff, measure] of measures.entries()) {
                if (!measure) continue;
                for (const entry of measure.staffEntries) {
                    for (const voice of entry.graphicalVoiceEntries) {
                        for (const note of voice.notes) {
                            const beat = note.sourceNote.getAbsoluteTimestamp().RealValue * 4;
                            const element = note.getSVGGElement();
                            if (staff === 0 && element) upperGraphics.set(beat, element);
                            if (note.sourceNote.isRest()) {
                                if (staff === 0 && element) upperRests.set(beat, element);
                                if (staff === 1 && upperRests.has(beat)) {
                                    const source = upperRests.get(beat);
                                    const copy = source.cloneNode(true);
                                    copy.removeAttribute('id');
                                    copy.querySelectorAll('[id]').forEach(el => el.removeAttribute('id'));
                                    const box = source.getBBox();
                                    copy.setAttribute('transform', `translate(0 ${measure.stave.getYForLine(2.5) - box.y - box.height / 2})`);
                                    copy.classList.add('tab-rest-copy');
                                    source.ownerSVGElement.appendChild(copy);
                                }
                                continue;
                            }
                            const mapped = this.byTime.get(beat.toFixed(7));
                            if (!mapped) continue;
                            const svg = element?.ownerSVGElement || upperGraphics.get(beat)?.ownerSVGElement;
                            if (!svg) continue;
                            let box;
                            if (staff === 0 && element) box = element.getBBox();
                            else {
                                const vf = note.vfnote[0];
                                box = {x: vf.getAbsoluteX() - 4, y: vf.getYs()[0] - 12, width: Math.max(vf.getWidth(), 16) + 8, height: 24};
                            }
                            const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                            for (const [key, value] of Object.entries({x: box.x - 3, y: box.y - 3, width: box.width + 6, height: box.height + 6,
                                rx: 4, tabindex: 0, role: 'button', 'data-tab-note': mapped.note_id, 'data-staff': staff,
                                'aria-label': `開始${mapped.start}の${staff === 1 ? 'TAB' : '五線譜'}音符を選択${mapped.missing ? '（未変換）' : ''}`,
                                'aria-pressed': String(mapped.note_id === this.selected)})) rect.setAttribute(key, value);
                            rect.classList.add('tab-hit');
                            rect.classList.toggle('tab-selected', mapped.note_id === this.selected);
                            rect.classList.toggle('tab-missing', mapped.missing);
                            rect.addEventListener('click', () => this.select(mapped.note_id));
                            rect.addEventListener('keydown', event => {
                                if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.select(mapped.note_id); }
                            });
                            svg.appendChild(rect);
                            if (staff === 1) {
                                const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                                label.setAttribute('x', note.vfnote[0].getAbsoluteX());
                                label.setAttribute('y', measure.stave.getYForLine(5) + 56);
                                label.setAttribute('text-anchor', 'middle');
                                label.setAttribute('font-size', '10');
                                label.classList.add('tab-rhythm');
                                label.textContent = ({whole: '全', half: '2分', quarter: '4分', eighth: '8分', '16th': '16分', '32nd': '32分', '64th': '64分'}[mapped.type] || mapped.type) + '·'.repeat(mapped.dots);
                                svg.appendChild(label);
                            }
                        }
                    }
                }
            }
        }
    }
}
