const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Test audio scheduling and UI state without requiring speakers or a browser.
function setup({resume, end = 8} = {}) {
    const elements = new Map();
    const listeners = {};
    const timers = new Map();
    const oscillators = [];
    let timerId = 0;
    class AudioContext {
        currentTime = 0;
        state = 'running';
        destination = {};
        resume() { return resume ? resume() : Promise.resolve(); }
        createOscillator() {
            const oscillator = {
                frequency: {}, connect() {}, disconnect() {},
                start(time) { this.startTime = time; },
                stop(time) { this.stopTime = time; },
            };
            oscillators.push(oscillator);
            return oscillator;
        }
        createGain() {
            return {connect() {}, disconnect() {}, gain: {
                setValueAtTime() {}, linearRampToValueAtTime() {},
            }};
        }
    }
    const document = {
        getElementById(id) {
            if (id === 'score-preview-data') return null;
            if (!elements.has(id)) elements.set(id, {});
            return elements.get(id);
        },
        addEventListener(name, callback) { listeners[name] = callback; },
    };
    let cursorIndex = 0;
    const cursor = {
        Iterator: {
            get EndReached() { return cursorIndex >= 9; },
            get CurrentSourceTimestamp() { return {RealValue: cursorIndex / 4}; },
        },
        next() { cursorIndex++; }, previous() { cursorIndex = Math.max(0, cursorIndex - 1); },
        reset() { cursorIndex = 0; }, show() { this.visible = true; },
        hide() { this.visible = false; }, update() {},
    };
    const sandbox = {document, window: {AudioContext, addEventListener() {}}, console,
        setInterval(callback) { timers.set(++timerId, callback); return timerId; },
        clearInterval(id) { timers.delete(id); },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/score_player.js'), 'utf8') +
        '\nglobalThis.Player = ScorePlayer;', sandbox);
    const player = new sandbox.Player({end, tempos: [{start: 0, bpm: 120}, {start: 4, bpm: 60}],
        events: [
            {start: 0, end: 4, midi: 69, channel: 'melody'},
            {start: 0, end: 4, midi: 48, channel: 'chord'},
            {start: 4, end: 8, midi: 71, channel: 'melody'},
        ]}, {cursor});
    return {player, elements, listeners, timers, oscillators, cursor, cursorIndex: () => cursorIndex};
}

test('tempo changes convert beats and seconds in both directions', () => {
    const {player} = setup();
    for (const [beat, seconds] of [[0, 0], [2, 1], [4, 2], [6, 4], [8, 6]]) {
        assert.equal(player.secondsAt(beat), seconds);
        assert.equal(player.beatAt(seconds), beat);
    }
});

test('pause silences voices and resume starts at the retained position', async () => {
    const {player, oscillators, timers} = setup();
    await player.play();
    assert.equal(oscillators.length, 1); // melody only
    assert.equal(oscillators[0].frequency.value, 440);
    assert.equal(oscillators[0].stopTime, 2);
    player.context.currentTime = 0.75;
    player.pause();
    assert.equal(player.offset, 0.75);
    assert.equal(player.active.size, 0);
    assert.equal(timers.size, 0);
    player.context.currentTime = 10;
    await player.play();
    assert.equal(oscillators[1].startTime, 10);
    assert.equal(oscillators[1].stopTime, 11.25);
});

test('speed and accompaniment changes retain position without transposing', async () => {
    const {player, oscillators} = setup();
    await player.play();
    player.context.currentTime = 0.5;
    player.configure('rate', 2);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(player.offset, 0.5);
    assert.equal(oscillators.at(-1).frequency.value, 440);
    assert.equal(oscillators.at(-1).stopTime, 1.25);
    player.configure('mode', 'all');
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(player.active.size, 2);
    assert.equal(player.offset, 0.5);
});

test('cursor follows the latest onset and stop or edit resets playback', async () => {
    const {player, cursorIndex, cursor, listeners, timers} = setup();
    await player.play();
    player.context.currentTime = 0.8;
    player.tick();
    assert.equal(cursorIndex(), 1);
    player.context.currentTime = 2.1;
    player.tick();
    assert.equal(cursorIndex(), 4);
    listeners.input({target: {closest: () => ({})}});
    assert.equal(player.state, 'stopped');
    assert.equal(player.offset, 0);
    assert.equal(cursorIndex(), 0);
    assert.equal(cursor.visible, false);
    assert.equal(timers.size, 0);
});

test('reaching the end clears audio and timers including an empty timeline', async () => {
    const {player, timers} = setup();
    await player.play();
    player.context.currentTime = 6;
    player.tick();
    assert.equal(player.state, 'stopped');
    assert.equal(player.active.size, 0);
    assert.equal(timers.size, 0);
    const empty = setup({end: 0});
    await empty.player.play();
    assert.equal(empty.timers.size, 0);
});

test('stop during pending audio permission prevents a delayed restart', async () => {
    let finish;
    const {player, oscillators, timers} = setup({resume: () => new Promise(resolve => { finish = resolve; })});
    const pending = player.play();
    assert.equal(player.state, 'starting');
    player.stop();
    finish();
    await pending;
    assert.equal(player.state, 'stopped');
    assert.equal(oscillators.length, 0);
    assert.equal(timers.size, 0);
});

test('audio startup errors restore controls and display the reason', async () => {
    const {player, elements, timers} = setup({resume: () => Promise.reject(new Error('Audio blocked'))});
    await player.play();
    assert.equal(player.state, 'stopped');
    assert.equal(elements.get('playback-status').textContent, 'Audio blocked');
    assert.equal(elements.get('play-score').disabled, false);
    assert.equal(timers.size, 0);
});
