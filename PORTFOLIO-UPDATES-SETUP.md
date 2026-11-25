# Portfolio Updates integration setup

The module stores check-ins, submissions, proposals, and lifecycle events in the local Daymark SQLite store first. Supabase and GitHub are optional external integrations; missing credentials leave drafts on this device and never make a publication claim.

## Supabase event logging

1. In the portfolio repository, run `portfolio-updates-supabase.sql` in the Supabase SQL editor.
2. Deploy `supabase/functions/daymark-portfolio-events/index.ts` as the `daymark-portfolio-events` Edge Function. Keep its default JWT verification enabled.
3. Configure Edge Function secrets `DAYMARK_INGEST_TOKEN` (a new random secret) and the existing `SUPABASE_SERVICE_ROLE_KEY`. The service role stays in Supabase and is never entered into Daymark.
4. In Daymark → Portfolio Updates → Private connections, save the Edge Function URL. Use **Configure private credentials securely** to enter the project’s public anon key, the same `DAYMARK_INGEST_TOKEN`, and the GitHub token in a native masked dialog.
5. Daymark writes only user-triggered events and retries its local outbox with the event UUID as the deduplication key.

The SQL enables RLS and revokes direct browser access. The Edge Function validates the app token and event schema, then writes with its server-side service role.

## GitHub review workflow

Create a fine-grained GitHub token scoped only to `Vaibhav-sa30/vaibhavsatish`, with:

- Contents: read and write
- Pull requests: read and write
- Checks: read
- Deployments: read

Enter the token in Daymark → Portfolio Updates → Private connections → **Configure private credentials securely**. Daymark uses Windows Credential Manager, with a per-user DPAPI-encrypted fallback if Credential Manager is unavailable. The token is only used by the Python backend and is never included in prompts, browser JavaScript, API responses, or Supabase.

The current content adapter supports additive timeline entries in `research.html`, `research-implementations.html`, and `research-writeups.html`. Other portfolio sections are still saved locally and are not automatically branched. The adapter starts from the latest `main`, validates a unique timeline list, escapes all generated text, and refuses a write if the expected structure is ambiguous.

The GitHub repository currently also has a GitHub Pages workflow on pushes to `main`. A reviewed merge can therefore trigger that existing workflow as well as Vercel. Confirm which deployment is intended before using the publish action if GitHub Pages is still a live destination.

Vercel preview readiness is read from the Vercel GitHub check on the pull request. Production is marked published only after GitHub reports a successful `production` deployment for the merged commit. If the Vercel GitHub integration is absent, private, or does not report those checks/statuses, Daymark leaves the update awaiting deployment and does not mark it published.

## Supabase Free usage

Only genuine check-ins and update lifecycle events are queued for syncing. No heartbeat writes are generated, and no write is promised to prevent a Free project from pausing. See [Supabase's inactivity policy](https://supabase.com/docs/guides/platform/free-project-pausing).
