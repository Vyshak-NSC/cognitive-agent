import { useCallback, useEffect, useMemo, useState } from "react";
import { getHealth, getProviders, sendChat } from "./api.js";
import Composer from "./components/Composer.jsx";
import Rail from "./components/Rail.jsx";
import Transcript from "./components/Transcript.jsx";

// A turn is {role, content, tool_calls, extras, meta}. `extras` is provider-specific data
// (Gemini thought signatures, OpenRouter reasoning) that must go back to the API unchanged.
const toWire = ({ role, content, tool_calls = [], extras = {} }) => ({
  role,
  content,
  tool_calls,
  extras,
});

export default function App() {
  const [providers, setProviders] = useState([]);
  const [version, setVersion] = useState(null);
  const [loadError, setLoadError] = useState(null);

  const [providerId, setProviderId] = useState(null);
  const [models, setModels] = useState({}); // provider id -> model typed by the user
  const [system, setSystem] = useState("");
  const [temperature, setTemperature] = useState(null); // null = provider default

  const [turns, setTurns] = useState([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(null); // {text, providerId} while waiting
  const [error, setError] = useState(null);
  const [railOpen, setRailOpen] = useState(false);

  useEffect(() => {
    Promise.all([getProviders(), getHealth()])
      .then(([list, health]) => {
        setProviders(list);
        setVersion(health.version);
        setProviderId((current) => current ?? list.find((p) => p.configured)?.id ?? list[0]?.id);
      })
      .catch((err) => setLoadError(err.message));
  }, []);

  const provider = useMemo(
    () => providers.find((p) => p.id === providerId) ?? null,
    [providers, providerId],
  );
  const model = provider ? (models[provider.id] ?? provider.model) : "";

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || !provider || pending) return;
    setError(null);
    setPending({ text, providerId: provider.id });
    const userTurn = { role: "user", content: text };
    try {
      const result = await sendChat({
        messages: [...turns, userTurn].map(toWire),
        provider: provider.id,
        model: model.trim() || null,
        system: system.trim() || null,
        temperature,
      });
      const assistantTurn = {
        role: "assistant",
        content: result.text ?? "",
        tool_calls: result.tool_calls ?? [],
        extras: result.extras ?? {},
        meta: {
          provider: result.provider,
          model: result.model,
          tokens: result.usage?.total_tokens ?? null,
        },
      };
      setTurns((current) => [...current, userTurn, assistantTurn]);
      setDraft("");
    } catch (err) {
      setError({ message: err.message, kind: err.kind, providerId: provider.id });
    } finally {
      setPending(null);
    }
  }, [draft, provider, pending, turns, model, system, temperature]);

  const clear = () => {
    setTurns([]);
    setError(null);
  };

  const blocked = provider && !provider.configured;

  return (
    <div className="app">
      <Rail
        providers={providers}
        provider={provider}
        onProvider={(id) => {
          setProviderId(id);
          setError(null);
        }}
        model={model}
        onModel={(value) => provider && setModels((m) => ({ ...m, [provider.id]: value }))}
        system={system}
        onSystem={setSystem}
        temperature={temperature}
        onTemperature={setTemperature}
        onClear={clear}
        canClear={turns.length > 0}
        version={version}
        loadError={loadError}
        open={railOpen}
      />
      <main className="stage">
        <header className="stage-head">
          <h1>CPA</h1>
          <button
            type="button"
            className="quiet rail-toggle"
            aria-expanded={railOpen}
            onClick={() => setRailOpen((open) => !open)}
          >
            {railOpen ? "Hide settings" : "Settings"}
          </button>
        </header>
        <Transcript
          turns={turns}
          pending={pending}
          error={error}
          providers={providers}
          loadError={loadError}
        />
        <Composer
          value={draft}
          onChange={setDraft}
          onSend={send}
          busy={Boolean(pending)}
          disabledReason={
            blocked ? `${provider.label} isn't set up. Add ${provider.missing.join(", ")} to .env.` : null
          }
          providerLabel={provider?.label}
        />
      </main>
    </div>
  );
}
