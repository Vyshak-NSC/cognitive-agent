"""General-purpose project file manager shared by the UI and agent."""
from pathlib import Path
import io
import re
import base64
import mimetypes
import html
import shutil
import zipfile
import streamlit as st

TEXT_EXTENSIONS = {
    ".txt",".md",".py",".js",".ts",".tsx",".jsx",".json",".yaml",".yml",".csv",
    ".xml",".html",".css",".sql",".toml",".ini",".cfg",".env",".java",".kt",
    ".go",".rs",".c",".cpp",".h",".hpp",".sh",".bat"
}
AREAS = {
    "workspace": ("Workspace", "AI-created and user-managed working files."),
    "source": ("Source", "User files that may be used as source material for cognition."),
}


def _safe(root, rel=""):
    root = root.resolve()
    p = (root / rel).resolve()
    p.relative_to(root)
    return p


def _is_text(p):
    return p.suffix.lower() in TEXT_EXTENSIONS or p.name.lower() in {"dockerfile","makefile"}


def _size(n):
    for unit in ("B","KB","MB","GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _root(store, area):
    root = store.workspace if area == "workspace" else store.source
    root.mkdir(parents=True, exist_ok=True)
    return root

def _folders(root):
    root = Path(root).resolve()
    return ["/"] + sorted(
        [p.relative_to(root).as_posix()
         for p in root.rglob("*")
         if p.is_dir()],
        key=str.lower
    )


def _entries(root, folder):
    root = Path(root).resolve()
    current = _safe(root, "" if folder == "/" else folder)
    return sorted(
        [{"name": p.name,
          "path": p.relative_to(root).as_posix(),
          "type": "Folder" if p.is_dir() else "File",
          "size": None if p.is_dir() else _size(p.stat().st_size)}
         for p in current.iterdir()],
        key=lambda x: (x["type"] != "Folder", x["name"].lower())
    )


def _clean_name(name):
    clean = Path(name).name
    if not clean or clean != name or clean in {".",".."}:
        raise ValueError("Enter a single valid file or folder name.")
    return clean


def _extract_zip_into(zip_bytes, root, folder, overwrite=False):
    """Extract a .zip's contents into `folder` (relative to `root`),
    preserving the archive's internal directory structure. This is how a
    whole local folder is uploaded in one action: zip it locally, upload
    the zip, and it's unpacked here with the nested structure intact —
    browsers don't let a plain <input type=file> read a real folder tree,
    so this is the reliable substitute for a native folder picker.

    Every archive member is resolved against `root` and rejected if it
    would land outside it (path traversal / absolute paths / drive
    letters), so a malicious or malformed zip can't write outside the
    project's own folder.
    """
    base = _safe(root, "" if folder == "/" else folder)
    written, skipped, rejected = [], [], []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/") or not name or name.startswith("__MACOSX/"):
                continue
            try:
                target = (base / name).resolve()
                target.relative_to(root.resolve())
            except (ValueError, RuntimeError):
                rejected.append(name)
                continue
            if target.exists() and not overwrite:
                skipped.append(name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src:
                target.write_bytes(src.read())
            written.append(name)
    return {"written": written, "skipped": skipped, "rejected": rejected}


def _asset_data_uri(path):
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _resolve_local_asset(html_file, reference, project_root):
    reference = html.unescape(reference).strip()
    if not reference or reference.startswith(("#", "data:", "http://", "https://", "//", "mailto:", "javascript:")):
        return None
    clean = reference.split("#", 1)[0].split("?", 1)[0]
    if not clean:
        return None
    try:
        candidate = (html_file.parent / clean).resolve()
        candidate.relative_to(Path(project_root).resolve())
    except (ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


def _inline_css_urls(css_text, css_file, project_root):
    pattern = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.I)
    def replace(match):
        asset = _resolve_local_asset(css_file, match.group(2), project_root)
        if asset is None:
            return match.group(0)
        try:
            return "url('" + _asset_data_uri(asset) + "')"
        except OSError:
            return match.group(0)
    return pattern.sub(replace, css_text)


def _prepare_html_preview(html_file, project_root):
    source = html_file.read_text(encoding="utf-8", errors="replace")

    link_re = re.compile(r"<link\b([^>]*?\bhref\s*=\s*['\"]([^'\"]+)['\"][^>]*)>", re.I)
    def replace_link(match):
        attrs, href = match.group(1), match.group(2)
        if not re.search(r"\brel\s*=\s*['\"]?[^'\">]*stylesheet", attrs, re.I):
            return match.group(0)
        css_path = _resolve_local_asset(html_file, href, project_root)
        if css_path is None or css_path.suffix.lower() != ".css":
            return match.group(0)
        try:
            css = _inline_css_urls(css_path.read_text(encoding="utf-8", errors="replace"), css_path, project_root)
            return f"<style>\n{css}\n</style>"
        except OSError:
            return match.group(0)
    source = link_re.sub(replace_link, source)

    script_re = re.compile(r"<script\b([^>]*?)(?:\bsrc\s*=\s*['\"]([^'\"]+)['\"][^>]*)>\s*</script>", re.I)
    def replace_script(match):
        attrs, src = match.group(1), match.group(2)
        js_path = _resolve_local_asset(html_file, src, project_root)
        if js_path is None or js_path.suffix.lower() not in {".js", ".mjs"}:
            return match.group(0)
        try:
            js = js_path.read_text(encoding="utf-8", errors="replace")
            attrs = re.sub(r"\s+src\s*=\s*(['\"])[^'\"]*\1", "", attrs, flags=re.I)
            return f"<script{attrs}>\n{js}\n</script>"
        except OSError:
            return match.group(0)
    source = script_re.sub(replace_script, source)

    attr_re = re.compile(r"(\b(?:src|poster|href)\s*=\s*)(['\"])([^'\"]+)\2", re.I)
    def replace_attr(match):
        prefix, quote, ref = match.groups()
        asset = _resolve_local_asset(html_file, ref, project_root)
        if asset is None or asset.suffix.lower() in {".css", ".js", ".mjs"}:
            return match.group(0)
        try:
            return f"{prefix}{quote}{_asset_data_uri(asset)}{quote}"
        except OSError:
            return match.group(0)
    return attr_re.sub(replace_attr, source)


def _render_html_preview(html_file, project_root):
    document = _prepare_html_preview(html_file, project_root)
    st.caption("Live preview · local CSS/JS/assets are loaded from this project.")
    st.components.v1.html(document, height=760, scrolling=True)


def _copy_across_areas(store, src_area, src_relative_path, dest_area, dest_folder, is_dir):
    """Copy a file or folder from one area's tree into the other (Source <->
    Workspace), used to promote/import an already-existing project folder
    as a bulk unit instead of re-uploading every file inside it one by
    one."""
    src_root = _root(store, src_area)
    dest_root = _root(store, dest_area)
    src_path = _safe(src_root, src_relative_path)
    name = src_path.name
    dest_path = _safe(dest_root, str(Path(dest_folder) / name) if dest_folder != "/" else name)
    if dest_path.exists():
        raise FileExistsError(f"{dest_path.relative_to(dest_root)} already exists in {AREAS[dest_area][0]}.")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if is_dir:
        shutil.copytree(src_path, dest_path)
    else:
        shutil.copy2(src_path, dest_path)
    return dest_path


def render_file_manager(store):
    st.subheader("Files")
    st.caption("Manage every project artifact directly. The agent uses the same filesystem.")
    area = st.radio(
        "File area", ["workspace","source"], horizontal=True,
        format_func=lambda x: AREAS[x][0], key="fm_area"
    )
    root = _root(store, area)
    folders = _folders(root)

    folder_key = f"fm_folder_{area}"
    pending_folder_key = f"fm_pending_folder_{area}"

    # Apply navigation requests before the selectbox with this key is
    # instantiated. This avoids Streamlit's
    # StreamlitWidgetAlreadyInstantiatedError.
    pending_folder = st.session_state.pop(pending_folder_key, None)
    if pending_folder in folders:
        st.session_state[folder_key] = pending_folder

    stored_folder = st.session_state.get(folder_key, "/")
    if stored_folder not in folders:
        st.session_state[folder_key] = "/"
        stored_folder = "/"

    folder = st.selectbox(
        "Folder",
        folders,
        index=folders.index(stored_folder),
        key=folder_key,
    )
    current = _safe(root, "" if folder == "/" else folder)

    # Toolbar
    c1,c2,c3,c4,c5 = st.columns(5)
    with c1:
        with st.popover("New", use_container_width=True):
            kind = st.radio("Create", ["File","Folder"], horizontal=True, key=f"new_kind_{area}")
            name = st.text_input("Name", key=f"new_name_{area}")
            content = st.text_area("Initial content", height=120, key=f"new_content_{area}") if kind=="File" else ""
            if st.button("Create", type="primary", use_container_width=True, key=f"new_btn_{area}"):
                try:
                    clean=_clean_name(name)
                    target=_safe(root, str(Path(folder)/clean) if folder!="/" else clean)
                    if target.exists(): raise FileExistsError("An item with that name already exists.")
                    if kind=="Folder": target.mkdir(parents=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content,encoding="utf-8")
                    st.rerun()
                except Exception as exc: st.error(str(exc))
    with c2:
        with st.popover("Upload", use_container_width=True):
            st.caption(f"Destination: **{AREAS[area][0]} / {folder if folder != '/' else '(root)'}**")
            ups=st.file_uploader("Choose file(s)", accept_multiple_files=True, key=f"upload_{area}")
            if ups and st.button("Save", type="primary", use_container_width=True, key=f"upload_btn_{area}"):
                errors=[]
                for up in ups:
                    try:
                        target=_safe(root, str(Path(folder)/Path(up.name).name) if folder!="/" else up.name)
                        if target.exists(): raise FileExistsError(f"{up.name}: destination already exists.")
                        target.write_bytes(up.getbuffer())
                    except Exception as exc: errors.append(str(exc))
                if errors: st.error("\n".join(errors))
                else: st.rerun()

            st.divider()
            st.markdown("**Upload a whole folder (as a .zip)**")
            st.caption(
                "Browsers won't let a plain upload button read a local folder directly, "
                "so zip the folder on your computer first — the structure inside it is "
                "preserved exactly when it's unpacked here."
            )
            zip_up = st.file_uploader("Choose a .zip", type=["zip"], key=f"upload_zip_{area}")
            overwrite = st.checkbox("Overwrite files that already exist", value=False, key=f"upload_zip_overwrite_{area}")
            if zip_up and st.button("Extract into this folder", type="primary", use_container_width=True, key=f"upload_zip_btn_{area}"):
                try:
                    result = _extract_zip_into(zip_up.getvalue(), root, folder, overwrite=overwrite)
                    msg = f"Extracted {len(result['written'])} file(s)."
                    if result["skipped"]:
                        msg += f" Skipped {len(result['skipped'])} already-existing file(s) (enable overwrite to replace them)."
                    if result["rejected"]:
                        msg += f" Refused {len(result['rejected'])} entr(y/ies) with unsafe paths."
                    st.success(msg)
                    st.rerun()
                except zipfile.BadZipFile:
                    st.error("That file isn't a valid .zip archive.")
                except Exception as exc:
                    st.error(str(exc))
    with c3:
        if st.button("Refresh", use_container_width=True):
            st.rerun()
    with c4:
        st.caption(f"{len(_entries(root,folder))} items")
    with c5:
        other_area = "source" if area == "workspace" else "workspace"
        with st.popover(f"Import from {AREAS[other_area][0]}", use_container_width=True):
            st.caption(
                f"Copy something that already exists in **{AREAS[other_area][0]}** into "
                f"**{AREAS[area][0]} / {folder if folder != '/' else '(root)'}** — useful for "
                f"promoting an already-uploaded folder in bulk instead of re-uploading its files."
            )
            other_root = _root(store, other_area)
            other_entries_flat = sorted(
                [{"path": p.relative_to(other_root).as_posix(), "is_dir": p.is_dir()}
                 for p in other_root.rglob("*")],
                key=lambda e: (not e["is_dir"], e["path"].lower()),
            )
            if not other_entries_flat:
                st.info(f"{AREAS[other_area][0]} is empty.")
            else:
                choice = st.selectbox(
                    "Item to import",
                    other_entries_flat,
                    format_func=lambda e: f"{'📁' if e['is_dir'] else '📄'} {e['path']}",
                    key=f"import_choice_{area}_{folder}",
                )
                if st.button("Copy in", type="primary", use_container_width=True, key=f"import_btn_{area}_{folder}"):
                    try:
                        _copy_across_areas(store, other_area, choice["path"], area, folder, choice["is_dir"])
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    entries=_entries(root,folder)
    if not entries:
        st.info("This folder is empty. Create a file/folder or upload an artifact.")
        return

    # --- Listing table with inline Edit / Delete actions -----------------
    # Rendered as manual columns (st.dataframe can't host buttons per row).
    # "Edit" only applies to text files; folders and binary files get a "—".
    edit_key = f"fm_inline_edit_{area}"
    editing_path = st.session_state.get(edit_key)
    if editing_path and editing_path not in {e["path"] for e in entries}:
        # Selected folder changed (or the file was moved/deleted) since the
        # editor was opened -- drop the stale reference.
        st.session_state.pop(edit_key, None)
        editing_path = None

    header = st.columns([0.6, 3.4, 1.2, 1, 1])
    header[0].markdown("**Type**")
    header[1].markdown("**Name**")
    header[2].markdown("**Size**")
    header[3].markdown("**Edit**")
    header[4].markdown("**Delete**")

    for e in entries:
        p_row = _safe(root, e["path"])
        row = st.columns([0.6, 3.4, 1.2, 1, 1])
        row[0].write("📁" if e["type"] == "Folder" else "📄")
        with row[1]:
            if e["type"] == "Folder":
                if st.button(
                    e["name"],
                    key=f"row_open_{area}_{e['path']}",
                    use_container_width=True,
                ):
                    # The folder selector has already been instantiated in this
                    # run, so do not mutate its widget-bound session-state key.
                    # Store the requested navigation and apply it at the top of
                    # the next rerun, before the selectbox is instantiated.
                    st.session_state[f"fm_pending_folder_{area}"] = e["path"]
                    st.session_state.pop(edit_key, None)
                    st.session_state.pop(f"fm_action_{area}", None)
                    st.rerun()
            else:
                st.write(e["name"])
        row[2].write(e["size"] or "—")
        with row[3]:
            if e["type"] == "File" and _is_text(p_row):
                if st.button("Edit", key=f"row_edit_{area}_{e['path']}", use_container_width=True):
                    st.session_state[edit_key] = e["path"]
                    st.rerun()
            else:
                st.write("—")
        with row[4]:
            if st.button("Delete", key=f"row_delete_{area}_{e['path']}", type="primary", use_container_width=True):
                try:
                    if e["type"] == "Folder":
                        shutil.rmtree(p_row)
                    else:
                        p_row.unlink()
                    if editing_path == e["path"]:
                        st.session_state.pop(edit_key, None)
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    if editing_path:
        editing_full = _safe(root, editing_path)
        st.divider()
        st.markdown(f"### ✏️ Editing `{editing_path}`")
        edited = st.text_area(
            "Contents",
            editing_full.read_text(encoding="utf-8", errors="replace"),
            height=420,
            key=f"row_editor_{area}_{editing_path}",
        )
        ec1, ec2 = st.columns(2)
        with ec1:
            if st.button("Save changes", type="primary", use_container_width=True, key=f"row_save_{area}_{editing_path}"):
                editing_full.write_text(edited, encoding="utf-8")
                st.success("Saved.")
        with ec2:
            if st.button("Close editor", use_container_width=True, key=f"row_close_{area}_{editing_path}"):
                st.session_state.pop(edit_key, None)
                st.rerun()
    st.divider()
    # -----------------------------------------------------------------------

    labels=[f"{e['type']}  ·  {e['name']}" for e in entries]
    selected_label=st.selectbox("More actions (view, download, move, copy, rename)", ["—"]+labels, key=f"fm_selected_{area}_{folder}")
    if selected_label=="—":
        return
    entry=entries[labels.index(selected_label)]
    p=_safe(root,entry["path"])

    if entry["type"]=="Folder":
        st.markdown(f"### 📁 `{entry['path']}/`")
        st.caption("Use the folder name in the listing to open it, or select it from the Folder control above.")
        actions=st.columns(3)
        with actions[0]:
            new_name=st.text_input("New name", value=p.name, key=f"folder_rename_{area}_{entry['path']}")
            if st.button("Rename / move", use_container_width=True, key=f"folder_move_{area}_{entry['path']}"):
                try:
                    clean=_clean_name(new_name)
                    dest=_safe(root, str(Path(folder)/clean) if folder!="/" else clean)
                    if dest.exists(): raise FileExistsError("Destination already exists.")
                    shutil.move(str(p),str(dest)); st.rerun()
                except Exception as exc: st.error(str(exc))
        with actions[1]:
            dests=[f for f in folders if f != entry["path"] and not f.startswith(entry["path"]+"/")]
            dest=st.selectbox("Copy to",dests,key=f"folder_copy_dest_{area}_{entry['path']}")
            if st.button("Copy folder",use_container_width=True,key=f"folder_copy_{area}_{entry['path']}"):
                try:
                    target=_safe(root,str(Path(dest)/p.name) if dest!="/" else p.name)
                    if target.exists(): raise FileExistsError("Destination already exists.")
                    shutil.copytree(p,target); st.rerun()
                except Exception as exc: st.error(str(exc))
        with actions[2]:
            st.write("")
            if st.button("Delete folder", type="primary", use_container_width=True, key=f"folder_del_{area}_{entry['path']}"):
                shutil.rmtree(p); st.rerun()
        return

    st.markdown(f"### 📄 `{entry['path']}`")
    if p.suffix.lower() in {".html", ".htm"}:
        a,b,c=st.columns(3)
        with a:
            if st.button("Preview",type="primary",use_container_width=True,key=f"preview_{area}_{entry['path']}"):
                st.session_state[f"fm_action_{area}"]="preview"
        with b:
            if st.button("Source",use_container_width=True,key=f"view_{area}_{entry['path']}"):
                st.session_state[f"fm_action_{area}"]="view"
        with c:
            if st.button("Download",use_container_width=True,key=f"download_{area}_{entry['path']}"):
                st.session_state[f"fm_action_{area}"]="download"
    else:
        a,b=st.columns(2)
        with a:
            if st.button("View",use_container_width=True,key=f"view_{area}_{entry['path']}"):
                st.session_state[f"fm_action_{area}"]="view"
        with b:
            if st.button("Download",use_container_width=True,key=f"download_{area}_{entry['path']}"):
                st.session_state[f"fm_action_{area}"]="download"

    with st.expander("Move / Copy / Rename", expanded=False):
        dests=folders
        x,y=st.columns(2)
        with x:
            new_name=st.text_input("Name",value=p.name,key=f"file_name_{area}_{entry['path']}")
            dest=st.selectbox("Destination folder",dests,key=f"file_dest_{area}_{entry['path']}")
            if st.button("Move / rename",use_container_width=True,key=f"file_move_{area}_{entry['path']}"):
                try:
                    clean=_clean_name(new_name)
                    target=_safe(root,str(Path(dest)/clean) if dest!="/" else clean)
                    if target.exists(): raise FileExistsError("Destination already exists.")
                    shutil.move(str(p),str(target)); st.rerun()
                except Exception as exc: st.error(str(exc))
        with y:
            copy_dest=st.selectbox("Copy destination",dests,key=f"file_copy_dest_{area}_{entry['path']}")
            copy_name=st.text_input("Copy as",value=p.name,key=f"file_copy_name_{area}_{entry['path']}")
            if st.button("Copy",use_container_width=True,key=f"file_copy_{area}_{entry['path']}"):
                try:
                    clean=_clean_name(copy_name)
                    target=_safe(root,str(Path(copy_dest)/clean) if copy_dest!="/" else clean)
                    if target.exists(): raise FileExistsError("Destination already exists.")
                    shutil.copy2(p,target); st.rerun()
                except Exception as exc: st.error(str(exc))

    action=st.session_state.get(f"fm_action_{area}","view")
    if action=="download":
        st.download_button("Download file",data=p.read_bytes(),file_name=p.name,use_container_width=True)
    elif action=="preview" and p.suffix.lower() in {".html", ".htm"}:
        try:
            _render_html_preview(p, root)
        except Exception as exc:
            st.error(f"Could not render HTML preview: {exc}")
    else:
        if _is_text(p):
            st.code(p.read_text(encoding="utf-8",errors="replace"),language=p.suffix.lstrip(".") or "text")
        else:
            st.info(f"Binary artifact · {_size(p.stat().st_size)}")