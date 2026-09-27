// ============================================================================
// ConnectionsWindow: the single place to manage every provider connection and
// generic API secret that feeds the LLM/TTS/STT model pickers and the HTTP node.
//
// Opens from the Settings button in CommandBar, the command palette "Open
// Connections" action, and the model picker's "Add a connection" row.
//
// Structure: a .scrim + a centered .conn-modal card (reuses .palette + rise
// animation grammar). Two segmented tabs: AI Providers | Secrets. Closes on
// scrim click or Esc.
// ============================================================================
import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "../lib/icons";
import { useEditor } from "../lib/editorContext";

// ── API shapes ──────────────────────────────────────────────────────────────

interface ProviderInfo {
  provider: string;
  connected: boolean;
  envVar: string | null;
}

interface ConnectionsResponse {
  providers: ProviderInfo[];
}

interface SecretInfo {
  name: string;
  source: "env" | "user";
  hasValue: boolean;
}

interface SecretsResponse {
  secrets: SecretInfo[];
}

// ── Provider metadata (display names, brand tints via tokens, modality) ─────

interface ProviderMeta {
  label: string;
  icon: string;
  colorVar: string;
  modality: string;
  isLocal: boolean;
  siteUrl: string;
  keyUrl?: string;
}

const PROVIDER_META: Record<string, ProviderMeta> = {
  ollama: {
    label: "Ollama",
    icon: "hardware-chip-outline",
    colorVar: "--good",
    modality: "LLM · local",
    isLocal: true,
    siteUrl: "https://ollama.com/download",
  },
  anthropic: {
    label: "Anthropic",
    icon: "sparkles-outline",
    colorVar: "--fn-ai",
    modality: "LLM · cloud",
    isLocal: false,
    siteUrl: "https://console.anthropic.com",
    keyUrl: "https://console.anthropic.com/settings/keys",
  },
  elevenlabs: {
    label: "ElevenLabs",
    icon: "musical-notes-outline",
    colorVar: "--t-audio",
    modality: "TTS · cloud",
    isLocal: false,
    siteUrl: "https://elevenlabs.io",
    keyUrl: "https://elevenlabs.io/app/settings/api-keys",
  },
  fish: {
    label: "Fish Audio",
    icon: "musical-note-outline",
    colorVar: "--t-audio",
    modality: "TTS · cloud",
    isLocal: false,
    siteUrl: "https://fish.audio",
    keyUrl: "https://fish.audio/go-api",
  },
  google: {
    label: "Google",
    icon: "globe-outline",
    colorVar: "--info",
    modality: "LLM · cloud",
    isLocal: false,
    siteUrl: "https://aistudio.google.com",
    keyUrl: "https://aistudio.google.com/app/apikey",
  },
  openai: {
    label: "OpenAI",
    icon: "logo-electron",
    colorVar: "--good",
    modality: "LLM + TTS + STT · cloud",
    isLocal: false,
    siteUrl: "https://platform.openai.com",
    keyUrl: "https://platform.openai.com/api-keys",
  },
  xai: {
    label: "xAI",
    icon: "flash-outline",
    colorVar: "--ink-dim",
    modality: "LLM · cloud",
    isLocal: false,
    siteUrl: "https://console.x.ai",
    keyUrl: "https://console.x.ai",
  },
};

// ── Ollama local model info shape ────────────────────────────────────────────

interface OllamaLocalModel {
  name: string;
  size: number;
  modified: string;
}

function fmtBytes(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${bytes} B`;
}

// ── Ollama pull + local library panel ────────────────────────────────────────

interface OllamaPanelProps {
  onRefetch: () => void;
}

function OllamaPanel({ onRefetch: _onRefetch }: OllamaPanelProps) {
  const { models } = useEditor();

  const [localModels, setLocalModels] = useState<OllamaLocalModel[]>([]);
  const [loadingLib, setLoadingLib] = useState(false);
  const [libNonce, setLibNonce] = useState(0);

  // Pull form state
  const [pullName, setPullName] = useState("");
  const [pulling, setPulling] = useState(false);
  const [pullStatus, setPullStatus] = useState<string>("");
  const [pullPercent, setPullPercent] = useState<number | null>(null);
  const [pullMB, setPullMB] = useState<string>("");
  const [pullDone, setPullDone] = useState<"success" | "error" | null>(null);
  const [pullError, setPullError] = useState<string>("");
  const abortRef = useRef<AbortController | null>(null);

  // Fetch local library
  useEffect(() => {
    let alive = true;
    setLoadingLib(true);
    (async () => {
      try {
        const res = await fetch("/api/connections/ollama/models");
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json() as { models: OllamaLocalModel[]; offline?: boolean };
        if (alive) setLocalModels(data.models ?? []);
      } catch {
        // ignore: Ollama offline
      } finally {
        if (alive) setLoadingLib(false);
      }
    })();
    return () => { alive = false; };
  }, [libNonce]);

  const refreshLib = () => setLibNonce((n) => n + 1);

  async function startPull() {
    const name = pullName.trim();
    if (!name || pulling) return;
    setPulling(true);
    setPullStatus("connecting…");
    setPullPercent(null);
    setPullMB("");
    setPullDone(null);
    setPullError("");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch("/api/connections/ollama/pull", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: name }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        throw new Error(`server returned ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try {
            const obj = JSON.parse(trimmed) as {
              status?: string;
              digest?: string;
              total?: number;
              completed?: number;
              error?: string;
            };
            if (obj.status === "error" || obj.error) {
              setPullDone("error");
              setPullError(obj.error ?? obj.status ?? "unknown error");
              setPulling(false);
              abortRef.current = null;
              return;
            }
            if (obj.status === "success") {
              setPullDone("success");
              setPullStatus("done");
              setPullPercent(100);
              setPulling(false);
              abortRef.current = null;
              refreshLib();
              setTimeout(() => {
                setPullDone(null);
                setPullName("");
                setPullStatus("");
                setPullPercent(null);
                setPullMB("");
              }, 2500);
              return;
            }
            if (obj.status) setPullStatus(obj.status);
            if (obj.total && obj.total > 0) {
              const pct = Math.round(((obj.completed ?? 0) / obj.total) * 100);
              setPullPercent(pct);
              const done_mb = ((obj.completed ?? 0) / 1e6).toFixed(0);
              const total_mb = (obj.total / 1e6).toFixed(0);
              setPullMB(`${done_mb} MB / ${total_mb} MB`);
            } else {
              setPullPercent(null);
            }
          } catch {
            // malformed NDJSON line: skip
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setPullStatus("cancelled");
      } else {
        setPullDone("error");
        setPullError(err instanceof Error ? err.message : String(err));
      }
      setPulling(false);
      abortRef.current = null;
    }
  }

  function cancelPull() {
    abortRef.current?.abort();
    abortRef.current = null;
  }

  async function deleteModel(name: string) {
    try {
      await fetch(`/api/connections/ollama/models/${encodeURIComponent(name)}`, { method: "DELETE" });
      refreshLib();
    } catch {
      // ignore
    }
  }

  const isIndeterminate = pulling && pullPercent === null;

  return (
    <div className="ollama-panel">
      {/* Library link */}
      <div className="prov-ollama-note">
        <Icon name="library-outline" />
        <span>
          Library:{" "}
          <a className="conn-link" href="https://ollama.com/library" target="_blank" rel="noopener noreferrer">
            ollama.com/library <Icon name="open-outline" />
          </a>
        </span>
      </div>

      {/* Pull form */}
      <div className="ollama-pull-form">
        <div className="ollama-pull-row">
          <div className="input ollama-pull-input">
            <input
              type="text"
              value={pullName}
              onChange={(e) => setPullName(e.target.value)}
              placeholder="model name, e.g. gemma4:e4b"
              disabled={pulling}
              spellCheck={false}
              onKeyDown={(e) => { if (e.key === "Enter") void startPull(); }}
            />
          </div>
          {pulling ? (
            <button
              type="button"
              className="conn-action-btn"
              onClick={cancelPull}
            >
              Cancel
            </button>
          ) : (
            <button
              type="button"
              className="conn-save-btn"
              disabled={!pullName.trim()}
              onClick={() => void startPull()}
            >
              <Icon name="download-outline" /> Pull
            </button>
          )}
        </div>

        {pulling && (
          <div className="ollama-cancel-note">
            Cancel stops the stream; Ollama keeps the download running.
          </div>
        )}

        {(pulling || pullDone) && (
          <div className="ollama-progress-block">
            <div className="ollama-progress-head">
              <span className="ollama-progress-name">{pullName}</span>
              <span className="ollama-progress-status">{pullStatus}</span>
              {pullPercent !== null && (
                <span className="ollama-progress-pct">{pullPercent}%</span>
              )}
            </div>
            <div className="ollama-bar-track">
              <div
                className={`ollama-bar-fill${isIndeterminate ? " indeterminate" : ""}`}
                style={!isIndeterminate ? { width: `${pullPercent ?? 0}%` } : undefined}
              />
            </div>
            {pullMB && <div className="ollama-progress-mb">{pullMB}</div>}
            {pullDone === "success" && (
              <div className="ollama-success-line">Model pulled successfully.</div>
            )}
            {pullDone === "error" && (
              <div className="insp-problem">{pullError}</div>
            )}
          </div>
        )}
      </div>

      {/* Local library list */}
      {loadingLib && localModels.length === 0 ? (
        <div className="ollama-lib-loading">loading library…</div>
      ) : localModels.length > 0 ? (
        <div className="ollama-lib-list">
          <div className="ollama-lib-head">Locally pulled</div>
          {localModels.map((m) => {
            const inPicker = models.has(`ollama/${m.name}`);
            return (
              <div key={m.name} className="ollama-lib-row">
                <span className="ollama-lib-name">{m.name}</span>
                <span className="ollama-lib-size">{fmtBytes(m.size)}</span>
                <span className={`mp-chip ctx ${inPicker ? "in-picker" : "not-mapped"}`}>
                  {inPicker ? "in picker" : "not mapped"}
                </span>
                <button
                  type="button"
                  className="conn-action-btn danger"
                  title={`Remove ${m.name}`}
                  onClick={() => void deleteModel(m.name)}
                  style={{ marginLeft: "auto" }}
                >
                  <Icon name="trash-outline" />
                </button>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="ollama-lib-empty">No models pulled yet.</div>
      )}
    </div>
  );
}

function providerMeta(provider: string): ProviderMeta {
  return (
    PROVIDER_META[provider.toLowerCase()] ?? {
      label: provider,
      icon: "server-outline",
      colorVar: "--ink-dim",
      modality: "provider",
      isLocal: false,
      siteUrl: "#",
    }
  );
}

// ── Shared eye-toggle password input ────────────────────────────────────────

interface SecretInputProps {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  id?: string;
}

function SecretInput({ value, onChange, placeholder, autoFocus, id }: SecretInputProps) {
  const [reveal, setReveal] = useState(false);
  return (
    <div className="input conn-secret-input">
      <Icon name="lock-closed-outline" style={{ color: "var(--t-secret)", width: 14, height: 14, flex: "none" }} />
      <input
        id={id}
        type={reveal ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? "••••••••"}
        spellCheck={false}
        autoComplete="off"
        autoFocus={autoFocus}
      />
      <button
        type="button"
        className="conn-eye"
        onClick={() => setReveal((r) => !r)}
        tabIndex={-1}
        title={reveal ? "hide" : "show"}
      >
        <Icon name={reveal ? "eye-outline" : "eye-off-outline"} />
      </button>
    </div>
  );
}

// ── Provider card ────────────────────────────────────────────────────────────

interface ProviderCardProps {
  info: ProviderInfo;
  onRefetch: () => void;
}

function ProviderCard({ info, onRefetch }: ProviderCardProps) {
  const meta = providerMeta(info.provider);
  const [keyValue, setKeyValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  const statusLabel = info.connected ? "CONNECTED" : "NOT CONNECTED";
  const statusCls = info.connected ? "power-on" : "power-off";

  async function saveKey() {
    if (!keyValue.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/connections/providers/${encodeURIComponent(info.provider)}/key`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: keyValue.trim() }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(txt || `${res.status}`);
      }
      setKeyValue("");
      onRefetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function removeKey() {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/connections/providers/${encodeURIComponent(info.provider)}/key`, {
        method: "DELETE",
      });
      if (res.status === 409) {
        // managed in server .env: show the message, don't treat as hard error
        const txt = await res.text().catch(() => "");
        setError(txt || "This key is managed in the server .env and cannot be removed here.");
        setConfirmRemove(false);
        setSaving(false);
        return;
      }
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(txt || `${res.status}`);
      }
      setConfirmRemove(false);
      onRefetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  // Determine if key is env-managed (connected but envVar is set, or the server
  // told us source=env). We rely on the connected state here; env detection
  // comes from a 409 on DELETE if the user tries to remove.
  const isOllama = meta.isLocal;

  return (
    <div className="prov-card">
      <div className="prov-card-head">
        <div
          className="prov-brand-tile"
          style={{ background: `color-mix(in srgb, var(${meta.colorVar}) 14%, transparent)`, border: `1px solid color-mix(in srgb, var(${meta.colorVar}) 36%, transparent)` }}
        >
          <Icon name={meta.icon} style={{ color: `var(${meta.colorVar})` }} />
        </div>
        <div className="prov-name-block">
          <span className="prov-name">{meta.label}</span>
          <span className="prov-modality">{meta.modality}</span>
        </div>
        <div className={`prov-status runstate ${statusCls}`} style={{ margin: 0 }}>
          <span className="dot" />
          {statusLabel}
        </div>
      </div>

      <div className="prov-card-body">
        {error && (
          <div className="insp-problem" style={{ marginBottom: 10 }}>
            {error}
          </div>
        )}

        {isOllama ? (
          info.connected ? (
            // Ollama connected: show pull UI + local library
            <OllamaPanel onRefetch={onRefetch} />
          ) : (
            // Ollama not running: show install note
            <div className="prov-ollama-note">
              <Icon name="hardware-chip-outline" />
              <span>
                Local ·{" "}
                <a
                  className="conn-link"
                  href="https://ollama.com/download"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  install at ollama.com/download
                  <Icon name="open-outline" />
                </a>
              </span>
            </div>
          )
        ) : info.connected ? (
          // Connected cloud: summary row + remove
          <div className="prov-connected-row">
            <Icon name="lock-closed-outline" style={{ color: "var(--t-secret)", width: 14, height: 14 }} />
            <span className="prov-key-mask">key set ••••••••</span>
            <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
              {!confirmRemove ? (
                <button
                  type="button"
                  className="conn-action-btn"
                  onClick={() => setConfirmRemove(true)}
                  disabled={saving}
                  title="Remove key"
                >
                  <Icon name="trash-outline" /> Remove
                </button>
              ) : (
                <div className="conn-confirm-row">
                  <span className="conn-confirm-label">Remove key?</span>
                  <button
                    type="button"
                    className="conn-action-btn danger"
                    onClick={() => void removeKey()}
                    disabled={saving}
                  >
                    {saving ? "…" : "Yes, remove"}
                  </button>
                  <button
                    type="button"
                    className="conn-action-btn"
                    onClick={() => setConfirmRemove(false)}
                    disabled={saving}
                  >
                    Cancel
                  </button>
                </div>
              )}
            </div>
          </div>
        ) : (
          // Not connected: add-key form
          <div className="prov-add-key-form">
            <div className="prov-add-key-label">
              {info.envVar ?? "API_KEY"}
            </div>
            <SecretInput
              value={keyValue}
              onChange={setKeyValue}
              placeholder="paste your API key"
            />
            <div className="prov-add-key-footer">
              <span className="prov-add-key-hint">
                Stored server-side. Models appear in the pickers once saved.
              </span>
              {meta.keyUrl && (
                <a
                  className="conn-link"
                  href={meta.keyUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  get a key <Icon name="open-outline" />
                </a>
              )}
            </div>
            <button
              type="button"
              className="db-add-table conn-save-btn"
              onClick={() => void saveKey()}
              disabled={saving || !keyValue.trim()}
            >
              {saving ? <Icon name="sync-outline" /> : <Icon name="save-outline" />}
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Secrets tab ──────────────────────────────────────────────────────────────

interface SecretsTabProps {
  secrets: SecretInfo[];
  onRefetch: () => void;
}

const NAME_RE = /^[A-Z0-9_]+$/;

function SecretsTab({ secrets, onRefetch }: SecretsTabProps) {
  const [showAdd, setShowAdd] = useState(false);
  const [newName, setNewName] = useState("");
  const [newValue, setNewValue] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [opError, setOpError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  function validateName(v: string) {
    if (!v) return "Name is required.";
    if (!NAME_RE.test(v)) return "Only uppercase letters, digits, and underscores.";
    if (secrets.some((s) => s.name === v)) return "A secret with this name already exists.";
    return null;
  }

  async function addSecret() {
    const err = validateName(newName);
    if (err) { setNameError(err); return; }
    setSaving(true);
    setOpError(null);
    try {
      const res = await fetch("/api/secrets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName, value: newValue }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(txt || `${res.status}`);
      }
      setNewName(""); setNewValue(""); setShowAdd(false);
      onRefetch();
    } catch (err) {
      setOpError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function deleteSecret(name: string) {
    setSaving(true);
    setOpError(null);
    try {
      const res = await fetch(`/api/secrets/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(txt || `${res.status}`);
      }
      setConfirmDelete(null);
      onRefetch();
    } catch (err) {
      setOpError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="conn-secrets-body">
      {opError && (
        <div className="insp-problem" style={{ marginBottom: 12 }}>
          {opError}
        </div>
      )}

      {secrets.length === 0 && !showAdd && (
        <div className="insp-empty" style={{ padding: "32px 0", gap: 10 }}>
          <span className="watermark" style={{ width: 40, height: 40 }}>
            <Icon name="lock-closed-outline" style={{ width: 40, height: 40 }} />
          </span>
          <div className="et">No secrets yet</div>
          <div className="eh">add a secret to use it in the HTTP node</div>
        </div>
      )}

      {secrets.map((s) => (
        <div key={s.name} className="secret-row">
          <Icon name="lock-closed-outline" className="secret-lock" />
          <span className="secret-token">
            <span className="secret-brace">{"{{secret."}</span>
            <span className="secret-name">{s.name}</span>
            <span className="secret-brace">{"}}"}</span>
          </span>
          {s.source === "env" && (
            <span className="secret-env-tag">from .env</span>
          )}
          <span className="secret-mask">••••••••</span>
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 4 }}>
            {s.source === "user" && (
              confirmDelete === s.name ? (
                <div className="conn-confirm-row">
                  <span className="conn-confirm-label" style={{ fontSize: "var(--fs-micro)" }}>
                    Nodes using {`{{secret.${s.name}}}`} will fail.
                  </span>
                  <button
                    type="button"
                    className="conn-action-btn danger"
                    onClick={() => void deleteSecret(s.name)}
                    disabled={saving}
                  >
                    {saving ? "…" : "Delete"}
                  </button>
                  <button
                    type="button"
                    className="conn-action-btn"
                    onClick={() => setConfirmDelete(null)}
                    disabled={saving}
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="db-card-ic"
                  style={{ border: "none", background: "none", padding: 0, cursor: "pointer", color: "var(--ink-ghost)" }}
                  onClick={() => setConfirmDelete(s.name)}
                  title={`Delete {{secret.${s.name}}}`}
                >
                  <Icon name="trash-outline" style={{ width: 14, height: 14 }} />
                </button>
              )
            )}
          </div>
        </div>
      ))}

      {showAdd ? (
        <div className="secret-add-form">
          <div className="field">
            <div className="field-lbl">
              <Icon name="lock-closed-outline" className="lockico" />
              Name (uppercase, digits, underscores)
            </div>
            <div className={`input ${nameError ? "conn-input-err" : ""}`}>
              <input
                type="text"
                value={newName}
                onChange={(e) => { setNewName(e.target.value.toUpperCase()); setNameError(null); }}
                placeholder="MY_SECRET"
                autoFocus
                spellCheck={false}
              />
            </div>
            {nameError && <div className="conn-field-error">{nameError}</div>}
          </div>
          <div className="field">
            <div className="field-lbl">Value</div>
            <SecretInput
              value={newValue}
              onChange={setNewValue}
              placeholder="secret value"
            />
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
            <button
              type="button"
              className="db-add-table conn-save-btn"
              onClick={() => void addSecret()}
              disabled={saving || !newName || !newValue}
            >
              {saving ? <Icon name="sync-outline" /> : <Icon name="add-outline" />}
              {saving ? "Saving…" : "Add secret"}
            </button>
            <button
              type="button"
              className="conn-action-btn"
              onClick={() => { setShowAdd(false); setNewName(""); setNewValue(""); setNameError(null); }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          className="db-add-table conn-save-btn"
          style={{ marginTop: secrets.length > 0 ? 14 : 0 }}
          onClick={() => setShowAdd(true)}
        >
          <Icon name="add-outline" /> Add secret
        </button>
      )}
    </div>
  );
}

// ── Main window ──────────────────────────────────────────────────────────────

type Tab = "providers" | "secrets";

interface ConnectionsWindowProps {
  open: boolean;
  onClose: () => void;
}

export function ConnectionsWindow({ open, onClose }: ConnectionsWindowProps) {
  const [tab, setTab] = useState<Tab>("providers");
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [secrets, setSecrets] = useState<SecretInfo[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(false);
  const [loadingSecrets, setLoadingSecrets] = useState(false);
  const [errorProviders, setErrorProviders] = useState<string | null>(null);
  const [errorSecrets, setErrorSecrets] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const refetch = useCallback(() => setNonce((n) => n + 1), []);
  const cardRef = useRef<HTMLDivElement>(null);

  // Close on Esc
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  // Fetch providers
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoadingProviders(true);
    (async () => {
      try {
        const res = await fetch("/api/connections");
        if (!res.ok) throw new Error(`${res.status}`);
        const json = (await res.json()) as ConnectionsResponse;
        if (alive) { setProviders(json.providers ?? []); setErrorProviders(null); }
      } catch (err) {
        if (alive) setErrorProviders(err instanceof Error ? err.message : String(err));
      } finally {
        if (alive) setLoadingProviders(false);
      }
    })();
    return () => { alive = false; };
  }, [open, nonce]);

  // Fetch secrets
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoadingSecrets(true);
    (async () => {
      try {
        const res = await fetch("/api/secrets");
        if (!res.ok) throw new Error(`${res.status}`);
        const json = (await res.json()) as SecretsResponse;
        if (alive) { setSecrets(json.secrets ?? []); setErrorSecrets(null); }
      } catch (err) {
        if (alive) setErrorSecrets(err instanceof Error ? err.message : String(err));
      } finally {
        if (alive) setLoadingSecrets(false);
      }
    })();
    return () => { alive = false; };
  }, [open, nonce]);

  if (!open) return null;

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div
        ref={cardRef}
        className="conn-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Connections"
      >
        {/* Header */}
        <div className="conn-head">
          <div className="conn-head-left">
            <div className="conn-head-tile">
              <Icon name="git-network-outline" />
            </div>
            <div className="conn-head-text">
              <div className="conn-title">CONNECTIONS</div>
              <div className="conn-sub">providers &amp; secrets</div>
            </div>
          </div>

          {/* Segmented tab control */}
          <div className="conn-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              className={`conn-tab ${tab === "providers" ? "active" : ""}`}
              onClick={() => setTab("providers")}
              aria-selected={tab === "providers"}
            >
              <Icon name="server-outline" />
              AI Providers
            </button>
            <button
              type="button"
              role="tab"
              className={`conn-tab ${tab === "secrets" ? "active" : ""}`}
              onClick={() => setTab("secrets")}
              aria-selected={tab === "secrets"}
            >
              <Icon name="lock-closed-outline" />
              Secrets
            </button>
          </div>

          <button type="button" className="pp-close" onClick={onClose} title="Close (Esc)">
            <Icon name="close-outline" />
          </button>
        </div>

        {/* Body */}
        <div className="conn-body">
          {tab === "providers" && (
            <>
              {loadingProviders && providers.length === 0 && (
                <div className="insp-empty" style={{ padding: "40px 0" }}>
                  <div className="eh">loading providers…</div>
                </div>
              )}
              {errorProviders && (
                <div className="insp-problem">{errorProviders}</div>
              )}
              {providers.map((p) => (
                <ProviderCard key={p.provider} info={p} onRefetch={refetch} />
              ))}
              {!loadingProviders && !errorProviders && providers.length === 0 && (
                <div className="insp-empty" style={{ padding: "40px 0" }}>
                  <div className="et">No providers configured</div>
                  <div className="eh">add a provider to the backend</div>
                </div>
              )}
            </>
          )}

          {tab === "secrets" && (
            <>
              {loadingSecrets && secrets.length === 0 && (
                <div className="insp-empty" style={{ padding: "40px 0" }}>
                  <div className="eh">loading secrets…</div>
                </div>
              )}
              {errorSecrets && (
                <div className="insp-problem">{errorSecrets}</div>
              )}
              <SecretsTab secrets={secrets} onRefetch={refetch} />
            </>
          )}
        </div>
      </div>
    </>
  );
}
