/**
 * @jest-environment jsdom
 */

const fs = require('fs');
const path = require('path');

describe('Homepage UI Interactions', () => {
    beforeEach(() => {
        document.body.innerHTML = `
            <h1 id="greeting"></h1>
            <div id="linksGrid"></div>
            <input type="text" id="searchInput" />
        `;

        window.confirm = jest.fn();
        window.__mockLocationHref = '';

        let scriptCode = fs.readFileSync(path.resolve(__dirname, '../homepage.js'), 'utf8');
        scriptCode = scriptCode.replace(/window\.location\.href/g, 'window.__mockLocationHref');
        scriptCode = scriptCode.replace(/\.innerText/g, '.textContent');
        scriptCode += '\nwindow.saveLinks = saveLinks;\nwindow.renderGrid = renderGrid;\nwindow.showShortcutModal = showShortcutModal;';

        eval(scriptCode);
    });

    afterEach(() => {
        document.body.innerHTML = '';
        jest.restoreAllMocks();
    });

    test('initHomepage uses savedLinks and sets greeting', () => {
        const mockLinks = [{ name: 'TestSite', url: 'https://test.com' }];
        window.initHomepage('User123', mockLinks);

        expect(document.getElementById('greeting').textContent).toBe('Hi User123!');
        expect(window.currentLinks).toEqual(mockLinks);

        const cards = document.querySelectorAll('.card');
        expect(cards.length).toBe(2);
        expect(cards[0].querySelector('.title').textContent).toBe('TestSite');
    });

    test('initHomepage falls back to default links if empty', () => {
        window.initHomepage('Guest', []);

        expect(window.currentLinks.length).toBe(3);
        expect(window.currentLinks[0].name).toBe('Google');
    });

    test('saveLinks creates iframe with correct custom protocol scheme', () => {
        const testLinks = [{ name: 'MySite', url: 'https://mysite.com' }];

        window.saveLinks(testLinks);

        const iframe = document.getElementById('saveFrame');
        expect(iframe).not.toBeNull();
        expect(iframe.style.display).toBe('none');

        const encodedPayload = encodeURIComponent(JSON.stringify(testLinks));
        expect(iframe.src).toBe(`https://riemann-save.local/?data=${encodedPayload}`);
    });

    test('Add Link button opens custom modal and updates grid', () => {
        window.initHomepage('User', []);

        const addBtn = document.querySelector('.add-btn');
        addBtn.click();

        const siteNameInput = document.getElementById('siteName');
        const siteUrlInput = document.getElementById('siteUrl');
        const saveBtn = document.getElementById('saveBtn');

        expect(siteNameInput).not.toBeNull();
        expect(siteUrlInput).not.toBeNull();

        siteNameInput.value = 'New Site';
        siteUrlInput.value = 'newsite.com';

        saveBtn.click();

        expect(window.currentLinks.length).toBe(4);
        expect(window.currentLinks[3].name).toBe('New Site');
        expect(window.currentLinks[3].url).toBe('https://newsite.com');
    });

    test('Search input Enter key redirects correctly', () => {
        const searchInput = document.getElementById('searchInput');

        searchInput.value = 'example.com';
        searchInput.dispatchEvent(new window.KeyboardEvent('keypress', { key: 'Enter' }));
        expect(window.__mockLocationHref).toBe('https://example.com');

        searchInput.value = 'hello world';
        searchInput.dispatchEvent(new window.KeyboardEvent('keypress', { key: 'Enter' }));
        expect(window.__mockLocationHref).toBe('https://www.google.com/search?q=hello%20world');
    });
});