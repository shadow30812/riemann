(function () {
    if (document.getElementById('riemann-emoji-fallback')) return;

    var css = `
        @font-face {
            font-family: "Riemann Noto Emoji";
            src: url("{{FONT_URI}}") format("truetype");
        }
        body, p, span, div, h1, h2, h3, h4, h5, h6, input, textarea { 
            font-family: inherit, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Riemann Noto Emoji", "Twemoji Mozilla" !important; 
        }
    `;
    var style = document.createElement('style');
    style.id = 'riemann-emoji-fallback';
    style.innerHTML = css;
    document.documentElement.appendChild(style);
})();