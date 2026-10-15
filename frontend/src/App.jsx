import { useEffect, useMemo, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? "http://localhost:8000" : window.location.origin);
const PAGE_SIZE = 60;
const LICENSE_CATEGORIES = [
  "No Credit Required",
  "Credit Required Commercial",
  "Credit Required must Modify",
  "Credit Required Share Alike",
];

function fontLicenseCategories(font) {
  const categories = font.license_categories?.length ? font.license_categories : [font.license_category];
  return categories.filter((category) => LICENSE_CATEGORIES.includes(category));
}

function groupByLicense(fonts, selectedLicense) {
  if (selectedLicense !== "All licenses") {
    return fonts.length ? { [selectedLicense]: fonts } : {};
  }
  return fonts.reduce((groups, font) => {
    for (const category of fontLicenseCategories(font)) {
      (groups[category] ??= []).push(font);
    }
    return groups;
  }, {});
}

function buildPrompt(fonts, brief) {
  return [
    "Find the best font for my project from this shortlist. Compare readability, personality, and the listed license.",
    brief ? `Project brief: ${brief}` : "",
    "Verify the license details at the linked source before use.",
    "",
    ...fonts.map(
      (font) =>
        `- ${font.family} (${fontLicenseCategories(font).join("; ")})${font.license_label ? ` | Source license: ${font.license_label}` : ""}${font.license_summary ? ` | Terms: ${font.license_summary}` : ""} — ${font.source_url}${font.license_url ? ` | License: ${font.license_url}` : " | License details unavailable"}`,
    ),
  ].join("\n");
}

function readableApiMessage(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (typeof item === "string") return item;
      if (!item || typeof item !== "object") return String(item);
      const location = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
      const message = typeof item.msg === "string" ? item.msg : item.message;
      return [location, typeof message === "string" ? message : JSON.stringify(item)].filter(Boolean).join(": ");
    }).filter(Boolean).join(" · ");
  }
  if (value && typeof value === "object") {
    if (typeof value.message === "string") return value.message;
    if (typeof value.detail === "string") return value.detail;
    return JSON.stringify(value);
  }
  return value == null ? "" : String(value);
}

export default function App() {
  const [fonts, setFonts] = useState([]);
  const [query, setQuery] = useState("");
  const [license, setLicense] = useState("All licenses");
  const [selected, setSelected] = useState(new Set());
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [brief, setBrief] = useState("");
  const [routing, setRouting] = useState(false);
  const [routeMessage, setRouteMessage] = useState("");
  const [routedIds, setRoutedIds] = useState([]);
  const [dafontStatus, setDafontStatus] = useState("");
  const [supplementalStatus, setSupplementalStatus] = useState("");

  useEffect(() => {
    let active = true;
    fetch(`${API_BASE}/api/fonts`)
      .then((response) => {
        if (!response.ok) throw new Error(`Catalog request failed (${response.status})`);
        return response.json();
      })
      .then((data) => {
        if (!active) return;
        setFonts(data.fonts || []);
        setStatus("ready");
        let polls = 0;
        const pollDaFont = async () => {
          if (!active || polls++ > 120) return;
          try {
            const response = await fetch(`${API_BASE}/api/dafont`);
            const data = await response.json();
            if (!active) return;
            setFonts((current) => {
              const merged = new Map(current.map((font) => [font.id, font]));
              for (const font of data.fonts || []) merged.set(font.id, font);
              return [...merged.values()];
            });
            setDafontStatus(data.done ? `DaFont · ${data.count.toLocaleString()} fonts` : `DaFont · loading ${data.count.toLocaleString()}…`);
            if (!data.done && !data.error) window.setTimeout(pollDaFont, 4000);
            if (data.error) setDafontStatus("DaFont catalog unavailable");
          } catch {
            setDafontStatus("DaFont catalog unavailable");
          }
        };
        window.setTimeout(pollDaFont, 1000);
        let supplementalPolls = 0;
        const pollSupplemental = async () => {
          if (!active || supplementalPolls++ > 180) return;
          try {
            const response = await fetch(`${API_BASE}/api/supplemental`);
            const data = await response.json();
            if (!active) return;
            setFonts((current) => {
              const merged = new Map(current.map((font) => [font.id, font]));
              for (const font of data.fonts || []) merged.set(font.id, font);
              return [...merged.values()];
            });
            const states = Object.entries(data.sources || {}).map(([source, value]) => `${source} · ${value}`);
            setSupplementalStatus(states.join(" · "));
            if (!data.done) window.setTimeout(pollSupplemental, 5000);
          } catch {
            setSupplementalStatus("Supplemental catalogs unavailable");
          }
        };
        window.setTimeout(pollSupplemental, 1200);
      })
      .catch((requestError) => {
        if (!active) return;
        setError(requestError.message);
        setStatus("error");
      });
    return () => {
      active = false;
    };
  }, []);

  const licenses = ["All licenses", ...LICENSE_CATEGORIES];
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return fonts.filter(
      (font) =>
        fontLicenseCategories(font).length > 0 &&
        (!needle || `${font.family} ${font.category}`.toLocaleLowerCase().includes(needle)) &&
        (license === "All licenses" || fontLicenseCategories(font).includes(license)),
    );
  }, [fonts, query, license]);
  const groups = useMemo(() => groupByLicense(filtered, license), [filtered, license]);
  const selectedFonts = fonts.filter((font) => selected.has(font.id));
  const visibleFonts = filtered.slice(0, limit);
  const routedFonts = routedIds.map((id) => fonts.find((font) => font.id === id)).filter(Boolean);

  useEffect(() => {
    const previewCss = new Set([...visibleFonts, ...routedFonts].map((font) => font.preview_css_url).filter(Boolean));
    const existing = new Map(
      [...document.querySelectorAll('link[data-fontfinder-preview-url]')]
        .map((item) => [item.dataset.fontfinderPreviewUrl, item]),
    );
    for (const [url, link] of existing) {
      if (!previewCss.has(url)) link.remove();
    }
    previewCss.forEach((url) => {
      if (existing.has(url)) return;
      const sourceLink = document.createElement("link");
      sourceLink.rel = "stylesheet";
      sourceLink.href = url;
      sourceLink.dataset.fontfinderPreviewUrl = url;
      document.head.appendChild(sourceLink);
    });
  }, [visibleFonts.map((font) => font.id).join("|"), routedFonts.map((font) => font.id).join("|")]);

  function toggleSelected(font) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(font.id)) next.delete(font.id);
      else next.add(font.id);
      return next;
    });
    setCopied(false);
  }

  async function copyPrompt() {
    if (!selectedFonts.length) return;
    await navigator.clipboard.writeText(buildPrompt(selectedFonts, brief.trim()));
    setCopied(true);
  }

  async function routePrompt() {
    if (!brief.trim()) return;
    setRouting(true);
    setRoutedIds([]);
    setRouteMessage("Routing your brief with the embedded local LFM model…");
    const candidates = fonts
      .filter((font) => fontLicenseCategories(font).length > 0)
      .map(({ id, family, category, source }) => ({ id, family, category, source }));
    try {
      const response = await fetch(`${API_BASE}/api/smart-route`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: brief.trim(), fonts: candidates }),
      });
      const data = await response.json();
      if (!response.ok) {
        const message = readableApiMessage(data.detail) || `Routing failed (${response.status})`;
        if (response.status === 422 && candidates.length > 60) {
          throw new Error(`The Python API is still using the old 60-font limit. Restart it to load the full-catalog routing update. (${message})`);
        }
        throw new Error(message);
      }
      const fontIds = Array.isArray(data.font_ids) ? data.font_ids.filter((id) => typeof id === "string") : [];
      setRoutedIds(fontIds);
      setSelected(new Set(fontIds));
      const summary = typeof data.summary === "string" ? data.summary : "Matched your brief.";
      const model = typeof data.model === "string" ? data.model : "local model";
      setRouteMessage(fontIds.length
        ? `${summary} · ${fontIds.length} fonts via ${model}`
        : `No confident matches from the loaded candidates via ${model}. Try a more specific brief.`);
      setCopied(false);
    } catch (routeError) {
      setRouteMessage(readableApiMessage(routeError.message || routeError) || "Routing failed. Check the Python API and try again.");
    } finally {
      setRouting(false);
    }
  }

  function changeFilters(next) {
    setLimit(PAGE_SIZE);
    next();
  }

  return (
    <main className="shell">
      <header className="topbar">
        <a className="wordmark" href="#top" aria-label="FontFinder home">
          <span className="mark">F</span> fontfinder
        </a>
        <span className="top-note">Open font catalog · license sorted</span>
      </header>

      <section className="hero" id="top">
        <p className="eyebrow">A better starting point for type</p>
        <h1>Find a font.<br /><em>Know its license.</em></h1>
        <p className="intro">
          Search thousands of open fonts, sort by license, and pass a clean shortlist to your AI workflow.
        </p>
      </section>

      <section className="catalog" aria-label="Font catalog">
        <div className="prompt-route">
          <label htmlFor="project-brief">Describe the project; route to matching fonts</label>
          <div className="prompt-route-controls">
            <textarea id="project-brief" value={brief} onChange={(event) => { setBrief(event.target.value); setRoutedIds([]); setRouteMessage(""); }} placeholder="e.g. A warm, readable headline font for a neighborhood bakery…" maxLength={1200} />
            <button onClick={routePrompt} disabled={!brief.trim() || routing || status !== "ready"}>{routing ? "Routing…" : "Smart route"}</button>
          </div>
          <p className="route-status" aria-live="polite">{routeMessage || "Uses your local model file. Your brief stays on this machine."}</p>
        </div>
        {(routing || routedIds.length > 0 || routeMessage.startsWith("No confident matches")) && (
          <section className="route-results" aria-label="Fonts matched to this brief" aria-live="polite">
            <div className="route-results-heading">
              <div>
                <p className="eyebrow">Smart route</p>
                <h2>Matches for this brief</h2>
              </div>
              <span>{routing ? "Finding fonts…" : `${routedFonts.length} matched`}</span>
            </div>
            {routing ? <p className="route-empty">Searching the catalog for a fitting typeface…</p> : routedFonts.length === 0 ? (
              <p className="route-empty">No confident matches yet. Try describing the mood, use, or readability needs.</p>
            ) : (
              <div className="font-grid route-font-grid">
                {routedFonts.map((font) => (
                  <article className={`font-card ${selected.has(font.id) ? "selected" : ""}`} key={`route:${font.id}`}>
                    <button
                      className="select-font"
                      onClick={() => toggleSelected(font)}
                      aria-pressed={selected.has(font.id)}
                      aria-label={`${selected.has(font.id) ? "Remove" : "Add"} ${font.family} to shortlist`}
                    >
                      {selected.has(font.id) ? "✓" : "+"}
                    </button>
                    <div className="specimen" style={{ fontFamily: `"${font.preview_family || font.family}", serif` }} aria-label={`${font.family} specimen: Sphinx of black quartz, judge my vow.`}>
                      <div className="specimen-heading">
                        {font.preview_url ? <img src={font.preview_url} alt={`${font.family} source preview`} loading="lazy" /> : <span className="specimen-mark">Aa</span>}
                        <span className="specimen-kind">26-letter pangram</span>
                      </div>
                      <p className="specimen-sentence">Sphinx of black quartz, judge my vow.</p>
                    </div>
                    <h3>{font.family}</h3>
                    <p className="font-meta">{font.source || font.category} · {fontLicenseCategories(font).join(" · ")}</p>
                    {font.license_summary && <p className="license-summary">{font.license_summary}</p>}
                    <div className="card-links">
                      <a href={font.source_url} target="_blank" rel="noreferrer">View font ↗</a>
                      {font.license_url && <a href={font.license_url} target="_blank" rel="noreferrer">License ↗</a>}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        )}
        <div className="toolbar">
          <label className="searchbox">
            <span className="search-icon" aria-hidden="true">⌕</span>
            <input
              value={query}
              onChange={(event) => changeFilters(() => setQuery(event.target.value))}
              placeholder="Search font or style…"
              aria-label="Search fonts"
            />
            <kbd>/</kbd>
          </label>
          <label className="license-filter">
            <span>License</span>
            <select
              value={license}
              onChange={(event) => changeFilters(() => setLicense(event.target.value))}
            >
              {licenses.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <div className="result-count">
            <strong>{status === "ready" ? filtered.length.toLocaleString() : "—"}</strong>
            <span>fonts</span>
          </div>
        </div>
        <p className="license-help">
          Categories follow linked license terms and can overlap. Only fonts with identified terms allowing free commercial use are shown; “must Modify” means mark changes when modifying the font.
        </p>

        {status === "loading" && <div className="message">Loading public font catalogs…</div>}
        {status === "error" && (
          <div className="message error">
            <strong>Couldn’t load the font catalog.</strong>
            <span>{error}. Start the Python API, then refresh.</span>
          </div>
        )}
        {status === "ready" && filtered.length === 0 && (
          <div className="message">No fonts match these filters.</div>
        )}

        {Object.entries(groups).map(([groupLicense, items]) => {
          const visible = items.slice(0, limit);
          return (
            <section className="license-group" key={groupLicense}>
              <div className="group-heading">
                <div className="group-title">
                  <span className="license-dot" />
                  <h2>{groupLicense}</h2>
                  <span className="open-tag">ABSTRACTED</span>
                </div>
                <span className="group-total">{items.length.toLocaleString()} fonts</span>
              </div>
              <div className="font-grid">
                {visible.map((font) => (
                  <article className={`font-card ${selected.has(font.id) ? "selected" : ""}`} key={font.id}>
                    <button
                      className="select-font"
                      onClick={() => toggleSelected(font)}
                      aria-pressed={selected.has(font.id)}
                      aria-label={`${selected.has(font.id) ? "Remove" : "Add"} ${font.family} to shortlist`}
                    >
                      {selected.has(font.id) ? "✓" : "+"}
                    </button>
                    <div className="specimen" style={{ fontFamily: `"${font.preview_family || font.family}", serif` }} aria-label={`${font.family} specimen: Sphinx of black quartz, judge my vow.`}>
                      <div className="specimen-heading">
                        {font.preview_url ? <img src={font.preview_url} alt={`${font.family} source preview`} loading="lazy" /> : <span className="specimen-mark">Aa</span>}
                        <span className="specimen-kind">26-letter pangram</span>
                      </div>
                      <p className="specimen-sentence">Sphinx of black quartz, judge my vow.</p>
                    </div>
                    <h3>{font.family}</h3>
                    <p className="font-meta">{font.source || font.category} · {fontLicenseCategories(font).join(" · ")}</p>
                    {font.license_summary && <p className="license-summary">{font.license_summary}</p>}
                    <div className="card-links">
                      <a href={font.source_url} target="_blank" rel="noreferrer">View font ↗</a>
                      {font.license_url && <a href={font.license_url} target="_blank" rel="noreferrer">License ↗</a>}
                      {(font.provider_links || []).map((provider) => (
                        <a key={`${font.id}:${provider.label}`} href={provider.url} target="_blank" rel="noreferrer">{provider.label} ↗</a>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
              {items.length > visible.length && (
                <button className="load-more" onClick={() => setLimit((current) => current + PAGE_SIZE)}>
                  Show more {groupLicense} fonts
                </button>
              )}
            </section>
          );
        })}
      </section>

      <nav className="asset-sources" aria-label="Game font asset libraries">
        <span>Game font assets</span>
        <a href="https://opengameart.org/art-search-advanced?field_art_tags_tid=font" target="_blank" rel="noreferrer">OpenGameArt · font tag ↗</a>
        <a href="https://opengameart.org/content/cc0-fonts" target="_blank" rel="noreferrer">OpenGameArt · CC0 fonts ↗</a>
        <a href="https://itch.io/game-assets/tag-cc0/tag-fonts" target="_blank" rel="noreferrer">itch.io · CC0 font assets ↗</a>
        <a href="https://kenney.nl/assets/kenney-fonts" target="_blank" rel="noreferrer">Kenney Fonts · CC0 ↗</a>
      </nav>

      <footer className={`handoff ${selectedFonts.length ? "active" : ""}`}>
        <div>
          <span className="handoff-kicker">LLM HANDOFF</span>
          <strong>{selectedFonts.length ? `${selectedFonts.length} font${selectedFonts.length === 1 ? "" : "s"} selected` : "Build a font shortlist"}</strong>
        </div>
        <button onClick={copyPrompt} disabled={!selectedFonts.length}>
          {copied ? "Copied to clipboard ✓" : "Copy prompt + links"}
        </button>
      </footer>
      <p className="source-note">Catalog: Fontsource, DaFont, Font Library, Fontshare, Bunny Fonts · {dafontStatus} · {supplementalStatus} · <a href="https://fontesk.com/" target="_blank" rel="noreferrer">Fontesk browse ↗</a> · Verify terms at the linked source.</p>
    </main>
  );
}
