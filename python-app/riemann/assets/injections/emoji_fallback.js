(function () {
    if (document.getElementById('riemann-emoji-fallback')) return;

    const css = `
        const style = document.createElement('style');
        style.textContent = \`
            @font-face {
                font-family: "Riemann Noto Emoji";
                src: url("{{FONT_URI}}") format("truetype");
            }
            p, span:not(.yt-icon):not(yt-icon), div:not(#icon), h1, h2, h3, h4, h5, h6 { 
                font-family: inherit, "Riemann Noto Emoji", sans-serif; 
            }
        \`;
        document.head.appendChild(style);
            `;
    var style = document.createElement('style');
    style.id = 'riemann-emoji-fallback';
    style.innerHTML = css;
    document.documentElement.appendChild(style);
})();