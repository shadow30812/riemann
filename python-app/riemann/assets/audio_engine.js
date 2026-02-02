(function () {
    if (window.RiemannAudio) return;

    class RiemannAudio {
        constructor() {
            this.ctx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: 'playback' });
            this.sourceNode = null;
            this.nodes = {};
            this.initialized = false;
            this.enabled = false;
            this.smartMode = false;
            this.mediaElement = null;
            this.smartInterval = null; // [NEW] Timer for efficient background logic

            this.presets = {
                'universal': { name: '✨ Universal Hi-Fi', gain: 1.2, sat: 40, width: 1.2, bass: 3.5, treble: 2.5, air: 0.15 },
                'laptop': { name: '💻 Laptop Speakers', gain: 1.5, sat: 20, width: 1.0, bass: -5.0, treble: 4.0, air: 0.05 },
                'bass': { name: '🎧 Bass Boost', gain: 1.1, sat: 60, width: 1.1, bass: 8.0, treble: 1.0, air: 0.0 },
                'vocal': { name: '🎙️ Vocal / Podcast', gain: 1.2, sat: 10, width: 0.8, bass: -2.0, treble: 1.0, air: 0.0 },
                'flat': { name: '➖ Flat / Reference', gain: 1.0, sat: 0, width: 1.0, bass: 0.0, treble: 0.0, air: 0.0 }
            };

            this.currentPreset = 'universal';
            this.params = { ...this.presets['universal'] };

            this.initUI();
            this.startObserver();

            // [NEW] Fallback Unlocker
            document.addEventListener('click', () => {
                if (this.ctx.state === 'suspended') this.ctx.resume();
            }, { once: false, passive: true });
        }

        async initAudio() {
            if (this.initialized) return;

            // 1. DSP: Impulse Response
            const sampleRate = this.ctx.sampleRate;
            const length = sampleRate * 0.5;
            const impulse = this.ctx.createBuffer(2, length, sampleRate);
            for (let ch = 0; ch < 2; ch++) {
                const data = impulse.getChannelData(ch);
                for (let i = 0; i < length; i++) {
                    data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, 3);
                }
            }

            // 2. DSP: Create Nodes
            this.nodes.preAmp = this.ctx.createGain();
            this.nodes.saturator = this.ctx.createWaveShaper();
            this.nodes.saturator.oversample = '4x'; // High quality saturation

            this.nodes.splitter = this.ctx.createChannelSplitter(2);
            this.nodes.midGain = this.ctx.createGain();
            this.nodes.sideGain = this.ctx.createGain();
            this.nodes.merger = this.ctx.createChannelMerger(2);

            this.nodes.lowShelf = this.ctx.createBiquadFilter();
            this.nodes.lowShelf.type = 'lowshelf';
            this.nodes.lowShelf.frequency.value = 200;

            this.nodes.highShelf = this.ctx.createBiquadFilter();
            this.nodes.highShelf.type = 'highshelf';
            this.nodes.highShelf.frequency.value = 3000;

            this.nodes.convolver = this.ctx.createConvolver();
            this.nodes.convolver.buffer = impulse;
            this.nodes.reverbGain = this.ctx.createGain();
            this.nodes.dryGain = this.ctx.createGain();

            this.nodes.compressor = this.ctx.createDynamicsCompressor();
            this.nodes.compressor.threshold.value = -24;
            this.nodes.compressor.knee.value = 30;
            this.nodes.compressor.ratio.value = 12;
            this.nodes.compressor.attack.value = 0.003;
            this.nodes.compressor.release.value = 0.25;

            this.nodes.limiter = this.ctx.createDynamicsCompressor();
            this.nodes.limiter.threshold.value = -0.5;
            this.nodes.limiter.ratio.value = 20;
            this.nodes.limiter.attack.value = 0;

            this.nodes.analyser = this.ctx.createAnalyser();
            this.nodes.analyser.fftSize = 512;
            this.nodes.analyser.smoothingTimeConstant = 0.85;

            // 3. Connect Graph
            this.nodes.preAmp.connect(this.nodes.saturator);
            this.nodes.saturator.connect(this.nodes.splitter);

            this.nodes.splitter.connect(this.nodes.midGain, 0);
            this.nodes.splitter.connect(this.nodes.sideGain, 1);
            this.nodes.midGain.connect(this.nodes.merger, 0, 0);
            this.nodes.sideGain.connect(this.nodes.merger, 0, 1);

            this.nodes.merger.connect(this.nodes.lowShelf);
            this.nodes.lowShelf.connect(this.nodes.highShelf);

            this.nodes.highShelf.connect(this.nodes.dryGain);
            this.nodes.highShelf.connect(this.nodes.convolver);
            this.nodes.convolver.connect(this.nodes.reverbGain);

            this.nodes.dryGain.connect(this.nodes.compressor);
            this.nodes.reverbGain.connect(this.nodes.compressor);

            this.nodes.compressor.connect(this.nodes.limiter);
            this.nodes.limiter.connect(this.nodes.analyser);

            this.initialized = true;
            this.loadPreset(this.currentPreset);

            // [NEW] Start the Efficient Smart Loop (Detached from Visualizer)
            this.startSmartLoop();
        }

        makeDistortionCurve(amount) {
            const k = typeof amount === 'number' ? amount : 50;
            const n_samples = 44100;
            const curve = new Float32Array(n_samples);
            const deg = Math.PI / 180;
            for (let i = 0; i < n_samples; ++i) {
                let x = i * 2 / n_samples - 1;
                curve[i] = (3 + k) * x * 20 * deg / (Math.PI + k * Math.abs(x));
            }
            return curve;
        }

        startObserver() {
            const attach = () => {
                const media = document.querySelector('video, audio');
                if (media && this.mediaElement !== media) {
                    try {
                        this.mediaElement = media;
                        media.crossOrigin = "anonymous";

                        this.initAudio().then(() => {
                            if (this.sourceNode) this.sourceNode.disconnect();
                            this.sourceNode = this.ctx.createMediaElementSource(media);
                            if (this.enabled) this.enable(); // Re-attach logic
                            else this.sourceNode.connect(this.ctx.destination);
                        });
                    } catch (e) { console.error("Riemann Attach Error:", e); }
                }
            };
            setInterval(attach, 1000);
        }

        // [NEW] Efficient Background Loop for Smart Logic
        startSmartLoop() {
            if (this.smartInterval) clearInterval(this.smartInterval);

            // Run only 25 times per second (40ms) instead of 60 (16ms)
            // This is huge for battery savings.
            this.smartInterval = setInterval(() => {
                if (!this.smartMode || !this.initialized || !this.enabled) return;

                const bufferLength = this.nodes.analyser.frequencyBinCount;
                const dataArray = new Uint8Array(bufferLength);
                this.nodes.analyser.getByteFrequencyData(dataArray);

                this.runSmartLogic(dataArray);
            }, 40);
        }

        runSmartLogic(dataArray) {
            // 1. Calculate Energy
            const bufferLength = dataArray.length;
            let bassSum = 0, midSum = 0, highSum = 0;

            for (let i = 0; i < bufferLength; i++) {
                if (i < 4) bassSum += dataArray[i];
                else if (i < 60) midSum += dataArray[i];
                else highSum += dataArray[i];
            }

            const bassAvg = bassSum / 4;
            const midAvg = midSum / 56;
            const highAvg = highSum / (bufferLength - 60);

            // 2. Target Curve
            const targetBass = midAvg * 1.2;
            const targetHigh = midAvg * 0.9;
            const targetLoudness = 140;

            // 3. Adjust (Gentle Nudge)
            let newGain = this.params.gain;
            if (midAvg > 10) {
                const gainCorrection = (targetLoudness - midAvg) * 0.001;
                newGain = Math.max(0.8, Math.min(2.0, this.params.gain + gainCorrection));
            }

            const bassCorrection = (targetBass - bassAvg) * 0.05;
            let newBass = Math.max(-5, Math.min(8, this.params.bass + bassCorrection));

            const highCorrection = (targetHigh - highAvg) * 0.05;
            let newTreble = Math.max(-2, Math.min(6, this.params.treble + highCorrection));

            // Apply
            this.setParam('preAmp', 'gain', newGain);
            this.setParam('lowShelf', 'gain', newBass);
            this.setParam('highShelf', 'gain', newTreble);

            // Only update UI if it's actually visible
            if (this.ui && this.ui.style.display !== 'none') {
                this.updateUISliders();
            }
        }

        setParam(node, param, value) {
            if (!this.initialized) return;

            if (node === 'preAmp') this.params.gain = value;
            if (node === 'saturator') this.params.sat = value;
            if (node === 'width') this.params.width = value;
            if (node === 'lowShelf') this.params.bass = value;
            if (node === 'highShelf') this.params.treble = value;
            if (node === 'reverbGain') this.params.air = value;

            if (node === 'saturator') {
                this.nodes.saturator.curve = this.makeDistortionCurve(value);
            } else if (node === 'width') {
                this.nodes.sideGain.gain.value = value;
                this.nodes.midGain.gain.value = 2 - value;
            } else {
                this.nodes[node][param].setTargetAtTime(value, this.ctx.currentTime, 0.1);
            }
        }

        loadPreset(key) {
            const p = this.presets[key];
            if (!p) return;
            this.currentPreset = key;
            this.smartMode = false;
            this.updateSmartButton();

            this.setParam('preAmp', 'gain', p.gain);
            this.setParam('saturator', null, p.sat);
            this.setParam('width', null, p.width);
            this.setParam('lowShelf', 'gain', p.bass);
            this.setParam('highShelf', 'gain', p.treble);
            this.setParam('reverbGain', 'gain', p.air);

            this.updateUISliders();
            const select = this.ui.querySelector('select');
            if (select) select.value = key;
        }

        toggleSmartMode() {
            if (!this.smartMode) {
                this.loadPreset('flat');
                this.smartMode = true;
            } else {
                this.smartMode = false;
            }
            this.updateSmartButton();
        }

        updateSmartButton() {
            if (!this.ui) return;
            const btn = this.ui.querySelector('#riemann-smart-btn');
            if (btn) {
                btn.style.background = this.smartMode ? '#FF4500' : '#333';
                btn.style.color = this.smartMode ? '#fff' : '#aaa';
                btn.textContent = this.smartMode ? '🧠 SMART ON' : '🧠 SMART OFF';
            }
        }

        updateUISliders() {
            if (!this.ui) return;
            const set = (id, val) => {
                const el = this.ui.querySelector(`#riemann-slider-${id}`);
                if (el) el.value = val;
            };
            set('gain', this.params.gain);
            set('sat', this.params.sat);
            set('width', this.params.width);
            set('bass', this.params.bass);
            set('treble', this.params.treble);
            set('air', this.params.air);
        }

        enable() {
            if (!this.initialized || !this.sourceNode) return;

            if (this.ctx.state === 'suspended') {
                this.ctx.resume().then(() => console.log("Riemann: Audio Resumed"));
            }

            this.enabled = true;
            this.sourceNode.disconnect();
            this.sourceNode.connect(this.nodes.preAmp);
            this.nodes.analyser.connect(this.ctx.destination);
            this.ui.style.display = 'flex';
        }

        disable() {
            if (!this.initialized || !this.sourceNode) return;
            this.enabled = false;
            this.sourceNode.disconnect();
            this.nodes.analyser.disconnect();
            this.sourceNode.connect(this.ctx.destination);
            this.ui.style.display = 'none';
        }

        initUI() {
            const container = document.createElement('div');
            container.id = 'riemann-overlay';
            Object.assign(container.style, {
                position: 'fixed', bottom: '20px', right: '20px', width: '280px',
                background: 'rgba(15, 15, 15, 0.98)', color: '#eee',
                padding: '15px', borderRadius: '12px', zIndex: '2147483647',
                fontFamily: 'Segoe UI, monospace', border: '1px solid #FF4500',
                boxShadow: '0 0 20px rgba(255, 69, 0, 0.2)',
                display: 'none', flexDirection: 'column', gap: '10px'
            });

            // HEADER
            const header = document.createElement('div');
            header.style.display = 'flex';
            header.style.justifyContent = 'space-between';
            header.style.alignItems = 'center';

            const leftSide = document.createElement('div');
            const icon = document.createElement('span');
            icon.textContent = '◆ ';
            icon.style.color = '#FF4500';
            const title = document.createElement('span');
            title.textContent = 'AUDIO ENGINE';
            title.style.fontWeight = 'bold';
            title.style.fontSize = '14px';
            leftSide.appendChild(icon);
            leftSide.appendChild(title);
            header.appendChild(leftSide);

            const smartBtn = document.createElement('button');
            smartBtn.id = 'riemann-smart-btn';
            smartBtn.textContent = '🧠 SMART OFF';
            Object.assign(smartBtn.style, {
                fontSize: '10px', padding: '2px 6px', borderRadius: '4px',
                border: '1px solid #555', background: '#333', color: '#aaa', cursor: 'pointer',
                marginLeft: 'auto', marginRight: '10px'
            });
            smartBtn.onclick = () => this.toggleSmartMode();
            header.appendChild(smartBtn);

            const closeBtn = document.createElement('span');
            closeBtn.textContent = '✕';
            closeBtn.style.cursor = 'pointer';
            closeBtn.onclick = () => { container.style.display = 'none'; };
            header.appendChild(closeBtn);
            container.appendChild(header);

            // PRESET SELECTOR
            const presetRow = document.createElement('div');
            presetRow.style.display = 'flex';
            const select = document.createElement('select');
            Object.assign(select.style, {
                width: '100%', padding: '4px', borderRadius: '4px',
                background: '#222', color: '#fff', border: '1px solid #444'
            });
            for (const [key, val] of Object.entries(this.presets)) {
                const opt = document.createElement('option');
                opt.value = key;
                opt.textContent = val.name;
                if (key === this.currentPreset) opt.selected = true;
                select.appendChild(opt);
            }
            select.onchange = (e) => this.loadPreset(e.target.value);
            presetRow.appendChild(select);
            container.appendChild(presetRow);

            // SLIDERS
            const createSlider = (label, id, min, max, val, callback) => {
                const row = document.createElement('div');
                row.style.display = 'flex';
                row.style.alignItems = 'center';
                row.style.fontSize = '11px';
                row.style.marginBottom = '2px';

                const labelDiv = document.createElement('div');
                labelDiv.textContent = label;
                labelDiv.style.width = '60px';
                labelDiv.style.color = '#aaa';
                row.appendChild(labelDiv);

                const input = document.createElement('input');
                input.id = `riemann-slider-${id}`;
                input.type = 'range';
                input.min = min;
                input.max = max;
                input.step = 0.1;
                input.value = val;
                input.style.flex = '1';
                input.style.accentColor = '#FF4500';
                input.oninput = (e) => {
                    this.smartMode = false;
                    this.updateSmartButton();
                    callback(parseFloat(e.target.value));
                };
                row.appendChild(input);
                container.appendChild(row);
            };

            const p = this.params;
            createSlider('GAIN', 'gain', 0, 3, p.gain, v => this.setParam('preAmp', 'gain', v));
            createSlider('WARMTH', 'sat', 0, 400, p.sat, v => this.setParam('saturator', null, v));
            createSlider('WIDTH', 'width', 0, 2, p.width, v => this.setParam('width', null, v));
            createSlider('BASS', 'bass', -20, 20, p.bass, v => this.setParam('lowShelf', 'gain', v));
            createSlider('TREBLE', 'treble', -20, 20, p.treble, v => this.setParam('highShelf', 'gain', v));
            createSlider('AIR', 'air', 0, 1, p.air, v => this.setParam('reverbGain', 'gain', v));

            // VISUALIZER
            const canvas = document.createElement('canvas');
            canvas.width = 250; canvas.height = 60;
            canvas.style.marginTop = '8px';
            canvas.style.background = '#111';
            canvas.style.borderRadius = '4px';
            canvas.style.border = '1px solid #333';
            container.appendChild(canvas);

            document.body.appendChild(container);
            this.ui = container;
            this.canvasCtx = canvas.getContext('2d');
            this.drawVisualizer();
        }

        drawVisualizer() {
            requestAnimationFrame(() => this.drawVisualizer());
            // [FIX] Strict Battery Check
            // If the UI is hidden, we STOP rendering pixels completely.
            // This allows the GPU to sleep.
            if (!this.initialized || this.ui.style.display === 'none') return;

            const bufferLength = this.nodes.analyser.frequencyBinCount;
            const dataArray = new Uint8Array(bufferLength);
            this.nodes.analyser.getByteFrequencyData(dataArray);

            const ctx = this.canvasCtx;
            const w = ctx.canvas.width;
            const h = ctx.canvas.height;
            ctx.clearRect(0, 0, w, h);

            const barWidth = (w / bufferLength) * 2.5;
            let x = 0;
            for (let i = 0; i < bufferLength; i++) {
                const barHeight = (dataArray[i] / 255) * h;
                ctx.fillStyle = `rgb(${dataArray[i] + 50}, 100, 50)`;
                ctx.fillRect(x, h - barHeight, barWidth, barHeight);
                x += barWidth + 1;
            }
        }
    }
    window.RiemannAudio = new RiemannAudio();
})();