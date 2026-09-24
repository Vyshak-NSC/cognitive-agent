"""Compact project file manager for Workspace and Source."""
from pathlib import Path
import base64, html, io, mimetypes, re, shutil, zipfile
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
    return sorted([{'name':p.name,'path':p.relative_to(root).as_posix(),'type':'Folder' if p.is_dir() else 'File','size':None if p.is_dir() else _size(p.stat().st_size)} for p in cur.iterdir()],key=lambda x:(x['type']!='Folder',x['name'].lower()))

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

def _transfer(store,src_area,rel,dst_area,dst_folder,move):
    service=ProjectFileService(store)
    src=service.path(src_area,rel)
    dst_rel=(Path(dst_folder)/src.name).as_posix() if dst_folder!='/' else src.name
    if move: return service.move(src_area,rel,dst_area,dst_rel)
    return service.copy(src_area,rel,dst_area,dst_rel)

def _preview(path,root):
    if _is_text(path):
        if path.suffix.lower() in {'.html','.htm'}: st.components.v1.html(_prepare_html_preview(path,root),height=650,scrolling=True)
        else: st.code(path.read_text(encoding='utf-8',errors='replace'),language=path.suffix.lstrip('.') or 'text')
    elif path.suffix.lower() in {'.png','.jpg','.jpeg','.gif','.webp'}: st.image(path.read_bytes())
    elif path.suffix.lower()=='.pdf': st.components.v1.html(f'<iframe src="{_asset_data_uri(path)}" width="100%" height="700"></iframe>',height=710)
    else: st.info(f'Binary file · {_size(path.stat().st_size)}. Use Download or Replace.')

def render_file_manager(store):
    st.subheader('Files')
    st.caption('Workspace and Source are managed from one compact browser. Select an item, then choose an action.')
    files=ProjectFileService(store)
    top1,top2=st.columns([1,2])
    with top1:
        area=st.segmented_control('Area',['workspace','source'],format_func=lambda x:AREAS[x][0],default=st.session_state.get('fm_area','workspace'),key='fm_area') or 'workspace'
    root=_root(store,area); folders=_folders(root)
    fk=f'fm_folder_{area}'; pending=st.session_state.pop(f'fm_pending_folder_{area}',None)
    if pending in folders: st.session_state[fk]=pending
    current=st.session_state.get(fk,'/'); current=current if current in folders else '/'
    with top2: folder=st.selectbox('Folder',folders,index=folders.index(current),key=fk)

    with st.popover('＋ Add / import',use_container_width=False):
        tab1,tab2,tab3=st.tabs(['Create','Upload','From other area'])
        with tab1:
            kind=st.radio('Create',['File','Folder'],horizontal=True,key=f'new_kind_{area}'); name=st.text_input('Name',key=f'new_name_{area}')
            content=st.text_area('Initial text / content',height=140,key=f'new_content_{area}') if kind=='File' else ''
            if st.button('Create',type='primary',key=f'create_{area}'):
                try:
                    target=_safe(root,str(Path(folder)/_clean_name(name)) if folder!='/' else _clean_name(name))
                    if target.exists(): raise FileExistsError('Destination already exists.')
                    rel=target.relative_to(root).as_posix()
                    if kind=='Folder': files.create_folder(area,rel)
                    else: files.write_text(area,rel,content)
                    st.rerun()
                except Exception as e: st.error(str(e))
        with tab2:
            ups=st.file_uploader('Files',accept_multiple_files=True,key=f'ups_{area}')
            if ups and st.button('Upload',type='primary',key=f'upload_{area}'):
                try:
                    batch=[]
                    for up in ups:
                        rel=(Path(folder)/Path(up.name).name).as_posix() if folder!='/' else Path(up.name).name
                        batch.append((rel,up.getvalue()))
                    files.write_many(area,batch,description=f'Upload {len(batch)} file(s) to {area}')
                    st.rerun()
                except Exception as e: st.error(str(e))
            z=st.file_uploader('Or extract a ZIP',type=['zip'],key=f'zip_{area}'); overwrite=st.checkbox('Overwrite existing',key=f'ow_{area}')
            if z and st.button('Extract ZIP',key=f'extract_{area}'):
                try:
                    files.vcs.checkpoint(f'Pre-change: extract ZIP into {area}/{folder}')
                    _extract_zip_into(z.getvalue(),root,folder,overwrite)
                    files.vcs.commit(f'Extract ZIP into {area}/{folder}',bind_timeline=False)
                    st.rerun()
                except Exception as e: st.error(str(e))
        with tab3:
            other='source' if area=='workspace' else 'workspace'; oroot=_root(store,other)
            opts=[p.relative_to(oroot).as_posix() for p in oroot.rglob('*')]
            if opts:
                rel=st.selectbox(f'From {AREAS[other][0]}',opts,key=f'import_{area}')
                mode=st.radio('Operation',['Copy','Move'],horizontal=True,key=f'import_mode_{area}')
                if st.button(f'{mode} here',type='primary',key=f'import_go_{area}'):
                    try: _transfer(store,other,rel,area,folder,mode=='Move'); st.rerun()
                    except Exception as e: st.error(str(e))
            else: st.info(f'{AREAS[other][0]} is empty.')

    entries=_entries(root,folder)
    if not entries: st.info('This folder is empty.'); return
    labels=[f"{'📁' if e['type']=='Folder' else '📄'} {e['name']}" for e in entries]
    selected=st.selectbox('Item',range(len(entries)),format_func=lambda i:labels[i],key=f'fm_item_{area}_{folder}')
    e=entries[selected]; p=_safe(root,e['path'])
    if e['type']=='Folder':
        c1,c2=st.columns([4,1]); c1.caption(f"Folder · {e['path']}")
        if c2.button('Open',use_container_width=True,key=f'open_{area}_{e["path"]}'):
            st.session_state[f'fm_pending_folder_{area}']=e['path']; st.rerun()
    else: st.caption(f"{e['path']} · {e['size']}")

    action=st.selectbox('Action',['View','Edit / replace','Rename / move','Copy','Move to other area','Copy to other area','Download','Delete'],key=f'action_{area}_{e["path"]}')
    if action=='View':
        if e['type']=='Folder': st.info('Open the folder to view its contents.')
        else: _preview(p,root)
    elif action=='Edit / replace':
        if e['type']=='Folder': st.info('Folders cannot be edited; rename/move them or edit their contents.')
        elif _is_text(p):
            text=st.text_area('Contents',p.read_text(encoding='utf-8',errors='replace'),height=430,key=f'edit_{area}_{e["path"]}')
            if st.button('Save changes',type='primary',key=f'save_{area}_{e["path"]}'):
                files.write_text(area,e['path'],text,overwrite=True,description=f'Edit {area}/{e["path"]}')
                st.success('Saved.')
        else:
            replacement=st.file_uploader('Replace binary file',key=f'replace_{area}_{e["path"]}')
            if replacement and st.button('Replace',type='primary',key=f'replace_go_{area}_{e["path"]}'):
                files.write_bytes(area,e['path'],replacement.getvalue(),overwrite=True,description=f'Replace {area}/{e["path"]}')
                st.rerun()
    elif action=='Rename / move':
        name=st.text_input('Name',value=p.name,key=f'rname_{area}_{e["path"]}'); dest=st.selectbox('Folder',folders,key=f'rdest_{area}_{e["path"]}')
        if st.button('Apply',type='primary',key=f'rgo_{area}_{e["path"]}'):
            try:
                target=_safe(root,str(Path(dest)/_clean_name(name)) if dest!='/' else _clean_name(name))
                if target!=p and target.exists(): raise FileExistsError('Destination already exists.')
                files.move(area,e['path'],area,target.relative_to(root).as_posix()); st.rerun()
            except Exception as ex: st.error(str(ex))
    elif action=='Copy':
        dest=st.selectbox('Destination folder',folders,key=f'cdest_{area}_{e["path"]}')
        if st.button('Copy',type='primary',key=f'cgo_{area}_{e["path"]}'):
            try:
                target=_safe(root,str(Path(dest)/p.name) if dest!='/' else p.name)
                if target.exists(): raise FileExistsError('Destination already exists.')
                files.copy(area,e['path'],area,target.relative_to(root).as_posix()); st.rerun()
            except Exception as ex: st.error(str(ex))
    elif action in {'Move to other area','Copy to other area'}:
        other='source' if area=='workspace' else 'workspace'; dests=_folders(_root(store,other)); dest=st.selectbox(f'{AREAS[other][0]} folder',dests,key=f'xarea_{area}_{e["path"]}')
        if st.button(action,type='primary',key=f'xgo_{area}_{e["path"]}'):
            try: _transfer(store,area,e['path'],other,dest,action.startswith('Move')); st.rerun()
            except Exception as ex: st.error(str(ex))
    elif action=='Download':
        if e['type']=='Folder': st.info('Folder download is not enabled here; use individual files.')
        else: st.download_button('Download file',p.read_bytes(),file_name=p.name,use_container_width=True)
    elif action=='Delete':
        st.warning(f"Delete {e['type'].lower()} '{e['name']}' permanently?")
        if st.button('Delete permanently',type='primary',key=f'del_{area}_{e["path"]}'):
            files.delete(area,e['path']); st.rerun()
