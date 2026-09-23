"""XML tools using lxml so edits target the XML tree rather than flattening it to text."""
from lxml import etree
from ragapp.tools.definitions import Tool
from ragapp.workspace.manager import resolve_workspace_path

def _path(username,p): return resolve_workspace_path(username,p)
def read_xml(username, relative_path):
    path=_path(username,relative_path)
    if not path.is_file(): raise FileNotFoundError(f"File not found: {relative_path}")
    raw=path.read_text(encoding="utf-8")
    root=etree.fromstring(raw.encode("utf-8"), parser=etree.XMLParser(remove_blank_text=False))
    return {"path":relative_path,"content":raw,"root":root.tag,"pretty_content":etree.tostring(root,encoding="unicode",pretty_print=True)}
def write_xml(username,relative_path,content):
    path=_path(username,relative_path); path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): raise FileExistsError(f"File already exists: {relative_path}")
    etree.fromstring(content.encode("utf-8")); path.write_text(content,encoding="utf-8")
    return {"path":relative_path,"status":"created"}
def edit_xml(username,relative_path,operation,xpath=None,value=None,attribute=None,element_xml=None):
    path=_path(username,relative_path)
    if not path.is_file(): raise FileNotFoundError(f"File not found: {relative_path}")
    parser=etree.XMLParser(remove_blank_text=False); tree=etree.parse(str(path),parser); matches=tree.xpath(xpath) if xpath else []
    if operation=="set_text":
        if not matches: raise ValueError("XPath matched no nodes")
        for node in matches: node.text=value
    elif operation=="set_attribute":
        if not matches or attribute is None: raise ValueError("set_attribute requires a matching XPath and attribute")
        for node in matches: node.set(attribute,value or "")
    elif operation=="delete":
        if not matches: raise ValueError("XPath matched no nodes")
        for node in matches: node.getparent().remove(node)
    elif operation=="append":
        if not matches or element_xml is None: raise ValueError("append requires a parent XPath and element_xml")
        child=etree.fromstring(element_xml.encode("utf-8"))
        for node in matches: node.append(etree.fromstring(element_xml.encode("utf-8")))
    else: raise ValueError("Unsupported XML edit operation")
    tree.write(str(path),encoding="utf-8",xml_declaration=True,pretty_print=False)
    return {"path":relative_path,"status":"edited"}
def delete_xml(username,relative_path):
    path=_path(username,relative_path)
    if not path.is_file(): raise FileNotFoundError(f"File not found: {relative_path}")
    path.unlink(); return {"path":relative_path,"status":"deleted"}
def build_xml_tools(username):
    return [
      Tool("read_xml","Read XML as raw content plus parsed structure; preserve XML rather than flattening it.",{"type":"object","properties":{"relative_path":{"type":"string"}},"required":["relative_path"]},lambda relative_path:read_xml(username,relative_path)),
      Tool("write_xml","Create a valid XML file from exact XML content.",{"type":"object","properties":{"relative_path":{"type":"string"},"content":{"type":"string"}},"required":["relative_path","content"]},lambda relative_path,content:write_xml(username,relative_path,content)),
      Tool("edit_xml","Edit XML using XPath operations so attributes/elements remain structured.",{"type":"object","properties":{"relative_path":{"type":"string"},"operation":{"type":"string","enum":["set_text","set_attribute","delete","append"]},"xpath":{"type":"string"},"value":{"type":"string"},"attribute":{"type":"string"},"element_xml":{"type":"string"}},"required":["relative_path","operation","xpath"]},lambda relative_path,operation,xpath=None,value=None,attribute=None,element_xml=None:edit_xml(username,relative_path,operation,xpath,value,attribute,element_xml)),
      Tool("delete_xml","Delete an existing XML file from the AI workspace.",{"type":"object","properties":{"relative_path":{"type":"string"}},"required":["relative_path"]},lambda relative_path:delete_xml(username,relative_path)),
    ]
