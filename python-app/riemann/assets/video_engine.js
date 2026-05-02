/**
 * @fileoverview Riemann Video Engine.
 * Injects a video speed control overlay into web pages.
 */

(function () {
    if (window.RiemannVideo) return;

    class RiemannVideo {
        constructor() {
            this.initialized = false;
            this.enabled = false;
            this.videoElement = null;
            this.currentSpeed = 1.0;

            this.presets = [0.125, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 8.0, 16.0];

            this.initUI();
            this.startObserver();
        }

        startObserver() {
            setInterval(() => {
                const video = document.querySelector('video');
                if (video && this.videoElement !== video) {
                    this.videoElement = video;
                    if (this.enabled) {
                        this.videoElement.playbackRate = this.currentSpeed;
                    }
                    this.videoElement.addEventListener('ratechange', () => {
                        if (this.enabled && this.videoElement.playbackRate !== this.currentSpeed) {
                            this.setSpeed(this.videoElement.playbackRate, false);
                        }
                    });
                }
            }, 1000);
        }

        setSpeed(speed, updateVideo = true) {
            this.currentSpeed = speed;
            if (updateVideo && this.videoElement) {
                this.videoElement.playbackRate = speed;
            }
            if (this.ui) {
                const slider = this.ui.querySelector('#riemann-video-slider');
                const display = this.ui.querySelector('#riemann-video-display');
                if (slider) slider.value = speed;
                if (display) display.textContent = speed.toFixed(2) + 'x';
            }
        }

        enable() {
            this.enabled = true;
            this.ui.style.display = 'flex';
            if (this.videoElement) {
                this.videoElement.playbackRate = this.currentSpeed;
            }
        }

        disable() {
            this.enabled = false;
            this.setSpeed(1.0);
            if (this.ui) {
                this.ui.style.display = 'none';
            }
        }

        initUI() {
            const container = document.createElement('div');
            container.id = 'riemann-video-overlay';
            Object.assign(container.style, {
                position: 'fixed', bottom: '20px', left: '20px', width: '280px',
                background: 'rgba(15, 20, 25, 0.98)', color: '#eee',
                padding: '15px', borderRadius: '12px', zIndex: '2147483647',
                fontFamily: 'Segoe UI, monospace', border: '1px solid #00E5FF',
                boxShadow: '0 0 20px rgba(0, 229, 255, 0.2)',
                display: 'none', flexDirection: 'column', gap: '10px'
            });

            const header = document.createElement('div');
            header.style.display = 'flex';
            header.style.justifyContent = 'space-between';
            header.style.alignItems = 'center';

            const leftSide = document.createElement('div');
            const icon = document.createElement('span');
            icon.textContent = '▶ ';
            icon.style.color = '#00E5FF';
            const title = document.createElement('span');
            title.textContent = 'VIDEO ENGINE';
            title.style.fontWeight = 'bold';
            title.style.fontSize = '14px';
            leftSide.appendChild(icon);
            leftSide.appendChild(title);
            header.appendChild(leftSide);

            const closeBtn = document.createElement('span');
            closeBtn.textContent = '✕';
            closeBtn.style.cursor = 'pointer';
            closeBtn.onclick = () => this.disable();
            header.appendChild(closeBtn);
            container.appendChild(header);

            let isDragging = false, startX, startY, initialX, initialY;
            header.style.cursor = 'move';
            header.addEventListener('mousedown', (e) => {
                isDragging = true;
                startX = e.clientX;
                startY = e.clientY;
                const rect = container.getBoundingClientRect();
                initialX = rect.left;
                initialY = rect.top;
                container.style.right = 'auto';
                container.style.bottom = 'auto';
                container.style.left = `${initialX}px`;
                container.style.top = `${initialY}px`;
            });
            document.addEventListener('mousemove', (e) => {
                if (!isDragging) return;
                container.style.left = `${initialX + (e.clientX - startX)}px`;
                container.style.top = `${initialY + (e.clientY - startY)}px`;
            });
            document.addEventListener('mouseup', () => { isDragging = false; });

            const presetGrid = document.createElement('div');
            Object.assign(presetGrid.style, {
                display: 'grid',
                gridTemplateColumns: 'repeat(5, 1fr)',
                gap: '4px',
                marginTop: '5px'
            });

            this.presets.forEach(speed => {
                const btn = document.createElement('button');
                btn.textContent = speed + 'x';
                Object.assign(btn.style, {
                    background: '#222', border: '1px solid #444', color: '#fff',
                    padding: '4px 2px', borderRadius: '4px', fontSize: '10px',
                    cursor: 'pointer', transition: 'background 0.2s'
                });
                btn.onmouseover = () => btn.style.background = '#333';
                btn.onmouseout = () => btn.style.background = '#222';
                btn.onclick = () => this.setSpeed(speed);
                presetGrid.appendChild(btn);
            });
            container.appendChild(presetGrid);

            const sliderRow = document.createElement('div');
            sliderRow.style.display = 'flex';
            sliderRow.style.alignItems = 'center';
            sliderRow.style.marginTop = '10px';
            sliderRow.style.gap = '10px';

            const input = document.createElement('input');
            input.id = 'riemann-video-slider';
            input.type = 'range';
            input.min = '0.125';
            input.max = '16';
            input.step = '0.125';
            input.value = this.currentSpeed;
            input.style.flex = '1';
            input.style.accentColor = '#00E5FF';
            input.oninput = (e) => this.setSpeed(parseFloat(e.target.value));

            const display = document.createElement('div');
            display.id = 'riemann-video-display';
            display.textContent = this.currentSpeed.toFixed(2) + 'x';
            display.style.width = '45px';
            display.style.textAlign = 'right';
            display.style.fontSize = '12px';
            display.style.fontWeight = 'bold';
            display.style.color = '#00E5FF';

            sliderRow.appendChild(input);
            sliderRow.appendChild(display);
            container.appendChild(sliderRow);

            document.body.appendChild(container);
            this.ui = container;
        }
    }

    window.RiemannVideo = new RiemannVideo();
})();