"""Narrow GitHub workflow for reviewed portfolio timeline additions."""

from __future__ import annotations

import base64
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date
from urllib.parse import urlparse

import portfolio_credentials
import portfolio_updates


OWNER = "Vaibhav-sa30"
REPO = "vaibhavsatish"
BASE_BRANCH = "main"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
CONTENT_FILES = {
    "research_timeline": "research.html",
    "research_papers": "research.html",
    "research_implementations": "research-implementations.html",
    "projects_and_implementations": "research-implementations.html",
    "research_writeups": "research-writeups.html",
    "research_artifacts": "artifacts.html",
    "ai_safety_writings": "writings.html",
    "writings_and_essays": "writings.html",
    "coursework_and_learning": "coursework.html",
    "professional_experience": "about.html",
}


class GitHubError(RuntimeError):
    pass


def render_entry(proposal, update_id, allowed_links):
    if not isinstance(proposal, dict) or proposal.get("operation") != "add_timeline_entry":
        raise ValueError("Only validated additive portfolio entries are enabled")
    title = str(proposal.get("title", "")).strip()
    date_text = str(proposal.get("date", "")).strip()
    description = str(proposal.get("description", "")).strip()
    links = proposal.get("links", [])
    if not title or len(title) > 180 or not description or len(description) > 3000:
        raise ValueError("The proposed title or description is missing or too long")
    if not isinstance(links, list) or any(link not in allowed_links for link in links):
        raise ValueError("A proposed reference was not provided by the user")
    # This marker makes retries idempotent and lets refinements target only this entry.
    marker = f"<!-- daymark-update:{update_id} -->"
    category = proposal.get("suggested_category")
    label = {
        "research_writeups": "Research writeup",
        "research_artifacts": "Research artifact",
        "ai_safety_writings": "AI Safety writing",
        "writings_and_essays": "Writing",
    }.get(category, "Professional experience" if category == "professional_experience" else "Learning milestone")
    rendered_links = "".join(
        f'<a href="{html.escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">Reference ↗</a>'
        for link in links
    )
    if category in {"research_timeline", "research_papers", "research_implementations", "projects_and_implementations"}:
        return (f"{marker}\n<li class=\"research-timeline-item\">\n"
                f"  <div class=\"research-timeline-trigger\">\n"
                f"    <span class=\"research-timeline-date\">{html.escape(date_text)}</span>\n"
                f"    <span class=\"research-timeline-title\">{html.escape(title)}</span>\n"
                f"    <span class=\"research-timeline-summary\">{html.escape(description)}</span>\n"
                f"    <span class=\"research-timeline-hint\">{rendered_links}</span>\n"
                f"  </div>\n</li>\n<!-- /daymark-update:{update_id} -->")
    if category in {"research_writeups", "research_artifacts", "ai_safety_writings", "writings_and_essays"}:
        return (f"{marker}\n<li class=\"pub-item\">\n"
                f"  <span class=\"kicker\">{label}</span>\n"
                f"  <span class=\"pub-title\">{html.escape(title)}</span>\n"
                f"  <span class=\"pub-venue\">{html.escape(date_text)}</span>\n"
                f"  <p class=\"pub-description\">{html.escape(description)}</p>\n"
                f"  <div class=\"pub-links\">{rendered_links}</div>\n"
                f"</li>\n<!-- /daymark-update:{update_id} -->")
    if category == "coursework_and_learning":
        return (f"{marker}\n<section class=\"coursework-sections\" aria-label=\"New learning milestones\">\n"
                f"  <section class=\"coursework-group\" aria-label=\"Additional coursework\">\n"
                f"    <ul class=\"coursework-list\"><li class=\"coursework-item\">\n"
                f"      <div class=\"coursework-item-heading\"><h3>{html.escape(title)}</h3></div>\n"
                f"      <p class=\"coursework-meta\">{html.escape(date_text)}</p>\n"
                f"      <p class=\"coursework-description\">{html.escape(description)}</p>\n"
                f"      <div class=\"pub-links\">{rendered_links}</div>\n"
                f"    </li></ul>\n  </section>\n</section>\n<!-- /daymark-update:{update_id} -->")
    if category == "professional_experience":
        return (f"{marker}\n<section class=\"about-position\" aria-label=\"Professional experience and fellowships\">\n"
                f"  <span class=\"kicker\">Professional experience &amp; fellowships</span>\n"
                f"  <h2 class=\"pub-title\">{html.escape(title)}</h2>\n"
                f"  <p class=\"pub-venue\">{html.escape(date_text)}</p>\n"
                f"  <p class=\"pub-description\">{html.escape(description)}</p>\n"
                f"  <div class=\"pub-links\">{rendered_links}</div>\n"
                f"</section>\n<!-- /daymark-update:{update_id} -->")
    raise ValueError("This portfolio section has no safe entry template")


class GitHubClient:
    def __init__(self, token=None, opener=None, db_path=None):
        self.token = token or portfolio_credentials.get_secret("github_token")
        if not self.token:
            raise GitHubError("Save a GitHub fine-grained token in Portfolio Updates settings first")
        self.opener = opener or urllib.request.urlopen
        self.db_path = db_path

    def request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{API}{path}", data=data, method=method,
            headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.token}",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Daymark-Portfolio-Updates",
                     "Content-Type": "application/json"},
        )
        try:
            with self.opener(request, timeout=20) as response:
                payload = response.read()
                return json.loads(payload.decode("utf-8")) if payload else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", errors="replace")).get("message", "")
            except Exception:
                pass
            raise GitHubError(f"GitHub API returned HTTP {exc.code}" + (f": {detail[:200]}" if detail else "")) from None
        except Exception as exc:
            raise GitHubError(f"GitHub request failed ({type(exc).__name__})") from None

    def _file_at(self, branch, path):
        encoded = urllib.parse.quote(path, safe="/")
        record = self.request("GET", f"/contents/{encoded}?ref={urllib.parse.quote(branch, safe='')}")
        if record.get("type") != "file" or record.get("encoding") != "base64":
            raise GitHubError("The approved target is not a normal text file")
        content = base64.b64decode(record["content"]).decode("utf-8")
        return content, record["sha"]

    @staticmethod
    def _insert(content, entry, update_id, category):
        marker = f"<!-- daymark-update:{update_id} -->"
        if marker in content:
            return content
        if category in {"research_timeline", "research_papers", "research_implementations", "projects_and_implementations"}:
            opening_pattern, closing_tag = r"<ol\s+class=[\"']research-timeline[\"']\s*>", "</ol>"
        elif category == "research_writeups":
            opening_pattern, closing_tag = r"<ul\s+class=[\"']pub-list\s+artifact-list[\"']\s*>", "</ul>"
        elif category in {"writings_and_essays", "ai_safety_writings"}:
            opening_pattern, closing_tag = r"<ul\s+class=[\"']pub-list[\"']\s*>", "</ul>"
        elif category == "research_artifacts":
            blocks = list(re.finditer(r"<div\s+class=[\"']interest-empty-state[\"']\s*>", content, re.I))
            placeholder = "Standalone research artifacts will appear here as I publish them."
            if len(blocks) != 1 or content.count(placeholder) != 1:
                raise GitHubError("Could not identify the verified empty artifact section; no file was changed")
            start = blocks[0].start()
            close = content.find("</div>", blocks[0].end())
            if close < 0:
                raise GitHubError("Could not find the end of the artifact section")
            content = content.replace(f"<p>{placeholder}</p>", "", 1)
            # Include an accessible list in the existing empty-state container.
            return content[:blocks[0].end()] + "\n<ul class=\"pub-list artifact-list\">\n" + entry + "\n</ul>\n" + content[blocks[0].end():]
        elif category in {"coursework_and_learning", "professional_experience"}:
            if content.count("</main>") != 1:
                raise GitHubError("Could not identify the unique main content area; no file was changed")
            return content.replace("</main>", entry + "\n</main>", 1)
        else:
            raise GitHubError("This portfolio section has no safe content adapter")
        openings = list(re.finditer(opening_pattern, content, re.I))
        if len(openings) != 1:
            raise GitHubError("The target page does not have exactly one verified content list; no file was changed")
        close = content.find(closing_tag, openings[0].end())
        if close < 0:
            raise GitHubError("Could not find the end of the verified content list")
        return content[:close] + "\n" + entry + "\n" + content[close:]

    def _create_commit(self, branch, parent_sha, path, new_content, message, existing=False):
        parent = self.request("GET", f"/git/commits/{parent_sha}")
        blob = self.request("POST", "/git/blobs", {"content": new_content, "encoding": "utf-8"})
        tree = self.request("POST", "/git/trees", {"base_tree": parent["tree"]["sha"], "tree": [{
            "path": path, "mode": "100644", "type": "blob", "sha": blob["sha"],
        }]})
        commit = self.request("POST", "/git/commits", {
            "message": message, "tree": tree["sha"], "parents": [parent_sha],
        })
        ref_path = f"/git/refs/heads/{urllib.parse.quote(branch, safe='/')}"
        ref_method = "PATCH" if existing else "POST"
        ref_body = {"sha": commit["sha"], "force": False} if existing else {"ref": f"refs/heads/{branch}", "sha": commit["sha"]}
        self.request(ref_method, ref_path, ref_body)
        return commit["sha"]

    def create_review_branch(self, update):
        proposal = update.get("proposal") or {}
        category = proposal.get("suggested_category")
        path = CONTENT_FILES.get(category)
        if not path or path != portfolio_updates.PORTFOLIO_FILES.get(category):
            raise GitHubError("This section has no verified safe content adapter yet; the draft remains saved")
        if proposal.get("clarifying_questions"):
            raise GitHubError("Answer the AI's clarification questions before preparing a preview")
        self.request("GET", "")  # Validate token and repository access before creating anything.
        branch = "portfolio/update-" + re.sub(r"[^a-z0-9]+", "-", proposal["title"].lower()).strip("-")[:35]
        branch += f"-{date.today().isoformat()}-{update['id'][:8]}"
        # Fetch latest main immediately before building the branch. We add into its
        # current list, preserving concurrent changes rather than using a stale clone.
        main_ref = self.request("GET", f"/git/ref/heads/{BASE_BRANCH}")
        base_sha = main_ref["object"]["sha"]
        content, _ = self._file_at(BASE_BRANCH, path)
        entry = render_entry(proposal, update["id"], update.get("supporting_links", []))
        updated = self._insert(content, entry, update["id"], category)
        commit_sha = self._create_commit(branch, base_sha, path, updated,
                                         f"Portfolio update: {proposal['title'][:60]}")
        try:
            portfolio_updates.update_workflow_state(
                update["id"], "awaiting_preview",
                fields={"branch_name": branch, "commit_sha": commit_sha, "preview_status": "queued"},
                event_type="branch_created", metadata={"path": path, "commit_sha": commit_sha},
                db_path=self.db_path,
            )
            pull = self.request("POST", "/pulls", {
                "title": f"Portfolio update: {proposal['title'][:100]}",
                "head": branch, "base": BASE_BRANCH,
                "body": f"Prepared by Daymark for review. Update record: {update['id']}\n\n{proposal.get('summary', '')}",
            })
        except Exception:
            # Branch naming includes this update's ID. Delete only that exact branch.
            try:
                self.request("DELETE", f"/git/refs/heads/{urllib.parse.quote(branch, safe='/')}")
            except Exception:
                pass
            raise
        portfolio_updates.update_workflow_state(
            update["id"], "awaiting_preview",
            fields={"pull_request_number": pull.get("number"), "pull_request_url": pull.get("html_url")},
            db_path=self.db_path,
        )
        return {"branch_name": branch, "commit_sha": commit_sha, "pull_request_url": pull.get("html_url")}

    def refine_review_branch(self, update):
        proposal = update.get("proposal") or {}
        category = proposal.get("suggested_category")
        path = CONTENT_FILES.get(category)
        branch = update.get("branch_name")
        if not path or not branch or not branch.endswith("-" + update["id"][:8]):
            raise GitHubError("This update has no safely identifiable review branch")
        ref = self.request("GET", f"/git/ref/heads/{urllib.parse.quote(branch, safe='/')}")
        head_sha = ref["object"]["sha"]
        content, _ = self._file_at(branch, path)
        start = f"<!-- daymark-update:{update['id']} -->"
        end = f"<!-- /daymark-update:{update['id']} -->"
        if content.count(start) != 1 or content.count(end) != 1:
            raise GitHubError("Could not identify exactly one Daymark entry to refine")
        first = content.index(start)
        last = content.index(end, first) + len(end)
        entry = render_entry(proposal, update["id"], update.get("supporting_links", []))
        refined = content[:first] + entry + content[last:]
        commit_sha = self._create_commit(branch, head_sha, path, refined,
                                         f"Refine portfolio update: {proposal['title'][:60]}", existing=True)
        portfolio_updates.update_workflow_state(
            update["id"], "awaiting_preview", fields={"commit_sha": commit_sha, "preview_status": "queued"},
            event_type="update_refined", metadata={"commit_sha": commit_sha},
            db_path=self.db_path,
        )
        return commit_sha

    def approve_review(self, update):
        number = update.get("pull_request_number")
        branch = update.get("branch_name")
        if not number or not branch or not branch.endswith("-" + update["id"][:8]):
            raise GitHubError("This update has no safely identifiable review pull request")
        pull = self.request("GET", f"/pulls/{int(number)}")
        expected = update.get("commit_sha")
        if pull.get("state") == "closed" and pull.get("merged") and pull.get("merge_commit_sha"):
            merge_sha = pull["merge_commit_sha"]
            portfolio_updates.update_workflow_state(
                update["id"], "publishing", fields={"commit_sha": merge_sha, "production_status": "queued"},
                db_path=self.db_path,
            )
            return merge_sha
        if pull.get("state") != "open" or pull.get("head", {}).get("ref") != branch:
            raise GitHubError("The review pull request is no longer open")
        if pull.get("head", {}).get("sha") != expected:
            raise GitHubError("The review branch changed since the last preview; refresh the preview first")
        result = self.request("PUT", f"/pulls/{int(number)}/merge", {
            "sha": expected, "merge_method": "squash",
        })
        if not result.get("merged"):
            raise GitHubError("GitHub did not confirm the merge")
        merge_sha = result.get("sha")
        portfolio_updates.update_workflow_state(
            update["id"], "publishing", fields={"commit_sha": merge_sha, "production_status": "queued"},
            event_type="update_approved", metadata={"merge_sha": merge_sha},
            db_path=self.db_path,
        )
        return merge_sha

    def discard_review(self, update):
        branch = update.get("branch_name")
        number = update.get("pull_request_number")
        if not branch or not branch.endswith("-" + update["id"][:8]):
            raise GitHubError("This update has no safely identifiable review branch")
        if number:
            pull = self.request("GET", f"/pulls/{int(number)}")
            if pull.get("head", {}).get("ref") != branch:
                raise GitHubError("Pull request branch does not match this update")
            if pull.get("state") == "open":
                self.request("PATCH", f"/pulls/{int(number)}", {"state": "closed"})
        self.request("DELETE", f"/git/refs/heads/{urllib.parse.quote(branch, safe='/')}")
        portfolio_updates.update_workflow_state(
            update["id"], "discarded", event_type="update_discarded", metadata={"branch_deleted": True},
            db_path=self.db_path,
        )

    def wait_for_deployment(self, update_id, commit_sha, production=False, max_wait_seconds=900):
        import time

        deadline = time.monotonic() + max_wait_seconds
        while time.monotonic() < deadline:
            current = portfolio_updates.get_update(update_id, self.db_path)
            if not current or current["status"] == "discarded":
                return {"status": "discarded", "url": None}
            check = self.production_check(commit_sha) if production else self.preview_check(commit_sha)
            if check["status"] == "ready" and check["url"]:
                if production:
                    portfolio_updates.update_workflow_state(
                        update_id, "published",
                        fields={"production_url": check["url"], "production_status": "ready", "error": None},
                        event_type="production_deployment_completed", metadata={"url": check["url"]},
                        db_path=self.db_path,
                    )
                else:
                    portfolio_updates.update_workflow_state(
                        update_id, "ready_for_review",
                        fields={"preview_url": check["url"], "preview_status": "ready", "error": None},
                        event_type="preview_deployment_ready", metadata={"url": check["url"]},
                        db_path=self.db_path,
                    )
                return check
            if check["status"] == "failed":
                portfolio_updates.update_workflow_state(
                    update_id, "failed",
                    fields={"preview_status" if not production else "production_status": "failed",
                            "error": "Vercel deployment check failed"},
                    event_type="deployment_failed", metadata={"deployment_url": check.get("url")},
                    db_path=self.db_path,
                )
                return check
            time.sleep(15)
        portfolio_updates.update_workflow_state(
            update_id, "publishing" if production else "awaiting_preview",
            fields={"production_status" if production else "preview_status": "delayed",
                    "error": "Deployment check is still pending; Daymark will not mark this update published."},
            db_path=self.db_path,
        )
        return {"status": "delayed", "url": None}

    def preview_check(self, commit_sha):
        try:
            deployments = self.request("GET", f"/deployments?sha={urllib.parse.quote(commit_sha, safe='')}&per_page=100")
            previews = [item for item in deployments
                        if "preview" in str(item.get("environment", "")).lower()]
            if previews:
                statuses = self.request("GET", f"/deployments/{previews[0]['id']}/statuses?per_page=10")
                if statuses:
                    status = statuses[0]
                    url = status.get("environment_url") or status.get("target_url")
                    if status.get("state") == "success" and url:
                        host = urlparse(url).hostname or ""
                        if host.endswith(".vercel.app"):
                            return {"status": "ready", "url": url}
                    if status.get("state") in {"failure", "error"}:
                        return {"status": "failed", "url": url}
                    return {"status": "building", "url": url}
        except GitHubError:
            # Some integrations publish checks but not GitHub deployment records.
            pass
        result = self.request("GET", f"/commits/{commit_sha}/check-runs?per_page=100")
        runs = result.get("check_runs", [])
        vercel = [run for run in runs if "vercel" in run.get("name", "").lower()]
        if not vercel:
            return {"status": "queued", "url": None}
        successful = next((run for run in reversed(vercel)
                           if run.get("status") == "completed" and run.get("conclusion") == "success"), None)
        if successful:
            url = successful.get("details_url")
            host = urlparse(url or "").hostname or ""
            if not url or urlparse(url).scheme != "https" or not host.endswith(".vercel.app"):
                return {"status": "queued", "url": None}
            return {"status": "ready", "url": url}
        failed = next((run for run in reversed(vercel)
                       if run.get("status") == "completed" and run.get("conclusion") not in ("success", "neutral", "skipped")), None)
        if failed:
            return {"status": "failed", "url": failed.get("details_url") or failed.get("html_url")}
        return {"status": "building", "url": next((run.get("details_url") for run in reversed(vercel) if run.get("details_url")), None)}

    def production_check(self, commit_sha):
        deployments = self.request("GET", f"/deployments?sha={urllib.parse.quote(commit_sha, safe='')}&per_page=100")
        production = [item for item in deployments if str(item.get("environment", "")).lower() == "production"]
        if not production:
            return {"status": "queued", "url": None}
        deployment = production[0]
        statuses = self.request("GET", f"/deployments/{deployment['id']}/statuses?per_page=10")
        if not statuses:
            return {"status": "queued", "url": None}
        latest = statuses[0]
        url = latest.get("environment_url") or latest.get("target_url")
        if latest.get("state") == "success" and url:
            host = urlparse(url).hostname or ""
            if urlparse(url).scheme != "https" or (host != "vaibhavsatish-research.vercel.app" and not host.endswith(".vercel.app")):
                return {"status": "queued", "url": None}
            return {"status": "ready", "url": url}
        if latest.get("state") in {"failure", "error"}:
            return {"status": "failed", "url": url}
        return {"status": "building", "url": url}
