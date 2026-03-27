(function () {
    var existing = document.getElementById('riemann-dark');
    if (existing) existing.remove();
    function getBrightness(elem) {
        var style = window.getComputedStyle(elem);
        var color = style.backgroundColor;
        if (color === 'rgba(0, 0, 0, 0)' || color === 'transparent') return 255;
        var rgb = color.match(/\d+/g);
        if (!rgb) return 255;
        var r = parseInt(rgb[0]), g = parseInt(rgb[1]), b = parseInt(rgb[2]);
        return (0.299 * r + 0.587 * g + 0.114 * b);
    }
    var bodyB = getBrightness(document.body);
    var htmlB = getBrightness(document.documentElement);
    if (!(bodyB < 140 || htmlB < 140)) {
        var css = `html { filter: invert(1) hue-rotate(180deg) !important; }
                   img, video, iframe, canvas, :fullscreen { filter: invert(1) hue-rotate(180deg) !important; }`;
        var style = document.createElement('style');
        style.id = 'riemann-dark';
        style.innerHTML = css;
        document.head.appendChild(style);
    }
})();