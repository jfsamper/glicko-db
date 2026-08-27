(function () {
  const DASHBOARD_PANEL_ID = "tournament-dashboard-panel";
  const ASYNC_ACTION_ATTR = "data-async-admin-action";
  const busyForms = new WeakSet();

  // Compatibility pattern: requestSubmit ? this.form.requestSubmit() : this.form.submit()
  function triggerFormSubmit(form) {
    if (form) {
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        form.submit();
      }
    }
  }

  const initialPanel = document.getElementById(DASHBOARD_PANEL_ID);
  if (!initialPanel) {
    return;
  }

  async function fetchResponse(url, options) {
    const response = await fetch(
      url,
      Object.assign({ credentials: "same-origin" }, options || {})
    );
    if (!response.ok) {
      throw new Error(`Request failed (${response.status})`);
    }
    return response;
  }

  function parseDashboardPanel(html) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    return doc.getElementById(DASHBOARD_PANEL_ID);
  }

  async function refreshPanel(url) {
    const response = await fetchResponse(url, {
      headers: {
        "X-Requested-With": "XMLHttpRequest",
      },
    });
    const html = await response.text();
    const nextPanel = parseDashboardPanel(html);
    const currentPanel = document.getElementById(DASHBOARD_PANEL_ID);

    if (!nextPanel || !currentPanel) {
      window.location.assign(url);
      return;
    }

    currentPanel.replaceWith(nextPanel);
    attachAsyncListeners(nextPanel);

    const nextUrl = new URL(url, window.location.origin);
    const nextPath = `${nextUrl.pathname}${nextUrl.search}`;
    const currentPath = `${window.location.pathname}${window.location.search}`;
    if (nextPath !== currentPath) {
      window.history.replaceState({}, "", nextPath);
    }
  }

  async function submitAsync(form) {
    if (busyForms.has(form)) {
      return;
    }
    busyForms.add(form);

    const method = (form.method || "POST").toUpperCase();
    const action = form.action || window.location.href;
    const formData = new FormData(form);

    try {
      if (method === "GET") {
        const target = new URL(action, window.location.origin);
        target.search = new URLSearchParams(formData).toString();
        await refreshPanel(target.toString());
        return;
      }

      const response = await fetchResponse(action, {
        method,
        body: formData,
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          Accept: "application/json",
        },
      });

      const contentType = (response.headers.get("content-type") || "").toLowerCase();
      if (contentType.includes("application/json")) {
        const payload = await response.json();
        if (payload && payload.redirect_url) {
          await refreshPanel(payload.redirect_url);
          return;
        }
      }

      if (response.url) {
        await refreshPanel(response.url);
        return;
      }

      form.submit();
    } catch (_error) {
      form.submit();
    } finally {
      busyForms.delete(form);
    }
  }

  function attachAsyncListeners(scope) {
    const root = scope || document;
    const forms = root.querySelectorAll(`form[${ASYNC_ACTION_ATTR}]`);

    forms.forEach((form) => {
      if (form.dataset.asyncBound === "1") {
        return;
      }
      form.dataset.asyncBound = "1";

      form.addEventListener("submit", (event) => {
        event.preventDefault();
        submitAsync(form);
      });
    });
  }

  attachAsyncListeners(initialPanel);
})();
