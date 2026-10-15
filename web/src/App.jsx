import { useCallback, useEffect, useMemo, useState } from 'react';
import { getHealth, getProviders, sendChat } from './api.js';
import Composer from './components/Composer.jsx';
import Transcript from './components/Transcript.jsx';

import {
  MessageCircle,
  Folder,
  Brain,
  ClipboardList,
  History,
  Settings as SettingsIcon,
} from 'lucide-react';
const toWire = ({ role, content, tool_calls = [], extras = {} }) => ({
  role,
  content,
  tool_calls,
  extras,
});
// const navItems = [
//   ['chat', 'chat', 'Chat'],
//   ['files', '▱', 'Files'],
//   ['cognition', '♧', 'Cognition'],
//   ['review', '▤', 'Review'],
//   ['history', '◴', 'History'],
//   ['settings', '⚙', 'Settings'],
// ];
const navItems = [
  ['chat', MessageCircle, 'Chat'],
  ['files', Folder, 'Files'],
  ['cognition', Brain, 'Cognition'],
  ['review', ClipboardList, 'Review'],
  ['history', History, 'History'],
  ['settings', SettingsIcon, 'Settings'],
];
export default function App() {
  const [providers, setProviders] = useState([]);
  const [version, setVersion] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [providerId, setProviderId] = useState(null);
  const [models, setModels] = useState({});
  const [system, setSystem] = useState('');
  const [temperature, setTemperature] = useState(null);
  const [section, setSection] = useState('chat');
  const [chats, setChats] = useState([{ id: 1, title: 'New chat', turns: [] }]);
  const [activeChatId, setActiveChatId] = useState(1);
  const [nextChatId, setNextChatId] = useState(2);
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);
  const [preview, setPreview] = useState(false);
  const [execution, setExecution] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [menuChatId, setMenuChatId] = useState(null);
  const [renameChatId, setRenameChatId] = useState(null);
  const [renameDraft, setRenameDraft] = useState('');
  const activeChat = chats.find((chat) => chat.id === activeChatId) ?? chats[0];
  const turns = activeChat?.turns ?? [];
  const setTurns = (updater) =>
    setChats((current) =>
      current.map((chat) =>
        chat.id === activeChatId
          ? {
              ...chat,
              turns:
                typeof updater === 'function' ? updater(chat.turns) : updater,
            }
          : chat
      )
    );
  useEffect(() => {
    Promise.all([getProviders(), getHealth()])
      .then(([list, health]) => {
        setProviders(list);
        setVersion(health.version);
        setProviderId(
          (current) =>
            current ??
            (
              list.find((p) => p.default) ??
              list.find((p) => p.configured) ??
              list[0]
            )?.id
        );
      })
      .catch((err) => setLoadError(err.message));
  }, []);
  const provider = useMemo(
    () => providers.find((p) => p.id === providerId) ?? null,
    [providers, providerId]
  );
  const model = provider ? (models[provider.id] ?? provider.model ?? '') : '';
  const setModel = (value) =>
    provider && setModels((current) => ({ ...current, [provider.id]: value }));
  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || !provider || pending) return;
    setError(null);
    setPending({ text, providerId: provider.id });
    const userTurn = { role: 'user', content: text };
    try {
      const result = await sendChat({
        messages: [...turns, userTurn].map(toWire),
        provider: provider.id,
        model: model.trim() || null,
        system: system.trim() || null,
        temperature,
      });
      setTurns((current) => [
        ...current,
        userTurn,
        {
          role: 'assistant',
          content: result.text ?? '',
          tool_calls: result.tool_calls ?? [],
          extras: result.extras ?? {},
          meta: {
            provider: result.provider,
            model: result.model,
            totalTokens: result.usage?.total_tokens ?? null,
            promptTokens: result.usage?.prompt_tokens ?? null,
            completionTokens: result.usage?.completion_tokens ?? null,
          },
        },
      ]);
      setChats((current) =>
        current.map((chat) =>
          chat.id === activeChatId && chat.title === 'New chat'
            ? { ...chat, title: text.replace(/\s+/g, ' ').slice(0, 30) }
            : chat
        )
      );
      setDraft('');
    } catch (err) {
      setError({
        message: err.message,
        kind: err.kind,
        providerId: provider.id,
      });
    } finally {
      setPending(null);
    }
  }, [
    draft,
    provider,
    pending,
    turns,
    model,
    system,
    temperature,
    activeChatId,
  ]);
  const createChat = () => {
    const id = nextChatId;
    setNextChatId(id + 1);
    setChats((current) => [...current, { id, title: 'New chat', turns: [] }]);
    setActiveChatId(id);
    setDraft('');
    setError(null);
    setPending(null);
    setMenuChatId(null);
  };
  const deleteChat = (id) => {
    setChats((current) => {
      const remaining = current.filter((chat) => chat.id !== id);
      if (!remaining.length) {
        const replacement = { id: nextChatId, title: 'New chat', turns: [] };
        setNextChatId((n) => n + 1);
        setActiveChatId(replacement.id);
        return [replacement];
      }
      if (activeChatId === id) setActiveChatId(remaining[0].id);
      return remaining;
    });
    setMenuChatId(null);
    setError(null);
  };
  const saveRename = (id) => {
    const title = renameDraft.trim();
    if (title)
      setChats((current) =>
        current.map((chat) => (chat.id === id ? { ...chat, title } : chat))
      );
    setRenameChatId(null);
    setMenuChatId(null);
  };

  return (
    <div className="app-shell">
      <header className="global-header">
        <div className="product-brand">
          <span className="brand-mark"><Brain size={20} /></span>
          <span className="brand-copy">
            <strong>Cognitive Persistence</strong>
            <small>Persistent project cognition</small>
          </span>
        </div>
        <nav className="icon-nav" aria-label="Main navigation">
          {navItems.map(([id, icon, label]) => (
            <button
              key={id}
              title={label}
              aria-label={label}
              className={`icon-nav-item ${section === id ? 'active' : ''}`}
              onClick={() => setSection(id)}
            >
              <Icon size={16} strokeWidth={1.8} />
            </button>
          ))}
        </nav>
        <div className="header-right">
          <select aria-label="Project" defaultValue="n">
            <option value="n">n</option>
          </select>
          <button className="icon-button" title="More options">
            •••
          </button>
          <button className="user-chip">
            <span>T</span> test <span className="logout">⇥</span>
          </button>
        </div>
      </header>
      <div className="toolbar">
        <button
          className="sidebar-toggle"
          type="button"
          onClick={() => setSidebarOpen((open) => !open)}
          aria-expanded={sidebarOpen}
          aria-label={
            sidebarOpen ? 'Collapse chat sidebar' : 'Expand chat sidebar'
          }
          title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
        >
          ☰
        </button>
        <div className="toolbar-options">
          <div className="project-stats">
            <i /> <span>0 files</span>
            <span>0 entities</span>
          </div>
        </div>
      </div>
      {section === 'settings' ? (
        <main className="settings-stage">
          <div className="settings-tabs">
            <button className="selected">⚙ Settings</button>
            <button onClick={() => {}}>♙ Providers</button>
            <button>ⓘ About</button>
            <button>☷ Logs</button>
          </div>
          {loadError && (
            <div className="global-error" role="alert">
              {loadError}
            </div>
          )}
          <section className="settings-card">
            <div className="settings-card-heading">
              <span className="settings-icon">⬡</span>
              <div>
                <h2>Model configuration</h2>
              </div>
            </div>
            <div className="settings-fields">
              <label className="setting-field">
                <span>Provider</span>
                <select
                  aria-label="Provider"
                  value={providerId ?? ''}
                  onChange={(e) => {
                    setProviderId(e.target.value);
                    setError(null);
                  }}
                >
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                      {p.configured ? '' : ' · Not configured'}
                    </option>
                  ))}
                </select>
              </label>
              <label className="setting-field">
                <span>{provider?.model_label ?? 'Model'}</span>
                <input
                  aria-label={provider?.model_label ?? 'Model'}
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder={provider?.model ?? ''}
                />
                <small>Leave empty to use the provider's default.</small>
              </label>
              {provider && !provider.configured && (
                <div className="settings-notice">
                  {provider.label} needs configuration:{' '}
                  {provider.missing.join(', ')}
                </div>
              )}
            </div>
          </section>
          <section className="settings-card">
            <div className="settings-card-heading">
              <span className="settings-icon">☷</span>
              <div>
                <h2>Response behaviour</h2>
              </div>
            </div>
            <label className="setting-field">
              <span>System prompt</span>
              <textarea
                rows={3}
                value={system}
                onChange={(e) => setSystem(e.target.value)}
                placeholder="You are a helpful AI assistant."
              />
              <small>This prompt is sent with every request.</small>
            </label>

            <label className="toggle-label">
              <input
                type="checkbox"
                checked={temperature !== null}
                onChange={(e) => setTemperature(e.target.checked ? 1 : null)}
              />
              <span>Set temperature</span>
            </label>

            {temperature !== null && (
              <div className="temp-row">
                <label htmlFor="temp">Temperature</label>
                <input
                  id="temp"
                  type="range"
                  min="0"
                  max="2"
                  step="0.1"
                  value={temperature}
                  onChange={(e) => setTemperature(Number(e.target.value))}
                />
                <output>{temperature.toFixed(1)}</output>
              </div>
            )}

            <small className="helper">
              Lower values are more deterministic. Higher values are more
              creative.
            </small>
          </section>
          <section className="settings-card">
            <div className="settings-card-heading">
              <span className="settings-icon">▣</span>
              <div>
                <h2>Interface</h2>
              </div>
            </div>
            <label className="setting-field">
              <span>Theme</span>
              <select defaultValue="dark">
                <option value="dark">Dark (default)</option>
              </select>
            </label>
          </section>
          {version && <small className="helper">CPA v{version}</small>}
        </main>
      ) : (
        <main
          className={`workspace ${sidebarOpen && section === 'chat' ? 'with-chat-sidebar' : ''}`}
        >
          {section === 'chat' && sidebarOpen && (
            <aside className="chat-sidebar" aria-label="Chat controls">
              <div className="sidebar-section">
                <h2>Agent</h2>
                <select aria-label="Agent" defaultValue="default">
                  <option value="default">Default agent</option>
                </select>
              </div>
              <div className="sidebar-section sidebar-toggles">
                <h2>Options</h2>
                <label className="toggle-label">
                  <input
                    type="checkbox"
                    checked={preview}
                    onChange={(e) => setPreview(e.target.checked)}
                  />
                  <span>Show preview</span>
                </label>
                <label className="toggle-label">
                  <input
                    type="checkbox"
                    checked={execution}
                    onChange={(e) => setExecution(e.target.checked)}
                  />
                  <span>Show execution</span>
                </label>
              </div>
              <div className="sidebar-chat-heading">
                <h2>Chats</h2>
                <button className="action-button" onClick={createChat}>
                  ＋ New
                </button>
              </div>
              <div className="chat-list">
                {chats.map((chat) => (
                  <div
                    className={`chat-list-item ${chat.id === activeChatId ? 'active' : ''}`}
                    key={chat.id}
                  >
                    <button
                      className="chat-select"
                      onClick={() => {
                        setActiveChatId(chat.id);
                        setError(null);
                        setMenuChatId(null);
                      }}
                      title={chat.title}
                    >
                      {renameChatId === chat.id ? (
                        <input
                          autoFocus
                          value={renameDraft}
                          aria-label="Rename chat"
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => setRenameDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') saveRename(chat.id);
                            if (e.key === 'Escape') setRenameChatId(null);
                          }}
                          onBlur={() => saveRename(chat.id)}
                        />
                      ) : (
                        <span>{chat.title}</span>
                      )}
                    </button>
                    <button
                      className="chat-menu-trigger"
                      aria-label={`Menu for ${chat.title}`}
                      title="Chat options"
                      onClick={() =>
                        setMenuChatId(menuChatId === chat.id ? null : chat.id)
                      }
                    >
                      ⋯
                    </button>
                    {menuChatId === chat.id && (
                      <div className="chat-context-menu">
                        <button
                          onClick={() => {
                            setRenameChatId(chat.id);
                            setRenameDraft(chat.title);
                            setMenuChatId(null);
                          }}
                        >
                          Rename
                        </button>
                        <button
                          className="danger-text"
                          onClick={() => deleteChat(chat.id)}
                        >
                          Delete
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </aside>
          )}
          {section === 'chat' ? (
            <>
              <div className="chat-main">
                <div className="chat-panel">
                  {loadError && (
                    <div className="global-error" role="alert">
                      {loadError}
                    </div>
                  )}
                  {turns.length === 0 && !pending && !error ? (
                    <div className="task-card">
                      <span className="task-bolt">ϟ</span>
                      <div>
                        <h2>Start a task.</h2>
                        <p>
                          You can ask the agent to create, inspect, edit, move,
                          copy, or delete project files without uploading
                          anything first.
                        </p>
                      </div>
                    </div>
                  ) : (
                    <Transcript
                      turns={turns}
                      pending={pending}
                      error={error}
                      providers={providers}
                    />
                  )}
                </div>
                <Composer
                  value={draft}
                  onChange={setDraft}
                  onSend={send}
                  busy={Boolean(pending)}
                  disabledReason={
                    provider && !provider.configured
                      ? `${provider.label} isn't set up. Add ${provider.missing.join(', ')} to .env.`
                      : null
                  }
                  providerLabel={provider?.label}
                />
              </div>
            </>
          ) : (
            <div className="chat-panel placeholder-panel">
              <div className="task-card">
                <span className="task-bolt">✧</span>
                <div>
                  <h2>
                    {navItems.find((item) => item[0] === section)?.[2] ||
                      'Coming soon'}
                  </h2>
                  <p>
                    This area is a placeholder for a future v11 feature.
                    Existing chat and model settings remain available.
                  </p>
                </div>
              </div>
            </div>
          )}
        </main>
      )}
    </div>
  );
}
