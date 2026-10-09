"""Memory facade — the three pillars behind one small interface.

    procedural  SKILL.md files      how to act
    semantic    facts table (FTS5)  what is durably true
    episodic    episodes table      what happened, when

Plus the two agents that manage them:
    retrieval_gate   decides IF a turn needs memory   (hero moment #1)
    consolidation    distills chats into facts, every N exchanges
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import anthropic

from waku.config import Settings
from waku.memory import consolidation, retrieval_gate, slot_gate
from waku.memory.episodic.store import SqliteEpisodeStore
from waku.memory.procedural.loader import SkillLoader
from waku.memory.semantic.store import SqliteFactStore
from waku.ops.tracing import metered


def bundled_skill_dirs() -> list[Path]:
    """Where the skills that SHIP with Waku live — and why there are two answers.

    Contributors add skills to `skills/` at the repo root: that is what
    CONTRIBUTING.md documents, what CI validates, and what a checkout has. But
    the wheel only packages the `waku/` directory, so a `pip install waku-agent`
    would have found nothing there and silently started with zero skills —
    procedural memory, one of the four pillars, quietly missing. (It did, until
    2026-07-31.) pyproject force-includes the same folder into the wheel at
    `waku/skills`, so an installed Waku finds it next to the code.

    Exactly one of these exists at a time — the package copy only in a built
    wheel, the repo copy only in a checkout — so returning both is not a
    double-load, it is "wherever you installed from, the skills came too".
    """
    here = Path(__file__).resolve()
    return [p for p in (here.parents[1] / "skills", here.parents[2] / "skills") if p.is_dir()]


def _fact_file_text(row: sqlite3.Row) -> str:
    """One fact as the file the waku-memory importer reads (spec 003).

    Front matter first, then the fact. The text depends only on the row, so an
    unchanged fact produces an identical file and re-importing it is free.
    """
    def one_line(value) -> str:
        return " ".join(str(value or "").split())

    return (f"---\nsubject: {one_line(row['subject'])}\nsource: {one_line(row['source'])}\n"
            f"created_at: {one_line(row['created_at'])}\n---\n{row['content'].strip()}\n")


class Memory:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, client: anthropic.Anthropic,
                 episode_store=None):
        # episode_store: inject an already-built store (the dashboard caches ONE
        # NotionEpisodeStore process-wide — its constructor hits the network,
        # so building one per Memory would re-query Notion on every poll).
        self.conn = conn
        self.settings = settings
        self.client = client
        if settings.contract_review and (
            settings.semantic_store != "sqlite" or settings.episodic_store != "sqlite"
            or (episode_store is not None and not isinstance(episode_store, SqliteEpisodeStore))
        ):
            raise ValueError("ContractGuard Phase 1 requires SQLite semantic and episodic stores")
        self.facts = self._make_fact_store(conn, settings)
        self.episodes = episode_store if episode_store is not None else self._make_episode_store(conn, settings)
        self.skills = SkillLoader([*bundled_skill_dirs(), settings.home / "skills"])
        # Spec 006: sends each fact consolidation keeps to Waku Memory. app.py
        # sets it once the MCP servers have connected; None means no send.
        self.remember = None
        if settings.contract_review:
            from waku.memory.contractguard import seed_clause_knowledge

            seed_clause_knowledge(self.facts)

    @staticmethod
    def _make_fact_store(conn, settings):
        # Every branch here returns something that satisfies FactStore
        # (semantic/base.py) and is held to it by the conformance suite — which
        # is the whole reason a hosted service can stand in for local SQLite
        # without anything upstream noticing.
        if settings.semantic_store == "supabase":
            from waku.memory.semantic.supabase_store import SupabaseFactStore

            return SupabaseFactStore(settings)
        if settings.semantic_store == "mem0":
            from waku.memory.semantic.mem0_store import Mem0FactStore

            return Mem0FactStore(settings)
        if settings.semantic_store == "zep":
            from waku.memory.semantic.zep_store import ZepFactStore

            return ZepFactStore(settings)
        if settings.semantic_store == "langmem":
            from waku.memory.semantic.langmem_store import LangMemFactStore

            return LangMemFactStore(settings)
        return SqliteFactStore(conn)

    @staticmethod
    def _make_episode_store(conn, settings):
        if settings.episodic_store == "notion":
            from waku.memory.episodic.notion_store import NotionEpisodeStore

            return NotionEpisodeStore()
        return SqliteEpisodeStore(conn)

    # ---- retrieval (gated — see retrieval_gate.py for why)
    def gated_retrieve(self, message: str, notify=None) -> str:
        if self.settings.contract_review:
            import json

            context = self.review_context(message, notify=notify)
            return "\n".join(context['semantic'] + [
                json.dumps(r, ensure_ascii=False) for r in context['episodic']])
        # Spec 011 A2: with a notify, the gate's model call is counted too.
        client = metered(self.client, "gate", notify) if notify else self.client
        retrieve, query, reason = retrieval_gate.should_retrieve(
            client, self.settings.small_model, message
        )
        if notify:
            notify("gate", {"decision": "retrieve" if retrieve else "skip", "reason": reason})
        if not retrieve:
            return ""
        facts = self.facts.search(query, self.settings.retrieval_top_k)
        # Spec 005: Jev keeps only the facts that change the answer. Off by
        # default, and it fails open, so without WAKU_SLOT_GATE=jev this is
        # every fact, as before.
        facts, verdicts = slot_gate.select(message, facts)
        if notify and verdicts:
            notify("slot", {"verdicts": verdicts})
        found = facts + self.episodes.search(query, top_k=3)
        return "\n".join(found)

    def review_context(self, message: str, *, clause_type: str | None = None,
                       exclude_contract_id: str | None = None, notify=None) -> dict:
        """Separate domain stores for one review step; exclusion happens before top-k."""
        from waku.memory.contractguard import CLAUSE_NAMES, clause_facts, review_history

        if not self.settings.contract_review:
            raise ValueError("review context requires WAKU_CONTRACT_REVIEW=1")
        if clause_type is not None and clause_type not in CLAUSE_NAMES:
            raise ValueError("unknown canonical clause type")
        client = metered(self.client, "gate", notify) if notify else self.client

        decision = retrieval_gate.review_decision(client, self.settings.small_model, message)
        if clause_type and decision.clause_type not in (None, clause_type):
            decision = retrieval_gate.ReviewDecision(False, False, "", "gate selected another clause")
        selected_clause = clause_type or decision.clause_type
        if notify:
            notify("gate", {"decision": "retrieve" if decision.semantic or decision.episodic else "skip",
                            "reason": decision.reason, "semantic": decision.semantic,
                            "episodic": decision.episodic, "clause_type": selected_clause})
        facts, episodes = [], []
        if decision.semantic:
            facts = clause_facts(self.facts, decision.query, selected_clause,
                                 self.settings.retrieval_top_k)
            facts, verdicts = slot_gate.select(message, facts)
            if notify and verdicts:
                notify("slot", {"verdicts": verdicts})
        if decision.episodic:
            episodes = review_history(self.episodes, clause_type=selected_clause,
                                      query=decision.query, exclude_contract_id=exclude_contract_id)
        if notify and (decision.semantic or decision.episodic):
            notify("retrieval", {"facts": len(facts), "episodes": len(episodes)})
        return {'semantic': facts, 'episodic': episodes}

    # ---- procedural
    def matching_skills(self, message: str) -> str:
        from waku.memory.contractguard import CLAUSE_SKILLS

        # Filter before taking the loader's two slots; mode-off clause matches
        # must not crowd out ordinary bundled or user-authored skills.
        matched = self.skills.match(message, max_skills=len(self.skills.skills))
        if not self.settings.contract_review:
            matched = [s for s in matched if s.name not in CLAUSE_SKILLS]
        matched = matched[:2]
        return "\n\n".join(f"### {s.name}\n{s.body}" for s in matched)

    def complete_review(self, record: dict, source_text: str, *, candidates: list | None = None,
                        recalled: str = "", notify=None) -> list[dict]:
        """Explicit grounded review completion after validated extraction and risk assessment.

        Persist history immediately, then optionally learn short lexical cues.
        An empty candidate list skips the model; None requests model proposals.
        """
        from waku.memory.contractguard import store_review

        if not self.settings.contract_review:
            raise ValueError("completed reviews require WAKU_CONTRACT_REVIEW=1")
        validated, added = store_review(self.episodes, record, source_text)
        if notify and added:
            notify("review_episode", {"contract_id": validated['contract_id'],
                                      "clause_type": validated['clause_type']})
        kept = consolidation.consolidate_review(
            metered(self.client, "consolidation", notify) if notify else self.client,
            self.settings.small_model, validated, self.facts, candidates=candidates, recalled=recalled)
        if notify:
            notify("consolidation", {"new_facts": len(kept), "kept": kept})
        return kept

    # ---- write paths
    def log_chat(self, user_message: str, reply: str, session_id: str = "default",
                 source: str = "cli", meta: dict | None = None) -> int:
        """Store one exchange and return the assistant row's id, which
        `update_meta` takes once the turn's receipt is built."""
        import json as _json
        self.conn.execute(
            "INSERT INTO chat_log (role, content, session_id, source) VALUES ('user', ?, ?, ?)",
            (user_message, session_id, source),
        )
        # meta (gate/latency/iterations/tools) rides on the assistant row so a
        # reopened thread can render the full turn card, not just the text.
        row = self.conn.execute(
            "INSERT INTO chat_log (role, content, session_id, source, meta) VALUES ('assistant', ?, ?, ?, ?)",
            (reply, session_id, source, _json.dumps(meta) if meta else None),
        )
        self.conn.commit()
        return row.lastrowid

    def update_meta(self, row_id: int, meta: dict) -> None:
        """Replace one assistant row's meta. The turn's receipt (spec 011)
        counts what consolidation kept, and consolidation reads the chat log
        the exchange was just written to, so the receipt is added after."""
        import json as _json
        self.conn.execute("UPDATE chat_log SET meta = ? WHERE id = ?",
                          (_json.dumps(meta), row_id))
        self.conn.commit()

    # ---- sessions (for the dashboard's chat history + "New chat")
    def session_history(self, session_id: str) -> list[tuple[str, str]]:
        """The (user, assistant) exchanges of one past session, in order — used
        to reload working memory when the user switches back to a conversation."""
        rows = self.conn.execute(
            "SELECT role, content FROM chat_log WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        pairs, pending = [], None
        for r in rows:
            if r["role"] == "user":
                pending = r["content"]
            elif pending is not None:
                pairs.append((pending, r["content"]))
                pending = None
        return pairs

    def list_sessions(self) -> list[dict]:
        """One row per conversation: id, first user message (the title), message
        count, and when it started — newest first."""
        rows = self.conn.execute(
            """SELECT session_id,
                      COUNT(*) AS messages,
                      MIN(created_at) AS started_at,
                      MAX(created_at) AS last_at
               FROM chat_log GROUP BY session_id ORDER BY last_at DESC"""
        ).fetchall()
        out = []
        for r in rows:
            first = self.conn.execute(
                "SELECT content FROM chat_log WHERE session_id = ? AND role = 'user' ORDER BY id LIMIT 1",
                (r["session_id"],),
            ).fetchone()
            out.append({
                "id": r["session_id"],
                "title": (first["content"][:60] if first else "(empty)"),
                "messages": r["messages"],
                "started_at": r["started_at"],
                "last_at": r["last_at"],
            })
        return out

    def export_markdown(self) -> None:
        """Mirror memory to a human-readable MEMORY.md next to state.db — so the
        whiteboard's `~/.waku/MEMORY.md` box is literally real, and "your memory
        is a file you can open" is true. state.db stays the queryable source of
        truth; this file is a generated view, refreshed after each turn."""
        facts = self.conn.execute(
            "SELECT subject, content FROM facts ORDER BY subject, id"
        ).fetchall()
        eps = self.conn.execute(
            "SELECT happened_at, summary FROM episodes ORDER BY happened_at DESC, id DESC"
        ).fetchall()
        lines = [
            "# Waku memory",
            "",
            ("_A human-readable mirror of what Waku remembers. The source of truth is "
            "`state.db` (the `facts` and `episodes` tables, keyword-searchable via FTS5); "
            "this file is regenerated after every turn._"),
            "",
            f"## Facts — semantic memory ({len(facts)})",
            "",
        ]
        lines += [f"- **{f['subject']}** — {f['content']}" for f in facts] or ["_none yet_"]
        lines += ["", f"## Episodes — episodic memory ({len(eps)})", ""]
        lines += [f"- **{e['happened_at']}** — {e['summary']}" for e in eps] or ["_none yet_"]
        (self.settings.home / "MEMORY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.export_fact_files()

    def export_fact_files(self) -> None:
        """Write each fact to <home>/memory/<id>.md, one file per fact.

        This is the shape Claude Code's memory already has, so the waku-memory
        importer can upload Waku's facts one memory each. Episodes stay in
        state.db. Only files named <id>.md are ever removed, and only when
        their fact is gone; anything else in the folder is left alone.
        """
        folder = self.settings.home / "memory"
        folder.mkdir(parents=True, exist_ok=True)
        rows = self.conn.execute(
            "SELECT id, subject, content, source, created_at FROM facts").fetchall()
        current = set()
        for row in rows:
            path = folder / f"{row['id']}.md"
            current.add(path.name)
            text = _fact_file_text(row)
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                path.write_text(text, encoding="utf-8")
        for path in folder.glob("*.md"):
            if path.stem.isdigit() and path.name not in current:
                path.unlink()

    def maybe_consolidate(self, notify=None, report: str = "", recalled: str = "") -> None:
        """`report` is the research report this turn saved, if it saved one
        (spec 009 B): its findings stay in it, not in loose facts. `recalled`
        is the memory this turn read: a fact that only repeats it is not kept
        again."""
        # Free-form chat can contain reports or gold annotations. Only a
        # grounded complete_review event may write domain knowledge.
        if self.settings.contract_review:
            return
        kept = consolidation.kept_if_due(
            self.conn,
            metered(self.client, "consolidation", notify) if notify else self.client,
            self.settings.small_model,
            self.settings.consolidate_every,
            self.facts,
            self.episodes,
            remember=self.remember,
            report=report,
            recalled=recalled,
        )
        # Spec 006: `kept` lists each fact (subject, content, project, its
        # Waku Memory id when the send succeeded, and `sent`), so a chat panel
        # can show what the turn kept, link each one, and say which ones Waku
        # Memory did not take. `new_facts` stays the count.
        if kept and notify:
            notify("consolidation", {"new_facts": len(kept), "kept": kept})
