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
 * Shows a shortcut dialog modal for conveniently saving and editing shortcuts
 *
 * @param {Array<Object>} links - Array of link objects containing name and url properties.
 * @param {Number} editIndex - Position at which to edit the shortcut 
 */
function showShortcutModal(links, editIndex = -1) {
    const isEdit = editIndex >= 0;
    const modal = document.createElement('div');
    modal.style.position = 'fixed';
    modal.style.top = '0'; modal.style.left = '0'; modal.style.width = '100%'; modal.style.height = '100%';
    modal.style.backgroundColor = 'rgba(0,0,0,0.6)';
    modal.style.display = 'flex';
    modal.style.alignItems = 'center';
    modal.style.justifyContent = 'center';
    modal.style.zIndex = '2000';
    modal.style.backdropFilter = 'blur(4px)';

    const existingName = isEdit ? links[editIndex].name : '';
    const existingUrl = isEdit ? links[editIndex].url : '';

    modal.innerHTML = `
        <div style="background: #1e1e23; padding: 25px; border-radius: 12px; border: 1px solid #444; width: 320px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); font-family: 'Segoe UI', system-ui, sans-serif;">
            <h3 style="margin-top: 0; color: #eee; font-weight: 500;">${isEdit ? 'Edit' : 'Add'} Website Shortcut</h3>
            <input id="siteName" placeholder="Website Name" value="${existingName}" style="width: 100%; box-sizing: border-box; margin-bottom: 15px; background: #111; color: white; border: 1px solid #555; padding: 12px; border-radius: 6px; outline: none; font-size: 14px;" />
            <input id="siteUrl" placeholder="URL (e.g., https://example.com)" value="${existingUrl}" style="width: 100%; box-sizing: border-box; margin-bottom: 25px; background: #111; color: white; border: 1px solid #555; padding: 12px; border-radius: 6px; outline: none; font-size: 14px;" />
            <div style="display: flex; justify-content: flex-end; gap: 10px;">
                <button id="cancelBtn" style="background: transparent; border: 1px solid #666; color: #ccc; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 14px; transition: 0.2s;">Cancel</button>
                <button id="saveBtn" style="background: #ff4500; border: none; color: white; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; transition: 0.2s;">Save</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    const nameInput = document.getElementById('siteName');
    nameInput.focus();

    document.getElementById('cancelBtn').onclick = () => modal.remove();
    document.getElementById('cancelBtn').onmouseover = function () { this.style.background = '#333'; };
    document.getElementById('cancelBtn').onmouseout = function () { this.style.background = 'transparent'; };

    document.getElementById('saveBtn').onmouseover = function () { this.style.filter = 'brightness(1.1)'; };
    document.getElementById('saveBtn').onmouseout = function () { this.style.filter = 'none'; };
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
            ? `<img src="https://www.google.com/s2/favicons?domain=${hostname}&sz=64" class="icon-img" alt="icon" onerror="this.outerHTML='<div class=\\'icon\\'>🌐</div>'">`
            : `<div class="icon">🌐</div>`;

        a.innerHTML = `${iconHtml}<div class="title">${link.name}</div>`;

        a.oncontextmenu = (e) => {
            e.preventDefault();
            document.querySelectorAll('.ctx-menu').forEach(el => el.remove());

            const ctxMenu = document.createElement('div');
            ctxMenu.className = 'ctx-menu';
            ctxMenu.style.position = 'absolute';
            ctxMenu.style.left = e.pageX + 'px';
            ctxMenu.style.top = e.pageY + 'px';
            ctxMenu.style.background = '#28282d';
            ctxMenu.style.border = '1px solid #444';
            ctxMenu.style.borderRadius = '8px';
            ctxMenu.style.padding = '8px 0';
            ctxMenu.style.zIndex = '1000';
            ctxMenu.style.boxShadow = '0 5px 15px rgba(0,0,0,0.5)';
            ctxMenu.style.minWidth = '120px';

            const createItem = (text, color, onClick) => {
                const item = document.createElement('div');
                item.innerText = text;
                item.style.padding = '10px 20px';
                item.style.cursor = 'pointer';
                item.style.color = color;
                item.style.fontSize = '14px';
                item.style.fontFamily = "'Segoe UI', system-ui, sans-serif";
                item.onmouseover = () => item.style.background = '#3a3a40';
                item.onmouseout = () => item.style.background = 'transparent';
                item.onclick = onClick;
                return item;
            };

            ctxMenu.appendChild(createItem('Edit', '#fff', (ev) => {
                ev.stopPropagation();
                ctxMenu.remove();
                showShortcutModal(links, index);
            }));

            ctxMenu.appendChild(createItem('Delete', '#ff4444', (ev) => {
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