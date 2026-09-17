class ToolRegistry:
    def __init__(self,tools): self._tools={t.name:t for t in tools}
    def as_function_declarations(self): return [{"name":t.name,"description":t.description,"parameters":t.parameters} for t in self._tools.values()]
    def call(self,name,args):
        if name not in self._tools:
            return {"status":"error","error":f"Unknown tool: {name}","available_tools":sorted(self._tools.keys())}
        try: return self._tools[name].handler(**args)
        except Exception as exc: return {"status":"error","error":str(exc),"received_arguments":args}