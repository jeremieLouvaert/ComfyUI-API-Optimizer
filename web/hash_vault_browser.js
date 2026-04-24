import { app } from "../../scripts/app.js";

const LIST_URL = "/akurate/hash_vault/list";
const THUMB_URL = (hash, name) =>
    `/view?filename=${encodeURIComponent(name)}&type=output&subfolder=hash_vault&rand=${hash.slice(0, 8)}`;

const STYLE = `
.akurate-hv-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,0.82);
    z-index: 10000; display: flex; align-items: center; justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.akurate-hv-modal {
    background: #1a1a1a; color: #eee; border: 1px solid #333; border-radius: 8px;
    width: 92vw; height: 90vh; display: flex; flex-direction: column;
    box-shadow: 0 20px 60px rgba(0,0,0,0.6);
}
.akurate-hv-header {
    padding: 14px 20px; border-bottom: 1px solid #333;
    display: flex; align-items: center; gap: 16px;
}
.akurate-hv-header h2 { margin: 0; font-size: 16px; font-weight: 500; }
.akurate-hv-count { color: #888; font-size: 13px; }
.akurate-hv-search {
    flex: 1; background: #0f0f0f; color: #eee; border: 1px solid #333;
    border-radius: 4px; padding: 6px 10px; font-size: 13px;
    font-family: inherit; outline: none;
}
.akurate-hv-search:focus { border-color: #4a90e2; }
.akurate-hv-close {
    background: transparent; color: #aaa; border: 1px solid #333;
    border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 14px;
}
.akurate-hv-close:hover { background: #333; color: #fff; }
.akurate-hv-body {
    flex: 1; overflow-y: auto; padding: 16px 20px;
}
.akurate-hv-grid {
    display: grid; gap: 14px;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
}
.akurate-hv-card {
    background: #111; border: 1px solid #2a2a2a; border-radius: 6px;
    overflow: hidden; cursor: pointer; transition: border-color 0.12s, transform 0.12s;
    display: flex; flex-direction: column;
}
.akurate-hv-card:hover { border-color: #4a90e2; transform: translateY(-1px); }
.akurate-hv-thumb {
    width: 100%; aspect-ratio: 1 / 1; background: #0a0a0a;
    display: flex; align-items: center; justify-content: center;
    color: #555; font-size: 36px;
}
.akurate-hv-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.akurate-hv-meta {
    padding: 8px 10px; font-size: 12px; line-height: 1.35;
    display: flex; flex-direction: column; gap: 2px; min-height: 54px;
}
.akurate-hv-label { color: #eee; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.akurate-hv-label.empty { color: #666; font-style: italic; font-weight: 400; }
.akurate-hv-sub { color: #888; font-size: 11px; }
.akurate-hv-empty {
    text-align: center; color: #666; padding: 60px 20px; font-size: 14px;
}
.akurate-hv-toast {
    position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%);
    background: #2a2a2a; color: #eee; border: 1px solid #4a90e2;
    padding: 10px 18px; border-radius: 6px; font-size: 13px;
    z-index: 10001; opacity: 0; transition: opacity 0.2s;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.akurate-hv-toast.show { opacity: 1; }
`;

function injectStyle() {
    if (document.getElementById("akurate-hv-style")) return;
    const el = document.createElement("style");
    el.id = "akurate-hv-style";
    el.textContent = STYLE;
    document.head.appendChild(el);
}

function formatBytes(n) {
    if (!n) return "—";
    if (n < 1024) return `${n} B`;
    if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
    return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function relativeAge(isoString) {
    if (!isoString) return "unknown";
    const then = new Date(isoString).getTime();
    const now = Date.now();
    const secs = Math.max(0, (now - then) / 1000);
    if (secs < 60) return "just now";
    if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
    if (secs < 86400 * 30) return `${Math.round(secs / 86400)}d ago`;
    if (secs < 86400 * 365) return `${Math.round(secs / (86400 * 30))}mo ago`;
    return `${Math.round(secs / (86400 * 365))}y ago`;
}

function showToast(text) {
    const toast = document.createElement("div");
    toast.className = "akurate-hv-toast";
    toast.textContent = text;
    document.body.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add("show"));
    setTimeout(() => {
        toast.classList.remove("show");
        setTimeout(() => toast.remove(), 250);
    }, 1600);
}

function buildCard(entry) {
    const card = document.createElement("div");
    card.className = "akurate-hv-card";

    const thumb = document.createElement("div");
    thumb.className = "akurate-hv-thumb";
    if (entry.thumbnail) {
        const img = document.createElement("img");
        img.src = THUMB_URL(entry.hash_key, entry.thumbnail);
        img.loading = "lazy";
        img.onerror = () => {
            thumb.innerHTML = "";
            thumb.textContent = "⋄";
        };
        thumb.appendChild(img);
    } else {
        const kind = entry.payload?.kind || "?";
        thumb.textContent = kind === "dict" ? "{…}" : kind === "tensor" ? "⊞" : "⋄";
    }
    card.appendChild(thumb);

    const meta = document.createElement("div");
    meta.className = "akurate-hv-meta";

    const label = document.createElement("div");
    label.className = "akurate-hv-label";
    if (entry.label) {
        label.textContent = entry.label;
    } else {
        label.classList.add("empty");
        label.textContent = entry.hash_key.slice(0, 12);
    }

    const sub = document.createElement("div");
    sub.className = "akurate-hv-sub";
    sub.textContent = `${relativeAge(entry.last_accessed_at)} · ${formatBytes(entry.pt_size)}`;

    meta.appendChild(label);
    meta.appendChild(sub);
    card.appendChild(meta);

    card.addEventListener("click", () => {
        navigator.clipboard.writeText(entry.hash_key).then(
            () => showToast(`Hash copied: ${entry.hash_key.slice(0, 12)}…`),
            () => showToast("Copy failed — selection blocked"),
        );
    });

    return card;
}

function buildModal(entries) {
    const backdrop = document.createElement("div");
    backdrop.className = "akurate-hv-backdrop";

    const modal = document.createElement("div");
    modal.className = "akurate-hv-modal";

    const header = document.createElement("div");
    header.className = "akurate-hv-header";
    const title = document.createElement("h2");
    title.textContent = "Hash Vault Browser";
    const count = document.createElement("span");
    count.className = "akurate-hv-count";
    const search = document.createElement("input");
    search.className = "akurate-hv-search";
    search.placeholder = "Filter by label…";
    search.type = "text";
    const close = document.createElement("button");
    close.className = "akurate-hv-close";
    close.textContent = "Close";
    header.append(title, count, search, close);

    const body = document.createElement("div");
    body.className = "akurate-hv-body";
    const grid = document.createElement("div");
    grid.className = "akurate-hv-grid";
    body.appendChild(grid);

    modal.append(header, body);
    backdrop.appendChild(modal);

    function render(filter) {
        const needle = (filter || "").trim().toLowerCase();
        grid.innerHTML = "";
        let shown = 0;
        for (const entry of entries) {
            if (needle) {
                const hay = (entry.label || "").toLowerCase() + " " + entry.hash_key.toLowerCase();
                if (!hay.includes(needle)) continue;
            }
            grid.appendChild(buildCard(entry));
            shown++;
        }
        if (shown === 0) {
            const empty = document.createElement("div");
            empty.className = "akurate-hv-empty";
            empty.textContent = entries.length === 0
                ? "Vault is empty — save something via Hash Vault (Save API Result) first."
                : "No entries match the filter.";
            grid.appendChild(empty);
        }
        count.textContent = needle ? `${shown} of ${entries.length}` : `${entries.length} entries`;
    }

    function dismiss() {
        backdrop.remove();
        document.removeEventListener("keydown", onKey);
    }
    function onKey(e) {
        if (e.key === "Escape") dismiss();
    }

    close.addEventListener("click", dismiss);
    backdrop.addEventListener("click", (e) => { if (e.target === backdrop) dismiss(); });
    document.addEventListener("keydown", onKey);
    search.addEventListener("input", () => render(search.value));

    document.body.appendChild(backdrop);
    search.focus();
    render("");
}

async function openBrowser() {
    injectStyle();
    try {
        const resp = await fetch(LIST_URL);
        if (!resp.ok) {
            showToast(`Vault list failed: HTTP ${resp.status}`);
            return;
        }
        const data = await resp.json();
        buildModal(data.entries || []);
    } catch (e) {
        showToast(`Vault list failed: ${e.message}`);
    }
}

function renderSidebarTab(el) {
    el.innerHTML = "";
    el.style.padding = "16px";
    el.style.color = "#ddd";
    el.style.fontFamily = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";

    const title = document.createElement("div");
    title.textContent = "Hash Vault";
    title.style.fontSize = "14px";
    title.style.fontWeight = "600";
    title.style.marginBottom = "4px";

    const sub = document.createElement("div");
    sub.textContent = "Loading…";
    sub.style.fontSize = "12px";
    sub.style.color = "#888";
    sub.style.marginBottom = "14px";

    const btn = document.createElement("button");
    btn.textContent = "Open Browser";
    btn.style.cssText = `
        width: 100%; padding: 8px 12px; font-size: 13px;
        background: #2a2a2a; color: #eee; border: 1px solid #3a3a3a;
        border-radius: 4px; cursor: pointer; font-family: inherit;
    `;
    btn.addEventListener("mouseenter", () => { btn.style.borderColor = "#4a90e2"; });
    btn.addEventListener("mouseleave", () => { btn.style.borderColor = "#3a3a3a"; });
    btn.addEventListener("click", openBrowser);

    const hint = document.createElement("div");
    hint.textContent = "Shortcut: Ctrl+Shift+H";
    hint.style.cssText = "font-size: 11px; color: #666; margin-top: 10px; text-align: center;";

    el.append(title, sub, btn, hint);

    // Populate count asynchronously
    fetch(LIST_URL)
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((d) => {
            const n = d.count ?? (d.entries || []).length;
            sub.textContent = `${n} cached ${n === 1 ? "entry" : "entries"}`;
        })
        .catch(() => { sub.textContent = "Vault list unavailable"; });
}

app.registerExtension({
    name: "akurate.HashVaultBrowser",
    commands: [
        {
            id: "akurate.HashVaultBrowser.open",
            label: "AKURATE: Hash Vault Browser",
            icon: "pi pi-database",
            function: openBrowser,
        },
    ],
    keybindings: [
        {
            combo: { key: "H", ctrl: true, shift: true },
            commandId: "akurate.HashVaultBrowser.open",
        },
    ],
    menuCommands: [
        {
            path: ["Extensions"],
            commands: ["akurate.HashVaultBrowser.open"],
        },
    ],
    setup() {
        // Register sidebar tab. Guard in case the frontend is older than
        // extensionManager.registerSidebarTab — fall back to menu-only.
        try {
            if (app.extensionManager?.registerSidebarTab) {
                app.extensionManager.registerSidebarTab({
                    id: "akurate.HashVaultBrowser.tab",
                    icon: "pi pi-database",
                    title: "Hash Vault",
                    tooltip: "AKURATE Hash Vault Browser",
                    type: "custom",
                    render: renderSidebarTab,
                });
            }
        } catch (e) {
            console.warn("[akurate.HashVaultBrowser] sidebar tab registration failed:", e);
        }
    },
});
