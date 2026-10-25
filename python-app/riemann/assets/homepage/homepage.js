const defaultLinks = [
    { name: 'Google', url: 'https://google.com' },
    { name: 'YouTube', url: 'https://youtube.com' },
    { name: 'YT Music', url: 'https://music.youtube.com' }
];

/**
 * Advanced fallback sequence for offline/missing favicons:
 * 1. Native website's /favicon.ico (If Google's API drops)
 * 2. Riemann's injected Local Icon Cache (If completely offline)
 * 3. Default Riemann Globe Icon (If all else fails)
 */
window.handleIconError = function (img, hostname) {
    if (!img.dataset.stage) {
        img.dataset.stage = '1';
        img.src = 'https://' + hostname + '/favicon.ico';
    } else if (img.dataset.stage === '1') {
        img.dataset.stage = '2';
        if (window.cachedIcons && window.cachedIcons[hostname]) {
            img.src = window.cachedIcons[hostname];
        } else {
            img.outerHTML = '<div class="icon">🌐</div>';
        }
    } else {
        img.outerHTML = '<div class="icon">🌐</div>';
    }
};

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
 * Shows a shortcut dialog modal for conveniently saving and editing shortcuts
 *
 * @param {Array<Object>} links - Array of link objects containing name and url properties.
 * @param {Number} editIndex - Position at which to edit the shortcut 
 */
function showShortcutModal(links, editIndex = -1) {
    const isEdit = editIndex >= 0;
    const modal = document.createElement('div');
    modal.className = 'modal-backdrop';

    const existingName = isEdit ? links[editIndex].name : '';
    const existingUrl = isEdit ? links[editIndex].url : '';

    modal.innerHTML = `
        <div class="modal">
            <h3>${isEdit ? 'Edit' : 'Add'} Website Shortcut</h3>
            <input id="siteName" placeholder="Website Name" value="${existingName}" />
            <input id="siteUrl" placeholder="URL (e.g., https://example.com)" value="${existingUrl}" />
            <div class="modal-actions">
                <button id="cancelBtn" class="btn btn-secondary">Cancel</button>
                <button id="saveBtn" class="btn btn-primary">Save</button>
            </div>
        </div>
    `;
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.remove();
    });
    document.body.appendChild(modal);

    const nameInput = document.getElementById('siteName');
    nameInput.focus();

    document.getElementById('cancelBtn').onclick = () => modal.remove();
    document.getElementById('saveBtn').onclick = () => {
        const name = nameInput.value.trim();
        let url = document.getElementById('siteUrl').value.trim();
        if (!name || !url) {
            return;
        }
        if (!url.startsWith('http')) url = 'https://' + url;

        if (isEdit) {
            links[editIndex] = { name, url };
        } else {
            links.push({ name, url });
        }
        saveLinks(links);
        renderGrid(window.currentLinks);
        modal.remove();
    };

    modal.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') document.getElementById('saveBtn').click();
    });
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
            ? `<img src="https://www.google.com/s2/favicons?domain=${encodeURIComponent(link.url)}&sz=64" class="icon-img" alt="icon" onerror="window.handleIconError(this, '${hostname}')">`
            : `<div class="icon">🌐</div>`;

        a.innerHTML = `${iconHtml}<div class="title">${link.name}</div>`;

        a.oncontextmenu = (e) => {
            e.preventDefault();
            document.querySelectorAll('.ctx-menu').forEach(el => el.remove());

            const ctxMenu = document.createElement('div');
            ctxMenu.className = 'ctx-menu';
            ctxMenu.style.left = e.pageX + 'px';
            ctxMenu.style.top = e.pageY + 'px';

            const createItem = (text, danger, onClick) => {
                const item = document.createElement('div');
                item.className = danger ? 'ctx-item danger' : 'ctx-item';
                item.innerText = text;
                item.onclick = onClick;
                return item;
            };

            ctxMenu.appendChild(createItem('Edit', false, (ev) => {
                ev.stopPropagation();
                ctxMenu.remove();
                showShortcutModal(links, index);
            }));

            ctxMenu.appendChild(createItem('Delete', true, (ev) => {
                ev.stopPropagation();
                ctxMenu.remove();
                if (confirm(`Remove ${link.name}?`)) {
                    links.splice(index, 1);
                    saveLinks(links);
                    renderGrid(window.currentLinks);
                }
            }));

            document.body.appendChild(ctxMenu);

            setTimeout(() => {
                document.addEventListener('click', function closeCtx() {
                    ctxMenu.remove();
                    document.removeEventListener('click', closeCtx);
                });
            }, 10);
        };
        grid.appendChild(a);
    });

    const addBtn = document.createElement('div');
    addBtn.className = 'card add-btn';
    addBtn.innerHTML = `<div class="icon">+</div><div class="title">Add Link</div>`;
    addBtn.onclick = () => showShortcutModal(links, -1);
    grid.appendChild(addBtn);
}

/**
 * Switches the page between the light and dark palettes.
 *
 * @param {string} theme - 'light' or 'dark'.
 */
window.setTheme = function (theme) {
    document.documentElement.dataset.theme = theme === 'light' ? 'light' : 'dark';
};

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