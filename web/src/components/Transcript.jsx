import { useEffect, useRef, useState } from 'react';
import Markdown from 'react-markdown';

function Reply({ turn, providers }) {
  const { provider, model, totalTokens, promptTokens, completionTokens } = turn.meta ?? {};
  const [showInfo, setShowInfo] = useState(false);
  const label = providers.find((p) => p.id === provider)?.label ?? provider;
  return (
    <article className="turn reply" data-provider={provider}>
      <header className="turn-head">
        <span className="who">CPA</span>
        <button
          type="button"
          className="turn-info"
          aria-label="Response model and token information"
          aria-expanded={showInfo}
          onClick={() => setShowInfo((value) => !value)}
          title="Show model and token usage"
        >
          ⓘ
        </button>
        {showInfo && (
          <div className="turn-metadata" role="status">
            <span>Provider: {label || 'Unknown'}</span>
            <span>Model: {model || 'Unknown'}</span>
            <span>Input Tokens: {promptTokens ?? 'Unavailable'}</span>
            <span>Output Tokens: {completionTokens ?? 'Unavailable'}</span>
            <span>Total Tokens: {totalTokens ?? 'Unavailable'}</span>
          </div>
        )}
      </header>
      <div className="prose">
        {turn.content ? (
          <Markdown>{turn.content}</Markdown>
        ) : (
          <p className="muted">
            The model asked to call a tool. Tools aren't enabled in this client
            yet.
          </p>
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
  const title =
    error.kind === 'unreachable' ? "The API isn't running" : 'Message not sent';
  return (
    <div className="failure" role="alert">
      <strong>{title}</strong>
      <p>{error.message}</p>
      {error.kind === 'setup' && provider && (
        <p>
          Check the {provider.label} entries in your .env file, then send the
          message again. It is still in the box below.
        </p>
      )}
    </div>
  );
}

export default function Transcript({
  turns,
  pending,
  error,
  providers,
}) {
  const end = useRef(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' });
  }, [turns, pending, error]);

  const waitingOn =
    pending && providers.find((p) => p.id === pending.providerId)?.label;

  return (
    <div
      className="transcript"
      role="log"
      aria-live="polite"
      aria-label="Conversation"
    >
      <div className="thread">
        {turns.map((turn, i) =>
          turn.role === 'assistant' ? (
            <Reply key={i} turn={turn} providers={providers} />
          ) : (
            <YourTurn key={i} text={turn.content} />
          )
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
