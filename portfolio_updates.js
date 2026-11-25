(() => {
  const $ = id => document.getElementById(id);
  const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const categoryNames = {
    research_timeline: 'Research timeline and milestones',
    research_papers: 'Research papers',
    research_implementations: 'Research implementations',
    research_writeups: 'Research writeups',
    research_artifacts: 'Research artifacts',
    ai_safety_writings: 'AI Safety writings',
    writings_and_essays: 'Other writings and essays',
    projects_and_implementations: 'Projects and technical implementations',
    coursework_and_learning: 'Coursework and learning milestones',
    professional_experience: 'Professional experience and fellowships',
    other: 'Other'
  };
  let polling = false;

  async function request(path, options = {}) {
    const response = await fetch(path, {
      cache: 'no-store',
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
    });
    const responseText = await response.text();
    let result;
    try {
      result = responseText ? JSON.parse(responseText) : null;
    } catch (_) {
      throw new Error(`Daymark server returned an unreadable response (HTTP ${response.status}). Restart Daymark to load the updated local server.`);
    }
    if (!result || typeof result !== 'object') {
      throw new Error(`Daymark server returned an empty response (HTTP ${response.status}). Restart Daymark to load the updated local server.`);
    }
    if (!response.ok || result.status !== 'ok') throw new Error(result.message || `Daymark returned HTTP ${response.status}`);
    return result;
  }

  function localTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
  }

  async function loadSettings() {
    const result = await request('/api/portfolio/checkin-settings');
    const settings = result.settings;
    $('portfolioCheckinEnabled').checked = settings.enabled;
    $('portfolioIntervalMinutes').value = settings.interval_minutes;
    $('portfolioWakeStart').value = settings.waking_start_hour;
    $('portfolioWakeEnd').value = settings.waking_end_hour;
    $('portfolioSnoozeMinutes').value = settings.snooze_minutes;
  }

  async function loadIntegrationSettings() {
    const result = await request('/api/portfolio/integration-settings');
    $('portfolioSupabaseUrl').value = result.supabase_ingest_url || '';
    const names = {
      supabase_anon_key: 'Supabase public key',
      supabase_ingest_token: 'Daymark ingest token',
      github_token: 'GitHub token'
    };
    const saved = Object.entries(names).filter(([key]) => result.secrets?.[key]).map(([, label]) => label);
    $('portfolioIntegrationStatus').textContent = saved.length
      ? `Saved in Windows Credential Manager: ${saved.join(', ')}.`
      : 'No private integration credentials saved yet.';
  }

  async function saveIntegrationSettings(event) {
    event.preventDefault();
    const status = $('portfolioIntegrationStatus');
    const button = event.currentTarget.querySelector('button[type="submit"]');
    status.textContent = 'Saving to Windows Credential Manager…';
    button.disabled = true;
    try {
      await request('/api/portfolio/integration-settings', {
        method: 'POST',
        body: JSON.stringify({ supabase_ingest_url: $('portfolioSupabaseUrl').value })
      });
      await loadIntegrationSettings();
      status.textContent += ' URL saved.';
    } catch (error) {
      status.textContent = `Could not save connections: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  }

  function renderUpdates(updates) {
    const host = $('portfolioUpdateHistory');
    if (!updates.length) {
      host.innerHTML = '<div class="empty-state">No update drafts yet. Start with one recent, verifiable development.</div>';
      return;
    }
    host.innerHTML = updates.map(update => {
      const label = categoryNames[update.category] || categoryNames.other;
      const links = (update.supporting_links || []).map(link =>
        `<a href="${escapeHTML(link)}" target="_blank" rel="noopener noreferrer">${escapeHTML(link)}</a>`
      ).join(' · ');
      const proposal = update.proposal;
      const proposalMarkup = proposal ? `<details><summary>AI proposal · ${escapeHTML(categoryNames[proposal.suggested_category] || 'Section to clarify')}</summary>
        ${proposal.summary ? `<p>${escapeHTML(proposal.summary)}</p>` : ''}
        ${proposal.title ? `<p><b>${escapeHTML(proposal.title)}</b>${proposal.date ? ` · ${escapeHTML(proposal.date)}` : ''}</p>` : ''}
        ${proposal.description ? `<p>${escapeHTML(proposal.description)}</p>` : ''}
        ${(proposal.clarifying_questions || []).length ? `<p><b>Needs your clarification</b></p><ul>${proposal.clarifying_questions.map(question => `<li>${escapeHTML(question)}</li>`).join('')}</ul>` : ''}
        ${proposal.target_file ? `<small>Proposed target: ${escapeHTML(proposal.target_file)} · no files changed</small>` : ''}
      </details>` : '';
      const preview = update.preview_url && update.preview_status === 'ready'
        ? `<section class="portfolio-review-preview"><div class="portfolio-review-actions"><a href="${escapeHTML(update.preview_url)}" target="_blank" rel="noopener noreferrer">Open preview ↗</a>${update.pull_request_url ? `<a href="${escapeHTML(update.pull_request_url)}" target="_blank" rel="noopener noreferrer">View pull request ↗</a>` : ''}</div><iframe src="${escapeHTML(update.preview_url)}" title="Portfolio preview" loading="lazy" sandbox="allow-scripts allow-same-origin allow-forms allow-popups" referrerpolicy="no-referrer"></iframe></section>`
        : update.branch_name ? `<small>Preview ${escapeHTML(update.preview_status || 'queued')} · branch ${escapeHTML(update.branch_name)} · commit ${escapeHTML((update.commit_sha || '').slice(0, 10))}</small>` : '';
      const production = update.production_url && update.status === 'published'
        ? `<p><b>Production is live:</b> <a href="${escapeHTML(update.production_url)}" target="_blank" rel="noopener noreferrer">Open published portfolio ↗</a></p>` : '';
      const canAct = !['publishing', 'published', 'discarded'].includes(update.status);
      const actions = canAct ? `<div class="portfolio-review-buttons">
        ${update.status === 'ready_for_review' && update.preview_url ? `<button type="button" class="primary-btn" data-portfolio-action="approve" data-update-id="${escapeHTML(update.id)}">Approve and publish</button>` : ''}
        <button type="button" class="secondary-btn" data-portfolio-action="refine" data-update-id="${escapeHTML(update.id)}">Refine</button>
        <button type="button" class="secondary-btn" data-portfolio-action="discard" data-update-id="${escapeHTML(update.id)}">Discard</button>
      </div><form class="portfolio-refine-form hidden" data-refine-id="${escapeHTML(update.id)}"><label>WHAT SHOULD CHANGE?</label><textarea rows="3" maxlength="3000" required placeholder="Ask for a wording or placement change"></textarea><button class="primary-btn" type="submit">Create revised preview</button></form>` : '';
      const events = update.events || [];
      const eventMarkup = events.length ? `<details><summary>Workflow history (${events.length})</summary><ul>${events.map(item => `<li>${escapeHTML(item.event_type.replaceAll('_', ' '))} · ${escapeHTML(localTime(item.created_at))}</li>`).join('')}</ul></details>` : '';
      const stateHint = {
        draft: 'Saved locally · waiting for an AI provider or proposal retry.',
        processing: 'Saved locally · AI is preparing a cautious proposal.',
        awaiting_clarification: 'Saved locally · answer the questions before a branch can be prepared.',
        ready_for_review: 'AI proposal ready · portfolio files remain unchanged until preview review.',
        awaiting_preview: 'A review branch was created; Daymark is waiting for the actual Vercel preview.',
        approved: 'Approval recorded · Daymark is verifying the merge and production deployment.',
        publishing: 'Merged · waiting for a successful production deployment check.',
        published: 'Published after the production deployment reported success.',
        discarded: 'Discarded · the live portfolio was not changed.',
        failed: `Saved locally · processing stopped: ${escapeHTML(update.error || 'unknown error')}`,
      }[update.status] || `Saved locally · ${escapeHTML(update.status.replaceAll('_', ' '))}.`;
      return `<article class="portfolio-history-item">
        <div class="portfolio-history-top"><b>${escapeHTML(label)}</b><span class="badge secondary-badge">${escapeHTML(update.status.replaceAll('_', ' ').toUpperCase())}</span></div>
        <time>${escapeHTML(localTime(update.created_at))}</time>
        <details><summary>View submitted update</summary><p>${escapeHTML(update.original_input)}</p>${links ? `<p class="portfolio-history-links">${links}</p>` : ''}</details>
        ${proposalMarkup}${preview}${production}${actions}${eventMarkup}<small>${stateHint}</small>
      </article>`;
    }).join('');
  }

  function renderCheckins(checkins) {
    const host = $('portfolioCheckinHistory');
    if (!checkins.length) {
      host.innerHTML = '<div class="empty-state">No check-ins recorded yet.</div>';
      return;
    }
    host.innerHTML = checkins.map(item => `<article class="portfolio-checkin-item">
      <time>${escapeHTML(localTime(item.created_at))}</time>
      <span>${item.response === 'nothing_to_update' ? 'Nothing to update' : 'I have an update'}</span>
      <small>${escapeHTML(item.source.replaceAll('_', ' '))}</small>
    </article>`).join('');
  }

  async function refresh() {
    try {
      const result = await request('/api/portfolio/updates');
      renderUpdates(result.updates || []);
      renderCheckins(result.checkins || []);
      const updates = result.updates || [];
      const active = updates.filter(update => ['processing', 'awaiting_clarification', 'awaiting_preview', 'ready_for_review'].includes(update.status)).length;
      $('portfolioHistorySummary').textContent = `${updates.length} saved update(s) · ${active} in progress or awaiting review. Drafts are stored on this device.`;
    } catch (error) {
      $('portfolioUpdateHistory').innerHTML = `<div class="empty-state">Could not load update history: ${escapeHTML(error.message)}</div>`;
    }
  }

  async function submitUpdate(event) {
    event.preventDefault();
    const status = $('portfolioFormStatus');
    const button = event.currentTarget.querySelector('button[type="submit"]');
    const links = $('portfolioLinks').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    status.textContent = 'Saving a local draft…';
    button.disabled = true;
    try {
      const result = await request('/api/portfolio/updates', {
        method: 'POST',
        body: JSON.stringify({
          category: $('portfolioCategory').value,
          original_input: $('portfolioUpdateText').value,
          supporting_links: links
        })
      });
      $('portfolioUpdateText').value = '';
      $('portfolioLinks').value = '';
      status.textContent = result.update?.status === 'processing'
        ? 'Saved locally. Daymark is preparing an AI proposal; this will appear in history. No portfolio files are changed yet.'
        : 'Saved locally. No portfolio files are changed yet.';
      await refresh();
    } catch (error) {
      status.textContent = `Could not save: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  }

  async function updateAction(id, action, body = {}) {
    const result = await request(`/api/portfolio/updates/${encodeURIComponent(id)}/${action}`, {
      method: 'POST', body: JSON.stringify(body)
    });
    await refresh();
    return result;
  }

  async function saveSettings(event) {
    event.preventDefault();
    const status = $('portfolioSettingsStatus');
    try {
      const result = await request('/api/portfolio/checkin-settings', {
        method: 'POST',
        body: JSON.stringify({
          enabled: $('portfolioCheckinEnabled').checked,
          interval_minutes: Number($('portfolioIntervalMinutes').value),
          waking_start_hour: Number($('portfolioWakeStart').value),
          waking_end_hour: Number($('portfolioWakeEnd').value),
          snooze_minutes: Number($('portfolioSnoozeMinutes').value)
        })
      });
      status.textContent = `Saved · every ${result.settings.interval_minutes}m, ${result.settings.waking_start_hour}:00–${result.settings.waking_end_hour}:00.`;
    } catch (error) {
      status.textContent = `Could not save: ${error.message}`;
    }
  }

  async function pollCommand() {
    if (polling) return;
    polling = true;
    try {
      const result = await request('/api/portfolio/ui-command');
      if (result.command === 'new_portfolio_update') {
        if (typeof showView === 'function') showView('portfolio');
        $('portfolioUpdateText').focus();
        await refresh();
      }
    } catch (_) {
      // The regular app load and API polling recover automatically after the server returns.
    } finally {
      polling = false;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('portfolioUpdateForm').addEventListener('submit', submitUpdate);
    $('portfolioCheckinSettingsForm').addEventListener('submit', saveSettings);
    $('portfolioIntegrationForm').addEventListener('submit', saveIntegrationSettings);
    $('openCredentialPrompt').addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const result = await request('/api/portfolio/credential-prompt');
        $('portfolioIntegrationStatus').textContent = result.message;
        window.setTimeout(() => loadIntegrationSettings().catch(() => {}), 3000);
      } catch (error) {
        $('portfolioIntegrationStatus').textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
    $('refreshPortfolioHistory').addEventListener('click', refresh);
    $('portfolioUpdateHistory').addEventListener('click', async event => {
      const button = event.target.closest('[data-portfolio-action]');
      if (!button) return;
      const id = button.dataset.updateId;
      const action = button.dataset.portfolioAction;
      if (action === 'refine') {
        const form = document.querySelector(`[data-refine-id="${CSS.escape(id)}"]`);
        form.classList.toggle('hidden');
        form.querySelector('textarea').focus();
        return;
      }
      button.disabled = true;
      try {
        const result = await updateAction(id, action);
        $('portfolioHistorySummary').textContent = result.message || 'Update saved.';
      } catch (error) {
        $('portfolioHistorySummary').textContent = `Could not ${action}: ${error.message}`;
      }
    });
    $('portfolioUpdateHistory').addEventListener('submit', async event => {
      const form = event.target.closest('[data-refine-id]');
      if (!form) return;
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      try {
        const result = await updateAction(form.dataset.refineId, 'refine', { instruction: form.querySelector('textarea').value });
        $('portfolioHistorySummary').textContent = result.message || 'Refinement queued.';
      } catch (error) {
        $('portfolioHistorySummary').textContent = `Could not refine: ${error.message}`;
      } finally {
        button.disabled = false;
      }
    });
    loadSettings().catch(error => { $('portfolioSettingsStatus').textContent = error.message; });
    loadIntegrationSettings().catch(error => { $('portfolioIntegrationStatus').textContent = error.message; });
    refresh();
    pollCommand();
    window.setInterval(pollCommand, 3000);
    window.setInterval(refresh, 10000);
  });

  window.PortfolioUpdates = { refresh };
})();
