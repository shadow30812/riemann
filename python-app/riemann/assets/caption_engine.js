(function () {
    if (window.RiemannCaptions) return;

    class RiemannCaptions {
        constructor() {
            this.enabled = false;
            this.ws = null;
            this.audioCtx = null;
            this.overlay = null;
            this.processor = null;
        }

        enable() {
            if (this.enabled) return;
            this.enabled = true;
            this.initUI();
            this.startCapture();
        }

        disable() {
            this.enabled = false;
            if (this.ws) Object.assign(this.ws, { onclose: null }), this.ws.close();
            if (this.audioCtx) this.audioCtx.close();
            if (this.overlay) this.overlay.style.display = 'none';
        }

        initUI() {
            if (this.overlay) return;
            this.overlay = document.createElement('div');
            Object.assign(this.overlay.style, {
                position: 'fixed',
                bottom: '10%',
                left: '50%',
                transform: 'translateX(-50%)',
                backgroundColor: 'rgba(0,0,0,0.85)',
                color: 'white',
                padding: '12px 24px',
                borderRadius: '8px',
                fontFamily: 'sans-serif',
                fontSize: '22px',
                zIndex: '2147483647',
                pointerEvents: 'auto',
                cursor: 'grab',
                textAlign: 'center',
                display: 'none',
                textShadow: '1px 1px 2px black',
                transition: 'opacity 0.2s',
                maxWidth: '80%',
                userSelect: 'none'
            });

            let isDragging = false;
            let offsetX = 0, offsetY = 0;

            this.overlay.addEventListener('mousedown', (e) => {
                isDragging = true;
                this.overlay.style.cursor = 'grabbing';

                if (this.overlay.style.transform !== 'none') {
                    const rect = this.overlay.getBoundingClientRect();
                    this.overlay.style.transform = 'none';
                    this.overlay.style.left = `${rect.left}px`;
                    this.overlay.style.top = `${rect.top}px`;
                    this.overlay.style.bottom = 'auto';
                }

                const rect = this.overlay.getBoundingClientRect();
                offsetX = e.clientX - rect.left;
                offsetY = e.clientY - rect.top;
            });

            window.addEventListener('mousemove', (e) => {
                if (!isDragging) return;
                this.overlay.style.left = `${e.clientX - offsetX}px`;
                this.overlay.style.top = `${e.clientY - offsetY}px`;
            });

            window.addEventListener('mouseup', () => {
                if (isDragging) {
                    isDragging = false;
                    this.overlay.style.cursor = 'grab';
                }
            });

            let currentFontSize = 22;
            this.overlay.addEventListener('wheel', (e) => {
                e.preventDefault();
                currentFontSize += (e.deltaY < 0) ? 2 : -2;
                currentFontSize = Math.max(12, Math.min(64, currentFontSize));
                this.overlay.style.fontSize = `${currentFontSize}px`;
            }, { passive: false });

            document.body.appendChild(this.overlay);
        }

        startCapture() {
            const video = document.querySelector('video');
            if (!video) return;

            const port = window.RIEMANN_CAPTIONS_PORT || 8765;
            this.ws = new WebSocket("ws://127.0.0.1:" + port);

            this.ws.onmessage = (event) => {
                if (event.data.trim()) {
                    this.overlay.style.display = 'block';
                    this.overlay.innerText = event.data;
                }
            };

            this.ws.onopen = () => {
                this.audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });

                let stream;
                if (video.captureStream) stream = video.captureStream();
                else if (video.mozCaptureStream) stream = video.mozCaptureStream();
                else return;

                const source = this.audioCtx.createMediaStreamSource(stream);
                this.processor = this.audioCtx.createScriptProcessor(4096, 1, 1);

                source.connect(this.processor);
                this.processor.connect(this.audioCtx.destination);

                let rollingBuffer = [];
                let newSamplesCount = 0;

                const SAMPLE_RATE = 16000;
                const CONTEXT_WINDOW = SAMPLE_RATE * 3;
                const UPDATE_INTERVAL = SAMPLE_RATE * 0.8;

                this.processor.onaudioprocess = (e) => {
                    if (!this.enabled || this.ws.readyState !== WebSocket.OPEN) return;

                    const pcm = new Float32Array(e.inputBuffer.getChannelData(0));
                    rollingBuffer.push(pcm);
                    newSamplesCount += pcm.length;

                    if (newSamplesCount >= UPDATE_INTERVAL) {
                        let totalLength = rollingBuffer.reduce((acc, arr) => acc + arr.length, 0);

                        while (totalLength > CONTEXT_WINDOW && rollingBuffer.length > 1) {
                            const removed = rollingBuffer.shift();
                            totalLength -= removed.length;
                        }

                        const merged = new Float32Array(totalLength);
                        let offset = 0;
                        for (let chunk of rollingBuffer) {
                            merged.set(chunk, offset);
                            offset += chunk.length;
                        }

                        this.ws.send(merged.buffer);
                        newSamplesCount = 0;
                    }
                };
            };
        }
    }

    window.RiemannCaptions = new RiemannCaptions();
})();