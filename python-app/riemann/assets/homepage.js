const defaultLinks = [
    { name: 'Google', url: 'https://google.com' },
    { name: 'YouTube', url: 'https://youtube.com' },
    { name: 'YT Music', url: 'https://music.youtube.com' }
];

/**
 * Serializes and saves the user's quick links to the host application via a custom URL scheme.
 *
 * @param {Array<Object>} links - Array of link objects containing name and url properties.
 */
function saveLinks(links) {
    const payload = encodeURIComponent(JSON.stringify(links));
    let iframe = document.getElementById('saveFrame');
    if (!iframe) {
        iframe = document.createElement('iframe');
        iframe.id = 'saveFrame';
        iframe.style.display = 'none';
        document.body.appendChild(iframe);
    }
    iframe.src = "https://riemann-save.local/?data=" + payload;
}

/**
 * Renders the grid of quick links in the DOM and binds interaction events.
 *
 * @param {Array<Object>} links - Array of link objects to render into the grid interface.
 */
function renderGrid(links) {
    const grid = document.getElementById('linksGrid');
    grid.innerHTML = '';

    links.forEach((link, index) => {
        const a = document.createElement('a');
        a.className = 'card';
        a.href = link.url;

        let hostname = "";
        try {
            hostname = new URL(link.url).hostname;
        } catch (e) {
            hostname = link.url;
        }

        const iconHtml = hostname
            ? `<img src="https://www.google.com/s2/favicons?domain=${hostname}&sz=64" class="icon-img" alt="icon" onerror="this.outerHTML='<div class=\\'icon\\'>🌐</div>'">`
            : `<div class="icon">🌐</div>`;

        a.innerHTML = `${iconHtml}<div class="title">${link.name}</div>`;

        a.oncontextmenu = (e) => {
            e.preventDefault();
            if (confirm(`Remove ${link.name}?`)) {
                links.splice(index, 1);
                saveLinks(links);
                renderGrid(window.currentLinks);
            }
        };
        grid.appendChild(a);
    });

    const addBtn = document.createElement('div');
    addBtn.className = 'card add-btn';
    addBtn.innerHTML = `<div class="icon">+</div><div class="title">Add Link</div>`;
    addBtn.onclick = () => {
        const name = prompt('Website Name:'); if (!name) return;
        let url = prompt('URL (e.g., https://example.com):'); if (!url) return;
        if (!url.startsWith('http')) url = 'https://' + url;

        links.push({ name: name, url: url });
        saveLinks(links);
        renderGrid(window.currentLinks);
    };
    grid.appendChild(addBtn);
}

/**
 * Initializes the homepage view, configuring the display greeting and bootstrapping the link dataset.
 *
 * @param {string} userName - The display name of the active user.
 * @param {Array<Object>} savedLinks - The collection of saved user links to restore, or empty if using defaults.
 */
window.initHomepage = function (userName, savedLinks) {
    document.getElementById('greeting').innerText = "Hi " + userName + "!";
    let links = savedLinks;
    if (!links || links.length === 0) {
        links = defaultLinks;
        saveLinks(links);
    }
    window.currentLinks = links;
    renderGrid(window.currentLinks);
};

document.getElementById('searchInput').addEventListener('keypress', function (e) {
    if (e.key === 'Enter') {
        const q = this.value.trim();
        if (q.startsWith('http') || (q.includes('.') && !q.includes(' '))) {
            window.location.href = q.startsWith('http') ? q : 'https://' + q;
        } else {
            window.location.href = 'https://www.google.com/search?q=' + encodeURIComponent(q);
        }
    }
});