import { useCallback, useEffect, useMemo, useState } from "react";
import { getHealth, getProviders, sendChat } from "./api.js";
import Composer from "./components/Composer.jsx";
import Transcript from "./components/Transcript.jsx";

const toWire = ({ role, content, tool_calls = [], extras = {} }) => ({ role, content, tool_calls, extras });

export default function App() {
  const [providers, setProviders] = useState([]);
  const [version, setVersion] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [providerId, setProviderId] = useState(null);
  const [models, setModels] = useState({});
  const [system, setSystem] = useState("");
  const [temperature, setTemperature] = useState(null);
  const [section, setSection] = useState("chat");
  const [turns, setTurns] = useState([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([getProviders(), getHealth()]).then(([list, health]) => {
      setProviders(list);
      setVersion(health.version);
      setProviderId((current) => current ?? list.find((p) => p.configured)?.id ?? list[0]?.id);
    }).catch((err) => setLoadError(err.message));
  }, []);

  const provider = useMemo(() => providers.find((p) => p.id === providerId) ?? null, [providers, providerId]);
  const model = provider ? (models[provider.id] ?? provider.model ?? "") : "";
  const setModel = (value) => provider && setModels((current) => ({ ...current, [provider.id]: value }));

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || !provider || pending) return;
    setError(null);
    setPending({ text, providerId: provider.id });
    const userTurn = { role: "user", content: text };
    try {
      const result = await sendChat({
        messages: [...turns, userTurn].map(toWire), provider: provider.id,
        model: model.trim() || null, system: system.trim() || null, temperature,
      });
      setTurns((current) => [...current, userTurn, {
        role: "assistant", content: result.text ?? "", tool_calls: result.tool_calls ?? [],
        extras: result.extras ?? {}, meta: { provider: result.provider, model: result.model, tokens: result.usage?.total_tokens ?? null },
      }]);
      setDraft("");
    } catch (err) {
      setError({ message: err.message, kind: err.kind, providerId: provider.id });
    } finally { setPending(null); }
  }, [draft, provider, pending, turns, model, system, temperature]);

  const clear = () => { setTurns([]); setError(null); };
  const blocked = provider && !provider.configured;

  return <div className="app-shell">
    <header className="global-header">
      <div className="brand-lockup" aria-label="CPA version 11">
        <span className="brand-mark">C</span><span className="brand-copy"><strong>CPA</strong><small>PROJECT ASSISTANT</small></span>
      </div>
      <nav className="top-nav" aria-label="Main navigation">
        <button type="button" className={`nav-item ${section === "chat" ? "active" : ""}`} aria-current={section === "chat" ? "page" : undefined} onClick={() => setSection("chat")}><span className="nav-glyph">◈</span> Chat</button>
        <button type="button" className={`nav-item ${section === "settings" ? "active" : ""}`} aria-current={section === "settings" ? "page" : undefined} onClick={() => setSection("settings")}><span className="nav-glyph">⚙</span> Settings</button>
      </nav>
      <div className="header-status"><span className={`status-indicator ${loadError ? "offline" : version ? "online" : "checking"}`} /><span>{loadError ? "API offline" : version ? "API connected" : "Connecting"}</span><span className="version-chip">v{version ?? "11"}</span></div>
    </header>

    {section === "chat" ? <main className="stage chat-stage">
      <header className="stage-head"><div><h1>Chat workspace</h1><p className="stage-subtitle">{provider ? `${provider.label} · ${model || "Default model"}` : "Connect to the API to begin"}</p></div><div className="stage-actions"><button type="button" className="quiet" onClick={() => setSection("settings")}>Settings</button><button type="button" className="quiet" onClick={clear} disabled={!turns.length}>Clear chat</button></div></header>
      {loadError && <div className="global-error" role="alert">{loadError}</div>}
      <Transcript turns={turns} pending={pending} error={error} providers={providers} loadError={loadError} />
      <Composer value={draft} onChange={setDraft} onSend={send} busy={Boolean(pending)} disabledReason={blocked ? `${provider.label} isn't set up. Add ${provider.missing.join(", ")} to .env.` : null} providerLabel={provider?.label} />
    </main> : <main className="settings-stage">
      <header className="settings-heading"><div><div className="eyebrow">PREFERENCES</div><h1>Settings</h1><p>Configure the model and response behaviour used by chat.</p></div><span className="settings-version">CPA v{version ?? "11"}</span></header>
      {loadError && <div className="global-error" role="alert">{loadError}</div>}
      <section className="settings-card">
        <div className="settings-card-heading"><div className="settings-icon">✦</div><div><h2>Model configuration</h2><p>Choose a provider and model for your next message.</p></div></div>
        <div className="settings-grid">
          <label className="setting-field"><span>Provider</span><select aria-label="Provider" value={providerId ?? ""} onChange={(e) => { setProviderId(e.target.value); setError(null); }}>
            {providers.map((p) => <option key={p.id} value={p.id}>{p.label}{p.configured ? "" : " · Not configured"}</option>)}
          </select><small>Provider credentials are configured in the backend environment.</small></label>
          <label className="setting-field"><span>{provider?.model_label || "Model"}</span><input aria-label={provider?.model_label || "Model"} value={model} onChange={(e) => setModel(e.target.value)} spellCheck="false" autoComplete="off" placeholder="Enter a model or deployment name"/><small>Overrides the provider's default model for this session.</small></label>
        </div>
        {provider && !provider.configured && <div className="settings-notice"><span className="notice-dot" />{provider.label} needs configuration: {provider.missing.join(", ")}</div>}
      </section>
      <section className="settings-card">
        <div className="settings-card-heading"><div className="settings-icon">☷</div><div><h2>Response behaviour</h2><p>Optional instructions and sampling controls for chat.</p></div></div>
        <label className="setting-field full-field"><span>System prompt</span><textarea rows={5} value={system} onChange={(e) => setSystem(e.target.value)} placeholder="Optional instructions sent before the conversation…"/><small>Applied to each chat request. Leave blank to use the provider default.</small></label>
        <div className="temperature-setting"><label className="switch-label"><span><strong>Set temperature</strong><small>Control response variability instead of using the provider default.</small></span><input type="checkbox" checked={temperature !== null} onChange={(e) => setTemperature(e.target.checked ? 1 : null)}/></label>
          {temperature !== null && <label className="temperature-range"><span>Temperature</span><input aria-label="Temperature" type="range" min="0" max="2" step="0.1" value={temperature} onChange={(e) => setTemperature(Number(e.target.value))}/><output>{temperature.toFixed(1)}</output><span className="range-minmax"><span>Precise</span><span>Creative</span></span></label>}
        </div>
      </section>
      <div className="settings-footer"><span>Changes apply to new messages in this browser session.</span><button type="button" className="primary" onClick={() => setSection("chat")}>Done · Back to chat</button></div>
    </main>}
  </div>;
}
