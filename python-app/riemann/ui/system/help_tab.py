"""
The Help page (F1, or Help in the menu bar): what Riemann can do, in plain
words for someone opening it for the first time, and every keyboard shortcut.

TOPICS holds the text: one section per topic, each a list of cards, each card
a title and its paragraphs. SHORTCUTS holds the key tables, one card per
place the keys work. A row is (keys, what they do); keys lists alternatives
for the same action, written as QKeySequence text ("Ctrl+K, K" is shown as
"Ctrl+K then K") or, for keys that are not one sequence, in words
("Shift+arrow keys"). tests/test_help.py fails when a QShortcut that the
window, a reader, an image or table tab, a browser tab or the AI panel registers is missing here, so
a new shortcut has to be described before it ships. Keys a widget reads in
its keyPressEvent (A, W, H, the presentation keys) can't be listed that way
and are kept in step by hand.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QWidget

from ..palette import tokens
from .base import SectionedSystemTab, card

TOPICS = [
    (
        "start",
        "Getting started",
        "play",
        [
            (
                "What Riemann is",
                [
                    "Riemann is a document reader and a web browser in one window. PDFs, web pages, Office "
                    "files, pictures, EPUB books, CSV tables, Markdown, text files and source code each "
                    "open in a tab of their own.",
                ],
            ),
            (
                "Opening things",
                [
                    "Press Ctrl+O to open a file, or drag files onto the window. A new tab (Ctrl+T) shows "
                    "a start page with your recent files and a box for typing a file's location.",
                    "Ctrl+B opens a browser tab. Type a web address, or a few words to search the web.",
                    "On Linux, \"Open with Riemann\" also appears in your file manager for the files Riemann "
                    "opens, and for folders.",
                ],
            ),
            (
                "Tabs and windows",
                [
                    "Ctrl+W closes a tab, and Ctrl+Shift+T brings back the last one you closed. "
                    "Ctrl+Tab moves to the next tab.",
                    "To see two tabs side by side, press Ctrl+\\ or right-click a tab and choose "
                    "\"Move to Other Pane\". Drag a tab out of the window to give it a window of its own.",
                    "Ctrl+N opens a new window.",
                ],
            ),
            (
                "Finding a command",
                [
                    "Press Ctrl+Shift+P and type a few letters of what you want, such as \"rotate\" or "
                    "\"bookmarks\". This command palette runs any menu command or toolbar button.",
                    "Every key is listed under Keyboard shortcuts, the last topic on the left.",
                ],
            ),
            (
                "Riemann remembers where you were",
                [
                    "Your open tabs, split panes and the page you were on in each document are saved as "
                    "you go. If Riemann closes by accident, it offers to bring them back the next time it "
                    "starts. File > Restore Last Opened Tabs does the same at any time.",
                    "History (Ctrl+H) lists the web pages, documents and folders you opened.",
                ],
            ),
        ],
    ),
    (
        "reading",
        "Reading documents",
        "book-open",
        [
            (
                "Moving around",
                [
                    "Scroll with the mouse or the arrow keys. Page Up and Page Down move a page at a "
                    "time, and Home and End jump to the start and the end.",
                    "Click a link in the document to jump to it, and press Alt+Left to go back. Rest the "
                    "pointer on a citation to see the reference it points to.",
                    "Ctrl+Alt+S opens a sidebar with the document's sections and page thumbnails. Click "
                    "one to jump there.",
                ],
            ),
            (
                "Size and layout",
                [
                    "Ctrl+= and Ctrl+- zoom in and out, or hold Ctrl and turn the mouse wheel. A fits the "
                    "whole page in the window, W fits its width and H its height.",
                    "C switches between continuous scrolling and one page at a time. D shows two pages "
                    "side by side, like an open book. R shows the text reflowed to fit the window, "
                    "without the page layout.",
                    "F or F11 goes full screen. Press it again for a full screen without toolbars, and "
                    "once more to come back.",
                ],
            ),
            (
                "Colours",
                [
                    "N changes the page colours: light, dark, and smart dark, which keeps pictures and "
                    "charts in their own colours.",
                    "Ctrl+D switches the rest of Riemann between light and dark.",
                ],
            ),
            (
                "Finding and copying text",
                [
                    "Ctrl+F finds words in the document. Ctrl+I finds passages by meaning, even when they "
                    "use other words. It runs on your computer and sends nothing away.",
                    "Double-click a word to select it, triple-click to select a line, or drag across the "
                    "text. Ctrl+C copies what you selected.",
                ],
            ),
            (
                "Other ways to read",
                [
                    "Read aloud (Ctrl+Shift+U) speaks the document with your computer's voice and "
                    "highlights each sentence as it goes. Esc stops it.",
                    "Presentation mode (Shift+F5) shows one page at a time on black. L turns the pointer "
                    "into a laser dot, B and W blank the screen, and Esc leaves.",
                    "Notes (Ctrl+Alt+N) opens a notes panel for the document. The notes are kept in a "
                    "Markdown file next to the document's file.",
                ],
            ),
        ],
    ),
    (
        "editing",
        "Marking up PDFs",
        "highlighter",
        [
            (
                "Highlights, notes and drawings",
                [
                    "Ctrl+Shift+A shows the annotation tools: highlight, underline and strike out text, "
                    "sticky notes, text labels, a pen, shapes, and tick and cross stamps. Ctrl+Z undoes "
                    "and Ctrl+Shift+Z redoes.",
                    "They are saved inside the PDF as standard annotations, so other PDF readers show "
                    "them too.",
                ],
            ),
            (
                "Forms",
                ["Click a form field to fill it in. What you type is saved into the file."],
            ),
            (
                "Pages",
                [
                    "The Pages menu, or the page thumbnails in the sidebar, rotates, deletes, moves and "
                    "extracts pages, and inserts another PDF.",
                    "These changes wait in a private copy until you save. Closing the tab or quitting "
                    "asks whether to save them, and Discard puts everything back.",
                ],
            ),
            (
                "Hiding text for good",
                [
                    "Select text, right-click it and choose \"Mark for Redaction\", or drag a box with the "
                    "Redact tool. Then press \"Apply redactions\".",
                    "Riemann removes what is under each mark from the file itself, not only from view. It "
                    "checks the result first. If a page can't be redacted exactly, it changes nothing "
                    "and tells you which page.",
                    "To write new words over old ones, select them and choose \"Replace Text…\". The new "
                    "text gets a similar font, size and colour.",
                ],
            ),
            (
                "Saving, passwords and signatures",
                [
                    "Ctrl+S saves a copy with your annotations. Ctrl+Shift+S saves a copy that needs a "
                    "password to open, or removes the password from a file that has one.",
                    "A signed PDF shows a banner when it opens, and the signature panel says whether the "
                    "signature is valid. Riemann asks before you change a signed file, because changes "
                    "break its signatures.",
                ],
            ),
        ],
    ),
    (
        "text",
        "Text and code",
        "file-text",
        [
            (
                "Text files",
                [
                    "Markdown files open formatted, with maths typeset. Text files, logs and source code "
                    "open in the same kind of tab. Code is shown in colour when the optional Pygments "
                    "component is installed.",
                    "HTML files open in the browser. Files Riemann can't show are refused with a message.",
                ],
            ),
        ],
    ),
    (
        "office",
        "Office files",
        "file-text",
        [
            (
                "Word, PowerPoint, Excel and OpenDocument",
                [
                    "When LibreOffice is installed, Riemann uses it to show Word, PowerPoint, OpenDocument "
                    "and RTF files as pages, the way they print. The first time takes a few seconds; after "
                    "that the file opens straight away.",
                    "These tabs are read-only, and your file is never changed. To mark one up, use Save as "
                    "PDF (Ctrl+S): the PDF opens in the tab, and highlights and notes work there.",
                    "To change the file itself, click Edit in LibreOffice above the pages. When you save "
                    "it there, the tab updates on its own and stays on the same page.",
                    "Without LibreOffice, Riemann offers to install it, with your system's packages, "
                    "Flatpak or winget. It shows the commands first and runs nothing until you click "
                    "Install. Settings > Documents can install it later too.",
                    "Riemann's copy of LibreOffice runs no macros and loads no pictures from the web, so "
                    "opening a file never tells its sender. Settings > Documents shows where LibreOffice "
                    "was found, and Settings > Privacy deletes the converted copies.",
                ],
            ),
            (
                "Without LibreOffice",
                [
                    "Riemann can still show Word (.docx), PowerPoint (.pptx), OpenDocument and RTF files in "
                    "a basic view: the text, headings, tables and pictures, without the page layout. A note "
                    "at the top says so, and the sidebar lists the headings or slides.",
                    "When Riemann asks to install LibreOffice, \"Use the basic view\" opens the file this "
                    "way. After that, files of the same kind open in the basic view without asking.",
                    "Older formats (.doc, .ppt) and drawings need LibreOffice.",
                ],
            ),
            (
                "Spreadsheets",
                [
                    "Excel and OpenDocument spreadsheets open as a table, with a tab for each sheet along "
                    "the bottom. Click a cell to see its formula in the line under the table.",
                    "With LibreOffice, the formulas are worked out again first, so files saved by other "
                    "programs show their results. \"Show as pages\" opens the workbook as it prints, "
                    "charts and pictures included.",
                    "Spreadsheets are read-only here. Open with in the toolbar opens one in Excel or "
                    "LibreOffice to change it.",
                ],
            ),
        ],
    ),
    (
        "ebooks",
        "E-books",
        "book-open",
        [
            (
                "EPUB books",
                [
                    "EPUB books open in a reader tab as one long page, in the same colours as Markdown. "
                    "N switches between light and dark pages, and the zoom works as it does for text.",
                    "The sidebar lists the book's chapters: click one to go there. Links inside the book "
                    "work too.",
                    "Riemann leaves out the book's own fonts, styles and scripts, and never loads "
                    "pictures from the web. Very large pictures are left out with a note at the top.",
                    "A book that takes more than two minutes to read, or can't be read at all, is "
                    "refused with a message.",
                ],
            ),
        ],
    ),
    (
        "tables",
        "Tables",
        "list-tree",
        [
            (
                "CSV and TSV files",
                [
                    "CSV and TSV files open as a table, with the first row kept at the top when it holds "
                    "column names. Riemann works out whether the values are separated by commas, "
                    "semicolons or tabs, and which text encoding the file uses.",
                    "A large file opens straight away: more rows are read as you scroll down.",
                    "\"Open as text\" in the toolbar shows the file as plain text instead.",
                ],
            ),
            (
                "Sorting, finding and copying",
                [
                    "Click a column's heading to sort by it, again to reverse the order, and a third time "
                    "to put the rows back as they are in the file. Numbers sort by value, and empty "
                    "cells go last.",
                    "Ctrl+F finds text in the table. Return goes to the next match and Shift+Return to "
                    "the one before.",
                    "Select cells and press Ctrl+C to copy them. They paste into a spreadsheet as cells.",
                ],
            ),
            (
                "Changing a CSV or TSV file",
                [
                    "Double-click a cell, or press F2, to change it, and Return to keep the change. Delete "
                    "empties the selected cells, and Ctrl+Z undoes the last change.",
                    "Ctrl+S (or Save in the toolbar) saves the changes. Riemann asks first: Save replaces "
                    "the file, and Save As writes a new one. The file keeps its separators and text "
                    "encoding, and a sorted table is saved in the file's own order.",
                    "Closing the tab with changes that aren't saved asks whether to save them. Previews "
                    "and files over 32 MB can't be changed, and the row of column names stays as it is.",
                ],
            ),
        ],
    ),
    (
        "images",
        "Pictures",
        "image",
        [
            (
                "Looking at a picture",
                [
                    "Pictures open in a tab of their own: PNG, JPEG, GIF, WebP, AVIF, TIFF, SVG, "
                    "Photoshop files and many older formats. Photos taken on their side are turned the "
                    "right way up.",
                    "A fits the whole picture, W its width and H its height. Ctrl+= and Ctrl+- zoom, or "
                    "hold Ctrl and turn the mouse wheel. Drag to move around, and double-click to switch "
                    "between fitting and actual size.",
                    "Left and Right show the previous or next picture in the same folder. A TIFF with "
                    "several pages has Page Up and Page Down for its pages, and Space pauses a moving GIF.",
                ],
            ),
            (
                "Turning, copying and saving",
                [
                    "Ctrl+R turns the view clockwise and Ctrl+Shift+R the other way. The file itself is "
                    "never changed.",
                    "N changes the background: dark, light, or a checkerboard that shows the see-through "
                    "parts of a picture.",
                    "Ctrl+C copies the picture. \"Save as PDF\" in the toolbar makes a PDF of it, one page "
                    "per page of the picture.",
                ],
            ),
        ],
    ),
    (
        "web",
        "Browsing the web",
        "browser",
        [
            (
                "Pages and searching",
                [
                    "Ctrl+B opens a browser tab. The address bar takes a web address or words to search "
                    "for. Settings > Appearance chooses the search engine.",
                    "F5 reloads the page, Ctrl+F finds words on it, and Ctrl+= and Ctrl+- zoom.",
                ],
            ),
            (
                "Bookmarks, history and downloads",
                [
                    "The bookmark button in the address bar saves the page. Ctrl+K then K lists your "
                    "bookmarks, Ctrl+H your history and Ctrl+J your downloads.",
                    "A PDF, e-book or Office file you download opens in a tab when it finishes. Turn that off "
                    "in Settings, under Downloads.",
                ],
            ),
            (
                "Videos and music",
                [
                    "The download button on the toolbar saves the video on the page. Ctrl+Shift+J lists "
                    "the videos you downloaded.",
                    "Ctrl+G shows a control for the video's speed, and Ctrl+M switches music mode on "
                    "and off.",
                    "The captions button shows live captions in English. It needs the optional "
                    "faster-whisper component.",
                ],
            ),
            (
                "Privacy",
                [
                    "Sites ask before they use your camera, microphone, location or notifications, and "
                    "pop-ups are blocked. Settings > Site permissions changes your answers.",
                    "A page with changes you haven't saved, such as a half-written message, asks before "
                    "you close it or quit.",
                    "Ctrl+Shift+N opens an incognito window. Nothing you open there goes into your "
                    "history, and its tabs are not saved.",
                ],
            ),
        ],
    ),
    (
        "folders",
        "Folders and your library",
        "folder",
        [
            (
                "Folders",
                [
                    "Ctrl+K then Ctrl+O opens a folder as a file tree on the left. Click a file to preview "
                    "it in a temporary tab, and double-click it to open it for good.",
                    "Ctrl+Shift+F hides or shows the tree, and Ctrl+K then F closes the folder. "
                    "Workspaces (Ctrl+Shift+E) lists your favourite and recent folders.",
                ],
            ),
            (
                "Library",
                [
                    "Library (Ctrl+L) searches every paper you've opened, by words, by meaning, or by "
                    "details such as author:Smith year:2024.",
                ],
            ),
        ],
    ),
    (
        "ai",
        "AI assistant",
        "sparkles",
        [
            (
                "Asking about a document",
                [
                    "Ctrl+Alt+A opens a panel where you can ask questions about the open PDF, or an Office file "
                    "shown as pages. Answers "
                    "name the pages they come from, and clicking a page number jumps there.",
                    "It needs an account with an AI provider (Claude, OpenAI, Gemini or OpenRouter) and "
                    "its API key, which you add with the key button in the panel. A local Ollama server "
                    "works too and keeps everything on your computer.",
                    "The document's text is sent to the provider you pick. For long files, that is the "
                    "pages near the one you are reading.",
                ],
            ),
        ],
    ),
]

SHORTCUTS = [
    (
        "Everywhere",
        [
            (("Ctrl+O",), "Open files"),
            (("Ctrl+T",), "New tab with the start page"),
            (("Ctrl+B",), "New browser tab"),
            (("Ctrl+W",), "Close the tab"),
            (("Ctrl+Shift+T",), "Reopen the last tab you closed"),
            (("Ctrl+Tab",), "Next tab"),
            (("Ctrl+Shift+Tab",), "Previous tab"),
            (("Ctrl+\\",), "Two tabs side by side, or back to one"),
            (("Ctrl+N",), "New window"),
            (("Ctrl+Shift+N",), "New incognito window"),
            (("Ctrl+Shift+P",), "Command palette: run any command by name"),
            (("F1",), "This help page"),
            (("Ctrl+H",), "History"),
            (("Ctrl+K, K",), "Bookmarks"),
            (("Ctrl+J",), "Downloads"),
            (("Ctrl+Shift+J",), "Video downloads"),
            (("Ctrl+L",), "Library"),
            (("Ctrl+,",), "Settings"),
            (("Ctrl+D",), "Light or dark interface"),
            (("N",), "Change the page colours of the tab"),
            (("F11",), "Full screen"),
            (("Esc",), "Leave full screen"),
            (("Ctrl+Alt+A",), "AI assistant"),
            (("Ctrl+K, Ctrl+O",), "Open a folder"),
            (("Ctrl+K, F",), "Close the folder"),
            (("Ctrl+Shift+F",), "Hide or show the folder tree"),
            (("Ctrl+Shift+E",), "Workspaces: favourite and recent folders"),
            (("Ctrl+Shift+B",), "Hide or show the tabs"),
            (("Ctrl+Q",), "Quit"),
        ],
    ),
    (
        "In a document",
        [
            (("Right", "Left"), "Next or previous page"),
            (("Page Down", "Page Up"), "Down or up one page"),
            (("Space", "Shift+Space"), "Down or up one screen"),
            (("Home", "End"), "Start or end of the document"),
            (("Alt+Left", "Alt+Right"), "Back or forward after following a link"),
            (("Ctrl+=", "Ctrl+-"), "Zoom in or out"),
            (("A",), "Fit the whole page"),
            (("W",), "Fit the page width"),
            (("H",), "Fit the page height"),
            (("C",), "Continuous scrolling, or one page at a time"),
            (("D",), "Two pages side by side"),
            (("R",), "Reflowed text"),
            (("F",), "Full screen"),
            (("Ctrl+F",), "Find words"),
            (("Ctrl+I",), "Find passages by meaning"),
            (("Ctrl+A",), "Select all the text"),
            (("Ctrl+C",), "Copy the selected text"),
            (("Shift+arrow keys", "Ctrl+Shift+Left", "Ctrl+Shift+Right"), "Make the selection longer or shorter"),
            (("Ctrl+Alt+S",), "Sections and thumbnails sidebar"),
            (("Ctrl+Shift+A",), "Annotation tools"),
            (("Ctrl+Z",), "Undo an annotation"),
            (("Ctrl+Shift+Z",), "Redo an annotation"),
            (("Ctrl+Alt+N",), "Notes panel"),
            (("Ctrl+Shift+U",), "Read aloud"),
            (("Shift+F5",), "Presentation mode"),
            (("Ctrl+S",), "Save a copy with your annotations"),
            (("Ctrl+Shift+S",), "Add or remove a password"),
            (("Ctrl+R", "Ctrl+Shift+R"), "Rotate the view clockwise or anticlockwise"),
            (("Ctrl+P",), "Print"),
            (("F5",), "Reload the file from disk"),
        ],
    ),
    (
        "On a web page",
        [
            (("F5", "Ctrl+R"), "Reload"),
            (("Ctrl+Shift+R",), "Reload without the cache"),
            (("F6",), "Go to the address bar"),
            (("Ctrl+F",), "Find words"),
            (("Ctrl+=", "Ctrl++"), "Zoom in"),
            (("Ctrl+-", "Ctrl+_"), "Zoom out"),
            (("Ctrl+0",), "Normal size"),
            (("Ctrl+Shift+=", "Ctrl+Shift++"), "Larger text"),
            (("Ctrl+Shift+-", "Ctrl+Shift+_"), "Smaller text"),
            (("Alt+Left", "Alt+Right"), "Back or forward"),
            (("Ctrl+G",), "Video speed control"),
            (("Ctrl+M",), "Music mode"),
            (("F12", "Ctrl+Shift+I"), "Developer tools"),
        ],
    ),
    (
        "In a table",
        [
            (("Ctrl+F",), "Find text in the table"),
            (("Return", "Shift+Return"), "Next or previous match"),
            (("Ctrl+C",), "Copy the selected cells"),
            (("F2",), "Change the selected cell (CSV and TSV)"),
            (("Delete",), "Empty the selected cells"),
            (("Ctrl+Z",), "Undo the last change to the cells"),
            (("Ctrl+S",), "Save the changes to the file (it asks first)"),
            (("Esc",), "Close the find bar"),
        ],
    ),
    (
        "In a picture",
        [
            (("Right", "Left"), "Next or previous picture in the folder"),
            (("Page Down", "Page Up"), "Next or previous page of the picture"),
            (("A",), "Fit the whole picture"),
            (("W", "H"), "Fit the width or the height"),
            (("Ctrl+=", "Ctrl++", "Ctrl+-"), "Zoom in or out"),
            (("Ctrl+0",), "Actual size"),
            (("Ctrl+R", "Ctrl+Shift+R"), "Turn the view clockwise or anticlockwise"),
            (("Ctrl+C",), "Copy the picture"),
            (("Space",), "Pause or play a moving picture"),
            (("N",), "Dark, light or checkerboard background"),
            (("F",), "Full screen"),
        ],
    ),
    (
        "In presentation mode",
        [
            (("Right", "Space", "Page Down"), "Next page (a left click works too)"),
            (("Left", "Backspace", "Page Up"), "Previous page (or a right click)"),
            (("Home", "End"), "First or last page"),
            (("Page number, then Return",), "Go to that page"),
            (("L",), "Laser pointer"),
            (("B", "W"), "Black or white screen"),
            (("Ctrl+=", "Ctrl+-"), "Zoom around the pointer"),
            (("Ctrl+0",), "Fit the page again"),
            (("Esc",), "Leave"),
        ],
    ),
    (
        "In the AI assistant",
        [
            (("Ctrl+L",), "Go to the question box"),
        ],
    ),
]


def shown_keys(keys: str) -> str:
    return keys.replace(", ", " then ")


class HelpTab(SectionedSystemTab):
    page = "help"
    title = "Help"
    subtitle = "What Riemann can do, and its keyboard shortcuts."
    icon_name = "circle-question-mark"

    def build_sections(self) -> None:
        self._pages: list[QWidget] = []
        for key, label, icon, cards in TOPICS:
            layout = self.add_section(key, label, icon)
            self._pages.append(layout.parentWidget())
            for title, paragraphs in cards:
                inner = card(layout, title)
                for text in paragraphs:
                    paragraph = QLabel(text)
                    paragraph.setObjectName("helpText")
                    paragraph.setWordWrap(True)
                    paragraph.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    inner.addWidget(paragraph)
        layout = self.add_section("shortcuts", "Keyboard shortcuts", "type")
        self._pages.append(layout.parentWidget())
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        for title, rows in SHORTCUTS:
            grid = QGridLayout()
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(8)
            grid.setColumnStretch(1, 1)
            for row, (keys, action) in enumerate(rows):
                caps = QHBoxLayout()
                caps.setSpacing(6)
                for i, sequence in enumerate(keys):
                    if i:
                        word = QLabel("or")
                        word.setObjectName("hint")
                        caps.addWidget(word)
                    cap = QLabel(shown_keys(sequence))
                    cap.setObjectName("keyCap")
                    cap.setFont(mono)
                    caps.addWidget(cap)
                caps.addStretch(1)
                holder = QWidget()
                holder.setObjectName("settingRow")
                holder.setLayout(caps)
                caps.setContentsMargins(0, 0, 0, 0)
                grid.addWidget(holder, row, 0)
                what = QLabel(action)
                what.setObjectName("helpText")
                what.setWordWrap(True)
                grid.addWidget(what, row, 1)
            inner = card(layout, title)
            inner.addLayout(grid)

    def on_theme(self, dark: bool) -> None:
        super().on_theme(dark)
        t = tokens(dark)
        style = f"""
            QLabel#helpText {{ color: {t["text"]}; font-size: 14px; }}
            QLabel#keyCap {{
                color: {t["text"]};
                background: {t["raised"]};
                border: 1px solid {t["border"]};
                border-radius: 5px;
                padding: 2px 7px;
            }}
        """
        for page in getattr(self, "_pages", []):
            page.setStyleSheet(style)


__all__ = ["HelpTab", "SHORTCUTS", "TOPICS", "shown_keys"]
