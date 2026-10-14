import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";

const PROVIDERS = [
  { id: "gemini", label: "Gemini", model: "gemini-x", model_label: "Model", configured: true, missing: [] },
  { id: "openai", label: "OpenAI (GPT)", model: "gpt-x", model_label: "Model", configured: false, missing: ["OPENAI_API_KEY"] },
  { id: "azure_openai", label: "Azure OpenAI", model: "dep", model_label: "Deployment name", configured: true, missing: [] },
];

const json = (status, body) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

const reply = (over = {}) => ({
  provider: "gemini",
  model: "gemini-served",
  text: "Hello from the model",
  tool_calls: [],
  usage: { total_tokens: 12 },
  extras: {},
  ...over,
});

let chatCalls;
let chatHandler;

beforeEach(() => {
  chatCalls = [];
  chatHandler = () => json(200, reply());
  globalThis.fetch = vi.fn((url, options) => {
    if (url === "/api/health") return json(200, { status: "ok", version: "11.0.2" });
    if (url === "/api/providers") return json(200, PROVIDERS);
    if (url === "/api/chat") {
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
  await screen.findByRole("button", { name: /Gemini/ });
  return user;
}

describe("provider list", () => {
  it("selects the first set-up provider and says what an unset one needs", async () => {
    await ready();
    expect(screen.getByRole("button", { name: /Gemini/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Add OPENAI_API_KEY")).toBeInTheDocument();
    expect(screen.getByText("Version 11.0.2")).toBeInTheDocument();
  });

  it("blocks sending to a provider that is not set up, and names the missing variable", async () => {
    const user = await ready();
    await user.click(screen.getByRole("button", { name: /^OpenAI \(GPT\)/ }));
    expect(screen.getByText(/OpenAI \(GPT\) isn't set up\. Add OPENAI_API_KEY/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Message"), "hi");
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });

  it("uses the provider's own label for the model field", async () => {
    const user = await ready();
    await user.click(screen.getByRole("button", { name: /Azure OpenAI/ }));
    expect(screen.getByLabelText("Deployment name")).toHaveValue("dep");
  });
});

describe("chatting", () => {
  it("sends the message and shows the reply tagged with its provider", async () => {
    const user = await ready();
    await user.type(screen.getByLabelText("Message"), "hello there{Enter}");
    expect(await screen.findByText("Hello from the model")).toBeInTheDocument();
    expect(screen.getByText("12 tokens")).toBeInTheDocument();
    expect(screen.getByText("gemini-served")).toBeInTheDocument();
    expect(chatCalls[0]).toMatchObject({
      provider: "gemini",
      model: "gemini-x",
      system: null,
      temperature: null,
    });
    expect(chatCalls[0].messages).toEqual([
      { role: "user", content: "hello there", tool_calls: [], extras: {} },
    ]);
    expect(screen.getByLabelText("Message")).toHaveValue("");
  });

  it("sends provider-specific extras and tool calls back on the next message", async () => {
    const toolCall = { id: "c1", name: "f", arguments: { a: 1 }, error: null };
    chatHandler = () =>
      json(200, reply({ tool_calls: [toolCall], text: null, extras: { gemini_parts: [{ text: "sig" }] } }));
    const user = await ready();
    await user.type(screen.getByLabelText("Message"), "first{Enter}");
    await screen.findByText(/asked to call a tool/);

    chatHandler = () => json(200, reply());
    await user.type(screen.getByLabelText("Message"), "second{Enter}");
    await screen.findByText("Hello from the model");
    const assistant = chatCalls[1].messages[1];
    expect(assistant).toMatchObject({ role: "assistant", tool_calls: [toolCall] });
    expect(assistant.extras).toEqual({ gemini_parts: [{ text: "sig" }] });
  });

  it("lets you switch provider mid-conversation and keeps each reply's own tag", async () => {
    const user = await ready();
    await user.type(screen.getByLabelText("Message"), "one{Enter}");
    await screen.findByText("Hello from the model");

    await user.click(screen.getByRole("button", { name: /Azure OpenAI/ }));
    chatHandler = () => json(200, reply({ provider: "azure_openai", model: "dep-served", text: "Second voice" }));
    await user.type(screen.getByLabelText("Message"), "two{Enter}");
    await screen.findByText("Second voice");

    expect(chatCalls[1].provider).toBe("azure_openai");
    expect(chatCalls[1].messages).toHaveLength(3); // the whole conversation goes along
    const replies = document.querySelectorAll("article.reply");
    expect([...replies].map((r) => r.dataset.provider)).toEqual(["gemini", "azure_openai"]);
  });

  it("sends the system prompt and temperature when set", async () => {
    const user = await ready();
    await user.type(screen.getByLabelText("System prompt"), "Be brief");
    await user.click(screen.getByLabelText("Set temperature"));
    await user.type(screen.getByLabelText("Message"), "hi{Enter}");
    await screen.findByText("Hello from the model");
    expect(chatCalls[0]).toMatchObject({ system: "Be brief", temperature: 1 });
  });

  it("does not send on Shift and Enter", async () => {
    const user = await ready();
    await user.type(screen.getByLabelText("Message"), "a{Shift>}{Enter}{/Shift}b");
    expect(chatCalls).toHaveLength(0);
    expect(screen.getByLabelText("Message")).toHaveValue("a\nb");
  });
});

describe("failures", () => {
  it("keeps your message in the box and explains a setup problem", async () => {
    chatHandler = () => json(400, { error: "ConfigError", detail: "No API key configured for Gemini." });
    const user = await ready();
    await user.type(screen.getByLabelText("Message"), "keep me{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Message not sent");
    expect(alert).toHaveTextContent("No API key configured for Gemini.");
    expect(alert).toHaveTextContent("Check the Gemini entries in your .env file");
    expect(screen.getByLabelText("Message")).toHaveValue("keep me");
    expect(document.querySelectorAll("article.turn")).toHaveLength(0);
  });

  it("shows a provider failure without blaming the setup", async () => {
    chatHandler = () => json(502, { error: "RequestError", detail: "gemini request failed: quota" });
    const user = await ready();
    await user.type(screen.getByLabelText("Message"), "hi{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("gemini request failed: quota");
    expect(alert).not.toHaveTextContent("Check the Gemini entries");
  });

  it("explains how to start the API when it cannot be reached", async () => {
    globalThis.fetch = vi.fn(() => Promise.reject(new TypeError("network down")));
    render(<App />);
    await waitFor(() => expect(screen.getByText(/Start it with: uv run cpa-api/)).toBeInTheDocument());
  });
});

describe("conversation controls", () => {
  it("clears the conversation", async () => {
    const user = await ready();
    await user.type(screen.getByLabelText("Message"), "hi{Enter}");
    await screen.findByText("Hello from the model");
    await user.click(screen.getByRole("button", { name: "Clear conversation" }));
    expect(screen.queryByText("Hello from the model")).not.toBeInTheDocument();
    expect(screen.getByText("Start a conversation")).toBeInTheDocument();
  });
});
