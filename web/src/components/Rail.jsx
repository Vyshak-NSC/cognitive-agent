export default function Rail({
  providers,
  provider,
  onProvider,
  model,
  onModel,
  system,
  onSystem,
  temperature,
  onTemperature,
  onClear,
  canClear,
  version,
  loadError,
  open,
}) {
  return (
    <aside className="rail" data-open={open} aria-label="Provider settings">
      <section>
        <h2>Provider</h2>
        {loadError && <p className="notice">{loadError}</p>}
        <ul className="providers">
          {providers.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className="provider"
                data-provider={p.id}
                aria-pressed={p.id === provider?.id}
                onClick={() => onProvider(p.id)}
              >
                <span className="dot" data-ready={p.configured} aria-hidden="true" />
                <span className="provider-text">
                  <span className="provider-name">{p.label}</span>
                  <span className="provider-state">
                    {p.configured ? p.model : `Add ${p.missing.join(", ")}`}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      {provider && (
        <section className="fields">
          <label>
            <span>{provider.model_label}</span>
            <input
              value={model}
              onChange={(e) => onModel(e.target.value)}
              spellCheck={false}
              autoComplete="off"
            />
          </label>
          <label>
            <span>System prompt</span>
            <textarea
              rows={4}
              value={system}
              onChange={(e) => onSystem(e.target.value)}
              placeholder="Optional. Sent before every conversation."
            />
          </label>
          <div className="temperature">
            <label className="check">
              <input
                type="checkbox"
                checked={temperature !== null}
                onChange={(e) => onTemperature(e.target.checked ? 1 : null)}
              />
              <span>Set temperature</span>
            </label>
            {temperature !== null && (
              <label className="slider">
                <span className="sr-only">Temperature</span>
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.1"
                  value={temperature}
                  onChange={(e) => onTemperature(Number(e.target.value))}
                />
                <output>{temperature.toFixed(1)}</output>
              </label>
            )}
          </div>
        </section>
      )}

      <footer className="rail-foot">
        <button type="button" className="quiet" onClick={onClear} disabled={!canClear}>
          Clear conversation
        </button>
        {version && <span className="version">Version {version}</span>}
      </footer>
    </aside>
  );
}
