# Copilot instructions for `lucid_exports`

## Commands

Install dependencies from the README:

```bash
pip install playwright python-dotenv requests
playwright install chromium
```

Run the exporter with a Lucid folder URL or folder ID:

```bash
./export_folder.py "<folder_url>"
./export_folder.py <folder_id>
```

There is no dedicated build, lint, or test suite checked into this repository. The main executable is the top-level `export_folder.py` script.

## High-level architecture

This repository is a single-script tool centered on `export_folder.py`.

- `main()` parses either a full Lucid folder URL or a raw folder ID, loads persisted checkpoint state, opens a visible Chromium browser with Playwright, and waits for manual login confirmation.
- Discovery prefers the Lucid REST API when `LUCID_API_KEY` is available. `get_folders_hierarchy_api()` and `get_documents_from_folder_api()` use `requests` to find documents and preserve subfolder paths when the API exposes enough hierarchy information.
- If API discovery is unavailable or incomplete, the script falls back to browser-based discovery. That fallback combines network-response inspection (`attach_network_document_collector()` and JSON payload parsing) with DOM scraping (`collect_document_candidates()`) to find document IDs and titles from the loaded Lucid folder page.
- Export is always browser-driven. `export_document()` opens each document in the editor, uses Playwright locators to open the Lucid menu, selects the Visio/VSDX export option, and saves downloads into `./exports/<folder_name>/`.

## Key conventions

- The project is intentionally not packaged: make changes directly in `export_folder.py` and preserve the script-style entrypoint.
- Keep browser automation headful and interactive unless the user explicitly asks otherwise. The current workflow depends on a real Chromium window plus manual login and ENTER prompts before discovery starts.
- Prefer the existing dual discovery strategy over replacing one path with the other: API discovery is optional, but browser discovery is the reliable fallback and the primary behavior when no `LUCID_API_KEY` is present.
- Route user-facing diagnostics through `log()` so output continues to appear both in the terminal and in `export_log.txt`.
- Preserve filename/path sanitization via `sanitize_filename()` when adding new output paths or exported artifact names.
- Resume behavior is part of the tool contract. Keep `.export_checkpoint.json` compatible with the existing `folder_id`, `folder_name`, `completed`, and `failed` shape, and continue treating existing non-empty exported files as already completed work.
- Browser-discovered documents currently use an empty `folder_path`, while API-discovered documents may include nested folder paths. Be careful not to assume subfolder preservation is available in both modes.
- Generated artifacts and local secrets are expected to stay uncommitted. `.gitignore` already excludes `.env`, checkpoint files, logs, temporary download directories, and exported files under `exports/`.
