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
                pointerEvents: 'none',
                textAlign: 'center',
                display: 'none',
                textShadow: '1px 1px 2px black',
                transition: 'opacity 0.2s',
                maxWidth: '80%'
            });
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

                let audioBuffer = [];
                let bufferSize = 0;
                const TARGET_SAMPLES = 16000 * 3;  

                this.processor.onaudioprocess = (e) => {
                    if (!this.enabled || this.ws.readyState !== WebSocket.OPEN) return;

                    const pcm = e.inputBuffer.getChannelData(0);
                    audioBuffer.push(new Float32Array(pcm));
                    bufferSize += pcm.length;

                    if (bufferSize >= TARGET_SAMPLES) {
                        const merged = new Float32Array(bufferSize);
                        let offset = 0;
                        for (let chunk of audioBuffer) {
                            merged.set(chunk, offset);
                            offset += chunk.length;
                        }
                        this.ws.send(merged.buffer);

                        audioBuffer = [];
                        bufferSize = 0;
                    }
                };
            };
        }
    }

    window.RiemannCaptions = new RiemannCaptions();
})();