# FontFinder

A small React and FastAPI app for searching public font catalogs and grouping results by license.

## Download the desktop app

Open the repository's **Releases** page and download the asset for your system. Each version tag, such as `v0.1.0`, creates one GitHub Release containing Windows x64, Linux x64, macOS Apple silicon, and macOS Intel builds. The executable starts a local-only API and opens FontFinder in your browser.

- **Windows:** extract the ZIP and open `FontFinder.exe`.
- **Linux:** extract the TAR.GZ, then run `./FontFinder` from a terminal.
- **macOS:** extract the ZIP and open `FontFinder.app`.

The GGUF model is not included in the release files. Smart route looks for the model under the current user's `.lmstudio/models/lmstudio-community/LFM2.5-350M-GGUF/` folder; set `FONTFINDER_MODEL_PATH` if the model is stored elsewhere. Catalog search and license filtering work without the model. The macOS executable is not code-signed or notarized; macOS may ask you to approve it before first launch.

To create a tagged release, push a version tag (for example, `git tag v0.1.0 && git push origin v0.1.0`). GitHub Actions builds the platform executables and attaches them to that tag's release.

## Run locally

1. Start the Python API from `backend`:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   uvicorn app:app --reload --port 8000
   ```

2. In another terminal, start the React UI from `frontend`:

   ```powershell
   npm install
   npm run dev
   ```

3. Open the URL printed by Vite. The UI forwards `/api` requests to `http://localhost:8000`.

## Portfolio sample for testing

Open `http://localhost:5173/portfolio-demo.html` to view the fictional Mara Vale portfolio. Its **Copy type brief** button copies a detailed, commercial-use font brief; paste it into FontFinder's Smart route field to try the local font matcher.

The catalog combines Google Fonts via Fontsource, Fontsource's `other` catalog, Font Library, Fontshare, and DaFont. Each font card starts with an uppercase/lowercase preview, followed by the all-letter pangram “Sphinx of black quartz, judge my vow.” Fontsource families load an independent stylesheet from the Fontsource CDN, including variable-font stylesheets; Font Library and Fontshare use their family stylesheets. Bunny Fonts remains linked as an alternate provider for Google Fonts. Font Library and Fontshare are scraped politely in the background and retain each family page and license details; no font files are downloaded. DaFont is read from its public theme listing pages in a slow, background refresh; that scraper reads category listings and does not fetch font downloads. Each DaFont record keeps its font page, displayed license label, license information link, and preview image.

Fontesk appears as a browse link only: its published site terms prohibit data mining and data extraction, so FontFinder does not scrape it. License categories derive from the source license terms and can overlap. “Credit Required must Modify” means identify changes when modifying the font. The catalog excludes noncommercial, paid, and unclear/unverified license terms, and only includes identified licenses that permit free commercial use. The four available categories are No Credit Required, Credit Required Commercial, Credit Required must Modify, and Credit Required Share Alike. Font Library results are sampled through actual license facets to include attribution and share-alike examples.

Game-asset discovery links open font-filtered results on OpenGameArt and itch.io, plus Kenney's dedicated CC0 font pack. These remain source-native browse pages because game asset uploads may be bitmap glyph sheets or bundled packs rather than installable font families, and each item can carry its own license.

## Local smart routing

The project brief's **Smart route** action sends the full eligible font catalog to the local `llama-cpp-python` runtime, independent of the catalog search box and license display filter. The embedded GGUF first infers search facets from the brief. FontFinder scores the full catalog, limits catalog-source dominance, and breaks ties with a stable prompt-and-font hash rather than input order; the model then reranks the diverse candidates. Malformed or unusable model output falls back to this same relevance-ranked candidate set. Prompt routing does not take part in catalog scraping and does not call LM Studio or a hosted service. The default model path is under the current user's `.lmstudio/models/lmstudio-community/LFM2.5-350M-GGUF/` folder; override it with `FONTFINDER_MODEL_PATH` if needed. The first route request loads the 350M model into memory. This file is the chat model, so routing uses local chat inference, not vector embeddings.

Install `llama-cpp-python` with the backend requirements before using Smart route. On Windows, the package may need a compatible prebuilt wheel or C++ build tools.

## API start command

From the `backend` directory:

```powershell
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

## Notes

- Google/Fontsource catalog data is cached in memory for 12 hours; DaFont and supplemental scraper results refresh once per backend process.
- Search, license filtering, and prompt generation happen in the browser after the catalog loads.
- The copy button produces a plain text font shortlist with source and license URLs for an LLM handoff.
- The backend does not treat missing DaFont license details as permission; uncertain listings are excluded, as are paid and noncommercial fonts.
