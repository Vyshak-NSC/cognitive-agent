"""Explorer-style project file manager for Workspace and Source."""
from pathlib import Path
import base64, html, io, mimetypes, re, zipfile
import pandas as pd
import streamlit as st
from ragapp.core.project_files import ProjectFileService

TEXT_EXTENSIONS={'.txt','.md','.py','.js','.mjs','.ts','.tsx','.jsx','.json','.yaml','.yml','.csv','.xml','.html','.htm','.css','.sql','.toml','.ini','.cfg','.env','.java','.kt','.go','.rs','.c','.cpp','.h','.hpp','.sh','.bat','.ps1','.r','.tex'}
AREAS={'workspace':('Workspace','Working files and generated artifacts.'),'source':('Source','Source material used by cognition.')}

def _safe(root,rel=''):
    root=Path(root).resolve(); p=(root/rel).resolve(); p.relative_to(root); return p

def _root(store,area):
    root=store.workspace if area=='workspace' else store.source; root.mkdir(parents=True,exist_ok=True); return root

def _is_text(p): return p.suffix.lower() in TEXT_EXTENSIONS or p.name.lower() in {'dockerfile','makefile'}
def _size(n):
    for u in ('B','KB','MB','GB'):
        if n<1024 or u=='GB': return f'{n:.0f} {u}' if u=='B' else f'{n:.1f} {u}'
        n/=1024

def _folders(root):
    root=Path(root).resolve(); return ['/']+sorted((p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_dir()),key=str.lower)

def _entries(root,folder):
    root=Path(root).resolve(); cur=_safe(root,'' if folder=='/' else folder)
    out=[]
    for p in cur.iterdir():
        is_dir=p.is_dir()
        out.append({'name':p.name,'path':p.relative_to(root).as_posix(),'kind':'Folder' if is_dir else 'File','size':'' if is_dir else _size(p.stat().st_size),'modified':p.stat().st_mtime})
    return sorted(out,key=lambda x:(x['kind']!='Folder',x['name'].lower()))

def _clean_name(name):
    clean=Path(name).name
    if not clean or clean!=name or clean in {'.','..'}: raise ValueError('Enter one valid file or folder name.')
    return clean

def _asset_data_uri(path):
    mime,_=mimetypes.guess_type(path.name); mime=mime or 'application/octet-stream'; return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

def _resolve_local_asset(html_file,reference,project_root):
    reference=html.unescape(reference).strip()
    if not reference or reference.startswith(('#','data:','http://','https://','//','mailto:','javascript:')): return None
    clean=reference.split('#',1)[0].split('?',1)[0]
    try: p=(html_file.parent/clean).resolve(); p.relative_to(Path(project_root).resolve())
    except (ValueError,OSError): return None
    return p if p.is_file() else None

def _prepare_html_preview(html_file,project_root):
    source=html_file.read_text(encoding='utf-8',errors='replace')
    def attr(m):
        a=_resolve_local_asset(html_file,m.group(3),project_root)
        if a is None: return m.group(0)
        try: return f'{m.group(1)}{m.group(2)}{_asset_data_uri(a)}{m.group(2)}'
        except OSError: return m.group(0)
    return re.sub(r'(\b(?:src|poster|href)\s*=\s*)([\'\"])([^\'\"]+)\2',attr,source,flags=re.I)

def _extract_zip_into(data,root,folder,overwrite=False):
    base=_safe(root,'' if folder=='/' else folder); out={'written':[],'skipped':[],'rejected':[]}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            name=info.filename
            if not name or name.endswith('/') or name.startswith('__MACOSX/'): continue
            try: target=(base/name).resolve(); target.relative_to(Path(root).resolve())
            except (ValueError,RuntimeError): out['rejected'].append(name); continue
            if target.exists() and not overwrite: out['skipped'].append(name); continue
            target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(zf.read(info)); out['written'].append(name)
    return out

def _preview(path,root):
    if _is_text(path):
        if path.suffix.lower() in {'.html','.htm'}: st.components.v1.html(_prepare_html_preview(path,root),height=650,scrolling=True)
        elif path.suffix.lower()=='.md': st.markdown(path.read_text(encoding='utf-8',errors='replace'))
        else: st.code(path.read_text(encoding='utf-8',errors='replace'),language=path.suffix.lstrip('.') or 'text')
    elif path.suffix.lower() in {'.png','.jpg','.jpeg','.gif','.webp'}: st.image(path.read_bytes())
    elif path.suffix.lower()=='.pdf': st.components.v1.html(f'<iframe src="{_asset_data_uri(path)}" width="100%" height="700"></iframe>',height=710)
    else: st.info(f'Binary file · {_size(path.stat().st_size)}')

def _selection_rows(event):
    try: return list(event.selection.rows)
    except Exception: return []

def _zip_selection(root,entries):
    bio=io.BytesIO()
    with zipfile.ZipFile(bio,'w',zipfile.ZIP_DEFLATED) as zf:
        for e in entries:
            p=_safe(root,e['path'])
            if p.is_dir():
                for child in p.rglob('*'):
                    if child.is_file(): zf.write(child,child.relative_to(root).as_posix())
            else: zf.write(p,p.relative_to(root).as_posix())
    return bio.getvalue()

def render_file_manager(store):
    st.subheader('Files')
    files=ProjectFileService(store)

    h1,h2,h3=st.columns([1.2,2.8,1])
    with h1:
        area=st.segmented_control('Area',['workspace','source'],format_func=lambda x:AREAS[x][0],default=st.session_state.get('fm_area','workspace'),key='fm_area') or 'workspace'
    root=_root(store,area); folders=_folders(root)
    fk=f'fm_folder_{area}'; pending=st.session_state.pop(f'fm_pending_folder_{area}',None)
    if pending in folders: st.session_state[fk]=pending
    current=st.session_state.get(fk,'/'); current=current if current in folders else '/'
    with h2: folder=st.selectbox('Folder',folders,index=folders.index(current),key=fk,label_visibility='collapsed')
    with h3:
        if folder!='/' and st.button('↑ Up',use_container_width=True):
            parent=Path(folder).parent.as_posix(); st.session_state[f'fm_pending_folder_{area}']='/' if parent=='.' else parent; st.rerun()

    with st.popover('＋ Add / import'):
        tab1,tab2=st.tabs(['Create','Upload'])
        with tab1:
            kind=st.radio('Create',['File','Folder'],horizontal=True,key=f'new_kind_{area}'); name=st.text_input('Name',key=f'new_name_{area}')
            content=st.text_area('Initial content',height=120,key=f'new_content_{area}') if kind=='File' else ''
            if st.button('Create',type='primary',key=f'create_{area}'):
                try:
                    rel=(Path(folder)/_clean_name(name)).as_posix() if folder!='/' else _clean_name(name)
                    files.create_folder(area,rel) if kind=='Folder' else files.write_text(area,rel,content)
                    st.rerun()
                except Exception as e: st.error(str(e))
        with tab2:
            ups=st.file_uploader('Files',accept_multiple_files=True,key=f'ups_{area}')
            if ups and st.button('Upload',type='primary',key=f'upload_{area}'):
                try:
                    batch=[(((Path(folder)/Path(up.name).name).as_posix() if folder!='/' else Path(up.name).name),up.getvalue()) for up in ups]
                    files.write_many(area,batch,description=f'Upload {len(batch)} file(s) to {area}'); st.rerun()
                except Exception as e: st.error(str(e))
            z=st.file_uploader('Extract ZIP',type=['zip'],key=f'zip_{area}'); overwrite=st.checkbox('Overwrite existing',key=f'ow_{area}')
            if z and st.button('Extract',key=f'extract_{area}'):
                try:
                    files.vcs.checkpoint(f'Pre-change: extract ZIP into {area}/{folder}'); _extract_zip_into(z.getvalue(),root,folder,overwrite); files.vcs.commit(f'Extract ZIP into {area}/{folder}',bind_timeline=False); st.rerun()
                except Exception as e: st.error(str(e))

    entries=_entries(root,folder)
    if not entries: st.info('This folder is empty.'); return
    table=pd.DataFrame([{'Name':('📁 ' if e['kind']=='Folder' else '📄 ')+e['name'],'Type':e['kind'],'Size':e['size']} for e in entries])
    event=st.dataframe(table,hide_index=True,use_container_width=True,on_select='rerun',selection_mode='multi-row',key=f'fm_grid_{area}_{folder}',column_config={'Name':st.column_config.TextColumn(width='large'),'Type':st.column_config.TextColumn(width='small'),'Size':st.column_config.TextColumn(width='small')})
    rows=_selection_rows(event); selected=[entries[i] for i in rows if 0<=i<len(entries)]
    st.caption(f'{len(selected)} selected' if selected else 'Select one or more files/folders. Select one file to preview it immediately.')

    if selected:
        t1,t2,t3,t4,t5,t6=st.columns(6)
        one=selected[0] if len(selected)==1 else None
        if t1.button('Open',disabled=not(one and one['kind']=='Folder'),use_container_width=True):
            st.session_state[f'fm_pending_folder_{area}']=one['path']; st.rerun()
        if t2.button('Copy',use_container_width=True): st.session_state[f'fm_bulk_{area}']='copy'
        if t3.button('Move',use_container_width=True): st.session_state[f'fm_bulk_{area}']='move'
        if t4.button('Rename',disabled=one is None,use_container_width=True): st.session_state[f'fm_bulk_{area}']='rename'
        zip_data=_zip_selection(root,selected)
        t5.download_button('Download',zip_data,file_name=f'{area}-selection.zip',mime='application/zip',use_container_width=True)
        if t6.button('Delete',type='primary',use_container_width=True): st.session_state[f'fm_bulk_{area}']='delete'

        op=st.session_state.get(f'fm_bulk_{area}')
        if op in {'copy','move'}:
            with st.container(border=True):
                st.markdown(f'**{op.title()} {len(selected)} selected item(s)**')
                dest_area=st.segmented_control('Destination area',['workspace','source'],default=area,key=f'bulk_dest_area_{area}_{op}') or area
                dests=_folders(_root(store,dest_area)); dest=st.selectbox('Destination folder',dests,key=f'bulk_dest_{area}_{op}')
                c1,c2=st.columns(2)
                if c1.button(op.title(),type='primary',key=f'bulk_go_{area}_{op}'):
                    try:
                        rels=[e['path'] for e in selected]
                        (files.copy_many if op=='copy' else files.move_many)(area,rels,dest_area,'' if dest=='/' else dest)
                        st.session_state.pop(f'fm_bulk_{area}',None); st.rerun()
                    except Exception as ex: st.error(str(ex))
                if c2.button('Cancel',key=f'bulk_cancel_{area}_{op}'): st.session_state.pop(f'fm_bulk_{area}',None); st.rerun()
        elif op=='delete':
            with st.container(border=True):
                st.warning(f'Delete {len(selected)} selected item(s)? Folders and their contents will be removed.')
                c1,c2=st.columns(2)
                if c1.button('Delete selected',type='primary',key=f'bulk_del_go_{area}'):
                    try: files.delete_many(area,[e['path'] for e in selected]); st.session_state.pop(f'fm_bulk_{area}',None); st.rerun()
                    except Exception as ex: st.error(str(ex))
                if c2.button('Cancel',key=f'bulk_del_cancel_{area}'): st.session_state.pop(f'fm_bulk_{area}',None); st.rerun()
        elif op=='rename' and one:
            with st.container(border=True):
                new_name=st.text_input('New name',value=one['name'],key=f'fm_rename_{area}_{one["path"]}')
                c1,c2=st.columns(2)
                if c1.button('Rename',type='primary',key=f'fm_rename_go_{area}'):
                    try:
                        parent=Path(one['path']).parent; target=(parent/_clean_name(new_name)).as_posix(); files.move(area,one['path'],area,target); st.session_state.pop(f'fm_bulk_{area}',None); st.rerun()
                    except Exception as ex: st.error(str(ex))
                if c2.button('Cancel',key=f'fm_rename_cancel_{area}'): st.session_state.pop(f'fm_bulk_{area}',None); st.rerun()

    if len(selected)==1 and selected[0]['kind']=='File':
        e=selected[0]; p=_safe(root,e['path'])
        st.divider(); st.markdown(f"**{e['name']}**  ·  `{e['path']}`  ·  {e['size']}")
        if _is_text(p):
            edit=st.toggle('Edit',key=f'fm_edit_toggle_{area}_{e["path"]}')
            if edit:
                content=st.text_area('Contents',p.read_text(encoding='utf-8',errors='replace'),height=500,key=f'fm_edit_{area}_{e["path"]}',label_visibility='collapsed')
                if st.button('Save changes',type='primary',key=f'fm_save_{area}_{e["path"]}'):
                    files.write_text(area,e['path'],content,overwrite=True,description=f'Edit {area}/{e["path"]}'); st.success('Saved.')
            else: _preview(p,root)
        else: _preview(p,root)
