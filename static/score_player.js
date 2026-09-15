/* Local-only Web Audio preview; score beats are independent of playback speed. */
class ScorePlayer {
    constructor(data, osmd) {
        this.data = data;
        this.osmd = osmd;
        this.context = null;
        this.active = new Set();
        this.state = 'stopped';
        this.offset = 0;
        this.rate = 1;
        this.mode = 'melody';
        this.generation = 0;
        this.tempos = data.tempos.map((tempo, i) => ({...tempo, seconds: 0}));
        for (let i = 1; i < this.tempos.length; i++) {
            const previous = this.tempos[i - 1];
            this.tempos[i].seconds = previous.seconds + (this.tempos[i].start - previous.start) * 60 / previous.bpm;
        }
        this.end = this.secondsAt(data.end);
        this.events = data.events.map(event => ({...event, start: this.secondsAt(event.start), end: this.secondsAt(event.end)}));
        this.status = document.getElementById('playback-status');
        this.positionLabel = document.getElementById('playback-position');
        this.playButton = document.getElementById('play-score');
        this.pauseButton = document.getElementById('pause-score');
        this.stopButton = document.getElementById('stop-score');
        this.playButton.onclick = () => this.play();
        this.pauseButton.onclick = () => this.pause();
        this.stopButton.onclick = () => this.stop();
        document.getElementById('playback-speed').onchange = event => this.configure('rate', Number(event.target.value));
        document.getElementById('playback-mode').onchange = event => this.configure('mode', event.target.value);
        // Cancel scheduled and sounding notes before any edit/upload navigation.
        document.addEventListener('submit', () => this.stop());
        document.addEventListener('input', event => {
            if (event.target.closest('form')) this.stop();
        });
        window.addEventListener('pagehide', () => this.stop());
        this.status.textContent = '再生できます。';
        this.controls();
    }

    secondsAt(beat) {
        const tempo = [...this.tempos].reverse().find(item => item.start <= beat) || this.tempos[0];
        return tempo.seconds + (beat - tempo.start) * 60 / tempo.bpm;
    }

    beatAt(seconds) {
        const tempo = [...this.tempos].reverse().find(item => item.seconds <= seconds) || this.tempos[0];
        return tempo.start + (seconds - tempo.seconds) * tempo.bpm / 60;
    }

    get position() {
        return this.state === 'playing' ? Math.min(this.end, this.offset + (this.context.currentTime - this.anchor) * this.rate) : this.offset;
    }

    controls() {
        this.playButton.disabled = ['playing', 'starting'].includes(this.state);
        this.pauseButton.disabled = this.state !== 'playing';
        this.stopButton.disabled = this.state === 'stopped';
    }

    async play() {
        if (['playing', 'starting'].includes(this.state)) return;
        if (this.data.missing?.length && !document.getElementById('allow-missing-playback')?.checked) {
            this.status.textContent = '未変換の箇所を確認し、確認欄にチェックを入れてください。';
            return;
        }
        const generation = ++this.generation;
        this.state = 'starting';
        this.controls();
        try {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (!AudioContext) throw new Error('このブラウザは音声再生に対応していません。');
            this.context ||= new AudioContext();
            await this.context.resume();
            if (generation !== this.generation) return;
            if (this.context.state !== 'running') throw new Error('音声を開始できません。ブラウザの音声設定を確認してください。');
            this.queue = this.events.filter(event => event.end > this.offset && (this.mode === 'all' || event.channel === 'melody'));
            this.next = 0;
            this.anchor = this.context.currentTime;
            this.state = 'playing';
            this.status.textContent = '再生中';
            this.controls();
            this.tick();
            if (this.state === 'playing') this.timer = setInterval(() => this.tick(), 25);
        } catch (error) {
            if (generation !== this.generation) return;
            this.stop();
            this.status.textContent = error.message;
        }
    }

    sound(event) {
        const now = this.context.currentTime;
        const start = Math.max(now, this.anchor + (Math.max(event.start, this.offset) - this.offset) / this.rate);
        const end = this.anchor + (event.end - this.offset) / this.rate;
        if (end <= start) return;
        const oscillator = this.context.createOscillator();
        const gain = this.context.createGain();
        oscillator.type = event.channel === 'melody' ? 'triangle' : 'sine';
        oscillator.frequency.value = 440 * 2 ** ((event.midi - 69) / 12);
        const volume = event.channel === 'melody' ? 0.12 : 0.025;
        const fade = Math.min(0.008, (end - start) / 3);
        gain.gain.setValueAtTime(0, start);
        gain.gain.linearRampToValueAtTime(volume, start + fade);
        gain.gain.setValueAtTime(volume, end - fade);
        gain.gain.linearRampToValueAtTime(0, end);
        oscillator.connect(gain);
        gain.connect(this.context.destination);
        const voice = {oscillator, gain};
        this.active.add(voice);
        oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); this.active.delete(voice); };
        oscillator.start(start);
        oscillator.stop(end);
    }

    tick() {
        if (this.state !== 'playing') return;
        const position = this.position;
        while (this.next < this.queue.length && this.queue[this.next].start <= position + 0.12 * this.rate) this.sound(this.queue[this.next++]);
        this.showPosition(position);
        if (position >= this.end) this.stop('再生が終了しました。');
    }

    showPosition(seconds) {
        const beat = this.beatAt(seconds);
        this.positionLabel.textContent = `曲の先頭から ${beat.toFixed(2)}（四分音符＝1）`;
        const cursor = this.osmd.cursor;
        if (beat < (this.cursorBeat || 0)) cursor.reset();
        // Stay at the latest notated onset (including rests) without moving ahead of audio.
        while (!cursor.Iterator.EndReached) {
            const current = cursor.Iterator.CurrentSourceTimestamp.RealValue * 4;
            if (current > beat + 1e-7) { cursor.previous(); break; }
            cursor.next();
            if (cursor.Iterator.EndReached) { cursor.previous(); break; }
        }
        this.cursorBeat = beat;
        cursor.show();
        cursor.update();
    }

    silence() {
        clearInterval(this.timer);
        for (const voice of this.active) {
            try { voice.oscillator.stop(); } catch (_) { /* Already ended. */ }
            voice.oscillator.disconnect();
            voice.gain.disconnect();
        }
        this.active.clear();
    }

    pause() {
        if (this.state === 'playing') this.offset = this.position;
        ++this.generation;
        this.silence();
        this.state = 'paused';
        this.status.textContent = '一時停止中';
        this.controls();
    }

    stop(message = '停止しました。') {
        ++this.generation;
        this.silence();
        this.state = 'stopped';
        this.offset = 0;
        this.cursorBeat = 0;
        this.osmd.cursor.reset();
        this.osmd.cursor.hide();
        this.positionLabel.textContent = '曲の先頭から 0（四分音符＝1）';
        this.status.textContent = message;
        this.controls();
    }

    configure(field, value) {
        const resume = this.state === 'playing';
        if (resume) this.pause();
        this[field] = value;
        if (resume) this.play();
    }
}

(async () => {
    const element = document.getElementById('score-preview-data');
    if (!element) return;
    const status = document.getElementById('playback-status');
    try {
        const data = JSON.parse(element.textContent);
        const osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay('score-sheet', {
            autoResize: true, backend: 'svg', drawTitle: false, drawPartNames: false,
            drawMeasureNumbers: true, followCursor: true,
        });
        if (data.guitar) {
            osmd.EngravingRules.MetronomeMarkYShift = -4;
            osmd.EngravingRules.MinSkyBottomDistBetweenSystems = 8;
            osmd.EngravingRules.PageBottomMargin = 10;
        }
        await osmd.load(data.xml);
        osmd.render();
        window.scorePlayer = new ScorePlayer(data, osmd);
        if (data.guitar) window.tabEditor = new TabEditor(data, osmd);
    } catch (error) {
        status.textContent = '五線譜を表示できませんでした。音符の選択や記譜内容を確認してください。';
        console.error(error);
    }
})();
