(function () {
    const clearAds = () => {
        const overlays = document.querySelectorAll('.ytp-ad-overlay-close-button');
        overlays.forEach(b => { b.click(); });

        const video = document.querySelector('video');
        const adShowing = document.querySelector('.ad-showing');
        if (video && adShowing) {
            video.playbackRate = 16.0;
            video.muted = true;
            if (isFinite(video.duration) && video.currentTime < video.duration) {
                video.currentTime = video.duration;
            }
        }

        const skipSelectors = [
            '.ytp-ad-skip-button',
            '.ytp-ad-skip-button-modern',
            '.videoAdUiSkipButton',
            '.ytp-skip-ad-button',
            'button[class*="ad-skip"]',
            '[id^="skip-button"]'
        ];

        const skipBtns = document.querySelectorAll(skipSelectors.join(', '));
        skipBtns.forEach(b => { b.click(); });
    };
    setInterval(clearAds, 50);
})();