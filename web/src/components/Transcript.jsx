import { useEffect, useRef } from "react";
import Markdown from "react-markdown";

function Reply({ turn, providers }) {
  const { provider, model, tokens } = turn.meta;
  const label = providers.find((p) => p.id === provider)?.label ?? provider;
  return (
    <article className="turn reply" data-provider={provider}>
      <header className="turn-head">
        <span className="who">{label}</span>
        <span className="detail">{model}</span>
        {tokens != null && <span className="detail tokens">{tokens} tokens</span>}
      </header>
      <div className="prose">
        {turn.content ? (
          <Markdown>{turn.content}</Markdown>
        ) : (
          <p className="muted">The model asked to call a tool. Tools aren't enabled in this client yet.</p>
        )}
      </div>
    </article>
  );
}

function YourTurn({ text }) {
  return (
    <article className="turn mine">
      <header className="turn-head">
        <span className="who">You</span>
      </header>
      <div className="prose">
        <p>{text}</p>
      </div>
    </article>
  );
}

function Failure({ error, providers }) {
  const provider = providers.find((p) => p.id === error.providerId);
  const title = error.kind === "unreachable" ? "The API isn't running" : "Message not sent";
  return (
    <div className="failure" role="alert">
      <strong>{title}</strong>
      <p>{error.message}</p>
      {error.kind === "setup" && provider && (
        <p>
          Check the {provider.label} entries in your .env file, then send the message again. It is
          still in the box below.
        </p>
      )}
    </div>
  );
}

export default function Transcript({ turns, pending, error, providers, loadError }) {
  const end = useRef(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [turns, pending, error]);

  const empty = turns.length === 0 && !pending;
  const waitingOn = pending && providers.find((p) => p.id === pending.providerId)?.label;

  return (
    <div className="transcript" role="log" aria-live="polite" aria-label="Conversation">
      <div className="thread">
        {empty && !loadError && (
          <div className="empty">
            <h2>Start a conversation</h2>
            <p>
              Pick a provider on the left and send a message. Each reply keeps the name of the
              provider that wrote it, so you can switch partway through and continue the same
              conversation with another.
            </p>
          </div>
        )}
        {turns.map((turn, i) =>
          turn.role === "assistant" ? (
            <Reply key={i} turn={turn} providers={providers} />
          ) : (
            <YourTurn key={i} text={turn.content} />
          ),
        )}
        {pending && (
          <>
            <YourTurn text={pending.text} />
            <p className="waiting">Waiting for {waitingOn}</p>
          </>
        )}
        {error && <Failure error={error} providers={providers} />}
        <div ref={end} />
      </div>
    </div>
  );
}
