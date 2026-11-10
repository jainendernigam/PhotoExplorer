"""Auto organize: saved Organize settings for chosen people.

"Run auto organize" takes, for every switched-on rule, the photos of its
people that are not inside its destination yet, and starts a normal Organize
job for them (so the report and Undo work as usual). Photos that are already
there are skipped by the plan, so running it again only files what is new.
"""
from __future__ import annotations

import json
import os
import time

from ..db import Database
from ..faces import people as people_mod
from .runner import MODES, OrganizeError, OrganizeManager, _inside, parse_options


class Rules:
    def __init__(self, db: Database, organizer: OrganizeManager):
        self.db, self.org = db, organizer

    def list(self) -> list[dict]:
        out = []
        names = {r[0]: r[1] for r in self.db.conn.execute("SELECT id, name FROM persons")}
        for r in self.db.conn.execute("SELECT * FROM organize_rules ORDER BY id"):
            d = dict(r)
            d["options"], d["people"] = json.loads(d["options"]), json.loads(d["people"])
            d["people_names"] = [names[p] for p in d["people"] if p in names]
            d["last_result"] = json.loads(d["last_result"]) if d["last_result"] else None
            job = self.db.conn.execute("SELECT status, done, undone_at FROM organize_jobs WHERE id = ?",
                                       (d["last_job_id"],)).fetchone() if d["last_job_id"] else None
            d["last_job"] = dict(job) if job else None     # for "Undo last run"
            out.append(d)
        return out

    def create(self, name: str, dest_root: str, mode: str, options: dict, people: list[int], people_mode: str) -> int:
        self._validate(dest_root, mode, options, people, people_mode)
        return self.db.conn.execute(
            "INSERT INTO organize_rules(name, dest_root, mode, options, people, people_mode, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ((name or "").strip()[:80] or "Auto organize", dest_root, mode, json.dumps(options),
             json.dumps(sorted(set(people))), people_mode, int(time.time()))).lastrowid

    def _validate(self, dest_root: str, mode: str, options: dict, people: list[int], people_mode: str) -> None:
        if mode not in MODES:
            raise OrganizeError("mode must be copy, hardlink, symlink or move")
        if not people:
            raise OrganizeError("Tick at least one person for a rule")
        if people_mode not in ("any", "all"):
            raise OrganizeError("people_mode must be any or all")
        if not dest_root or not os.path.isabs(dest_root):
            raise OrganizeError("Choose a destination folder")
        parse_options(options)                     # same checks as a normal Organize
        known = {r[0] for r in self.db.conn.execute("SELECT id FROM persons")}
        if not set(people) <= known:
            raise OrganizeError("Unknown person in the rule")

    def update(self, rule_id: int, name: str | None = None, enabled: bool | None = None, **fields) -> None:
        """Change a rule. fields: dest_root, mode, options, people, people_mode (any left out stay as they are).
        Photos it already filed stay where they are; the next run uses the new settings."""
        cur = next((r for r in self.list() if r["id"] == rule_id), None)
        if cur is None:
            raise OrganizeError("Rule not found")
        fields = {k: v for k, v in fields.items() if v is not None}
        if fields:
            new = {k: fields.get(k, cur[k]) for k in ("dest_root", "mode", "options", "people", "people_mode")}
            self._validate(**new)
            self.db.conn.execute(
                "UPDATE organize_rules SET dest_root = ?, mode = ?, options = ?, people = ?, people_mode = ? WHERE id = ?",
                (new["dest_root"], new["mode"], json.dumps(new["options"]), json.dumps(sorted(set(new["people"]))),
                 new["people_mode"], rule_id))
        if name is not None:
            self.db.conn.execute("UPDATE organize_rules SET name = ? WHERE id = ?", (name.strip()[:80] or "Auto organize", rule_id))
        if enabled is not None:
            self.db.conn.execute("UPDATE organize_rules SET enabled = ? WHERE id = ?", (int(enabled), rule_id))

    def delete(self, rule_id: int) -> None:
        self.db.conn.execute("DELETE FROM organize_rules WHERE id = ?", (rule_id,))

    def candidates(self, rule: dict) -> list[int]:
        cond, args = people_mod.media_filter_sql(rule["people"], rule["people_mode"])
        rows = self.db.conn.execute(
            f"SELECT m.id, m.path FROM media m WHERE m.status = 1 AND {cond} ORDER BY m.taken_at, m.id", args).fetchall()
        return [r[0] for r in rows if not _inside(r[1], rule["dest_root"])]

    def run(self, rule_ids: list[int] | None = None) -> list[dict]:
        """Starts one Organize job per rule that has something new to file."""
        results = []
        for rule in self.list():
            if rule_ids is not None and rule["id"] not in rule_ids:
                continue
            if rule_ids is None and not rule["enabled"]:
                continue
            res: dict = {"rule_id": rule["id"], "name": rule["name"], "job_id": None, "new": 0}
            try:
                ids = self.candidates(rule)
                opts = {**rule["options"], "persons": rule["options"].get("persons") or rule["people"]}
                if ids:
                    pv = self.org.preview(ids, rule["dest_root"], rule["mode"], opts, sample=0)
                    if pv["errors"]:
                        res["error"] = "; ".join(pv["errors"])
                    elif pv["new"]:
                        res["new"] = pv["new"]
                        res["job_id"] = self.org.start(ids, rule["dest_root"], rule["mode"], opts)
                res["message"] = res.get("error") or (f"{res['new']} new photo(s) being filed" if res["new"]
                                                      else "Nothing new")
            except (OrganizeError, OSError, ValueError) as e:
                res["error"] = res["message"] = str(e)
            self.db.conn.execute("UPDATE organize_rules SET last_run_at = ?, last_job_id = coalesce(?, last_job_id), "
                                 "last_result = ? WHERE id = ?",
                                 (int(time.time()), res["job_id"], json.dumps(res), rule["id"]))
            results.append(res)
        return results
