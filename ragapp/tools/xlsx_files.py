"""Excel tools using openpyxl; cell edits preserve existing workbook formatting."""
from openpyxl import Workbook, load_workbook
from ragapp.tools.definitions import Tool
from ragapp.workspace.manager import resolve_workspace_path

def _path(u,p): return resolve_workspace_path(u,p)
def read_xlsx(username,relative_path, sheet=None, max_rows=100, max_cols=30):
    path=_path(username,relative_path)
    if not path.is_file(): raise FileNotFoundError(f"File not found: {relative_path}")
    wb=load_workbook(path, data_only=False); names=wb.sheetnames if sheet is None else [sheet]
    out=[]
    for name in names:
        ws=wb[name]; rows=[]
        for row in ws.iter_rows(min_row=1,max_row=min(ws.max_row,max_rows),min_col=1,max_col=min(ws.max_column,max_cols)):
            rows.append([c.value for c in row])
        out.append({"name":name,"rows":rows})
    return {"path":relative_path,"sheets":out}
def write_xlsx(username,relative_path,sheets):
    path=_path(username,relative_path); path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): raise FileExistsError(f"File already exists: {relative_path}")
    wb=Workbook(); first=True
    for spec in sheets:
        ws=wb.active if first else wb.create_sheet(); first=False; ws.title=spec["name"]
        for i,row in enumerate(spec.get("rows",[]),1):
            for j,value in enumerate(row,1): ws.cell(i,j,value)
    wb.save(path); return {"path":relative_path,"status":"created"}
def edit_xlsx(username,relative_path,sheet,cell,value=None,operation="set"):
    path=_path(username,relative_path)
    if not path.is_file(): raise FileNotFoundError(f"File not found: {relative_path}")
    wb=load_workbook(path); ws=wb[sheet]
    if operation=="set": ws[cell]=value
    elif operation=="clear": ws[cell].value=None
    else: raise ValueError("operation must be set or clear")
    wb.save(path); return {"path":relative_path,"status":"edited"}
def delete_xlsx(username,relative_path):
    path=_path(username,relative_path)
    if not path.is_file(): raise FileNotFoundError(f"File not found: {relative_path}")
    path.unlink(); return {"path":relative_path,"status":"deleted"}
def build_xlsx_tools(username):
    return [
      Tool("read_xlsx","Read workbook sheets and cell values without flattening the workbook to plain text.",{"type":"object","properties":{"relative_path":{"type":"string"},"sheet":{"type":"string"},"max_rows":{"type":"integer"},"max_cols":{"type":"integer"}},"required":["relative_path"]},lambda relative_path,sheet=None,max_rows=100,max_cols=30:read_xlsx(username,relative_path,sheet,max_rows,max_cols)),
      Tool("write_xlsx","Create a real .xlsx workbook from sheet/row specifications.",{"type":"object","properties":{"relative_path":{"type":"string"},"sheets":{"type":"array","items":{"type":"object"}}},"required":["relative_path","sheets"]},lambda relative_path,sheets:write_xlsx(username,relative_path,sheets)),
      Tool("edit_xlsx","Edit an individual Excel cell while retaining the existing workbook and cell formatting.",{"type":"object","properties":{"relative_path":{"type":"string"},"sheet":{"type":"string"},"cell":{"type":"string"},"value":{},"operation":{"type":"string","enum":["set","clear"]}},"required":["relative_path","sheet","cell","operation"]},lambda relative_path,sheet,cell,value=None,operation="set":edit_xlsx(username,relative_path,sheet,cell,value,operation)),
      Tool("delete_xlsx","Delete an existing Excel workbook from the AI workspace.",{"type":"object","properties":{"relative_path":{"type":"string"}},"required":["relative_path"]},lambda relative_path:delete_xlsx(username,relative_path)),
    ]
