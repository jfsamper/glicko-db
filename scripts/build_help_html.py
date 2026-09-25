"""Build browser-ready HTML files from the localized user guides."""

from argparse import ArgumentParser
from pathlib import Path
import html
import re

import markdown


ROOT = Path(__file__).resolve().parents[1]
GUIDES = {
    "es": (ROOT / "docs" / "user_interface.md", "Ayuda de la interfaz"),
    "en": (ROOT / "docs" / "user_interface.en.md", "User Interface Help"),
    "pt": (ROOT / "docs" / "user_interface.pt.md", "Ajuda da interface"),
}
API_DOCS = {
  "es": (ROOT / "docs" / "api_endpoints.md", "Rutas y notas de integración"),
  "en": (ROOT / "docs" / "api_endpoints.en.md", "Routes and integration notes"),
  "pt": (ROOT / "docs" / "api_endpoints.pt.md", "Rotas e notas de integração"),
}
READMES = {
    "es": (ROOT / "README.md", "Glicko DB"),
    "en": (ROOT / "README.en.md", "Glicko DB"),
    "pt": (ROOT / "README.pt.md", "Glicko DB"),
}
OUTPUT_DIR = ROOT / "docs" / "generated"
GUIDE_OUTPUT_NAMES = {
    "es": "user_interface.html",
    "en": "user_interface.en.html",
    "pt": "user_interface.pt.html",
}
API_OUTPUT_NAMES = {
  "es": "api_endpoints.html",
  "en": "api_endpoints.en.html",
  "pt": "api_endpoints.pt.html",
}
README_OUTPUT_NAMES = {
    "es": "README.html",
    "en": "README.en.html",
    "pt": "README.pt.html",
}
SCREENSHOT_URL_PREFIX = "/help-assets/screenshots/"

LANGUAGE_LABELS = {
    "es": "Español",
    "en": "English",
    "pt": "Português",
}
LANGUAGE_CONTROL_LABELS = {
  "es": "Idioma",
  "en": "Language",
  "pt": "Idioma",
}
DOCUMENT_LINK_LABELS = {
  "es": ("Ayuda de la interfaz", "Rutas"),
  "en": ("Interface help", "Routes"),
  "pt": ("Ajuda da interface", "Rotas"),
}
THEME_BUTTON_LABELS = {
    "es": ("Activar tema oscuro", "Activar tema claro"),
    "en": ("Enable dark theme", "Enable light theme"),
    "pt": ("Ativar tema escuro", "Ativar tema claro"),
}
BACK_TO_TOP_LABELS = {
  "es": "Volver arriba",
  "en": "Back to top",
  "pt": "Voltar ao topo",
}

PAGE_STYLE = """
:root {
  color-scheme: light;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --help-background: #f4f7f9;
  --help-panel: #fff;
  --help-text: #1f2933;
  --help-heading: #17324d;
  --help-header: #17324d;
  --help-link: #075985;
  --help-border: #d9e1e7;
  --help-border-soft: #e5eaee;
  --help-code: #eef3f6;
  --help-table-border: #cbd5dc;
  --help-shadow: rgba(23, 50, 77, 0.08);
  color: var(--help-text);
  background: var(--help-background);
}
[data-theme="dark"] {
  color-scheme: dark;
  --help-background: #111827;
  --help-panel: #1f2937;
  --help-text: #f3f4f6;
  --help-heading: #bfdbfe;
  --help-header: #0b1220;
  --help-link: #7dd3fc;
  --help-border: #374151;
  --help-border-soft: #4b5563;
  --help-code: #273548;
  --help-table-border: #4b5563;
  --help-shadow: rgba(0, 0, 0, 0.35);
}
body {
  margin: 0;
  line-height: 1.6;
  color: var(--help-text);
  background: var(--help-background);
}
html {
  scroll-behavior: smooth;
}
.help-header {
  padding: 1.5rem max(1rem, calc((100vw - 1100px) / 2));
  color: #fff;
  background: var(--help-header);
}
.help-header-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  max-width: 1100px;
  margin: 0 auto;
}
.help-header-controls {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 1rem;
  flex-wrap: wrap;
  margin-left: auto;
}
.help-brand,
.help-document-links a {
  color: inherit;
}
.help-brand {
  font-size: 1.2rem;
  font-weight: 700;
  text-decoration: none;
}
.help-document-links {
  display: flex;
  flex-wrap: wrap;
  gap: 0.7rem;
  margin-left: 0;
  font-size: 0.95rem;
}
.help-document {
  box-sizing: border-box;
  max-width: 1100px;
  margin: 2rem auto;
  padding: 2rem clamp(1rem, 4vw, 3rem);
  color: var(--help-text);
  background: var(--help-panel);
  border: 1px solid var(--help-border);
  border-radius: 8px;
  box-shadow: 0 8px 24px var(--help-shadow);
}
.help-document h1,
.help-document h2,
.help-document h3,
.help-document h4 {
  color: var(--help-heading);
  line-height: 1.25;
  scroll-margin-top: 1rem;
}
.help-document h1 {
  margin-top: 0;
  font-size: clamp(1.8rem, 4vw, 2.6rem);
}
.help-document h2 {
  margin-top: 2.2rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--help-border-soft);
}
.help-document h3,
.help-document h4 {
  margin-top: 1.7rem;
}
.help-document a {
  color: var(--help-link);
}
.help-document img {
  display: block;
  width: min(100%, 560px);
  height: auto;
  margin: 1rem 0;
  border: 1px solid var(--help-border);
  border-radius: 4px;
}
.help-document pre {
  overflow-x: auto;
  padding: 1rem;
  background: var(--help-code);
  border-radius: 4px;
}
.help-document code {
  padding: 0.08em 0.25em;
  background: var(--help-code);
  border-radius: 3px;
}
.help-document pre code {
  padding: 0;
  background: transparent;
}
.help-document table {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
}
.help-document th,
.help-document td {
  padding: 0.45rem 0.65rem;
  border: 1px solid var(--help-table-border);
  text-align: left;
}
.help-floating-controls {
  position: fixed;
  right: 22px;
  bottom: 22px;

  display: flex;
  align-items: center;
  gap: 10px;

  z-index: 9999;
}
.help-floating-controls button,
.help-back-to-top {
  width: 54px;
  height: 54px;

  align-items: center;
  justify-content: center;

  border: 1px solid var(--help-border);
  border-radius: 50%;
  color: var(--help-text);
  background: var(--help-panel);
  box-shadow: 0 4px 14px var(--help-shadow);
  font: inherit;
  font-size: 1rem;
  font-weight: 700;
  line-height: 1;
  text-align: center;
  text-decoration: none;
  cursor: pointer;
}
.help-floating-controls button {
  display: flex;
  padding: 0;
}
.help-floating-controls .language-control {
  display: flex;
  align-items: center;
}
.help-floating-controls .language-select {
  width: 7.5rem;
  height: 54px;
  padding: 0 0.7rem;
  border: 1px solid var(--help-border);
  border-radius: 10px;
  color: var(--help-text);
  background: var(--help-panel);
  font: inherit;
  font-weight: 700;
  box-shadow: 0 4px 14px var(--help-shadow);
  cursor: pointer;
}
.help-floating-controls button:hover,
.help-back-to-top:hover {
  transform: translateY(-2px);
}
.help-floating-controls button:hover,
.help-floating-controls button:focus-visible,
.help-back-to-top:hover,
.help-back-to-top:focus-visible {
  color: var(--help-panel);
  background: var(--help-link);
}
.help-floating-controls button:focus-visible,
.help-floating-controls .language-select:focus-visible,
.help-back-to-top:focus-visible {
  outline: 3px solid #facc15;
  outline-offset: 2px;
}
.help-back-to-top {
  display: flex;
  font-size: 1.5rem;
}
.help-floating-controls button:active,
.help-back-to-top:active {
  transform: translateY(0);
}
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
@media (prefers-reduced-motion: reduce) {
  html {
    scroll-behavior: auto;
  }
}
@media (max-width: 640px) {
  .help-header-inner {
    align-items: flex-start;
    flex-direction: column;
  }
  .help-header-controls {
    justify-content: flex-start;
    margin-left: 0;
  }
  .help-document {
    margin: 1rem 0;
    border-right: 0;
    border-left: 0;
    border-radius: 0;
  }
}
"""


def rewrite_screenshot_urls(rendered_html):
    return re.sub(
        r'((?:href|src)=["\'])screenshots/',
        rf"\1{SCREENSHOT_URL_PREFIX}",
        rendered_html,
    )


def rewrite_document_links(rendered_html, language, document_type):
  if document_type == "guide":
    source_name = {
      "es": "api_endpoints.md",
      "en": "api_endpoints.en.md",
      "pt": "api_endpoints.pt.md",
    }[language]
    target = f"/help/api?lang={language}"
  elif document_type == "api":
    source_name = {
      "es": "user_interface.md",
      "en": "user_interface.en.md",
      "pt": "user_interface.pt.md",
    }[language]
    target = f"/help?lang={language}"
  else:
    for code, (source_path, _) in READMES.items():
      rendered_html = rendered_html.replace(
        f'href="{source_path.name}"', f'href="/help/readme?lang={code}"'
      )
    for code in READMES:
      guide_name = GUIDE_OUTPUT_NAMES[code].replace(".html", ".md")
      api_name = API_OUTPUT_NAMES[code].replace(".html", ".md")
      rendered_html = rendered_html.replace(
        f'href="docs/{guide_name}"', f'href="/help?lang={code}"'
      )
      rendered_html = rendered_html.replace(
        f'href="docs/{api_name}"', f'href="/help/api?lang={code}"'
      )
    return rendered_html
  rendered_html = rendered_html.replace(
    f'href="{source_name}"', f'href="{target}"'
  )
  return re.sub(
    r'href="\.\./README(?:\.en|\.pt)?\.md"',
    f'href="/help/readme?lang={language}"',
    rendered_html,
  )


def render_document(language, source_path, title, document_type):
    source = source_path.read_text(encoding="utf-8").removeprefix("\ufeff")
    rendered = markdown.markdown(
        source,
        extensions=["extra", "sane_lists", "toc"],
        output_format="html5",
    )
    rendered = rewrite_screenshot_urls(rendered)
    rendered = rewrite_document_links(rendered, language, document_type)
    language_options = []
    for code, label in LANGUAGE_LABELS.items():
      selected = " selected" if code == language else ""
      language_options.append(
        f'<option value="{code}"{selected}>{html.escape(label)}</option>'
        )
    interface_label, routes_label = DOCUMENT_LINK_LABELS[language]
    language_control_label = LANGUAGE_CONTROL_LABELS[language]
    dark_theme_label, light_theme_label = THEME_BUTTON_LABELS[language]
    back_to_top_label = BACK_TO_TOP_LABELS[language]
    return f"""<!doctype html>
<html lang="{language}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} | Glicko DB</title>
  <script>
    try {{
      const cookieTheme = document.cookie
        .split("; ")
        .find((part) => part.startsWith("user_theme="));
      const storedTheme = (cookieTheme && cookieTheme.slice("user_theme=".length))
        || localStorage.getItem("theme");
      if (storedTheme === "dark") {{
        document.documentElement.setAttribute("data-theme", "dark");
      }}
    }} catch (_error) {{}}
  </script>
  <style>{PAGE_STYLE}</style>
</head>
<body id="top">
  <header class="help-header">
    <div class="help-header-inner">
      <a class="help-brand" href="/?lang={language}">Glicko DB</a>
      <div class="help-header-controls">
        <nav class="help-document-links" aria-label="Documentation">
          <a href="/help?lang={language}">{html.escape(interface_label)}</a>
          <a href="/help/api?lang={language}">{html.escape(routes_label)}</a>
        </nav>
      </div>
    </div>
  </header>
  <main class="help-document">
{rendered}
  </main>
  <div class="help-floating-controls">
    <label class="language-control" for="language-select">
      <span class="visually-hidden">{html.escape(language_control_label)}</span>
      <select id="language-select" class="language-select" aria-label="{html.escape(language_control_label)}">
        {''.join(language_options)}
      </select>
    </label>
    <button id="theme-toggle" type="button"
      aria-label="{html.escape(dark_theme_label)}"
      title="{html.escape(dark_theme_label)}" aria-pressed="false">⚫</button>
    <a class="help-back-to-top" href="#top" aria-label="{html.escape(back_to_top_label)}"
      title="{html.escape(back_to_top_label)}">^</a>
  </div>
<script>
  const root = document.documentElement;
  const themeButton = document.getElementById("theme-toggle");
  const languageSelect = document.getElementById("language-select");

  function updateThemeButton() {{
    const dark = root.getAttribute("data-theme") === "dark";
    const label = dark
      ? "{html.escape(light_theme_label)}"
      : "{html.escape(dark_theme_label)}";
    themeButton.textContent = dark ? "⚪" : "⚫";
    themeButton.setAttribute("aria-label", label);
    themeButton.title = label;
    themeButton.setAttribute("aria-pressed", String(dark));
  }}

  function saveTheme(theme) {{
    try {{
      localStorage.setItem("theme", theme);
      document.cookie = "user_theme=" + theme
        + "; Max-Age=31536000; Path=/; SameSite=Lax";
    }} catch (_error) {{}}
  }}

  updateThemeButton();
  themeButton.addEventListener("click", () => {{
    const dark = root.getAttribute("data-theme") === "dark";
    const nextTheme = dark ? "light" : "dark";

    if (nextTheme === "dark") {{
      root.setAttribute("data-theme", "dark");
    }} else {{
      root.removeAttribute("data-theme");
    }}

    saveTheme(nextTheme);
    updateThemeButton();
  }});

  languageSelect.addEventListener("change", () => {{
    const url = new URL(window.location.href);
    url.searchParams.set("lang", languageSelect.value);
    window.location.href = url.toString();
  }});
</script>
</body>
</html>
"""


def build_guides(output_dir=OUTPUT_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)
    for language, (source_path, title) in GUIDES.items():
        output_path = output_dir / GUIDE_OUTPUT_NAMES[language]
        output_path.write_text(
            render_document(language, source_path, title, "guide"),
            encoding="utf-8",
            newline="\n",
        )
        print(f"Built {output_path.relative_to(ROOT)}")
    for language, (source_path, title) in API_DOCS.items():
        output_path = output_dir / API_OUTPUT_NAMES[language]
        output_path.write_text(
            render_document(language, source_path, title, "api"),
            encoding="utf-8",
            newline="\n",
        )
        print(f"Built {output_path.relative_to(ROOT)}")
    for language, (source_path, title) in READMES.items():
        output_path = output_dir / README_OUTPUT_NAMES[language]
        output_path.write_text(
            render_document(language, source_path, title, "readme"),
            encoding="utf-8",
            newline="\n",
        )
        print(f"Built {output_path.relative_to(ROOT)}")


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory for generated HTML files (default: docs/generated).",
    )
    args = parser.parse_args()
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    build_guides(output_dir)


if __name__ == "__main__":
    main()