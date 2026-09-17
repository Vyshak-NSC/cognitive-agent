from __future__ import annotations
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc).isoformat()

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at", "by",
    "with", "from", "into", "this", "that", "these", "those", "is", "are", "was",
    "were", "be", "as", "it", "its", "their", "them", "we", "you", "your", "our",
    "use", "used", "using", "about", "over", "under", "after", "before", "through"
}

_SYNONYMS = {
    "project": ["project", "module", "component", "workspace"],
    "service": ["service", "endpoint", "api", "handler"],
    "data": ["data", "state", "payload", "record"],
    "config": ["config", "settings", "options", "parameters"],
    "file": ["file", "artifact", "document", "source"],
    "relationship": ["relationship", "dependency", "link", "connection"],
    "decision": ["decision", "choice", "policy", "direction"],
    "change": ["change", "update", "modification", "revision"],
    "issue": ["issue", "problem", "bug", "fault"],
    "memory": ["memory", "context", "state", "history"],
}


def _tokenize_query(value):
    raw = re.findall(r"[A-Za-z0-9_'-]+", str(value).lower())
    toks = []
    for token in raw:
        token = token.strip("'-_")
        if not token or token in _STOPWORDS:
            continue
        toks.append(token)
    return toks


def _expand_tokens(tokens):
    expanded = []
    seen = set()
    for token in tokens:
        if token not in seen:
            expanded.append(token)
            seen.add(token)
        for group in _SYNONYMS.values():
            if token in group:
                for sibling in group:
                    if sibling not in seen:
                        expanded.append(sibling)
                        seen.add(sibling)
    return expanded


class MetadataDB:
    """Local metadata/index database. It contains pointers and compact summaries,
    while the authoritative entity content remains in cognition/entities/*.json.
    No LLM calls are made by this class."""
    def __init__(self, store):
        self.path = store.root / ".system" / "metadata.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()
    def conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c
    def init(self):
        with self.conn() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS artifacts(
                id TEXT PRIMARY KEY, area TEXT, path TEXT, name TEXT,
                type TEXT, description TEXT, summary TEXT, sha256 TEXT,
                size INTEGER, modified_at TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_artifact_path ON artifacts(area,path);
            CREATE TABLE IF NOT EXISTS entities(
                id TEXT PRIMARY KEY, type TEXT, name TEXT, path TEXT,
                description TEXT, summary TEXT, tags TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
            CREATE TABLE IF NOT EXISTS entity_tags(
                entity_id TEXT, tag TEXT, PRIMARY KEY(entity_id,tag)
            );
            CREATE INDEX IF NOT EXISTS idx_entity_tags_tag ON entity_tags(tag);
            CREATE TABLE IF NOT EXISTS relations(
                id TEXT PRIMARY KEY, from_id TEXT, to_id TEXT,
                relation_type TEXT, description TEXT, timeline TEXT,
                source_artifact TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_rel_from ON relations(from_id);
            CREATE INDEX IF NOT EXISTS idx_rel_to ON relations(to_id);
            CREATE INDEX IF NOT EXISTS idx_rel_type ON relations(relation_type);
            CREATE TABLE IF NOT EXISTS sections(
                id TEXT PRIMARY KEY, entity_id TEXT, section_key TEXT,
                path TEXT, summary TEXT, tags TEXT, updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sections_entity ON sections(entity_id);
            CREATE TABLE IF NOT EXISTS attribute_states(
                id TEXT PRIMARY KEY, entity_id TEXT, attribute TEXT,
                value_json TEXT, summary TEXT, valid_from TEXT, valid_to TEXT,
                sequence INTEGER, source_artifact TEXT, source_locator TEXT,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_attr_entity ON attribute_states(entity_id,attribute);
            CREATE INDEX IF NOT EXISTS idx_attr_timeline ON attribute_states(valid_from,valid_to);
            CREATE TABLE IF NOT EXISTS events(
                id TEXT PRIMARY KEY, description TEXT, entities TEXT,
                timeline TEXT, source_artifact TEXT, source_locator TEXT,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_timeline ON events(timeline);
            CREATE TABLE IF NOT EXISTS source_chunks(
                id TEXT PRIMARY KEY, artifact_id TEXT, area TEXT, path TEXT,
                chunk_index INTEGER, locator TEXT, summary TEXT, content TEXT,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_artifact ON source_chunks(artifact_id);
            """)
    @staticmethod
    def _json(value):
        return json.dumps(value if value is not None else {}, ensure_ascii=False)
    def upsert_artifact(self, a):
        ident = a.get("id") or uuid.uuid4().hex
        t = now()
        with self.conn() as c:
            c.execute("""INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET area=excluded.area,path=excluded.path,name=excluded.name,
            type=excluded.type,description=excluded.description,summary=excluded.summary,
            sha256=excluded.sha256,size=excluded.size,modified_at=excluded.modified_at,updated_at=excluded.updated_at""",
            (ident,a.get("area"),a.get("path"),a.get("name"),a.get("type","file"),a.get("description",""),
             a.get("summary",""),a.get("sha256"),a.get("size"),a.get("modified_at"),a.get("created_at",t),t))
        return ident
    def upsert_entity(self, e):
        ident = e.get("id") or uuid.uuid4().hex
        t = now()
        tags = sorted({str(x) for x in e.get("tags",[])})
        with self.conn() as c:
            c.execute("""INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET type=excluded.type,name=excluded.name,path=excluded.path,
            description=excluded.description,summary=excluded.summary,tags=excluded.tags,updated_at=excluded.updated_at""",
            (ident,e.get("type","other"),e.get("name",ident),e.get("path"),e.get("description",e.get("summary","")),
             e.get("summary",""),self._json(tags),e.get("created_at",t),t))
            c.execute("DELETE FROM entity_tags WHERE entity_id=?",(ident,))
            c.executemany("INSERT OR IGNORE INTO entity_tags(entity_id,tag) VALUES(?,?)",[(ident,t) for t in tags])
        return ident
    def upsert_section(self, s):
        ident=s.get("id") or f"{s['entity_id']}:{s['section_key']}"
        with self.conn() as c:
            c.execute("""INSERT INTO sections VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET entity_id=excluded.entity_id,section_key=excluded.section_key,
            path=excluded.path,summary=excluded.summary,tags=excluded.tags,updated_at=excluded.updated_at""",
            (ident,s["entity_id"],s["section_key"],s.get("path"),s.get("summary",""),self._json(s.get("tags",[])),now()))
        return ident

    
    def delete_entity(self, entity_id):
        with self.conn() as c:
            c.execute(
                "DELETE FROM entity_tags WHERE entity_id=?",
                (entity_id,),
            )
            c.execute(
                "DELETE FROM sections WHERE entity_id=?",
                (entity_id,),
            )
            c.execute(
                "DELETE FROM attribute_states WHERE entity_id=?",
                (entity_id,),
            )
            c.execute(
                "DELETE FROM entities WHERE id=?",
                (entity_id,),
            )

    def delete_artifact(self, artifact_id):
        with self.conn() as c:
            c.execute(
                "DELETE FROM source_chunks WHERE artifact_id=?",
                (artifact_id,),
            )
            c.execute(
                "DELETE FROM artifacts WHERE id=?",
                (artifact_id,),
            )

    def delete_relations_for_artifact(self, artifact_id):
        with self.conn() as c:
            c.execute(
                "DELETE FROM relations WHERE source_artifact=?",
                (artifact_id,),
            )

    def delete_relations_for_entities(self, entity_ids):
        entity_ids = [str(x) for x in (entity_ids or []) if str(x)]
        if not entity_ids:
            return
        marks = ",".join("?" * len(entity_ids))
        with self.conn() as c:
            c.execute(
                f"DELETE FROM relations WHERE from_id IN ({marks}) OR to_id IN ({marks})",
                entity_ids + entity_ids,
            )

    def add_attribute_state(self,s):
        ident=s.get("id") or uuid.uuid4().hex
        with self.conn() as c:
            c.execute("""INSERT OR REPLACE INTO attribute_states VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (ident,s["entity_id"],s["attribute"],self._json(s.get("value")),s.get("summary",""),s.get("valid_from"),s.get("valid_to"),
             s.get("sequence",0),s.get("source_artifact"),s.get("source_locator"),s.get("created_at",now())))
        return ident
    def add_relation(self,r):
        ident=r.get("id") or uuid.uuid4().hex
        with self.conn() as c:
            c.execute("""INSERT OR REPLACE INTO relations VALUES(?,?,?,?,?,?,?,?,?)""",
            (ident,r["from_id"],r["to_id"],r.get("relation_type","related_to"),r.get("description",""),r.get("timeline"),r.get("source_artifact"),r.get("created_at",now()),now()))
        return ident
    def add_event(self,e):
        ident=e.get("id") or uuid.uuid4().hex
        with self.conn() as c:
            c.execute("""INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?)""",
            (ident,e.get("description",e.get("event","")),self._json(e.get("entities",[])),e.get("timeline"),e.get("source_artifact"),e.get("source_locator"),e.get("created_at",now())))
        return ident
    def rebuild_event_index(self, store):
        """Rebuild the derived SQLite event index from canonical event JSON files."""
        with self.conn() as c:
            c.execute("DELETE FROM events")
        for event in store.events(limit=100000):
            self.add_event({
                "id": event.get("id"),
                "description": event.get("description", ""),
                "entities": event.get("entities", []),
                "timeline": (event.get("narrative_position") or {}).get("label") or event.get("timeline"),
                "source_artifact": event.get("source_artifact"),
                "source_locator": event.get("source_locator"),
                "created_at": event.get("created_at", now()),
            })

    def delete_events_for_artifact(self, artifact_id):
        with self.conn() as c:
            c.execute("DELETE FROM events WHERE source_artifact=?", (artifact_id,))

    def delete_source_chunks_for_artifact(self, artifact_id):
        with self.conn() as c:
            c.execute("DELETE FROM source_chunks WHERE artifact_id=?", (artifact_id,))

    def rebuild_entity_index(self, store):
        """Rebuild entities/sections/attributes tables from authoritative entity JSON.

        Relations/events are intentionally preserved because they have their own
        source_artifact provenance and are cleaned separately when needed.
        """
        master = store.master_metadata()
        with self.conn() as c:
            c.execute("DELETE FROM entity_tags")
            c.execute("DELETE FROM sections")
            c.execute("DELETE FROM attribute_states")
            c.execute("DELETE FROM entities")

        for eid, meta in master.get("entities", {}).items():
            self.upsert_entity(meta)
            path = store.root / meta.get("path", "")
            data = store._read_json(path, {}) if path.exists() else {}
            for section_key, section in (data.get("sections") or {}).items():
                self.upsert_section({
                    "entity_id": eid,
                    "section_key": section_key,
                    "path": meta.get("path"),
                    "summary": section.get("summary", "") if isinstance(section, dict) else str(section),
                    "tags": section.get("tags", []) if isinstance(section, dict) else [],
                })
            for attr, states in (data.get("attributes") or {}).items():
                for state in states or []:
                    self.add_attribute_state({
                        "id": f"{eid}:{attr}:{state.get('sequence', 0)}:{state.get('source_artifact') or 'local'}",
                        "entity_id": eid,
                        "attribute": attr,
                        "value": state.get("value"),
                        "summary": state.get("summary", ""),
                        "valid_from": state.get("valid_from") or state.get("timeline"),
                        "valid_to": state.get("valid_to"),
                        "sequence": state.get("sequence", 0),
                        "source_artifact": state.get("source_artifact"),
                        "source_locator": state.get("source_locator"),
                        "created_at": state.get("recorded_at"),
                    })

    def sync_entity_index(self, store, entity_id):
        """Refresh the SQLite projection for one authoritative entity JSON file."""
        entity_id = str(entity_id)
        master = store.master_metadata()
        meta = master.get("entities", {}).get(entity_id)
        if not meta:
            return

        path = store.root / meta.get("path", "")
        data = store._read_json(path, {}) if path.exists() else {}

        with self.conn() as c:
            c.execute("DELETE FROM entity_tags WHERE entity_id=?", (entity_id,))
            c.execute("DELETE FROM sections WHERE entity_id=?", (entity_id,))
            c.execute("DELETE FROM attribute_states WHERE entity_id=?", (entity_id,))
            c.execute("DELETE FROM entities WHERE id=?", (entity_id,))

        self.upsert_entity({
            **meta,
            "description": data.get("summary", meta.get("summary", "")),
            "summary": data.get("summary", meta.get("summary", "")),
            "tags": data.get("tags", meta.get("tags", [])),
        })

        for section_key, section in (data.get("sections") or {}).items():
            self.upsert_section({
                "entity_id": entity_id,
                "section_key": section_key,
                "path": meta.get("path"),
                "summary": section.get("summary", "") if isinstance(section, dict) else str(section),
                "tags": section.get("tags", []) if isinstance(section, dict) else [],
            })

        for attr, states in (data.get("attributes") or {}).items():
            for state in states or []:
                self.add_attribute_state({
                    "id": f"{entity_id}:{attr}:{state.get('sequence', 0)}:{state.get('source_artifact') or 'local'}",
                    "entity_id": entity_id,
                    "attribute": attr,
                    "value": state.get("value"),
                    "summary": state.get("summary", ""),
                    "valid_from": state.get("valid_from") or state.get("timeline"),
                    "valid_to": state.get("valid_to"),
                    "sequence": state.get("sequence", 0),
                    "source_artifact": state.get("source_artifact"),
                    "source_locator": state.get("source_locator"),
                    "created_at": state.get("recorded_at"),
                })

    def add_source_chunk(self,cdata):
        ident=cdata.get("id") or uuid.uuid4().hex
        with self.conn() as c:
            c.execute("""INSERT OR REPLACE INTO source_chunks VALUES(?,?,?,?,?,?,?,?,?)""",
            (ident,cdata.get("artifact_id"),cdata.get("area"),cdata.get("path"),cdata.get("chunk_index",0),cdata.get("locator"),cdata.get("summary",""),cdata.get("content",""),cdata.get("created_at",now())))
        return ident
    def entities(self,limit=1000):
        with self.conn() as c: rows=c.execute("SELECT * FROM entities ORDER BY name LIMIT ?",(limit,)).fetchall()
        return [dict(r) for r in rows]
    def get_entity(self,ident):
        with self.conn() as c: r=c.execute("SELECT * FROM entities WHERE id=?",(ident,)).fetchone()
        return dict(r) if r else None
    def search_entities(self,tokens,limit=30):
        toks = _expand_tokens(_tokenize_query(" ".join(str(x) for x in (tokens or []))))
        if not toks:
            return []

        rows = []
        with self.conn() as c:
            base = c.execute("SELECT e.id, e.type, e.name, e.path, e.description, e.summary, e.tags, e.created_at, e.updated_at FROM entities e LEFT JOIN entity_tags t ON t.entity_id=e.id").fetchall()
            for row in base:
                data = dict(row)
                name = (data.get("name") or "").lower()
                summary = (data.get("summary") or "").lower()
                tags = (data.get("tags") or "")
                score = 0
                for token in toks:
                    if token in name:
                        score += 10 if name == token else 6
                    if token in summary:
                        score += 4
                    if token in (tags or "").lower():
                        score += 5
                    if data.get("id", "").lower().startswith(token):
                        score += 3
                if score > 0:
                    rows.append((data["id"], score, data))

        ranked = sorted(rows, key=lambda item: (-item[1], item[0]))[:max(1, limit)]
        return [item[2] for item in ranked]
    def relations_for(self,entity_ids,limit=100):
        if not entity_ids:return []
        marks=','.join('?'*len(entity_ids)); args=list(entity_ids)+list(entity_ids)
        with self.conn() as c:
            rows=c.execute(f"SELECT * FROM relations WHERE from_id IN ({marks}) OR to_id IN ({marks}) LIMIT ?",args+[limit]).fetchall()
        return [dict(r) for r in rows]
    def sections_for(self,entity_ids,limit=200):
        if not entity_ids:return []
        marks=','.join('?'*len(entity_ids))
        with self.conn() as c: rows=c.execute(f"SELECT * FROM sections WHERE entity_id IN ({marks}) LIMIT ?",list(entity_ids)+[limit]).fetchall()
        return [dict(r) for r in rows]
    def attribute_states(self,entity_ids,attributes=None,timeline=None,limit=500):
        if not entity_ids:return []
        marks=','.join('?'*len(entity_ids)); args=list(entity_ids)
        sql=f"SELECT * FROM attribute_states WHERE entity_id IN ({marks})"
        if attributes:
            am=','.join('?'*len(attributes)); sql+=f" AND attribute IN ({am})"; args+=list(attributes)
        if timeline:
            sql+=" AND (valid_from IS NULL OR valid_from <= ?) AND (valid_to IS NULL OR valid_to >= ?)"; args += [timeline,timeline]
        sql+=" ORDER BY entity_id,attribute,sequence DESC LIMIT ?"; args.append(limit)
        with self.conn() as c: rows=c.execute(sql,args).fetchall()
        out=[]
        for r in rows:
            d=dict(r); d["value"]=json.loads(d.pop("value_json")); out.append(d)
        return out
    def source_chunks_for(self,artifact_ids=None,paths=None,limit=50):
        clauses=[]; args=[]
        if artifact_ids:
            marks=','.join('?'*len(artifact_ids)); clauses.append(f"artifact_id IN ({marks})"); args+=list(artifact_ids)
        if paths:
            marks=','.join('?'*len(paths)); clauses.append(f"path IN ({marks})"); args+=list(paths)
        where=" WHERE "+" OR ".join(clauses) if clauses else ""
        with self.conn() as c: rows=c.execute(f"SELECT * FROM source_chunks{where} ORDER BY chunk_index LIMIT ?",args+[limit]).fetchall()
        return [dict(r) for r in rows]
    def relations(self,limit=2000):
        with self.conn() as c: rows=c.execute("SELECT * FROM relations LIMIT ?",(limit,)).fetchall()
        return [dict(r) for r in rows]
    def events(self,limit=1000):
        with self.conn() as c: rows=c.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
        return [dict(r) for r in rows]
