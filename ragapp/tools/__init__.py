from ragapp.tools.cognition_tools import build_cognition_tools
from ragapp.tools.workspace_files import build_workspace_file_tools
from ragapp.tools.regular_files import build_regular_file_tools
from ragapp.tools.docx_files import build_docx_tools
from ragapp.tools.pdf_files import build_pdf_tools
from ragapp.tools.xml_files import build_xml_tools
from ragapp.tools.pptx_files import build_pptx_tools
from ragapp.tools.xlsx_files import build_xlsx_tools
from ragapp.tools.binary_files import build_binary_file_tools
from ragapp.tools.project_files import build_project_file_tools
from ragapp.tools.vcs_tools import build_vcs_tools
from ragapp.tools.agent_tools import build_agent_tools
from ragapp.tools.workflow_tools import build_workflow_tools


def build_default_tools(username, store, include_cognition=True, session_id=None):
    """Build the agent toolset for the active project.

    Cognition is optional. The agent remains fully usable for ordinary chat and
    workspace/artifact operations before a project has been compiled.
    """
    tools = []
    tools.extend(build_project_file_tools())
    tools.extend(build_vcs_tools(store))
    tools.extend(build_agent_tools(store))
    tools.extend(build_workflow_tools(store))
    if include_cognition and store.exists():
        tools.extend(build_cognition_tools(store, session_id=session_id))
    tools.extend(build_workspace_file_tools(username))
    tools.extend(build_regular_file_tools(username))
    tools.extend(build_docx_tools(username))
    tools.extend(build_pdf_tools(username))
    tools.extend(build_xml_tools(username))
    tools.extend(build_pptx_tools(username))
    tools.extend(build_xlsx_tools(username))
    tools.extend(build_binary_file_tools(username))
    return tools
