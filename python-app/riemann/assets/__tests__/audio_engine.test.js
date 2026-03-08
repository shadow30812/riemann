/**
 * @jest-environment jsdom
 */

const fs = require('fs');
const path = require('path');

class MockAudioNode {
    constructor() {
        this.connect = jest.fn();
        this.disconnect = jest.fn();
        this.frequency = { value: 0 };
        this.threshold = { value: 0 };
        this.knee = { value: 0 };
        this.ratio = { value: 0 };
        this.attack = { value: 0 };
        this.release = { value: 0 };
        this.gain = { value: 1, setTargetAtTime: jest.fn() };
    }
}

class MockAudioContext {
    constructor() {
        this.state = 'running';
        this.sampleRate = 44100;
        this.currentTime = 0;
        this.destination = new MockAudioNode();
    }
    createGain() { return new MockAudioNode(); }
    createWaveShaper() { return new MockAudioNode(); }
    createChannelSplitter() { return new MockAudioNode(); }
    createChannelMerger() { return new MockAudioNode(); }
    createBiquadFilter() { return new MockAudioNode(); }
    createConvolver() { return new MockAudioNode(); }
    createDynamicsCompressor() { return new MockAudioNode(); }
    createAnalyser() {
        const analyser = new MockAudioNode();
        analyser.frequencyBinCount = 512;
        analyser.getByteFrequencyData = jest.fn();
        return analyser;
    }
    createMediaElementSource() { return new MockAudioNode(); }
    createBuffer() {
        return { getChannelData: () => new Float32Array(100) };
    }
    resume() { return Promise.resolve(); }
}

describe('RiemannAudio Engine', () => {
    beforeAll(() => {
        window.AudioContext = MockAudioContext;
        window.webkitAudioContext = MockAudioContext;
        window.requestAnimationFrame = jest.fn();

        window.HTMLCanvasElement.prototype.getContext = jest.fn(() => ({
            clearRect: jest.fn(),
            fillRect: jest.fn(),
            canvas: { width: 100, height: 100 }
        }));

        const scriptCode = fs.readFileSync(path.resolve(__dirname, '../audio_engine.js'), 'utf8');
        eval(scriptCode);
    });

    test('Engine initializes and exposes window.RiemannAudio', () => {
        expect(window.RiemannAudio).toBeDefined();
        expect(window.RiemannAudio.ctx).toBeInstanceOf(MockAudioContext);
        expect(window.RiemannAudio.currentPreset).toBe('flat');
    });

    test('UI overlay is generated and hidden by default', () => {
        const overlay = document.getElementById('riemann-overlay');
        expect(overlay).not.toBeNull();
        expect(overlay.style.display).toBe('none');

        const select = overlay.querySelector('select');
        expect(select).not.toBeNull();
        expect(select.options.length).toBe(Object.keys(window.RiemannAudio.presets).length);
    });

    test('initAudio constructs the DSP graph correctly', async () => {
        const engine = window.RiemannAudio;
        await engine.initAudio();

        expect(engine.initialized).toBe(true);
        expect(engine.nodes.preAmp).toBeDefined();
        expect(engine.nodes.compressor).toBeDefined();

        expect(engine.nodes.preAmp.connect).toHaveBeenCalledWith(engine.nodes.saturator);
    });

    test('loadPreset updates parameters and UI sliders', () => {
        const engine = window.RiemannAudio;
        engine.loadPreset('bass');

        expect(engine.currentPreset).toBe('bass');
        expect(engine.params.bass).toBe(8.0);
        expect(engine.smartMode).toBe(false);

        const bassSlider = document.getElementById('riemann-slider-bass');
        expect(bassSlider.value).toBe('8');
    });

    test('toggleSmartMode switches states and updates button', () => {
        const engine = window.RiemannAudio;
        const smartBtn = document.getElementById('riemann-smart-btn');

        engine.smartMode = false;
        engine.toggleSmartMode();

        expect(engine.smartMode).toBe(true);
        expect(engine.currentPreset).toBe('flat');
        expect(smartBtn.textContent).toContain('SMART ON');

        engine.toggleSmartMode();
        expect(engine.smartMode).toBe(false);
        expect(smartBtn.textContent).toContain('SMART OFF');
    });

    test('makeDistortionCurve generates correct curve length', () => {
        const engine = window.RiemannAudio;
        const curve = engine.makeDistortionCurve(50);

        expect(curve).toBeInstanceOf(Float32Array);
        expect(curve.length).toBe(44100);
    });
});