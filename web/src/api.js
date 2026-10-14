// The only file that knows the API's URLs and error shapes.
const BASE = "/api";

export class ApiError extends Error {
  constructor(message, kind = "request") {
    super(message);
    this.kind = kind; // "unreachable" | "setup" | "provider" | "request"
  }
}

async function request(path, options) {
  let response;
  try {
    response = await fetch(BASE + path, {
      headers: { "content-type": "application/json" },
      ...options,
    });
  } catch {
    throw new ApiError("Can't reach the API. Start it with: uv run cpa-api", "unreachable");
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    // A dev-server proxy answers 5xx with plain text when the API is not running.
    if (response.status >= 500) {
      throw new ApiError("Can't reach the API. Start it with: uv run cpa-api", "unreachable");
    }
  }
  if (response.ok) return body;

  if (Array.isArray(body?.detail)) {
    // FastAPI validation error: a list of {loc, msg}.
    throw new ApiError(body.detail.map((d) => d.msg).join("; "));
  }
  const kind = response.status === 400 ? "setup" : response.status === 502 ? "provider" : "request";
  throw new ApiError(body?.detail || `The API answered ${response.status}.`, kind);
}

export const getHealth = () => request("/health");
export const getProviders = () => request("/providers");
export const sendChat = (payload) =>
  request("/chat", { method: "POST", body: JSON.stringify(payload) });
