document.addEventListener("keydown", function (e) {
    if (e.key === "Backspace" && !e.altKey && !e.ctrlKey && !e.shiftKey && !e.metaKey) {
        const tag = document.activeElement.tagName;
        const type = document.activeElement.type;
        const isInput = (tag === "INPUT" && type !== "button" && type !== "submit" && type !== "checkbox" && type !== "radio")
            || tag === "TEXTAREA"
            || document.activeElement.isContentEditable;
        if (!isInput) {
            e.preventDefault();
            window.history.back();
        }
    }
});