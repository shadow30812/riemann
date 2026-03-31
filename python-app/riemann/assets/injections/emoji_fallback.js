(function () {
    if (document.getElementById('riemann-emoji-fallback')) return;

    var css = `
        @font-face {
        font-family: "Riemann Noto Emoji";
        src: url("{{FONT_URI}}") format("truetype");
        unicode-range: U+1F300-1F9FF, U+2600-26FF, U+2700-27BF;
        }
    `;
    var style = document.createElement('style');
    style.id = 'riemann-emoji-fallback';
    style.innerHTML = css;
    document.documentElement.appendChild(style);
})();