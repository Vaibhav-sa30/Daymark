"""Focused tests for persistent portfolio check-ins and offline event delivery."""

import tempfile
import unittest
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import portfolio_updates as store
import portfolio_github
import server
from unittest.mock import patch


class PortfolioStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "portfolio.sqlite3"
        self.now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_prompt_claim_persists_across_store_restarts_and_skips_missed_intervals(self):
        self.assertFalse(store.claim_due_checkin(self.now, self.db))
        self.assertFalse(store.claim_due_checkin(self.now.replace(second=30), self.db))
        self.assertTrue(store.claim_due_checkin(self.now.replace(minute=1), self.db))
        # A long sleep produces one prompt, with the timestamp moved forward.
        resumed = self.now.replace(day=27, hour=15)
        self.assertTrue(store.claim_due_checkin(resumed, self.db))
        self.assertFalse(store.claim_due_checkin(resumed, self.db))

    def test_snooze_reminds_at_snooze_deadline(self):
        store.claim_due_checkin(self.now, self.db)
        prompt_time = self.now.replace(hour=13)
        self.assertTrue(store.claim_due_checkin(prompt_time, self.db))
        store.snooze_checkin(prompt_time, self.db)
        self.assertFalse(store.claim_due_checkin(prompt_time.replace(hour=13, minute=59), self.db))
        self.assertTrue(store.claim_due_checkin(prompt_time.replace(hour=14), self.db))

    def test_waking_window_can_cross_midnight(self):
        store.save_checkin_settings({
            "waking_start_hour": 20,
            "waking_end_hour": 6,
        }, self.db)
        self.assertFalse(store.claim_due_checkin(self.now.replace(hour=21), self.db))
        self.assertFalse(store.claim_due_checkin(self.now.replace(hour=20, minute=1), self.db))
        self.assertTrue(store.claim_due_checkin(self.now.replace(day=26, hour=0), self.db))
        self.assertFalse(store.claim_due_checkin(self.now.replace(day=26, hour=7), self.db))

    def test_real_checkin_is_saved_and_queued_once(self):
        saved = store.record_checkin("nothing_to_update", source="daymark_popup",
                                     now=self.now, db_path=self.db)
        rows = store.list_checkins(db_path=self.db)
        pending = store.pending_outbox(now=self.now, db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], saved["id"])
        self.assertEqual(rows[0]["response"], "nothing_to_update")
        self.assertEqual(len(pending), 1)
        store.mark_outbox_synced(pending[0]["event_id"], self.now, self.db)
        self.assertEqual(store.pending_outbox(now=self.now, db_path=self.db), [])

    def test_update_draft_validates_section_and_links(self):
        draft = store.create_update("Built a prototype; evaluation is ongoing.",
                                    "research_implementations",
                                    ["https://github.com/example/project"], self.db)
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["supporting_links"], ["https://github.com/example/project"])
        with self.assertRaises(ValueError):
            store.create_update("A", "not_a_section", [], self.db)
        with self.assertRaises(ValueError):
            store.create_update("A", "other", ["javascript:alert(1)"], self.db)

    def test_proposal_and_lifecycle_state_are_persisted(self):
        update = store.create_update("Built an experiment; evaluation is ongoing.",
                                     "research_implementations", [], self.db)
        proposal = {
            "summary": "Add an ongoing experiment", "suggested_category": "research_implementations",
            "target_file": "research-implementations.html", "operation": "add_timeline_entry",
            "title": "Experiment", "date": "November 2026", "description": "Evaluation is ongoing.",
            "links": [], "clarifying_questions": [],
        }
        store.save_proposal(update["id"], proposal, db_path=self.db)
        store.update_workflow_state(update["id"], "awaiting_preview",
                                    fields={"branch_name": "portfolio/update-test"},
                                    event_type="branch_created", metadata={"path": "research-implementations.html"},
                                    db_path=self.db)
        loaded = store.get_update(update["id"], self.db)
        self.assertEqual(loaded["proposal"], proposal)
        self.assertEqual(loaded["status"], "awaiting_preview")
        self.assertEqual(loaded["branch_name"], "portfolio/update-test")
        self.assertEqual([e["event_type"] for e in store.list_update_events(update["id"], self.db)],
                         ["update_submitted", "proposal_generated", "branch_created"])
        with self.assertRaises(ValueError):
            store.update_workflow_state(update["id"], "published", fields={"arbitrary_file": "index.html"}, db_path=self.db)

    def test_timeline_entry_escapes_content_and_only_uses_user_links(self):
        proposal = {"operation": "add_timeline_entry", "title": "<script>alert(1)</script>",
                    "date": "November 2026", "description": "Ongoing & exploratory", "links": ["https://example.com"],
                    "suggested_category": "research_writeups"}
        markup = portfolio_github.render_entry(proposal, "update-123", ["https://example.com"])
        self.assertNotIn("<script>", markup)
        self.assertIn("&lt;script&gt;", markup)
        self.assertIn("Ongoing &amp; exploratory", markup)
        with self.assertRaises(ValueError):
            portfolio_github.render_entry({**proposal, "links": ["https://attacker.example"]},
                                          "update-123", ["https://example.com"])

    def test_timeline_insertion_preserves_latest_html_and_is_idempotent(self):
        source = '<ol class="research-timeline">\n<li>Concurrent main content</li>\n</ol>'
        entry = '<!-- daymark-update:test -->\n<li>New entry</li>\n<!-- /daymark-update:test -->'
        result = portfolio_github.GitHubClient._insert(source, entry, "test", "research_implementations")
        self.assertIn("Concurrent main content", result)
        self.assertEqual(portfolio_github.GitHubClient._insert(result, entry, "test", "research_implementations"), result)
        with self.assertRaises(Exception):
            portfolio_github.GitHubClient._insert('<ol class="research-timeline"></ol><ol class="research-timeline"></ol>', entry, "test", "research_implementations")

    def test_ai_proposal_stops_for_ambiguity_and_rejects_unsupplied_links(self):
        update = store.create_update("I started a new experiment last week.", "research_implementations", [], self.db)
        ambiguous = {
            "summary": "", "suggested_category": "research_implementations", "title": "",
            "date": "", "description": "An experiment has started.", "links": [],
            "clarifying_questions": [],
        }
        with patch.object(server, "call_llm", return_value={"response": json.dumps(ambiguous)}):
            result = server.build_portfolio_proposal(update, db_path=self.db)
        self.assertEqual(result["status"], "awaiting_clarification")
        self.assertTrue(result["proposal"]["clarifying_questions"])

        invalid_link = {**ambiguous, "title": "Experiment", "links": ["https://not-provided.example"]}
        with patch.object(server, "call_llm", return_value={"response": json.dumps(invalid_link)}):
            with self.assertRaises(ValueError):
                server.build_portfolio_proposal(update, db_path=self.db)

    def test_existing_update_database_is_migrated_without_losing_drafts(self):
        connection = sqlite3.connect(self.db)
        connection.execute("CREATE TABLE portfolio_updates (id TEXT PRIMARY KEY, original_input TEXT NOT NULL, category TEXT NOT NULL, supporting_links_json TEXT NOT NULL DEFAULT '[]', proposal_json TEXT, status TEXT NOT NULL DEFAULT 'draft', branch_name TEXT, commit_sha TEXT, preview_url TEXT, preview_status TEXT, production_url TEXT, production_status TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.execute("INSERT INTO portfolio_updates (id,original_input,category,created_at,updated_at) VALUES ('legacy','Old saved draft','other','2026-11-25T10:00:00+00:00','2026-11-25T10:00:00+00:00')")
        connection.commit()
        connection.close()
        migrated = store.get_update("legacy", self.db)
        self.assertEqual(migrated["original_input"], "Old saved draft")
        connection = sqlite3.connect(self.db)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(portfolio_updates)")}
        connection.close()
        self.assertTrue({"refinement_input", "pull_request_number", "pull_request_url"}.issubset(columns))

    def test_branch_preview_approval_and_discard_workflow_is_sha_guarded(self):
        update = store.create_update("Started a small experiment. Evaluation is ongoing.",
                                     "research_implementations", [], self.db)
        proposal = {
            "summary": "Add an experiment entry", "suggested_category": "research_implementations",
            "target_file": "research-implementations.html", "operation": "add_timeline_entry",
            "title": "Small experiment", "date": "November 2026", "description": "Evaluation is ongoing.",
            "links": [], "clarifying_questions": [],
        }
        store.save_proposal(update["id"], proposal, db_path=self.db)
        update = store.get_update(update["id"], self.db)

        class FakeGitHub(portfolio_github.GitHubClient):
            def __init__(self, db_path):
                super().__init__(token="test-token", db_path=db_path)
                self.calls = []
                self.pull_branch = ""

            def _file_at(self, branch, path):
                return '<ol class="research-timeline"><li>Concurrent main entry</li></ol>', "file-sha"

            def request(self, method, path, body=None):
                self.calls.append((method, path, body))
                if method == "GET" and path == "": return {"ok": True}
                if method == "GET" and path == "/git/ref/heads/main": return {"object": {"sha": "base-sha"}}
                if method == "GET" and path == "/git/commits/base-sha": return {"tree": {"sha": "tree-base"}}
                if method == "POST" and path == "/git/blobs": return {"sha": "blob-sha"}
                if method == "POST" and path == "/git/trees": return {"sha": "tree-new"}
                if method == "POST" and path == "/git/commits": return {"sha": "branch-commit"}
                if method == "POST" and path.startswith("/git/refs/heads/"): return {"ref": body["ref"]}
                if method == "POST" and path == "/pulls": return {"number": 42, "html_url": "https://github.com/Vaibhav-sa30/vaibhavsatish/pull/42"}
                if method == "GET" and path == "/pulls/42":
                    return {"state": "open", "merged": False, "head": {"ref": self.pull_branch or update.get("branch_name"), "sha": "branch-commit"}}
                if method == "PUT" and path == "/pulls/42/merge": return {"merged": True, "sha": "merge-commit"}
                if method == "PATCH" and path == "/pulls/42": return {"state": "closed"}
                if method == "DELETE": return {}
                raise AssertionError((method, path))

            def preview_check(self, sha):
                return {"status": "ready", "url": "https://daymark-preview.vercel.app"}

            def production_check(self, sha):
                return {"status": "ready", "url": "https://vaibhavsatish-research.vercel.app"}

        github = FakeGitHub(self.db)
        branch = github.create_review_branch(update)
        github.pull_branch = branch["branch_name"]
        saved = store.get_update(update["id"], self.db)
        self.assertEqual(saved["status"], "awaiting_preview")
        self.assertEqual(saved["commit_sha"], "branch-commit")
        self.assertEqual(saved["pull_request_number"], 42)
        github.wait_for_deployment(update["id"], branch["commit_sha"], max_wait_seconds=1)
        saved = store.get_update(update["id"], self.db)
        self.assertEqual(saved["status"], "ready_for_review")
        self.assertEqual(saved["preview_url"], "https://daymark-preview.vercel.app")
        self.assertEqual(github.approve_review(saved), "merge-commit")
        self.assertEqual(store.get_update(update["id"], self.db)["status"], "publishing")
        github.wait_for_deployment(update["id"], "merge-commit", production=True, max_wait_seconds=1)
        published = store.get_update(update["id"], self.db)
        self.assertEqual(published["status"], "published")
        self.assertEqual(published["production_url"], "https://vaibhavsatish-research.vercel.app")

        discard = store.create_update("Another implementation entry.", "research_implementations", [], self.db)
        store.update_workflow_state(discard["id"], "awaiting_preview", fields={
            "branch_name": f"portfolio/update-discard-{discard['id'][:8]}",
            "pull_request_number": 42,
        }, db_path=self.db)
        branch_name = store.get_update(discard["id"], self.db)["branch_name"]
        github.pull_branch = branch_name
        github.discard_review(store.get_update(discard["id"], self.db))
        self.assertEqual(store.get_update(discard["id"], self.db)["status"], "discarded")
        self.assertTrue(any(call[0] == "DELETE" and branch_name in call[1] for call in github.calls))

    def test_approval_refuses_a_branch_that_changed_after_preview(self):
        update = store.create_update("An implementation update.", "research_implementations", [], self.db)
        branch = f"portfolio/update-safe-{update['id'][:8]}"
        store.save_proposal(update["id"], {"suggested_category": "research_implementations"}, db_path=self.db)
        store.update_workflow_state(update["id"], "ready_for_review", fields={
            "branch_name": branch, "pull_request_number": 8, "commit_sha": "previewed-sha",
            "preview_url": "https://example.vercel.app", "preview_status": "ready",
        }, db_path=self.db)

        class ChangedBranch(portfolio_github.GitHubClient):
            def __init__(self):
                super().__init__(token="test-token", db_path=self_db)
                self.methods = []

            def request(self, method, path, body=None):
                self.methods.append(method)
                return {"state": "open", "head": {"ref": branch, "sha": "newer-sha"}}

        self_db = self.db
        github = ChangedBranch()
        with self.assertRaises(portfolio_github.GitHubError):
            github.approve_review(store.get_update(update["id"], self.db))
        self.assertNotIn("PUT", github.methods)
        self.assertEqual(store.get_update(update["id"], self.db)["status"], "ready_for_review")


if __name__ == "__main__":
    unittest.main()
