# Cognitive Persistence Agent — React Frontend

This is a React/Vite frontend for the FastAPI backend in `api.py`. It keeps the Streamlit feature model but replaces the sidebar navigation with a persistent top navigation bar and a denser dark application shell.

## 1. Start the FastAPI backend

Run the API from the same Python environment that contains the `ragapp` package. For example:

```bash
uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

If your backend module is named differently, use that module name instead.

### Required backend change (file previews)

Apply `api-raw-endpoint.patch` (adds `GET /projects/{u}/{p}/files/{area}/raw/{path}`). HTML previews load from
this route inside a sandboxed iframe so relative CSS/JS/images resolve; without it HTML and PDF previews show a 404.

```bash
patch -p1 < api-raw-endpoint.patch
```

## 2. Configure the frontend

Copy `.env.example` to `.env` if you need a different backend URL or username:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_DEFAULT_USERNAME=test
```

## 3. Install and run

```bash
npm install
npm run dev
```

Then open the Vite URL, normally `http://localhost:5173`.

For a production build:

```bash
npm run build
npm run preview
```

## Feature mapping

- Chat: persistent sessions, new/rename/delete chat, branch activation, prompt editing/forking, agent selection, streaming execution sections, execution trace, and the same chat task workflow.
- Files: Source/Workspace browsing, search, text editing, save, create, delete, move, copy, history and preview/download.
- Cognition: metrics, compilation, ledger, relationships, state map, available files and impact analysis.
- Review: edit/save, approve and reject drafts.
- Instructions: add, edit and deactivate persistent instructions.
- History: Git log and manual checkpoint.
- Settings: provider/model, endpoint, API key, fallback providers, limits and semantic propagation.
- Project controls: project selector, create, rename and delete.

The frontend intentionally does not reimplement business logic. All persistence and mutations go through the FastAPI endpoints so the React client remains a presentation layer over the same `ragapp` services used by Streamlit.

## Layout and preview design

- The shell is a single bounded flex column (`100dvh`). Only the chat thread, the file lists and the preview body scroll; the page itself never does.
- Markdown (chat and `.md` files) goes through one component: GFM + sanitised raw HTML + syntax highlighting + copy buttons.
- File preview by type: `.md` rendered, `.html` sandboxed iframe, images/PDF native, `.mmd` Mermaid (lazy-loaded), everything else highlighted source.
