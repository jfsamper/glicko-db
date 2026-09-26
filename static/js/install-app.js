(() => {
  const promptMessage = document.getElementById("install-app-prompt");
  const installButton = document.getElementById("install-app-action");
  const dismissButton = document.getElementById("dismiss-install-app");

  if (!promptMessage || !installButton || !dismissButton) return;

  const userAgent = navigator.userAgent;
  const isSafari = /Safari/i.test(userAgent)
    && !/(Chrome|CriOS|FxiOS|EdgiOS|OPR|Android)/i.test(userAgent);
  const isMobile = navigator.userAgentData?.mobile
    ?? /Android|iPhone|iPad|iPod|IEMobile|Windows Phone|BlackBerry|Opera Mini|Mobile/i.test(userAgent);
  const isInstalled = window.matchMedia("(display-mode: standalone)").matches
    || navigator.standalone === true;
  const dismissedKey = "glicko-install-prompt-dismissed";
  let deferredInstallPrompt = null;

  function wasDismissed() {
    try {
      return sessionStorage.getItem(dismissedKey) === "true";
    } catch (_error) {
      return false;
    }
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    if (!isMobile || isSafari || isInstalled || wasDismissed()) return;

    event.preventDefault();
    deferredInstallPrompt = event;
    promptMessage.hidden = false;
  });

  installButton.addEventListener("click", async () => {
    if (!deferredInstallPrompt) return;

    const installPromptEvent = deferredInstallPrompt;
    deferredInstallPrompt = null;
    promptMessage.hidden = true;

    try {
      await installPromptEvent.prompt();
      await installPromptEvent.userChoice;
    } catch (_error) {}
  });

  dismissButton.addEventListener("click", () => {
    deferredInstallPrompt = null;
    promptMessage.hidden = true;

    try {
      sessionStorage.setItem(dismissedKey, "true");
    } catch (_error) {}
  });

  window.addEventListener("appinstalled", () => {
    deferredInstallPrompt = null;
    promptMessage.hidden = true;
  });
})();