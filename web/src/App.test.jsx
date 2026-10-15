import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App.jsx';

const PROVIDERS = [
  {
    id: 'gemini',
    label: 'Gemini',
    model: 'gemini-x',
    model_label: 'Model',
    configured: true,
    missing: [],
  },
  {
    id: 'openai',
    label: 'OpenAI (GPT)',
    model: 'gpt-x',
    model_label: 'Model',
    configured: false,
    missing: ['OPENAI_API_KEY'],
  },
  {
    id: 'azure_openai',
    label: 'Azure OpenAI',
    model: 'dep',
    model_label: 'Deployment name',
    configured: true,
    missing: [],
  },
];

const json = (status, body) =>
  Promise.resolve({
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  });

const reply = (over = {}) => ({
  provider: 'gemini',
  model: 'gemini-served',
  text: 'Hello from the model',
  tool_calls: [],
  usage: { prompt_tokens: 7, completion_tokens: 5, total_tokens: 12 },
  extras: {},
  ...over,
});

let chatCalls;
let chatHandler;

beforeEach(() => {
  chatCalls = [];
  chatHandler = () => json(200, reply());
  globalThis.fetch = vi.fn((url, options) => {
    if (url === '/api/health')
      return json(200, { status: 'ok', version: '11.0.3' });
    if (url === '/api/providers') return json(200, PROVIDERS);
    if (url === '/api/chat') {
      const body = JSON.parse(options.body);
      chatCalls.push(body);
      return chatHandler(body);
    }
    return json(404, {});
  });
});

async function ready() {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByText('Start a task.');
  // The composer names the provider once the list has loaded.
  await screen.findByPlaceholderText('Message Gemini');
  return user;
}

const send = async (user, text) => {
  await user.type(screen.getByLabelText('Message'), `${text}{Enter}`);
};

const openSettings = (user) =>
  user.click(screen.getByRole('button', { name: 'Settings' }));
const backToChat = (user) =>
  user.click(screen.getByRole('button', { name: 'Chat' }));
const pickProvider = async (user, id) => {
  await openSettings(user);
  await user.selectOptions(
    screen.getByRole('combobox', { name: 'Provider' }),
    id
  );
  await backToChat(user);
};

describe('settings', () => {
  it('lists providers and marks the ones that are not set up', async () => {
    const user = await ready();
    await openSettings(user);
    const select = screen.getByRole('combobox', { name: 'Provider' });
    expect(select).toHaveValue('gemini'); // first set-up provider is chosen
    expect(
      within(select).getByRole('option', {
        name: /OpenAI \(GPT\).*Not configured/,
      })
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Model')).toHaveValue('gemini-x');
  });

  it('names the missing variable and blocks sending for a provider that is not set up', async () => {
    const user = await ready();
    await openSettings(user);
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Provider' }),
      'openai'
    );
    expect(
      screen.getByText(/needs configuration: OPENAI_API_KEY/)
    ).toBeInTheDocument();
    await backToChat(user);
    await user.type(screen.getByLabelText('Message'), 'hi');
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
    expect(
      screen.getByText(/isn't set up\. Add OPENAI_API_KEY/)
    ).toBeInTheDocument();
  });

  it('remembers a different model for each provider', async () => {
    const user = await ready();
    await openSettings(user);
    await user.clear(screen.getByLabelText('Model'));
    await user.type(screen.getByLabelText('Model'), 'my-gemini');
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Provider' }),
      'azure_openai'
    );
    expect(screen.getByLabelText('Deployment name')).toHaveValue('dep');
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Provider' }),
      'gemini'
    );
    expect(screen.getByLabelText('Model')).toHaveValue('my-gemini');
  });
});

describe('chatting', () => {
  it('sends the message and shows the reply, with model and token details on request', async () => {
    const user = await ready();
    await send(user, 'hello there');
    expect(await screen.findByText('Hello from the model')).toBeInTheDocument();
    expect(chatCalls[0]).toMatchObject({
      provider: 'gemini',
      model: 'gemini-x',
      system: null,
    });
    expect(chatCalls[0].messages).toEqual([
      { role: 'user', content: 'hello there', tool_calls: [], extras: {} },
    ]);
    expect(screen.getByLabelText('Message')).toHaveValue('');

    await user.click(
      screen.getByRole('button', {
        name: 'Response model and token information',
      })
    );
    const info = screen.getByRole('status');
    expect(info).toHaveTextContent('Provider: Gemini');
    expect(info).toHaveTextContent('Model: gemini-served');
    expect(info).toHaveTextContent('Input Tokens: 7');
    expect(info).toHaveTextContent('Total Tokens: 12');
  });

  it('leaves temperature unset by default and sends it once enabled and changed', async () => {
    const user = await ready();
    await send(user, 'one');
    await screen.findByText('Hello from the model');
    expect(chatCalls[0].temperature).toBeNull();

    await openSettings(user);
    await user.type(screen.getByLabelText(/System prompt/), 'Be brief');

    await user.click(screen.getByRole('checkbox', { name: 'Set temperature' }));

    fireEvent.change(screen.getByLabelText('Temperature'), {
      target: { value: '1.4' },
    });

    await backToChat(user);
    await send(user, 'two');
    await waitFor(() => expect(chatCalls).toHaveLength(2));

    expect(chatCalls[1]).toMatchObject({
      system: 'Be brief',
      temperature: 1.4,
    });
  });

  it('sends provider-specific extras and tool calls back on the next message', async () => {
    const toolCall = { id: 'c1', name: 'f', arguments: { a: 1 }, error: null };
    chatHandler = () =>
      json(
        200,
        reply({
          tool_calls: [toolCall],
          text: null,
          extras: { gemini_parts: [{ text: 'sig' }] },
        })
      );
    const user = await ready();
    await send(user, 'first');
    await screen.findByText(/asked to call a tool/);

    chatHandler = () => json(200, reply());
    await send(user, 'second');
    await screen.findByText('Hello from the model');
    const assistant = chatCalls[1].messages[1];
    expect(assistant).toMatchObject({
      role: 'assistant',
      tool_calls: [toolCall],
    });
    expect(assistant.extras).toEqual({ gemini_parts: [{ text: 'sig' }] });
  });

  it('lets you switch provider mid-conversation and sends the whole conversation along', async () => {
    const user = await ready();
    await send(user, 'one');
    await screen.findByText('Hello from the model');

    await pickProvider(user, 'azure_openai');
    chatHandler = () =>
      json(
        200,
        reply({
          provider: 'azure_openai',
          model: 'dep-served',
          text: 'Second voice',
        })
      );
    await send(user, 'two');
    await screen.findByText('Second voice');

    expect(chatCalls[1].provider).toBe('azure_openai');
    expect(chatCalls[1].messages).toHaveLength(3);
    const replies = document.querySelectorAll('article.reply');
    expect([...replies].map((r) => r.dataset.provider)).toEqual([
      'gemini',
      'azure_openai',
    ]);
  });

  it('does not send on Shift and Enter', async () => {
    const user = await ready();
    await user.type(
      screen.getByLabelText('Message'),
      'a{Shift>}{Enter}{/Shift}b'
    );
    expect(chatCalls).toHaveLength(0);
    expect(screen.getByLabelText('Message')).toHaveValue('a\nb');
  });
});

describe('failures', () => {
  it('keeps your message in the box and explains a setup problem, even on the first message', async () => {
    chatHandler = () =>
      json(400, {
        error: 'ConfigError',
        detail: 'No API key configured for Gemini.',
      });
    const user = await ready();
    await send(user, 'keep me');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Message not sent');
    expect(alert).toHaveTextContent('No API key configured for Gemini.');
    expect(screen.getByLabelText('Message')).toHaveValue('keep me');
  });

  it('shows a provider failure without blaming the setup', async () => {
    chatHandler = () =>
      json(502, {
        error: 'RequestError',
        detail: 'gemini request failed: quota',
      });
    const user = await ready();
    await send(user, 'hi');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('gemini request failed: quota');
    expect(alert).not.toHaveTextContent('Check the Gemini entries');
  });

  it('explains how to start the API when it cannot be reached', async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.reject(new TypeError('network down'))
    );
    render(<App />);
    await waitFor(() =>
      expect(
        screen.getByText(/Start it with: uv run cpa-api/)
      ).toBeInTheDocument()
    );
  });
});

describe('several chats', () => {
  it("keeps each chat's messages separate", async () => {
    const user = await ready();
    await send(user, 'in chat one');
    await screen.findByText('Hello from the model');

    await user.click(screen.getByRole('button', { name: '＋ New' }));
    expect(screen.getByText('Start a task.')).toBeInTheDocument();
    await send(user, 'in chat two');
    await waitFor(() => expect(chatCalls).toHaveLength(2));
    expect(chatCalls[1].messages).toHaveLength(1); // chat two does not carry chat one along
  });

  it('renames and deletes a chat', async () => {
    const user = await ready();
    await user.click(screen.getByRole('button', { name: 'Menu for New chat' }));
    await user.click(screen.getByRole('button', { name: 'Rename' }));
    const box = screen.getByLabelText('Rename chat');
    await user.clear(box);
    await user.type(box, 'Planning{Enter}');
    expect(screen.getByText('Planning')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Menu for Planning' }));
    await user.click(screen.getByRole('button', { name: 'Delete' }));
    expect(screen.queryByText('Planning')).not.toBeInTheDocument();
    expect(screen.getByText('New chat')).toBeInTheDocument(); // never left with no chat
  });
});
