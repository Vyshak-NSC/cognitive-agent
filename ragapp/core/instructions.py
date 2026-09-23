from __future__ import annotations
import sqlite3, uuid
from datetime import datetime, timezone

def now(): return datetime.now(timezone.utc).isoformat()
class InstructionStore:
    def __init__(self,store):
        self.path=store.root/'.system'/'instructions.db'; self.path.parent.mkdir(parents=True,exist_ok=True); self.init()
    def conn(self): c=sqlite3.connect(self.path); c.row_factory=sqlite3.Row; return c
    def init(self):
        with self.conn() as c: c.execute('''CREATE TABLE IF NOT EXISTS instructions(id TEXT PRIMARY KEY,scope TEXT NOT NULL,tagged_entity_id TEXT,content TEXT NOT NULL,status TEXT NOT NULL,origin TEXT NOT NULL,created_at TEXT NOT NULL,deactivated_at TEXT)'''); c.execute('CREATE INDEX IF NOT EXISTS idx_instr_status ON instructions(status)')
    def list(self,status=None):
        with self.conn() as c: rows=c.execute('SELECT * FROM instructions '+('WHERE status=? ' if status else '')+'ORDER BY created_at DESC',((status,) if status else ())).fetchall()
        return [dict(r) for r in rows]
    def conflicts(self,content,scope='situational',tagged_entity_id=None):
        words=set(content.lower().split()); out=[]
        for x in self.list('active'):
            if x['scope']!=scope or (scope=='file_tagged' and x['tagged_entity_id']!=tagged_entity_id): continue
            old=set(x['content'].lower().split());
            if ('never' in words and 'never' not in old) or ('never' in old and 'never' not in words): out.append(x)
        return out
    def add(self,content,scope='situational',tagged_entity_id=None,origin='user_stated'): 
        i=str(uuid.uuid4());
        with self.conn() as c: c.execute('INSERT INTO instructions VALUES(?,?,?,?,?,?,?,?)',(i,scope,tagged_entity_id,content,'active',origin,now(),None))
        return i
    def deactivate(self,i):
        with self.conn() as c: c.execute('UPDATE instructions SET status="deactivated",deactivated_at=? WHERE id=?',(now(),i))
    def update(self,i,content):
        with self.conn() as c: c.execute('UPDATE instructions SET content=? WHERE id=? AND status="active"',(content,i))
    def applicable(self,entity_ids=None):
        entity_ids=set(entity_ids or []); out=[]
        for x in self.list('active'):
            if x['scope']=='system' or x['scope']=='situational' or (x['scope']=='file_tagged' and x['tagged_entity_id'] in entity_ids): out.append(x)
        return out
