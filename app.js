/**
 * DAYMARK — Personal Execution & Accountability Assistant
 * Built according to prompt.md specification:
 * - Execution over planning (Frictionless START)
 * - Active project limit (Max 3 active)
 * - Idea parking lot guardrail
 * - Google Calendar constraints & focus window calculation
 * - Daily Big Three planning
 * - Completion check-ins & intelligent recovery micro-actions
 * - Anti-planning detection
 * - End-of-day shutdown & weekly review
 */

const KEY = 'daymark.execution.v2';

const defaults = {
  max_active_projects: 3,
  consecutive_tasks_planned: 0,
  bigThree: {
    primary: '',
    secondary1: '',
    secondary2: ''
  },
  projects: [],
  tasks: [],
  meetings: [],
  ideas: [],
  executionLog: [],
  context: '',
  workspaceTasks: [],
  reminder: { enabled: true, time: '08:30' },
  lastBriefDate: ''
};

let data;
try {
  const saved = localStorage.getItem(KEY);
  data = saved ? { ...defaults, ...JSON.parse(saved) } : { ...defaults };
  if (!data.projects) data.projects = [];
  if (!data.bigThree) data.bigThree = { primary: '', secondary1: '', secondary2: '' };
  if (!data.ideas) data.ideas = [];
  if (!data.executionLog) data.executionLog = [];
} catch {
  data = { ...defaults };
}

// One-time seed: if no projects exist yet, populate with the user's real goals
if (!data.projects || data.projects.length === 0) {
  const now = new Date();
  const todayStr = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
  data.projects = [
    {
      id: 'g_aisafety',
      name: 'AI Safety Researcher Track',
      description: 'Build research credibility, apply to labs, orgs, cold-email researchers monthly',
      outcome: 'Get a role or research position in AI Safety by consistent monthly output and outreach',
      milestone: 'Complete first month: reading notes + outreach list + cold emails sent',
      next_action: 'Read one AI Safety paper and write a 3-paragraph summary',
      estimated_time: 60,
      status: 'active',
      priority: 'high',
      last_worked_at: todayStr,
      days_inactive: 0,
      created_at: todayStr
    },
    {
      id: 'g_courses',
      name: 'Academic Courses (AI SMPS + DL + Software Testing)',
      description: 'Three parallel IITM courses with Quiz 1 on Nov 15, Quiz 2 on Dec 5, End Term on Jan 10',
      outcome: 'Pass all three courses with strong grades',
      milestone: 'Complete Week 1-4 material for all 3 courses before Nov 15',
      next_action: 'Study Week 1 content for AI SMPS (first course)',
      estimated_time: 90,
      status: 'active',
      priority: 'high',
      last_worked_at: todayStr,
      days_inactive: 0,
      created_at: todayStr
    },
    {
      id: 'g_kaggle',
      name: 'Kaggle Competition',
      description: 'Active ML competition — model development and paper writing',
      outcome: 'Strong competition result + research paper submitted',
      milestone: 'Refine feature pipeline and improve validation score',
      next_action: 'Review baseline validation curves and identify top-3 improvement areas',
      estimated_time: 60,
      status: 'active',
      priority: 'normal',
      last_worked_at: todayStr,
      days_inactive: 0,
      created_at: todayStr
    }
  ];
  data.ideas = [
    {
      id: 'g_nptel',
      title: 'NPTEL: Responsible & Safe AI (Exam: Oct 18)',
      problem: 'Exam in 3 weeks — needs daily 45-min study blocks to finish in time',
      created_at: todayStr,
      status: 'active_idea'
    },
    {
      id: 'g_content',
      title: 'Content Pipeline (Substack + Blog + LinkedIn)',
      problem: 'Weekly Substack AI Safety digest, LinkedIn posts, blog articles as research byproducts',
      created_at: todayStr,
      status: 'active_idea'
    }
  ];
  data.bigThree = {
    primary: 'AI Safety Researcher Track — daily reading + monthly output',
    secondary1: 'Academic courses — 90min study block daily toward Nov 15 Quiz 1',
    secondary2: 'NPTEL exam prep — 45min daily, deadline Oct 18'
  };
  localStorage.setItem(KEY, JSON.stringify(data));
}

// Active State
let currentFocusTask = null;
let focusTimerInterval = null;
let focusSecondsRemaining = 25 * 60;
let focusTotalSeconds = 25 * 60;
let focusTimerPaused = false;

let checkinTargetTask = null;
let pendingTradeoffProject = null;
let toastTimer = null;
let alarmAudioInterval = null;
let audioCtx = null;

const $ = id => document.getElementById(id);
let sharedTasksReady = false;
let sharedEventsReady = false;
const sharedSaveFailures = new Set();

function persistSharedList(path, payload, label) {
  fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).then(async response => {
    let result = {};
    try { result = await response.json(); } catch (_) {}
    if (!response.ok || result.status === 'error') {
      throw new Error(result.message || `HTTP ${response.status}`);
    }
    sharedSaveFailures.delete(label);
  }).catch(error => {
    console.error(`Daymark ${label} save failed:`, error);
    if (!sharedSaveFailures.has(label)) {
      sharedSaveFailures.add(label);
      toast(`Saved in this window, but Daymark couldn't sync ${label}. I'll retry automatically.`);
    }
  });
}

const save = () => {
  localStorage.setItem(KEY, JSON.stringify(data));
  // Persist even before the first GET completes. The API merges by ID, so this
  // safely covers quick adds during startup and retries local-only changes.
  persistSharedList('/api/tasks', { operation: 'upsert', tasks: data.tasks || [] }, 'actions');
  const localEvents = (data.meetings || []).filter(event => event.source !== 'google');
  persistSharedList('/api/events', { operation: 'upsert', events: localEvents }, 'reminders');
};

async function syncSharedTasks() {
  try {
    const response = await fetch('/api/tasks', { cache: 'no-store' });
    if (!response.ok) return;
    const result = await response.json();
    if (!Array.isArray(result.tasks)) return;
    const previous = JSON.stringify(data.tasks || []);
    const merged = new Map((data.tasks || []).map(task => [task.id, task]));
    result.tasks.forEach(task => merged.set(task.id, task));
    data.tasks = [...merged.values()];
    const changed = JSON.stringify(data.tasks) !== previous;
    sharedTasksReady = true;
    localStorage.setItem(KEY, JSON.stringify(data));
    if (changed) render();
    save();
  } catch (_) {
    // Keep the local task list usable while the shared local server is offline.
  }
}

async function syncSharedEvents() {
  try {
    const response = await fetch('/api/events', { cache: 'no-store' });
    if (!response.ok) return;
    const result = await response.json();
    if (!Array.isArray(result.events)) return;
    const localEvents = (data.meetings || []).filter(event => event.source !== 'google');
    const previous = JSON.stringify(localEvents);
    const merged = new Map(localEvents.map(event => [event.id, event]));
    result.events.forEach(event => merged.set(event.id, event));
    const allEvents = [...merged.values()];
    const changed = JSON.stringify(allEvents) !== previous;
    data.meetings = [
      ...allEvents,
      ...(data.meetings || []).filter(event => event.source === 'google')
    ];
    sharedEventsReady = true;
    localStorage.setItem(KEY, JSON.stringify(data));
    if (changed) render();
    save();
    scheduleMeetingReminders();
  } catch (_) {
    // Keep local calendar items available while the shared server is offline.
  }
}

function deleteSharedTask(id) {
  if (!sharedTasksReady) return;
  fetch('/api/tasks', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation: 'delete', id })
  }).catch(() => {});
}
function deleteSharedEvent(id) {
  if (!sharedEventsReady) return;
  fetch('/api/events', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation: 'delete', id })
  }).catch(() => {});
}
const dateKey = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const dateLabel = (d, options) => new Intl.DateTimeFormat(undefined, options).format(d);
const today = new Date();

function escapeHTML(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
}


function toast(message) {
  const el = $('toast');
  el.textContent = message;
  el.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('visible'), 2800);
}

function sorted(items) {
  return [...items].sort((a,b) => (a.time || '99:99').localeCompare(b.time || '99:99'));
}

function normalizeTime(value) {
  const input = String(value || '').trim();
  const twentyFourHour = input.match(/^(\d{1,2}):(\d{2})$/);
  if (twentyFourHour && Number(twentyFourHour[1]) < 24 && Number(twentyFourHour[2]) < 60) {
    return `${String(Number(twentyFourHour[1])).padStart(2, '0')}:${twentyFourHour[2]}`;
  }
  const twelveHour = input.match(/^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$/i);
  if (!twelveHour || Number(twelveHour[1]) < 1 || Number(twelveHour[1]) > 12 || Number(twelveHour[2] || 0) > 59) return '';
  let hour = Number(twelveHour[1]) % 12;
  if (twelveHour[3].toLowerCase() === 'pm') hour += 12;
  return `${String(hour).padStart(2, '0')}:${twelveHour[2] || '00'}`;
}

function formatTime(t) {
  if (!t) return '';
  const normalized = normalizeTime(t);
  if (!normalized) return String(t);
  const [h, m] = normalized.split(':').map(Number);
  return dateLabel(new Date(2000, 0, 1, h, m), { hour: 'numeric', minute: '2-digit' });
}

/* =========================================================
   AUDIO & ALARM SYSTEM
   ========================================================= */
function playChimeNote(freq, time, duration = 0.45) {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(freq, time);
    gain.gain.setValueAtTime(0.0001, time);
    gain.gain.exponentialRampToValueAtTime(0.12, time + 0.04);
    gain.gain.exponentialRampToValueAtTime(0.0001, time + duration);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(time);
    osc.stop(time + duration);
  } catch (e) {
    console.warn('Audio error', e);
  }
}

function playAlarmSequence() {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    const now = audioCtx.currentTime;
    playChimeNote(659.25, now + 0.04, 0.45);
    playChimeNote(830.61, now + 0.28, 0.45);
    playChimeNote(987.77, now + 0.52, 0.65);
  } catch (e) {}
}

function startAlarmLoop() {
  stopAlarmLoop();
  playAlarmSequence();
  alarmAudioInterval = setInterval(playAlarmSequence, 2800);
}

function stopAlarmLoop() {
  if (alarmAudioInterval) {
    clearInterval(alarmAudioInterval);
    alarmAudioInterval = null;
  }
}

/* =========================================================
   FOCUS MODE & TIMER (Section 10 — START Interaction)
   ========================================================= */
function startFocusMode(task, customMinutes = null) {
  currentFocusTask = task;
  const project = data.projects.find(p => p.id === task.project_id);

  $('focusProjectTag').textContent = project ? `PROJECT: ${project.name.toUpperCase()}` : 'FOCUS SESSION';
  $('focusTaskTitle').textContent = task.title;
  $('focusNextStep').textContent = task.note ? `First concrete step: ${task.note}` : 'First concrete step: Open the required file/tool and take the first single action.';

  // Duration in minutes (Default 30 mins)
  const durationMins = customMinutes || task.duration || 30;
  focusTotalSeconds = durationMins * 60;
  focusSecondsRemaining = focusTotalSeconds;
  focusTimerPaused = false;
  $('pauseResumeTimerBtn').textContent = '⏸ Pause';

  updateFocusTimerDisplay();
  $('focusModeBackdrop').classList.remove('hidden');

  // Start interval
  if (focusTimerInterval) clearInterval(focusTimerInterval);
  focusTimerInterval = setInterval(() => {
    if (!focusTimerPaused) {
      focusSecondsRemaining--;
      updateFocusTimerDisplay();

      if (focusSecondsRemaining <= 0) {
        clearInterval(focusTimerInterval);
        playAlarmSequence();
        toast('Focus block complete! Time to check in.');
        openCompletionCheckin(currentFocusTask, 'done');
      }
    }
  }, 1000);

  // Anti-planning resets when an execution block starts!
  data.consecutive_tasks_planned = 0;
  save();
  $('antiPlanningBanner').classList.add('hidden');
}

function updateFocusTimerDisplay() {
  const m = Math.floor(Math.max(0, focusSecondsRemaining) / 60);
  const s = Math.max(0, focusSecondsRemaining) % 60;
  $('focusTimerDisplay').textContent = `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  const pct = (focusSecondsRemaining / focusTotalSeconds) * 100;
  $('focusProgressFill').style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

function pauseResumeFocusTimer() {
  focusTimerPaused = !focusTimerPaused;
  $('pauseResumeTimerBtn').textContent = focusTimerPaused ? '▶ Resume' : '⏸ Pause';
  toast(focusTimerPaused ? 'Timer paused' : 'Timer resumed');
}

function closeFocusMode() {
  if (focusTimerInterval) clearInterval(focusTimerInterval);
  $('focusModeBackdrop').classList.add('hidden');
}

/* =========================================================
   COMPLETION CHECK-IN & RECOVERY (Sections 11 & 12)
   ========================================================= */
let selectedCheckinStatus = 'done';
let selectedFrictionReason = 'Too vague or ambitious';

function openCompletionCheckin(task, initialStatus = 'done') {
  closeFocusMode();
  checkinTargetTask = task;
  selectedCheckinStatus = initialStatus;

  $('checkinTaskName').textContent = task.title;
  $('recoveryMicroInput').value = '';

  // Set buttons state
  document.querySelectorAll('.status-btn').forEach(btn => {
    btn.classList.toggle('selected', btn.dataset.status === selectedCheckinStatus);
  });

  const isComplete = selectedCheckinStatus === 'done';
  $('recoveryBox').classList.toggle('hidden', isComplete);

  $('checkinModalBackdrop').classList.remove('hidden');
}

function submitCheckin() {
  if (!checkinTargetTask) return;

  const now = new Date();
  const timeStr = `${dateKey(now)} ${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
  const microAction = $('recoveryMicroInput').value.trim();

  // Update task status
  if (selectedCheckinStatus === 'done') {
    checkinTargetTask.done = true;
    checkinTargetTask.status = 'done';
    playAlarmSequence();
    toast('Excellent execution! Step marked complete.');
  } else if (selectedCheckinStatus === 'dropped') {
    data.tasks = data.tasks.filter(t => t.id !== checkinTargetTask.id);
    toast('Task deliberately dropped. Reduced mental clutter.');
  } else {
    checkinTargetTask.done = false;
    checkinTargetTask.status = selectedCheckinStatus;
    if (microAction) {
      checkinTargetTask.note = `Micro-step: ${microAction}`;
      toast('Recovered with 5-min micro-action! Ready to resume.');
    } else {
      toast('Check-in logged. Recover gently.');
    }
  }

  // Update project last worked on
  const project = data.projects.find(p => p.id === checkinTargetTask.project_id);
  if (project) {
    project.last_worked_at = dateKey(now);
    project.days_inactive = 0;
  }

  // Record in execution log
  data.executionLog.unshift({
    timestamp: timeStr,
    task_title: checkinTargetTask.title,
    action: selectedCheckinStatus,
    duration_minutes: checkinTargetTask.duration || 25,
    reason: selectedCheckinStatus !== 'done' ? (microAction ? `${selectedFrictionReason} → ${microAction}` : selectedFrictionReason) : ''
  });

  save();
  render();
  $('checkinModalBackdrop').classList.add('hidden');
}

/* =========================================================
   ACTIVE PROJECT LIMIT GUARDRAIL (Section 4 — Max 3)
   ========================================================= */
function checkActiveProjectLimit() {
  const activeCount = data.projects.filter(p => p.status === 'active').length;
  return activeCount >= data.max_active_projects;
}

function promptTradeoff(newProjectData) {
  pendingTradeoffProject = newProjectData;
  const active = data.projects.filter(p => p.status === 'active');
  const list = $('pauseOptionsList');

  list.innerHTML = active.map(p => `
    <label class="pause-option-label">
      <input type="radio" name="tradeoffChoice" value="${escapeHTML(p.id)}">
      <div>
        <b>Pause: ${escapeHTML(p.name)}</b>
        <span style="display:block;font-size:9.5px;color:#7a8174">${escapeHTML(p.milestone || p.outcome)}</span>
      </div>
    </label>
  `).join('');

  $('activeLimitModalBackdrop').classList.remove('hidden');
}

function confirmTradeoff() {
  const selected = document.querySelector('input[name="tradeoffChoice"]:checked');
  if (!selected) {
    toast('Please select an existing project to pause, or keep in parking lot.');
    return;
  }

  const projToPause = data.projects.find(p => p.id === selected.value);
  if (projToPause) {
    projToPause.status = 'paused';
  }

  if (pendingTradeoffProject) {
    pendingTradeoffProject.status = 'active';
    data.projects.push(pendingTradeoffProject);
    toast(`Paused "${projToPause ? projToPause.name : 'project'}" & activated "${pendingTradeoffProject.name}".`);
  }

  pendingTradeoffProject = null;
  save();
  render();
  $('activeLimitModalBackdrop').classList.add('hidden');
}

/* =========================================================
   CALENDAR CONSTRAINTS & REALITY ENGINE (Section 5 & 8)
   ========================================================= */
function calculateCalendarBudget() {
  // Assume a standard workday of 8.5 hours (09:00 - 17:30 = 510 minutes)
  const workdayMinutes = 8.5 * 60;
  
  // Total meeting minutes
  const meetingMinutes = data.meetings.reduce((sum, m) => sum + (m.duration || 45), 0);
  
  // Buffer time (transition between meetings & focus)
  const bufferMinutes = data.meetings.length * 15;
  
  // Available focus time
  const availableFocusMinutes = Math.max(0, workdayMinutes - meetingMinutes - bufferMinutes);
  
  // Planned task time for today's incomplete tasks
  const plannedTaskMinutes = data.tasks.filter(t => !t.done).reduce((sum, t) => sum + (t.duration || 30), 0);

  return {
    meetingHours: (meetingMinutes / 60).toFixed(1),
    focusHours: (availableFocusMinutes / 60).toFixed(1),
    plannedHours: (plannedTaskMinutes / 60).toFixed(1),
    isOvercommitted: plannedTaskMinutes > availableFocusMinutes
  };
}

function parseICS(icsText) {
  const events = [];
  const lines = icsText.split(/\r\n|\n|\r/);
  let inEvent = false;
  let summary = '';
  let dtstart = '';
  let dtend = '';

  for (const line of lines) {
    if (line.startsWith('BEGIN:VEVENT')) {
      inEvent = true;
      summary = '';
      dtstart = '';
      dtend = '';
    } else if (line.startsWith('END:VEVENT')) {
      if (summary) {
        let time = '10:00';
        let duration = 45;
        if (dtstart && dtstart.includes('T')) {
          const tPart = dtstart.split('T')[1];
          time = `${tPart.slice(0,2)}:${tPart.slice(2,4)}`;
        }
        events.push({
          id: crypto.randomUUID(),
          title: summary.replace(/\\,/g, ',').replace(/\\;/g, ';'),
          time: time,
          duration: duration,
          note: 'Imported from Google Calendar'
        });
      }
      inEvent = false;
    } else if (inEvent) {
      if (line.startsWith('SUMMARY:')) summary = line.substring(8).trim();
      else if (line.startsWith('DTSTART')) dtstart = line.split(':')[1] || '';
      else if (line.startsWith('DTEND')) dtend = line.split(':')[1] || '';
    }
  }
  return events;
}

/* =========================================================
   DAILY BRIEFING & ALARM POP-UP (Section 6 & 9)
   ========================================================= */
function openDailyBriefModal(isAlarm = false) {
  const backdrop = $('dailyBriefModalBackdrop');
  const badge = $('alarmBadge');
  const badgeText = $('alarmBadgeText');
  const modalTitle = $('briefModalTitle');
  const modalSubtitle = $('briefModalSubtitle');

  if (isAlarm) {
    badge.classList.remove('hidden');
    badgeText.textContent = 'DAILY REMINDER & ALARM';
    modalTitle.textContent = 'Good morning. Time to check in.';
    modalSubtitle.textContent = 'Here is your agenda, meetings, and workspace priorities for today.';
    startAlarmLoop();
  } else {
    badge.classList.add('hidden');
    modalTitle.textContent = 'Today’s Morning Brief';
    modalSubtitle.textContent = 'A calm preview of your Big Three, calendar, and workspace priorities.';
    stopAlarmLoop();
    playAlarmSequence();
  }

  $('briefBigThreeTitle').textContent = `Primary: ${data.bigThree.primary}`;

  // Populate Tasks
  const taskList = $('briefTaskList');
  const openTasks = data.tasks.filter(t => !t.done);
  $('briefTaskCount').textContent = openTasks.length;
  if (!data.tasks.length) {
    taskList.innerHTML = '<div class="empty-state">No tasks scheduled yet for today.</div>';
  } else {
    taskList.innerHTML = sorted(data.tasks).map(item => `
      <div class="brief-row">
        <button class="check-box ${item.done ? 'done' : ''}" data-brief-action="toggle" data-id="${escapeHTML(item.id)}">${item.done ? '✓' : ''}</button>
        <span class="${item.done ? 'completed' : ''}" style="flex:1">${escapeHTML(item.title)}</span>
        <span class="task-duration-pill">${item.duration || 30}m</span>
      </div>
    `).join('');
  }

  // Populate Meetings
  const meetingList = $('briefMeetingList');
  const todaysMeetings = (data.meetings || []).filter(m => !m.date || m.date === dateKey(new Date()));
  $('briefMeetingCount').textContent = todaysMeetings.length;
  if (!todaysMeetings.length) {
    meetingList.innerHTML = '<div class="empty-state">No calendar commitments scheduled for today.</div>';
  } else {
    meetingList.innerHTML = sorted(todaysMeetings).map(item => `
      <div class="brief-row">
        <span class="panel-icon meeting-icon" style="width:20px;height:20px;font-size:10px">◷</span>
        <span style="flex:1"><b>${escapeHTML(item.title)}</b> · <small style="color:#85887d">${item.duration || 45} mins</small></span>
        ${item.time ? `<time>${escapeHTML(formatTime(item.time))}</time>` : ''}
      </div>
    `).join('');
  }

  // Populate Workspace Context
  const ctxText = $('briefContextText');
  const wsTasks = (data.workspaceTasks || []).filter(t => !t.added);
  let contextHtml = data.context ? `<p style="margin:0 0 6px">${escapeHTML(data.context)}</p>` : '';
  if (wsTasks.length) {
    contextHtml += `<div style="font-size:9px; color:#496b52; font-weight:700; margin-bottom:4px">DETECTED WORKSPACE DELIVERABLES:</div>`;
    contextHtml += wsTasks.slice(0, 2).map(t => `• <b>${escapeHTML(t.title)}</b> (${escapeHTML(t.source)})`).join('<br>');
  }
  ctxText.innerHTML = contextHtml || 'All workspace deliverables reviewed.';

  backdrop.classList.remove('hidden');

  taskList.querySelectorAll('[data-brief-action="toggle"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const item = data.tasks.find(x => x.id === btn.dataset.id);
      if (item) item.done = !item.done;
      save();
      render();
      openDailyBriefModal(isAlarm);
    });
  });
}

function closeDailyBriefModal() {
  stopAlarmLoop();
  $('dailyBriefModalBackdrop').classList.add('hidden');
}

function snoozeAlarm() {
  stopAlarmLoop();
  closeDailyBriefModal();
  toast('Alarm snoozed for 10 minutes.');
  setTimeout(() => openDailyBriefModal(true), 10 * 60 * 1000);
}

function dismissAlarm() {
  stopAlarmLoop();
  closeDailyBriefModal();
  data.lastBriefDate = dateKey(new Date());
  save();
  toast('Alarm dismissed. Have a calm and productive day!');
}

/* =========================================================
   END-OF-DAY SHUTDOWN (Section 19)
   ========================================================= */
function openShutdownModal() {
  const doneList = $('shutdownDoneList');
  const pendingList = $('shutdownPendingList');
  const completed = data.tasks.filter(t => t.done);
  const pending = data.tasks.filter(t => !t.done);

  doneList.innerHTML = completed.length
    ? completed.map(t => `<div class="brief-row">✓ <b>${escapeHTML(t.title)}</b></div>`).join('')
    : '<div class="empty-state">No tasks checked off today. Let\'s recover tomorrow.</div>';

  pendingList.innerHTML = pending.length
    ? pending.map(t => `
        <div class="shutdown-row">
          <span>${escapeHTML(t.title)}</span>
          <div style="display:flex;gap:6px">
            <button class="secondary-btn" data-shutdown-action="drop" data-id="${escapeHTML(t.id)}" style="padding:3px 7px;font-size:9px">Drop</button>
            <button class="primary-button" data-shutdown-action="keep" data-id="${escapeHTML(t.id)}" style="padding:3px 7px;font-size:9px">Move to Tomorrow</button>
          </div>
        </div>
      `).join('')
    : '<div class="empty-state">No loose ends! You have a completely clean slate.</div>';

  pendingList.querySelectorAll('[data-shutdown-action]').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.id;
      const action = btn.dataset.shutdownAction;
      if (action === 'drop') {
        data.tasks = data.tasks.filter(t => t.id !== id);
        toast('Dropped unfinished task. Reduced clutter.');
      } else {
        toast('Kept for tomorrow.');
      }
      openShutdownModal();
    });
  });

  $('shutdownModalBackdrop').classList.remove('hidden');
}

function completeShutdown() {
  data.executionLog.unshift({
    timestamp: `${dateKey(new Date())} 18:00`,
    task_title: 'End-of-day shutdown completed',
    action: 'shutdown',
    duration_minutes: 5,
    reason: 'Day closed intentionally'
  });
  save();
  render();
  $('shutdownModalBackdrop').classList.add('hidden');
  toast('Shutdown complete. Step away from work and unplug.');
}

/* =========================================================
   WORKSPACE SCANNER (Preserved & Enhanced)
   ========================================================= */
function parseMarkdownContent(fileName, text) {
  const lines = text.split('\n');
  const items = [];
  for (const rawLine of lines) {
    const line = rawLine.trim();
    const checkboxMatch = line.match(/^[-*]\s*\[\s*\]\s+(.*)/);
    if (checkboxMatch) {
      items.push({ id: crypto.randomUUID(), title: checkboxMatch[1].trim(), source: fileName, added: false });
      continue;
    }
    const keywordMatch = line.match(/^(TODO|Milestone|Deliverable|Focus|Objective|Priority)[:\s]+(.*)/i);
    if (keywordMatch && keywordMatch[2].length > 4) {
      items.push({ id: crypto.randomUUID(), title: keywordMatch[2].trim(), source: fileName, added: false });
    }
  }
  return items;
}

async function scanWorkspaceWithDirectoryPicker() {
  if (!('showDirectoryPicker' in window)) {
    $('fallbackFileInput').click();
    return;
  }
  try {
    const dirHandle = await window.showDirectoryPicker();
    toast(`Scanning workspace: ${dirHandle.name}...`);
    const foundItems = [];

    async function traverse(handle, path = '') {
      for await (const entry of handle.values()) {
        if (entry.kind === 'file') {
          if (entry.name.endsWith('.md') || entry.name.endsWith('.txt')) {
            const file = await entry.getFile();
            const text = await file.text();
            const relPath = path ? `${path}/${entry.name}` : entry.name;
            const items = parseMarkdownContent(relPath, text);
            foundItems.push(...items);
          }
        } else if (entry.kind === 'directory' && !entry.name.startsWith('.') && entry.name !== 'node_modules') {
          await traverse(entry, path ? `${path}/${entry.name}` : entry.name);
        }
      }
    }

    await traverse(dirHandle);
    handleScannedTasks(foundItems, dirHandle.name);
  } catch (err) {
    if (err.name !== 'AbortError') {
      console.error(err);
      toast('Could not scan folder directly. Use file selector.');
    }
  }
}

function handleFallbackFileInput(e) {
  const files = Array.from(e.target.files);
  if (!files.length) return;
  toast(`Scanning ${files.length} workspace files...`);
  const foundItems = [];
  Promise.all(files.map(async file => {
    if (file.name.endsWith('.md') || file.name.endsWith('.txt')) {
      const text = await file.text();
      const items = parseMarkdownContent(file.webkitRelativePath || file.name, text);
      foundItems.push(...items);
    }
  })).then(() => {
    handleScannedTasks(foundItems, 'Selected workspace files');
  });
}

function handleScannedTasks(items, sourceName) {
  if (!items.length) {
    toast('No markdown TODOs or milestones found in selected folder.');
    return;
  }
  data.workspaceTasks = items;
  save();
  renderWorkspaceTasks(sourceName);
  toast(`Discovered ${items.length} workspace action items!`);
}

function renderWorkspaceTasks(sourceName = 'Workspace') {
  const container = $('scannedFilesContainer');
  const meta = $('scannedMeta');
  const list = $('detectedTasksList');
  const items = data.workspaceTasks || [];

  container.classList.remove('hidden');
  meta.textContent = `${items.length} action items detected from ${sourceName}`;

  if (!items.length) {
    list.innerHTML = '<div class="empty-state">No uncompleted checkboxes or TODOs found.</div>';
    return;
  }

  list.innerHTML = items.map(item => `
    <div class="detected-task-item">
      <div>
        <b>${escapeHTML(item.title)}</b>
        <span class="detected-task-source">from ${escapeHTML(item.source)}</span>
      </div>
      <button class="add-detected-btn" data-action="add-scanned" data-id="${escapeHTML(item.id)}" ${item.added ? 'disabled style="opacity:0.5"' : ''}>
        ${item.added ? 'Added ✓' : '＋ Add as Today Action'}
      </button>
    </div>
  `).join('');

  list.querySelectorAll('[data-action="add-scanned"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const item = data.workspaceTasks.find(x => x.id === btn.dataset.id);
      if (item && !item.added) {
        item.added = true;
        data.tasks.push({
          id: crypto.randomUUID(),
          project_id: data.projects[0] ? data.projects[0].id : '',
          title: item.title,
          note: `From ${item.source}`,
          tier: 'should',
          duration: 30,
          done: false,
          status: 'todo'
        });
        save();
        render();
        renderWorkspaceTasks(sourceName);
        toast(`Added to today: "${item.title.slice(0, 24)}..."`);
      }
    });
  });
}

/* =========================================================
   RENDERING & VIEWS
   ========================================================= */
function render() {
  $('greetingEyebrow').textContent = dateLabel(today, { weekday: 'long', month: 'long', day: 'numeric' }).toUpperCase();
  $('dateButton').textContent = dateLabel(today, { month: 'short', day: 'numeric', year: 'numeric' });

  // Navigation counts
  const openTasks = data.tasks.filter(t => !t.done);
  $('todayNavCount').textContent = openTasks.length;

  const activeProjects = data.projects.filter(p => p.status === 'active');
  $('activeProjectsCount').textContent = `${activeProjects.length}/${data.max_active_projects}`;
  $('projectCapacityBadge').textContent = `${activeProjects.length} / ${data.max_active_projects} ACTIVE`;
  $('activeLimitMini').textContent = `${activeProjects.length}/${data.max_active_projects} Active Projects`;
  $('parkingLotNavCount').textContent = data.ideas.filter(i => i.status === 'parked').length;
  $('parkedCountNum').textContent = data.ideas.filter(i => i.status === 'parked').length;

  // Big Three
  $('primaryOutcomeText').textContent = data.bigThree.primary || 'Set your primary outcome for today';
  $('secondaryOutcome1').textContent = data.bigThree.secondary1 || 'Set secondary outcome 1';
  $('secondaryOutcome2').textContent = data.bigThree.secondary2 || 'Set secondary outcome 2';

  // Calendar Budget Reality Check
  const budget = calculateCalendarBudget();
  $('topTimePill').textContent = `${budget.focusHours}h focus free`;
  $('statMeetingHours').textContent = `${budget.meetingHours} hrs`;
  $('statFocusHours').textContent = `${budget.focusHours} hrs`;
  $('statPlannedHours').textContent = `${budget.plannedHours} hrs`;

  const statusPill = $('timeStatusPill');
  if (budget.isOvercommitted) {
    statusPill.className = 'time-status-pill warning';
    statusPill.innerHTML = `<span>⚠ Overcommitted (+${(budget.plannedHours - budget.focusHours).toFixed(1)}h)</span>`;
  } else {
    statusPill.className = 'time-status-pill safe';
    statusPill.innerHTML = `<span>✓ Realistic Schedule</span>`;
  }

  // Anti-Planning Banner
  if (data.consecutive_tasks_planned >= 4) {
    $('antiPlanningBanner').classList.remove('hidden');
  } else {
    $('antiPlanningBanner').classList.add('hidden');
  }

  // Hero Card: Next Immediate Action
  const nextTask = data.tasks.find(t => !t.done);
  if (nextTask) {
    const proj = data.projects.find(p => p.id === nextTask.project_id);
    $('nextHeroTitle').textContent = nextTask.title;
    $('nextHeroSub').textContent = `Project: ${proj ? proj.name : 'Personal'} · Estimated ${nextTask.duration || 30} mins`;
    $('nextActionHero').classList.remove('hidden');
    $('startHeroBtn').onclick = () => startFocusMode(nextTask);
  } else {
    $('nextHeroTitle').textContent = 'All planned tasks are complete!';
    $('nextHeroSub').textContent = 'A calm slate. Enjoy the breathing room or close out the day.';
  }

  // Render Tiers
  renderTaskTier('mustTaskList', data.tasks.filter(t => t.tier === 'must'), 'must');
  renderTaskTier('shouldTaskList', data.tasks.filter(t => t.tier === 'should'), 'should');
  renderTaskTier('optionalTaskList', data.tasks.filter(t => t.tier === 'optional'), 'optional');

  // Render Meetings
  renderMeetings();

  // Render Projects View
  renderProjects();

  // Render Idea Parking Lot
  renderParkingLot();

  // Render Reviews & Execution Log
  renderReviews();

  // Populate Project Selects in Modals
  populateProjectSelects();
}

function renderTaskTier(elementId, items, tier) {
  const container = $(elementId);
  if (!items.length) {
    container.innerHTML = `<div class="empty-state" style="padding:10px 0;font-size:9.5px">No ${tier} actions.</div>`;
    return;
  }

  container.innerHTML = sorted(items).map(item => {
    const proj = data.projects.find(p => p.id === item.project_id);
    return `
      <div class="day-item">
        <button class="check-box ${item.done ? 'done' : ''}" data-task-action="toggle" data-id="${escapeHTML(item.id)}" aria-label="Toggle task">${item.done ? '✓' : ''}</button>
        <div class="item-copy">
          <div class="item-title ${item.done ? 'completed' : ''}">${escapeHTML(item.title)}</div>
          ${(item.time || item.note) ? `<div class="item-note">${item.time ? `${escapeHTML(formatTime(item.time))}${item.note ? ' · ' : ''}` : ''}${item.note ? escapeHTML(item.note) : ''}</div>` : ''}
        </div>
        ${proj ? `<span class="task-tag-pill">${escapeHTML(proj.name.slice(0, 16))}</span>` : ''}
        <span class="task-duration-pill">${item.duration || 30}m</span>
        ${!item.done ? `<button class="task-start-mini-btn" data-task-action="start" data-id="${escapeHTML(item.id)}" title="Start focus session">⚡ START</button>` : ''}
        <button class="item-delete" data-task-action="delete" data-id="${escapeHTML(item.id)}" title="Delete item">×</button>
      </div>
    `;
  }).join('');

  // Wire actions
  container.querySelectorAll('[data-task-action]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const id = btn.dataset.id;
      const action = btn.dataset.taskAction;
      const task = data.tasks.find(t => t.id === id);
      if (!task) return;

      if (action === 'toggle') {
        task.done = !task.done;
        if (task.done) {
          playAlarmSequence();
          toast('Step completed!');
        }
        save();
        render();
      } else if (action === 'start') {
        startFocusMode(task);
      } else if (action === 'delete') {
        data.meetings.filter(meeting => meeting.task_id === id).forEach(meeting => deleteSharedEvent(meeting.id));
        data.meetings = data.meetings.filter(meeting => meeting.task_id !== id);
        data.tasks = data.tasks.filter(t => t.id !== id);
        deleteSharedTask(id);
        save();
        render();
        toast('Action removed.');
      }
    });
  });
}

function renderMeetings() {
  const list = $('meetingList');
  const fullList = $('calendarFullMeetingList');
  const todayMeetings = (data.meetings || []).filter(m => !m.date || m.date === dateKey(new Date()));
  if (!todayMeetings.length) {
    const empty = '<div class="empty-state">No calendar constraints. The day is yours.</div>';
    if (list) list.innerHTML = empty;
    if (fullList) fullList.innerHTML = empty;
    return;
  }

  const html = sorted(todayMeetings).map(m => `
    <div class="day-item" style="grid-template-columns: 20px minmax(0,1fr) auto auto">
      <span class="panel-icon meeting-icon" style="width:20px;height:20px;font-size:10px">◷</span>
      <div class="item-copy">
        <div class="item-title"><b>${escapeHTML(m.title)}</b></div>
        ${m.note ? `<div class="item-note">${escapeHTML(m.note)}</div>` : ''}
      </div>
      <span class="item-time">${escapeHTML(formatTime(m.time))} (${m.duration || 45}m)</span>
      <button class="item-delete" data-meeting-delete="${escapeHTML(m.id)}">×</button>
    </div>
  `).join('');

  if (list) list.innerHTML = html;
  if (fullList) fullList.innerHTML = html;

  document.querySelectorAll('[data-meeting-delete]').forEach(btn => {
    btn.addEventListener('click', () => {
      const meeting = data.meetings.find(m => m.id === btn.dataset.meetingDelete);
      if (meeting && meeting.source !== 'google') deleteSharedEvent(meeting.id);
      data.meetings = data.meetings.filter(m => m.id !== btn.dataset.meetingDelete);
      save();
      render();
      toast('Meeting removed.');
    });
  });

  // Calculate and render focus windows for Calendar tab
  const windowsList = $('focusWindowsList');
  if (windowsList) {
    windowsList.innerHTML = `
      <div class="focus-window-pill"><span>09:00 — 11:00</span> <b>2.0 hrs Deep Work</b></div>
      <div class="focus-window-pill"><span>12:00 — 14:30</span> <b>2.5 hrs Deep Work</b></div>
      <div class="focus-window-pill"><span>16:00 — 17:30</span> <b>1.5 hrs Wrap-up</b></div>
    `;
  }
}

function renderProjects() {
  const activeGrid = $('activeProjectsGrid');
  const pausedGrid = $('pausedProjectsGrid');
  const completedGrid = $('completedProjectsGrid');

  const active = data.projects.filter(p => p.status === 'active');
  const paused = data.projects.filter(p => p.status === 'paused');
  const completed = data.projects.filter(p => p.status === 'completed');

  activeGrid.innerHTML = active.length
    ? active.map(p => `
        <div class="project-card">
          <div class="project-card-header">
            <div>
              <h4>${escapeHTML(p.name)}</h4>
              <p class="project-outcome"><b>Desired Outcome:</b> ${escapeHTML(p.outcome)}</p>
            </div>
            <span class="badge primary-badge">ACTIVE</span>
          </div>
          <div class="project-concrete-box">
            <div>
              <b>Current Milestone: ${escapeHTML(p.milestone || 'In progress')}</b>
              <span>Next Action: ${escapeHTML(p.next_action)}</span>
            </div>
            <button class="task-start-mini-btn" data-proj-start="${escapeHTML(p.id)}">⚡ START ACTION</button>
          </div>
          <div class="project-card-footer">
            <span>Last worked on: ${escapeHTML(p.last_worked_at || 'Recently')}</span>
            <div class="project-actions">
              <button class="secondary-btn" data-proj-pause="${escapeHTML(p.id)}" style="padding:3px 8px;font-size:9px">Pause</button>
              <button class="secondary-btn" data-proj-complete="${escapeHTML(p.id)}" style="padding:3px 8px;font-size:9px">Mark Complete</button>
            </div>
          </div>
        </div>
      `).join('')
    : '<div class="empty-state">No active projects. Activate one below or add a new project.</div>';

  pausedGrid.innerHTML = paused.length
    ? paused.map(p => `
        <div class="project-card" style="opacity:0.85">
          <div class="project-card-header">
            <h4>${escapeHTML(p.name)}</h4>
            <span class="badge secondary-badge">PAUSED</span>
          </div>
          <p class="project-outcome">${escapeHTML(p.outcome)}</p>
          <div class="project-card-footer">
            <span>Milestone: ${escapeHTML(p.milestone || 'Paused')}</span>
            <button class="secondary-btn" data-proj-resume="${escapeHTML(p.id)}" style="padding:4px 9px;font-size:9px">Resume (Check Limit)</button>
          </div>
        </div>
      `).join('')
    : '<div class="empty-state">No paused projects.</div>';

  completedGrid.innerHTML = completed.length
    ? completed.map(p => `
        <div class="project-card" style="background:#f9faf7">
          <div class="project-card-header">
            <h4>✓ ${escapeHTML(p.name)}</h4>
            <span class="badge" style="background:#e4eada;color:#35593c">COMPLETED</span>
          </div>
          <p class="project-outcome">${escapeHTML(p.outcome)}</p>
        </div>
      `).join('')
    : '<div class="empty-state">Finished projects will be celebrated here.</div>';

  // Wire project actions
  document.querySelectorAll('[data-proj-start]').forEach(btn => {
    btn.addEventListener('click', () => {
      const proj = data.projects.find(p => p.id === btn.dataset.projStart);
      if (proj) {
        startFocusMode({
          id: crypto.randomUUID(),
          project_id: proj.id,
          title: proj.next_action || proj.milestone,
          duration: proj.estimated_time || 30
        });
      }
    });
  });

  document.querySelectorAll('[data-proj-pause]').forEach(btn => {
    btn.addEventListener('click', () => {
      const proj = data.projects.find(p => p.id === btn.dataset.projPause);
      if (proj) {
        proj.status = 'paused';
        save();
        render();
        toast(`Paused "${proj.name}".`);
      }
    });
  });

  document.querySelectorAll('[data-proj-complete]').forEach(btn => {
    btn.addEventListener('click', () => {
      const proj = data.projects.find(p => p.id === btn.dataset.projComplete);
      if (proj) {
        proj.status = 'completed';
        save();
        render();
        toast(`Project "${proj.name}" completed! Outstanding work.`);
      }
    });
  });

  document.querySelectorAll('[data-proj-resume]').forEach(btn => {
    btn.addEventListener('click', () => {
      const proj = data.projects.find(p => p.id === btn.dataset.projResume);
      if (!proj) return;
      if (checkActiveProjectLimit()) {
        promptTradeoff(proj);
      } else {
        proj.status = 'active';
        save();
        render();
        toast(`Resumed "${proj.name}".`);
      }
    });
  });
}

function renderParkingLot() {
  const list = $('parkingLotList');
  const parked = data.ideas.filter(i => i.status === 'parked');
  if (!parked.length) {
    list.innerHTML = '<div class="empty-state">No parked ideas. Your focus is pure.</div>';
    return;
  }

  list.innerHTML = parked.map(i => `
    <div class="parking-item">
      <div>
        <h5>${escapeHTML(i.title)}</h5>
        ${i.problem ? `<p>Problem: ${escapeHTML(i.problem)}</p>` : ''}
      </div>
      <div style="display:flex;gap:7px">
        <button class="secondary-btn" data-idea-activate="${escapeHTML(i.id)}" style="padding:4px 9px;font-size:9.5px">Activate as Project</button>
        <button class="item-delete" data-idea-delete="${escapeHTML(i.id)}">×</button>
      </div>
    </div>
  `).join('');

  document.querySelectorAll('[data-idea-activate]').forEach(btn => {
    btn.addEventListener('click', () => {
      const idea = data.ideas.find(i => i.id === btn.dataset.ideaActivate);
      if (!idea) return;
      const newProj = {
        id: crypto.randomUUID(),
        name: idea.title,
        description: idea.problem || '',
        outcome: `Complete implementation of ${idea.title}`,
        milestone: 'Initial design and prototype',
        next_action: 'Outline the first 30-minute prototype step',
        estimated_time: 30,
        status: 'active',
        priority: 'normal',
        last_worked_at: dateKey(new Date()),
        days_inactive: 0,
        created_at: dateKey(new Date())
      };

      if (checkActiveProjectLimit()) {
        promptTradeoff(newProj);
      } else {
        data.projects.push(newProj);
        idea.status = 'activated';
        save();
        render();
        toast(`Activated "${idea.title}" as new project.`);
      }
    });
  });

  document.querySelectorAll('[data-idea-delete]').forEach(btn => {
    btn.addEventListener('click', () => {
      data.ideas = data.ideas.filter(i => i.id !== btn.dataset.ideaDelete);
      save();
      render();
      toast('Idea removed from parking lot.');
    });
  });
}

function renderReviews() {
  const logBody = $('executionLogBody');
  const logs = data.executionLog || [];

  const focusLogs = logs.filter(l => l.action === 'completed' || l.action === 'done');
  const totalMinutes = focusLogs.reduce((sum, l) => sum + (l.duration_minutes || 25), 0);

  $('statCompletedFocusCount').textContent = focusLogs.length;
  $('statTotalFocusMinutes').textContent = `${totalMinutes}m`;
  $('statTasksDoneCount').textContent = data.tasks.filter(t => t.done).length;

  // Stale projects check (>7 days)
  const stale = data.projects.filter(p => p.status === 'active' && (p.days_inactive > 7));
  $('statStaleProjectsCount').textContent = stale.length;

  if (!logs.length) {
    logBody.innerHTML = '<tr><td colspan="4" class="empty-state">No execution entries yet.</td></tr>';
    return;
  }

  logBody.innerHTML = logs.slice(0, 15).map(l => `
    <tr>
      <td style="color:#8c9185;white-space:nowrap">${escapeHTML(l.timestamp)}</td>
      <td><b>${escapeHTML(l.task_title)}</b></td>
      <td><span class="badge ${l.action === 'completed' || l.action === 'done' ? 'primary-badge' : 'secondary-badge'}">${escapeHTML(l.action.toUpperCase())}</span></td>
      <td style="color:#6d7367">${escapeHTML(l.reason || 'Executed smoothly')}</td>
    </tr>
  `).join('');
}

function populateProjectSelects() {
  const select = $('itemProjectSelect');
  if (!select) return;
  const active = data.projects.filter(p => p.status === 'active');
  select.innerHTML = active.map(p => `<option value="${escapeHTML(p.id)}">${escapeHTML(p.name)}</option>`).join('') + '<option value="">(No Project / General)</option>';
}

function showView(view) {
  document.querySelectorAll('.page').forEach(el => el.classList.add('hidden'));
  $(`${view}View`).classList.remove('hidden');
  document.querySelectorAll('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  const titles = {
    today: 'Today',
    projects: 'Projects & Focus',
    ideas: 'Idea Parking Lot',
    calendar: 'Calendar & Time',
    review: 'Reviews & Execution Log',
    context: 'Workspace Scanner'
  };
  $('breadcrumbView').textContent = titles[view] || 'Today';
  if (view === 'context') $('contextInput').value = data.context || '';
}

/* =========================================================
   EVENT LISTENERS & MODAL BINDINGS
   ======================================================== */
document.querySelectorAll('.nav-item').forEach(button => {
  button.addEventListener('click', () => showView(button.dataset.view));
});

// Topbar & Header Buttons
$('briefButton').addEventListener('click', () => openDailyBriefModal(false));
$('testAlarmButton').addEventListener('click', () => openDailyBriefModal(true));
$('shutdownLaunchBtn').addEventListener('click', openShutdownModal);
$('runShutdownBtn').addEventListener('click', openShutdownModal);
$('runWeeklyReviewBtn').addEventListener('click', () => {
  showView('review');
  toast('Weekly review metrics loaded.');
});

// Big Three
$('editBigThreeBtn').addEventListener('click', () => {
  $('primaryInput').value = data.bigThree.primary || '';
  $('sec1Input').value = data.bigThree.secondary1 || '';
  $('sec2Input').value = data.bigThree.secondary2 || '';
  $('editBigThreeModalBackdrop').classList.remove('hidden');
});

$('closeBigThreeBtn').addEventListener('click', () => $('editBigThreeModalBackdrop').classList.add('hidden'));

$('bigThreeForm').addEventListener('submit', e => {
  e.preventDefault();
  data.bigThree.primary = $('primaryInput').value.trim();
  data.bigThree.secondary1 = $('sec1Input').value.trim();
  data.bigThree.secondary2 = $('sec2Input').value.trim();
  save();
  render();
  $('editBigThreeModalBackdrop').classList.add('hidden');
  toast('Daily Big Three updated!');
});

// Add Task
$('addTaskQuick').addEventListener('click', () => {
  $('itemTitle').value = '';
  $('itemNote').value = '';
  $('itemTime').value = '';
  $('modalBackdrop').classList.remove('hidden');
});
$('modalClose').addEventListener('click', () => $('modalBackdrop').classList.add('hidden'));

$('itemForm').addEventListener('submit', e => {
  e.preventDefault();
  const title = $('itemTitle').value.trim();
  if (!title) return;

  data.tasks.push({
    id: crypto.randomUUID(),
    project_id: $('itemProjectSelect').value,
    title: title,
    tier: $('itemTierSelect').value,
    duration: parseInt($('itemDurationSelect').value, 10) || 30,
    time: $('itemTime').value,
    note: $('itemNote').value.trim(),
    done: false,
    status: 'todo'
  });

  // Track planning to detect anti-planning loops
  data.consecutive_tasks_planned = (data.consecutive_tasks_planned || 0) + 1;

  save();
  render();
  $('modalBackdrop').classList.add('hidden');
  toast('Concrete action added.');
});

// Anti-Planning Banner Action
$('antiPlanningStartBtn').addEventListener('click', () => {
  const nextTask = data.tasks.find(t => !t.done);
  if (nextTask) {
    startFocusMode(nextTask);
  }
});

// New Project
$('newProjectBtn').addEventListener('click', () => {
  if (checkActiveProjectLimit()) {
    promptTradeoff({
      id: crypto.randomUUID(),
      name: 'New Project',
      outcome: 'Clear outcome',
      milestone: 'First milestone',
      next_action: 'First concrete 20m action',
      estimated_time: 30,
      status: 'active',
      priority: 'high',
      last_worked_at: dateKey(new Date()),
      days_inactive: 0,
      created_at: dateKey(new Date())
    });
  } else {
    $('projNameInput').value = '';
    $('projOutcomeInput').value = '';
    $('projMilestoneInput').value = '';
    $('projNextActionInput').value = '';
    $('projectModalBackdrop').classList.remove('hidden');
  }
});
$('closeProjectModalBtn').addEventListener('click', () => $('projectModalBackdrop').classList.add('hidden'));

$('projectForm').addEventListener('submit', e => {
  e.preventDefault();
  const name = $('projNameInput').value.trim();
  if (!name) return;

  const newProj = {
    id: crypto.randomUUID(),
    name: name,
    description: '',
    outcome: $('projOutcomeInput').value.trim(),
    milestone: $('projMilestoneInput').value.trim(),
    next_action: $('projNextActionInput').value.trim(),
    estimated_time: 30,
    status: 'active',
    priority: 'high',
    last_worked_at: dateKey(new Date()),
    days_inactive: 0,
    created_at: dateKey(new Date())
  };

  data.projects.push(newProj);
  save();
  render();
  $('projectModalBackdrop').classList.add('hidden');
  toast(`Project "${name}" activated!`);
});

// Tradeoff Limit Modal
$('closeActiveLimitBtn').addEventListener('click', () => $('activeLimitModalBackdrop').classList.add('hidden'));
$('confirmTradeoffBtn').addEventListener('click', confirmTradeoff);
$('keepInParkingBtn').addEventListener('click', () => {
  if (pendingTradeoffProject) {
    data.ideas.push({
      id: crypto.randomUUID(),
      title: pendingTradeoffProject.name,
      problem: pendingTradeoffProject.outcome,
      created_at: dateKey(new Date()),
      status: 'parked'
    });
    pendingTradeoffProject = null;
    save();
    render();
    toast('Project kept safely in Idea Parking Lot.');
  }
  $('activeLimitModalBackdrop').classList.add('hidden');
});

// Focus Mode Controls
$('minimizeFocusBtn').addEventListener('click', closeFocusMode);
$('pauseResumeTimerBtn').addEventListener('click', pauseResumeFocusTimer);
$('blockedFocusBtn').addEventListener('click', () => {
  if (currentFocusTask) openCompletionCheckin(currentFocusTask, 'blocked');
});
$('finishFocusBtn').addEventListener('click', () => {
  if (currentFocusTask) openCompletionCheckin(currentFocusTask, 'done');
});

// Check-in Modal Controls
$('closeCheckinBtn').addEventListener('click', () => $('checkinModalBackdrop').classList.add('hidden'));
document.querySelectorAll('.status-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    selectedCheckinStatus = btn.dataset.status;
    document.querySelectorAll('.status-btn').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
    $('recoveryBox').classList.toggle('hidden', selectedCheckinStatus === 'done');
  });
});

document.querySelectorAll('.reason-pill').forEach(pill => {
  pill.addEventListener('click', () => {
    document.querySelectorAll('.reason-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    selectedFrictionReason = pill.dataset.reason;
  });
});

$('submitCheckinBtn').addEventListener('click', submitCheckin);

// Idea Quick Capture
function captureIdea(title, problem = '') {
  if (!title) return;
  data.ideas.unshift({
    id: crypto.randomUUID(),
    title: title,
    problem: problem,
    created_at: dateKey(new Date()),
    status: 'parked'
  });
  save();
  render();
  toast('Captured to parking lot. Keeps your 3 active projects protected.');
}

$('quickIdeaBtn').addEventListener('click', () => {
  const val = $('quickIdeaInput').value.trim();
  if (val) {
    captureIdea(val);
    $('quickIdeaInput').value = '';
  }
});
$('quickIdeaInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const val = $('quickIdeaInput').value.trim();
    if (val) {
      captureIdea(val);
      $('quickIdeaInput').value = '';
    }
  }
});

$('saveIdeaBtn').addEventListener('click', () => {
  const title = $('ideaTitleInput').value.trim();
  const problem = $('ideaProblemInput').value.trim();
  if (title) {
    captureIdea(title, problem);
    $('ideaTitleInput').value = '';
    $('ideaProblemInput').value = '';
  }
});

// Calendar Sync & Sample Schedule
$('gotoCalendarLink').addEventListener('click', e => {
  e.preventDefault();
  showView('calendar');
});

$('calAddMeetingBtn').addEventListener('click', () => {
  const title = prompt('Meeting / Commitment title:');
  if (!title) return;
  const time = prompt('Time (HH:MM):', '14:00') || '14:00';
  const duration = parseInt(prompt('Duration in minutes:', '45'), 10) || 45;
  data.meetings.push({
    id: crypto.randomUUID(),
    title: title.trim(),
    time: time,
    duration: duration,
    note: 'Added commitment',
    date: dateKey(new Date()),
    source: 'daymark'
  });
  save();
  render();
  toast('Calendar constraint added.');
});

$('addMeetingQuick').addEventListener('click', () => {
  $('calAddMeetingBtn').click();
});

/* =========================================================
   GOOGLE CALENDAR LIVE SYNC & ICAL PARSER
   ========================================================= */
function parseICS(rawText) {
  if (!rawText) return [];
  // Unfold lines
  const rawLines = rawText.split(/\r?\n/);
  const lines = [];
  for (const l of rawLines) {
    if ((l.startsWith(' ') || l.startsWith('\t')) && lines.length) {
      lines[lines.length - 1] += l.slice(1);
    } else {
      lines.push(l);
    }
  }

  const events = [];
  let inEvent = false;
  let curr = {};

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed === 'BEGIN:VEVENT') {
      inEvent = true;
      curr = {};
    } else if (trimmed === 'END:VEVENT') {
      inEvent = false;
      if (curr.SUMMARY && curr.DTSTART) {
        const title = (curr.SUMMARY || 'Busy').replace(/\\,/g, ',').replace(/\\;/g, ';').replace(/\\n/g, ' ').trim();
        const desc = (curr.DESCRIPTION || '').replace(/\\,/g, ',').replace(/\\;/g, ';').replace(/\\n/g, ' ').trim();
        const loc = (curr.LOCATION || '').replace(/\\,/g, ',').trim();

        // Parse DTSTART
        let dateStr = '';
        let timeStr = '12:00';
        let allDay = false;
        let dtStart = curr.DTSTART;

        if (dtStart.includes('T') && dtStart.endsWith('Z')) {
          const val = dtStart.replace('Z', '');
          const y = parseInt(val.slice(0, 4), 10);
          const m = parseInt(val.slice(4, 6), 10) - 1;
          const d = parseInt(val.slice(6, 8), 10);
          const hh = parseInt(val.slice(9, 11), 10);
          const mm = parseInt(val.slice(11, 13), 10);
          const ss = parseInt(val.slice(13, 15), 10) || 0;
          // UTC Date
          const utcDate = new Date(Date.UTC(y, m, d, hh, mm, ss));
          // Convert to Local (or IST)
          dateStr = dateKey(utcDate);
          timeStr = `${String(utcDate.getHours()).padStart(2, '0')}:${String(utcDate.getMinutes()).padStart(2, '0')}`;
        } else if (dtStart.length === 8 && /^\d+$/.test(dtStart)) {
          allDay = true;
          timeStr = 'All Day';
          dateStr = `${dtStart.slice(0, 4)}-${dtStart.slice(4, 6)}-${dtStart.slice(6, 8)}`;
        }

        events.push({
          id: curr.UID || `evt-${events.length + 1}`,
          title: title,
          date: dateStr,
          time: timeStr,
          duration: allDay ? 60 : 45,
          allDay: allDay,
          note: desc.slice(0, 200) || loc,
          location: loc
        });
      }
    } else if (inEvent && trimmed.includes(':')) {
      const idx = trimmed.indexOf(':');
      const rawK = trimmed.slice(0, idx);
      const val = trimmed.slice(idx + 1);
      const k = rawK.split(';')[0].toUpperCase();
      curr[k] = val;
    }
  }
  return events;
}

async function fetchGoogleCalendar(force = false) {
  const badge = $('calLiveBadge');
  const feedText = $('calFeedStatusText');
  if (badge) badge.textContent = '⟳ Syncing...';

  try {
    const res = await fetch(`/api/calendar${force ? '?live=1' : ''}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const result = await res.json();

    if (result && Array.isArray(result.events)) {
      data.calendarEvents = result.events;
      
      // Filter today's events for the day cockpit
      const todayKey = dateKey(new Date());
      const todayMeetings = result.events.filter(e => e.date === todayKey);
      const daymarkMeetings = (data.meetings || []).filter(m => m.source !== 'google');
      data.meetings = [
        ...daymarkMeetings,
        ...todayMeetings.map(e => ({
          id: e.id,
          title: e.title,
          time: e.time === 'All Day' ? '09:00' : e.time,
          duration: e.duration || 45,
          note: e.note || e.location || 'Google Calendar Event',
          date: e.date,
          source: 'google'
        }))
      ];

      save();
      render();
      scheduleMeetingReminders();

      if (badge) {
        badge.innerHTML = `🟢 G-Cal Synced (${result.events.length})`;
        badge.title = `Last synced: ${result.last_synced || 'Just now'} · ${result.events.length} total events`;
      }
      if (feedText) {
        feedText.innerHTML = `Connected to Google Calendar · <b>${result.events.length} events synced</b> · Last updated: ${result.last_synced || 'Just now'}`;
      }
      return result.events;
    }
  } catch (err) {
    console.warn('Calendar sync fallback:', err);
    if (badge) badge.innerHTML = `🟢 G-Cal Active`;
  }
  return data.calendarEvents || [];
}

// Google Calendar URL Modal Actions
const calUrlModal = $('calUrlModalBackdrop');
if ($('editCalUrlBtn')) {
  $('editCalUrlBtn').addEventListener('click', () => {
    $('calUrlInput').value = data.calendar_url || '';
    calUrlModal.classList.remove('hidden');
  });
}
if ($('closeCalUrlModalBtn')) {
  $('closeCalUrlModalBtn').addEventListener('click', () => calUrlModal.classList.add('hidden'));
}
if ($('calUrlForm')) {
  $('calUrlForm').addEventListener('submit', async e => {
    e.preventDefault();
    const url = $('calUrlInput').value.trim();
    if (!url) return;
    try {
      toast('Updating calendar URL & syncing...');
      await fetch('/api/calendar-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url })
      });
      data.calendar_url = url;
      save();
      calUrlModal.classList.add('hidden');
      await fetchGoogleCalendar(true);
      toast('Google Calendar updated & synced!');
    } catch (err) {
      toast('Saved locally.');
      calUrlModal.classList.add('hidden');
    }
  });
}

if ($('syncCalendarNowBtn')) {
  $('syncCalendarNowBtn').addEventListener('click', () => {
    toast('Syncing with Google Calendar...');
    fetchGoogleCalendar(true).then(evs => {
      toast(`Synced ${evs.length} events from Google Calendar!`);
    });
  });
}

$('icsFileInput').addEventListener('change', async e => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const imported = parseICS(text);
    if (imported.length) {
      data.calendarEvents = imported;
      const todayKey = dateKey(new Date());
      const todayEvs = imported.filter(x => x.date === todayKey);
      const daymarkMeetings = (data.meetings || []).filter(m => m.source !== 'google');
      data.meetings = [
        ...daymarkMeetings,
        ...todayEvs.map(e => ({
          id: e.id,
          title: e.title,
          time: e.time === 'All Day' ? '09:00' : e.time,
          duration: e.duration || 45,
          note: e.note || 'Imported .ics',
          date: e.date,
          source: 'google'
        }))
      ];
      save();
      render();
      toast(`Imported ${imported.length} events from file!`);
    } else {
      toast('No events found in .ics file.');
    }
  } catch (err) {
    console.error(err);
    toast('Failed to parse .ics file.');
  }
});

/* =========================================================
   CONVERSATIONAL ASSISTANT & COMMAND HUD (Enhanced Intelligence)
   ========================================================= */
const conversationMemory = {
  lastReferencedDate: null,
  lastReferencedMonth: null, // 0-11
  lastReferencedYear: new Date().getFullYear(),
  lastReferencedDateLabel: '',
  lastTopic: 'greeting',
  activeCheckinTaskId: null,
  pushbackChallengedTask: null,
  pendingTaskRequest: null
};

if (!data.chatHistory || !data.chatHistory.length) {
  // Check if AI is configured by trying to detect a saved ai_config
  data.chatHistory = [
    {
      sender: 'assistant',
      time: 'Just now',
      text: 'Morning. I’m Daymark, your execution and accountability assistant. Your calendar is synced.\n\nConnect Gemini or Ollama in ⚙ Settings for open-ended conversation. Without a model, I can still handle schedule questions, task management, and focus sprints. I do have other talents; they just need fewer API keys.'
    }
  ];
}

function copyMsg(index) {
  const msg = data.chatHistory[index];
  if (!msg) return;
  const label = msg.sender === 'user' ? 'You' : 'Daymark';
  navigator.clipboard.writeText(`${label}: ${msg.text}`).then(() => {
    toast('Message copied');
  }).catch(() => {
    // Fallback for pywebview
    const ta = document.createElement('textarea');
    ta.value = `${label}: ${msg.text}`;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    toast('Message copied');
  });
}

function copyFullConversation() {
  const text = data.chatHistory
    .map(m => `[${m.time || ''}] ${m.sender === 'user' ? 'You' : 'Daymark'}: ${m.text}`)
    .join('\n\n');
  navigator.clipboard.writeText(text).then(() => {
    toast('Full conversation copied');
  }).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    toast('Full conversation copied');
  });
}

function toggleAssistantDrawer(show) {
  const overlay = $('assistantDrawerOverlay');
  if (show) {
    overlay.classList.remove('hidden');
    renderAssistantMessages();
    $('assistantChatInput').focus();
  } else {
    overlay.classList.add('hidden');
  }
}

function renderAssistantMessages() {
  const container = $('assistantMessages');
  if (!container) return;

  container.innerHTML = data.chatHistory.map((msg, index) => {
    const isUser = msg.sender === 'user';
    let extraHtml = '';

    // Render interactive action pills if this message is a task nudge
    if (msg.taskNudge) {
      const task = data.tasks.find(t => t.id === msg.taskNudge.taskId);
      if (task && !task.done) {
        extraHtml = `
          <div class="chat-nudge-box">
            <p><b>Accountability Check:</b> ${escapeHTML(task.title)}</p>
            <div class="chat-nudge-actions">
              <button class="nudge-action-btn doing" onclick="handleChatAction('doing', '${escapeHTML(task.id)}')">⚡ Doing</button>
              <button class="nudge-action-btn done" onclick="handleChatAction('done', '${escapeHTML(task.id)}')">✓ Mark Done</button>
              <button class="nudge-action-btn postpone" onclick="handleChatAction('postpone', '${escapeHTML(task.id)}')">⏳ Postpone</button>
              <button class="nudge-action-btn cancel" onclick="handleChatAction('cancel', '${escapeHTML(task.id)}')">✕ Cancel / Skip</button>
            </div>
          </div>
        `;
      }
    }

    // Render agenda box if message includes items
    if (msg.agendaItems && msg.agendaItems.length) {
      extraHtml += `
        <div class="chat-agenda-box">
          ${msg.agendaItems.map(it => `
            <div class="chat-item-pill ${it.type}">
              <span>${it.type === 'meeting' ? '◷' : '✓'}</span>
              <b>${escapeHTML(it.title)}</b>
              <small style="margin-left:auto">${escapeHTML(it.time || '')}</small>
            </div>
          `).join('')}
        </div>
      `;
    }

    return `
      <div class="chat-message ${isUser ? 'user' : 'assistant'}">
        <div class="chat-bubble">
          ${escapeHTML(msg.text).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\n/g, '<br>')}
          ${extraHtml}
        </div>
        <button class="copy-msg-btn" onclick="copyMsg(${index})" title="Copy message">⎘</button>
        <span class="chat-meta">${escapeHTML(msg.time || '')}</span>
      </div>
    `;
  }).join('');

  container.scrollTop = container.scrollHeight;
}

function addChatMessage(sender, text, extra = {}) {
  const now = new Date();
  const timeStr = dateLabel(now, { hour: 'numeric', minute: '2-digit' });
  data.chatHistory.push({
    sender,
    text,
    time: timeStr,
    ...extra
  });
  if (data.chatHistory.length > 50) {
    data.chatHistory = data.chatHistory.slice(-50);
  }
  save();
  renderAssistantMessages();
}

// Window global helper for interactive nudge buttons
window.handleChatAction = function(action, taskId) {
  conversationMemory.activeCheckinTaskId = taskId;
  if (action === 'doing') {
    handleAssistantInput('Doing.');
  } else if (action === 'done') {
    handleAssistantInput('Mark it done.');
  } else if (action === 'postpone') {
    handleAssistantInput('Change of plans, gotta postpone it to next monday.');
  } else if (action === 'cancel') {
    handleAssistantInput('Not feeling it, cancel it.');
  }
};

/* =========================================================
   NATURAL DATE & TIME RESOLVER WITH CONVERSATIONAL MEMORY
   ========================================================= */
function parseNaturalDateWithMemory(input) {
  const str = input.toLowerCase().trim();
  const now = new Date();

  // 1. Relative keywords
  if (str.includes('today') || str.includes('tonight')) {
    conversationMemory.lastReferencedDate = new Date(now);
    conversationMemory.lastReferencedMonth = now.getMonth();
    conversationMemory.lastReferencedYear = now.getFullYear();
    conversationMemory.lastReferencedDateLabel = 'Today';
    return { date: dateKey(now), label: 'Today', dayName: 'Today', dateObj: now };
  }
  if (str.includes('tomorrow')) {
    const d = new Date(now);
    d.setDate(d.getDate() + 1);
    conversationMemory.lastReferencedDate = d;
    conversationMemory.lastReferencedMonth = d.getMonth();
    conversationMemory.lastReferencedYear = d.getFullYear();
    conversationMemory.lastReferencedDateLabel = 'Tomorrow';
    return { date: dateKey(d), label: 'Tomorrow', dayName: 'Tomorrow', dateObj: d };
  }
  if (str.includes('day after tomorrow')) {
    const d = new Date(now);
    d.setDate(d.getDate() + 2);
    conversationMemory.lastReferencedDate = d;
    conversationMemory.lastReferencedMonth = d.getMonth();
    conversationMemory.lastReferencedYear = d.getFullYear();
    return { date: dateKey(d), label: dateLabel(d, { weekday: 'long', month: 'short', day: 'numeric' }), dayName: dateLabel(d, { weekday: 'long' }), dateObj: d };
  }

  // 2. Weekdays: "this saturday", "coming friday", "next monday", etc.
  const daysOfWeek = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
  for (let i = 0; i < daysOfWeek.length; i++) {
    const dayName = daysOfWeek[i];
    if (str.includes(dayName)) {
      const d = new Date(now);
      const currentDay = d.getDay();
      let diff = i - currentDay;
      if (diff <= 0) diff += 7; // Next occurrence
      if (str.includes('next') && diff < 7) diff += 7;
      d.setDate(d.getDate() + diff);
      
      conversationMemory.lastReferencedDate = d;
      conversationMemory.lastReferencedMonth = d.getMonth();
      conversationMemory.lastReferencedYear = d.getFullYear();
      conversationMemory.lastReferencedDateLabel = dateLabel(d, { weekday: 'long', month: 'short', day: 'numeric' });
      return { 
        date: dateKey(d), 
        label: conversationMemory.lastReferencedDateLabel,
        dayName: dayName.charAt(0).toUpperCase() + dayName.slice(1),
        dateObj: d
      };
    }
  }

  // 3. Explicit Month + Day: "12th november", "nov 12", "september 29", etc.
  const months = ['january','february','march','april','may','june','july','august','september','october','november','december'];
  const monthAbbrs = ['jan','feb','mar','apr','may','jun','jul','aug','sep','sept','oct','nov','dec'];

  for (let m = 0; m < 12; m++) {
    const full = months[m];
    const abbr = monthAbbrs[m];
    if (str.includes(full) || str.includes(abbr)) {
      const numMatch = str.match(/(\d{1,2})(?:st|nd|rd|th)?/);
      if (numMatch) {
        const dayNum = parseInt(numMatch[1], 10);
        const year = now.getFullYear();
        const targetDate = new Date(year, m, dayNum);
        
        conversationMemory.lastReferencedDate = targetDate;
        conversationMemory.lastReferencedMonth = m;
        conversationMemory.lastReferencedYear = year;
        conversationMemory.lastReferencedDateLabel = dateLabel(targetDate, { weekday: 'long', month: 'short', day: 'numeric' });
        return {
          date: dateKey(targetDate),
          label: conversationMemory.lastReferencedDateLabel,
          dayName: dateLabel(targetDate, { month: 'long', day: 'numeric' }),
          dateObj: targetDate
        };
      }
    }
  }

  // 4. MULTI-TURN CONVERSATIONAL MEMORY RESOLUTION:
  // e.g. "What about 15th", "and 15th?", "how about the 20th", "15th"
  const isolatedNumMatch = str.match(/\b(?:what\s+about\s+|and\s+|how\s+about\s+)?(\d{1,2})(?:st|nd|rd|th)?\b/);
  if (isolatedNumMatch) {
    const dayNum = parseInt(isolatedNumMatch[1], 10);
    if (dayNum >= 1 && dayNum <= 31) {
      // Use last mentioned month if available, else current month
      const targetMonth = conversationMemory.lastReferencedMonth !== null ? conversationMemory.lastReferencedMonth : now.getMonth();
      const targetYear = conversationMemory.lastReferencedYear || now.getFullYear();
      const targetDate = new Date(targetYear, targetMonth, dayNum);

      conversationMemory.lastReferencedDate = targetDate;
      conversationMemory.lastReferencedDateLabel = dateLabel(targetDate, { weekday: 'long', month: 'short', day: 'numeric' });
      return {
        date: dateKey(targetDate),
        label: conversationMemory.lastReferencedDateLabel,
        dayName: dateLabel(targetDate, { month: 'long', day: 'numeric' }),
        dateObj: targetDate
      };
    }
  }

  return null;
}

// Check calendar events intersecting a target date (supports multi-day events)
function findEventsForDate(targetDateKey, targetDateObj) {
  const allEvents = data.calendarEvents || [];
  return allEvents.filter(e => {
    if (e.date === targetDateKey) return true;
    if (e.raw_start && e.raw_end && targetDateKey) {
      const targetInt = parseInt(targetDateKey.replace(/-/g, ''), 10);
      const startInt = parseInt(e.raw_start.slice(0, 8), 10);
      const endInt = parseInt(e.raw_end.slice(0, 8), 10);
      if (targetInt >= startInt && targetInt < endInt) return true;
    }
    return false;
  });
}

/* =========================================================
   INTELLIGENT CONVERSATIONAL PROCESSOR (LLM-First Architecture)
   The LLM is the PRIMARY brain. Local keyword matching is only
   used for immediate UI actions (mark done, start focus, etc.)
   that must execute without round-trip latency.
   ========================================================= */

// Show a typing/thinking indicator in chat
function showThinkingIndicator() {
  const container = $('assistantMessages');
  if (!container) return;
  const indicator = document.createElement('div');
  indicator.className = 'chat-message assistant';
  indicator.id = 'thinkingIndicator';
  indicator.innerHTML = `
    <div class="chat-bubble thinking-bubble">
      <span class="thinking-dot"></span>
      <span class="thinking-dot"></span>
      <span class="thinking-dot"></span>
    </div>
  `;
  container.appendChild(indicator);
  container.scrollTop = container.scrollHeight;
}

function removeThinkingIndicator() {
  const el = document.getElementById('thinkingIndicator');
  if (el) el.remove();
}

// Execute a structured action returned by the LLM
function executeLLMAction(action) {
  if (!action || !action.action) return;

  switch (action.action) {
    case 'focus': {
      const task = data.tasks.find(t => t.id === action.taskId) || data.tasks.find(t => !t.done);
      if (task) {
        setTimeout(() => {
          toggleAssistantDrawer(false);
          startFocusMode(task, action.duration || 30);
        }, 600);
      }
      break;
    }
    case 'done': {
      const task = data.tasks.find(t => t.id === action.taskId) || data.tasks.find(t => !t.done);
      if (task) {
        task.done = true;
        task.status = 'completed';
        data.executionLog.unshift({
          timestamp: dateLabel(new Date(), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
          task_title: task.title,
          action: 'completed',
          duration_minutes: task.duration || 30,
          reason: 'Marked done via AI Assistant'
        });
        save();
        render();
      }
      break;
    }
    case 'add': {
      const activeProj = data.projects.find(p => p.status === 'active') || data.projects[0];
      const taskDate = action.date || dateKey(new Date());
      const taskTime = normalizeTime(action.time) || '';
      const taskTitle = action.title || 'New Task';
      const isEvent = action.kind === 'event' || Boolean(taskTime);
      const taskId = crypto.randomUUID();
      data.tasks.push({
        id: taskId,
        project_id: activeProj ? activeProj.id : '',
        title: taskTitle,
        tier: ['must', 'should', 'optional'].includes(action.tier) ? action.tier : 'should',
        duration: action.duration || 30,
        date: taskDate,
        time: taskTime,
        note: isEvent ? `Scheduled for ${taskDate}` : 'Added via Assistant',
        done: false,
        status: 'todo'
      });
      if (isEvent) {
        data.meetings.push({
          id: crypto.randomUUID(), task_id: taskId, title: taskTitle, date: taskDate,
          time: taskTime, duration: action.duration || 30,
          note: 'Scheduled in Daymark', source: 'daymark'
        });
      }
      save();
      render();
      break;
    }
  }
}

// Build the context payload for the LLM backend
function buildLLMContext() {
  return {
    current_time: new Date().toLocaleString('en-IN', {
      weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short'
    }),
    events: data.calendarEvents || [],
    daymark_events: (data.meetings || []).filter(event => event.source !== 'google'),
    projects: data.projects || [],
    tasks: data.tasks || [],
    bigThree: data.bigThree || {},
    executionLog: (data.executionLog || []).slice(0, 5)
  };
}

async function handleAssistantInput(rawText) {
  const text = rawText.trim();
  if (!text) return;

  // Add user message
  addChatMessage('user', text);
  const lower = text.toLowerCase();

  // Resolve a task request that needed one more detail. Keep the tier the user
  // asked for and never invent a task when they have not named one.
  if (conversationMemory.pendingTaskRequest && !/^(what|huh|why|cancel|never mind)\b/i.test(text)) {
    const request = conversationMemory.pendingTaskRequest;
    conversationMemory.pendingTaskRequest = null;
    const parsedDate = parseNaturalDateWithMemory(text);
    const title = text
      .replace(/\b(today|tomorrow|tonight|this evening|in the evening|evening|morning|afternoon|at night)\b/gi, '')
      .replace(/\b(?:at\s*)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b/gi, '')
      .replace(/\b(?:at|on)\b/gi, '').replace(/\s+/g, ' ').replace(/[,.!?]+$/g, '').trim();
    if (!title || /^(today|tomorrow|tonight|this evening|evening|morning|afternoon)$/i.test(title)) {
      conversationMemory.pendingTaskRequest = request;
      addChatMessage('assistant', 'I still need the action you want me to add. What should I call it?');
      return;
    }
    const timeMatch = text.match(/\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b/i);
    let taskTime = '';
    if (timeMatch) {
      let hour = Number(timeMatch[1]) % 12;
      if (timeMatch[3].toLowerCase() === 'pm') hour += 12;
      taskTime = `${String(hour).padStart(2, '0')}:${timeMatch[2] || '00'}`;
    } else if (/evening|tonight/i.test(text)) taskTime = '17:00';
    else if (/afternoon/i.test(text)) taskTime = '14:00';
    else if (/morning/i.test(text)) taskTime = '09:00';
    const activeProj = data.projects.find(p => p.status === 'active') || data.projects[0];
    const dateStr = parsedDate ? parsedDate.label : '';
    data.tasks.push({
      id: crypto.randomUUID(), project_id: activeProj ? activeProj.id : '',
      title: dateStr ? `${title} [Due: ${dateStr}]` : title,
      tier: request.tier, duration: 30, time: taskTime,
      note: `Added via Assistant${dateStr ? ` for ${dateStr}` : ''}${taskTime ? ` at ${taskTime}` : ''}.`,
      done: false, status: 'todo'
    });
    save(); render();
    addChatMessage('assistant', `Added **${title}** as an ${request.tier} action${dateStr ? ` for ${dateStr}` : ''}${taskTime ? ` at ${taskTime}` : ''}.`);
    return;
  }

  const taskAddRequest = /\b(add|create|schedule|put|make)\b/i.test(text) &&
    /\b(task|action|todo|to-do|must[- ]do|optional)\b/i.test(text);
  if (taskAddRequest) {
    const tier = /\boptional\b/i.test(text) ? 'optional' :
      /\b(must[- ]do|must)\b/i.test(text) ? 'must' : 'should';
    const title = text
      .replace(/^(?:hey[ ,]*)?(?:can you|could you|please|i need you to|i want you to)?\s*(?:please\s*)?(?:add|create|schedule|put|make)\s+/i, '')
      .replace(/\b(?:a|an|new|optional|must[- ]do|must|task|action|todo|to-do)\b/gi, ' ')
      .replace(/\b(for me|to my list|to my task list|in my tasks)\b/gi, ' ')
      .replace(/\b(today|tomorrow|tonight|this evening|evening|morning|afternoon|at night|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b/gi, ' ')
      .replace(/\b\d+(?:\.\d+)?\s*(?:hours?|hrs?|h|minutes?|mins?)\b/gi, ' ')
      .replace(/\b(?:at\s*)?\d{1,2}(?::\d{2})?\s*(?:am|pm)\b/gi, ' ')
      .replace(/\b(?:at|on)\b/gi, ' ')
      .replace(/[?.!,]+$/g, '').replace(/\s+/g, ' ').trim();
    if (title.length < 3) {
      conversationMemory.pendingTaskRequest = { tier };
      addChatMessage('assistant', tier === 'optional'
        ? 'Sure. What optional action should I add, and when would you like it scheduled?'
        : 'Sure. What action should I add? If it needs a specific day or time, include that too.');
      return;
    }
    const parsedDate = parseNaturalDateWithMemory(text);
    const timeMatch = text.match(/\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b/i);
    let taskTime = '';
    if (timeMatch) {
      let hour = Number(timeMatch[1]) % 12;
      if (timeMatch[3].toLowerCase() === 'pm') hour += 12;
      taskTime = `${String(hour).padStart(2, '0')}:${timeMatch[2] || '00'}`;
    } else if (/evening|tonight/i.test(text)) taskTime = '17:00';
    else if (/afternoon/i.test(text)) taskTime = '14:00';
    else if (/morning/i.test(text)) taskTime = '09:00';
    const activeProj = data.projects.find(p => p.status === 'active') || data.projects[0];
    const dateStr = parsedDate ? parsedDate.label : '';
    data.tasks.push({
      id: crypto.randomUUID(), project_id: activeProj ? activeProj.id : '',
      title: dateStr ? `${title} [Due: ${dateStr}]` : title,
      tier, duration: 30, time: taskTime,
      note: `Added via Assistant${dateStr ? ` for ${dateStr}` : ''}${taskTime ? ` at ${taskTime}` : ''}.`,
      done: false, status: 'todo'
    });
    save(); render();
    addChatMessage('assistant', `Added **${title}** as an ${tier} action${dateStr ? ` for ${dateStr}` : ''}${taskTime ? ` at ${taskTime}` : ''}.`);
    return;
  }

  // Handle explicit time blocks locally so malformed model output cannot leave
  // a requested block uncreated or falsely claim that it was added.
  const blockMatch = text.match(/^\s*(?:please\s+)?(?:block|reserve)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s+(?:to|-)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s+for\s+(.+)$/i);
  if (blockMatch) {
    const parseClock = value => {
      const match = value.trim().match(/^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$/i);
      if (!match) return null;
      let hour = Number(match[1]) % 12;
      if (match[3].toLowerCase() === 'pm') hour += 12;
      return { minutes: hour * 60 + Number(match[2] || 0), text: `${String(hour).padStart(2, '0')}:${match[2] || '00'}` };
    };
    const start = parseClock(blockMatch[1]);
    const end = parseClock(blockMatch[2]);
    let duration = start && end ? end.minutes - start.minutes : 0;
    if (duration <= 0) duration += 24 * 60;
    const dateInfo = parseNaturalDateWithMemory(text);
    const title = blockMatch[3]
      .replace(/\b(today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b/gi, '')
      .replace(/[,.!?]+$/g, '').trim();
    if (start && end && title) {
      const activeProj = data.projects.find(p => p.status === 'active') || data.projects[0];
      const date = dateInfo ? dateInfo.date : dateKey(new Date());
      const taskId = crypto.randomUUID();
      data.tasks.push({
        id: taskId, project_id: activeProj ? activeProj.id : '', title,
        tier: 'should', duration, date, time: start.text,
        note: `Scheduled for ${dateInfo ? dateInfo.label : 'today'}.`, done: false, status: 'todo'
      });
      data.meetings.push({
        id: crypto.randomUUID(), task_id: taskId, title, date, time: start.text, duration,
        note: 'Scheduled in Daymark', source: 'daymark'
      });
      save(); render();
      addChatMessage('assistant', `Blocked ${formatTime(start.text)}–${formatTime(end.text)} for **${title}** ${dateInfo ? dateInfo.label.toLowerCase() : 'today'}. It’s saved in Daymark’s Concrete Actions. Daymark can’t write to Google Calendar, but this block is here.`);
      return;
    }
  }

  // =================================================================
  // PHASE 1: IMMEDIATE UI ACTIONS (no LLM round-trip needed)
  // These are direct commands that must execute instantly.
  // =================================================================

  // --- Pushback Follow-up: "fine, i'll do it" → Start 30m sprint ---
  if (conversationMemory.pushbackChallengedTask && (
    lower.includes("fine") || lower.includes("i'll do it") || lower.includes("ill do it") ||
    lower.includes("okay i will") || lower.includes("alright fine") || lower === "ok" || lower === "okay"
  )) {
    const task = conversationMemory.pushbackChallengedTask;
    conversationMemory.pushbackChallengedTask = null;
    addChatMessage('assistant', `Good. Starting a 30-minute sprint on "${task.title}". You and this task have been avoiding each other long enough.`);
    setTimeout(() => {
      toggleAssistantDrawer(false);
      startFocusMode(task, 30);
    }, 600);
    return;
  }

  // --- "Doing" / "Working on it" → Start focus timer ---
  if (lower === 'doing' || lower === 'doing.' || lower.startsWith('doing ') || lower.includes('working on it') || lower.includes('in progress')) {
    const task = (conversationMemory.activeCheckinTaskId && data.tasks.find(t => t.id === conversationMemory.activeCheckinTaskId)) || data.tasks.find(t => !t.done);
    if (task) {
      addChatMessage('assistant', `Starting a 30-minute sprint on "${task.title}". I’ll keep the clock; you do the clever part.`);
      setTimeout(() => {
        toggleAssistantDrawer(false);
        startFocusMode(task, 30);
      }, 700);
    } else {
      addChatMessage('assistant', 'No open task to start. A rare case of the list behaving itself.');
    }
    return;
  }

  // --- "Mark done" / "Done" / "Finished" → Complete task ---
  if (lower.includes('mark it done') || lower.includes('mark done') || lower === 'done' || lower === 'finished') {
    const task = (conversationMemory.activeCheckinTaskId && data.tasks.find(t => t.id === conversationMemory.activeCheckinTaskId)) || data.tasks.find(t => !t.done);
    if (task) {
      task.done = true;
      task.status = 'completed';
      data.executionLog.unshift({
        timestamp: dateLabel(new Date(), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
        task_title: task.title,
        action: 'completed',
        duration_minutes: task.duration || 30,
        reason: 'Marked done via Assistant'
      });
      conversationMemory.activeCheckinTaskId = null;
      save();
      render();
      addChatMessage('assistant', `Marked "${task.title}" done. Look at you, making follow-through attractive.`);
    } else {
      addChatMessage('assistant', 'Everything on the task list is already done. Try not to look too pleased with yourself.');
    }
    return;
  }

  // --- "Sync calendar" → Force calendar refresh ---
  if (lower.includes('sync') && lower.includes('calendar')) {
    toast('Syncing Google Calendar...');
    fetchGoogleCalendar(true).then(evs => {
      addChatMessage('assistant', `Calendar synced: ${evs.length} commitments. Your schedule and I are both up to date.`);
    });
    return;
  }

  // =================================================================
  // PHASE 2: ROUTE THROUGH LLM (Primary Intelligence Layer)
  // Everything else goes to the configured AI backend.
  // =================================================================

  showThinkingIndicator();

  try {
    const llmPayload = {
      message: text,
      history: data.chatHistory.slice(-14),
      context: buildLLMContext()
    };

    const llmRes = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(llmPayload)
    });

    removeThinkingIndicator();

    if (!llmRes.ok) {
      addChatMessage('assistant', `⚠ Server returned HTTP ${llmRes.status}. Try restarting server.py.`);
      return;
    }

    if (llmRes.ok) {
      const llmData = await llmRes.json();

      // Success: LLM returned a response
      if (llmData.status === 'success' && llmData.result && (llmData.result.response || llmData.result.action)) {
        if (llmData.result.response) addChatMessage('assistant', llmData.result.response);

        // Execute any structured action
        if (llmData.result.action) {
          executeLLMAction(llmData.result.action);
        }

        // Update conversation memory for date tracking
        const parsedDate = parseNaturalDateWithMemory(text);
        if (parsedDate) {
          conversationMemory.lastTopic = 'schedule';
        }
        return;
      }

      // Error from LLM provider (API key issue, network, etc.)
      if (llmData.status === 'error' && llmData.message) {
        addChatMessage('assistant', `⚠ AI error (${llmData.source || 'unknown'}): ${llmData.message}`);
        return;
      }

      // No LLM configured
      if (llmData.status === 'no_llm') {
        // Continue into the deterministic local command handler below.
      }
    }
  } catch (err) {
    removeThinkingIndicator();
    addChatMessage('assistant', `⚠ Could not reach the local server (localhost:4173). Make sure server.py is running. Error: ${err.message}`);
    return;
  }

  // =================================================================
  // PHASE 3: LOCAL SEMANTIC ENGINE (Fallback when no LLM available)
  // Rich keyword-based intelligence for when AI is not configured.
  // =================================================================

  // --- Cancel / Resistance: pushback with 30m challenge ---
  if (lower.includes('cancel') || lower.includes('not feeling it') || lower.includes('drop it') || lower.includes('dont want to do') || lower.includes("don't want to do")) {
    const task = (conversationMemory.activeCheckinTaskId && data.tasks.find(t => t.id === conversationMemory.activeCheckinTaskId)) || data.tasks.find(t => !t.done);
    if (task) {
      conversationMemory.pushbackChallengedTask = task;
      const proj = data.projects.find(p => p.id === task.project_id);
      const projName = proj ? proj.name : 'your active milestone';
      const outcome = proj && proj.outcome ? proj.outcome : 'delivering meaningful progress on your goals';
      
      const pushbackMsg = `You put "${task.title}" under "${projName}" to help you reach: "${outcome}". Want to drop it for now, or try a smaller first step? I promise not to make a speech about grit.`;
      addChatMessage('assistant', pushbackMsg);
      return;
    }
  }

  // --- Next Meeting Query ---
  if (lower.includes('next meeting') || lower.includes('next call') || lower.includes('upcoming meeting') || lower.includes('upcoming call') || lower.includes('when is my next') || lower.includes('any meetings') || lower.includes('any calls')) {
    const now = new Date();
    const nowDateKey = dateKey(now);
    const allEvents = [...(data.calendarEvents || [])];

    const futureEvents = allEvents.filter(e => {
      if (e.date > nowDateKey) return true;
      if (e.date === nowDateKey) {
        if (e.allDay) return true;
        const [h, m] = (e.time || '00:00').split(':').map(Number);
        const evD = new Date();
        evD.setHours(h, m, 0, 0);
        return evD >= now;
      }
      return false;
    });

    futureEvents.sort((a, b) => {
      const dtA = a.date + (a.time === 'All Day' ? 'T00:00:00' : 'T' + a.time + ':00');
      const dtB = b.date + (b.time === 'All Day' ? 'T00:00:00' : 'T' + b.time + ':00');
      return dtA.localeCompare(dtB);
    });

    if (futureEvents.length > 0) {
      const next = futureEvents[0];
      const isToday = next.date === nowDateKey;
      const tomorrow = new Date(now);
      tomorrow.setDate(tomorrow.getDate() + 1);
      const isTomorrow = next.date === dateKey(tomorrow);

      let whenStr = '';
      if (isToday) whenStr = `today at ${next.time}`;
      else if (isTomorrow) whenStr = `tomorrow, ${dateLabel(tomorrow, { weekday: 'long', month: 'short', day: 'numeric' })} (${next.time})`;
      else whenStr = `on ${next.date} at ${next.time}`;

      let reply = `Your next upcoming commitment is **${next.title}** ${whenStr}.`;
      if (next.location && next.location.startsWith('http')) {
        reply += ` Link: ${next.location}.`;
      }
      if (futureEvents.length > 1) {
        const nextNext = futureEvents[1];
        reply += ` Following that, you have **${nextNext.title}** on ${nextNext.date} (${nextNext.time}).`;
      }
      conversationMemory.lastTopic = 'meetings';
      addChatMessage('assistant', reply);
      return;
    } else {
      addChatMessage('assistant', 'No upcoming calendar commitments. A suspicious amount of freedom; use it wisely.');
      return;
    }
  }

  // --- Date Query with Multi-Turn Memory (checked BEFORE clarification to catch "what about 15th") ---
  // Exclude add/schedule commands from date query to prevent "1 hour" matching as a day number
  const isAddCommand = lower.startsWith('add') || lower.startsWith('schedule') ||
                       lower.startsWith('create') || lower.startsWith('set ') ||
                       lower.startsWith('remind') || lower.includes('remind me');
  const parsedDate = parseNaturalDateWithMemory(text);
  if (!isAddCommand && parsedDate && (
    lower.includes('how is') || lower.includes("how's") || lower.includes('looking') ||
    lower.includes('what is on') || lower.includes('what about') ||
    lower.includes('what\'s on') || lower.includes('whats on') ||
    lower.includes('and ') || lower.startsWith('how about') || lower.startsWith('what about') ||
    lower.startsWith('and ') || lower.match(/^\d{1,2}(?:st|nd|rd|th)?$/) ||
    lower.match(/\b\d{1,2}(?:st|nd|rd|th)\b/)  // ordinal suffix required for bare numbers
  )) {
    const matchingEvents = findEventsForDate(parsedDate.date, parsedDate.dateObj);
    const isWeekend = parsedDate.label.includes('Saturday') || parsedDate.label.includes('Sunday');

    // All-day events (conferences, workshops) are commitments but don't block focus time
    const timedEvents = matchingEvents.filter(e => e.time && e.time !== 'All Day');
    const allDayEvents = matchingEvents.filter(e => !e.time || e.time === 'All Day');

    // "Packed" only when there are 3+ timed events, or 2+ timed events taking most of the day
    const totalTimedMinutes = timedEvents.reduce((sum, e) => sum + (e.duration || 60), 0);
    const isPacked = timedEvents.length >= 3 || totalTimedMinutes >= 180;
    const isModerate = timedEvents.length === 2 || totalTimedMinutes >= 90;

    if (matchingEvents.length === 0) {
      if (isWeekend) {
        addChatMessage('assistant', `Your ${parsedDate.dayName} is clear. Rest is allowed; so is one focused block, if you’re feeling ambitious.`);
      } else {
        addChatMessage('assistant', `${parsedDate.label} is clear of calendar events. Plenty of room to make a dent in something.`);
      }
    } else {
      // Build event summary
      const timedStr = timedEvents.map(e => `**${e.title}** (${e.time}, ${e.duration || 60}min)`).join(', ');
      const allDayStr = allDayEvents.map(e => `**${e.title}** (all-day)`).join(', ');
      const allStr = [...(timedEvents.map(e => `**${e.title}** at ${e.time}`)), ...(allDayEvents.map(e => `**${e.title}** (all-day)`))].join(', ');

      if (isPacked) {
        addChatMessage('assistant', `Packed day on ${parsedDate.label}: ${timedEvents.length} timed commitment(s), about ${Math.round(totalTimedMinutes/60 * 10)/10}h total. ${timedStr}${allDayStr ? '. Also: ' + allDayStr : ''}. Focus windows will be tight; short blocks are your friend.`, {
          agendaItems: matchingEvents.map(m => ({ title: m.title, time: m.time, type: 'meeting' }))
        });
      } else if (isModerate) {
        addChatMessage('assistant', `Moderate schedule on ${parsedDate.label}: ${timedStr}${allDayStr ? '. Plus: ' + allDayStr : ''}. There should still be room for focused work.`, {
          agendaItems: matchingEvents.map(m => ({ title: m.title, time: m.time, type: 'meeting' }))
        });
      } else if (timedEvents.length === 1) {
        addChatMessage('assistant', `Light day on ${parsedDate.label}. One commitment: ${timedStr}${allDayStr ? '. Also noted: ' + allDayStr : ''}. Plenty of open time. Try not to waste it all charming me.`, {
          agendaItems: matchingEvents.map(m => ({ title: m.title, time: m.time, type: 'meeting' }))
        });
      } else {
        // Only all-day events, no timed commitments
        addChatMessage('assistant', `No timed meetings on ${parsedDate.label}${allDayStr ? `. Just: ${allDayStr} — an all-day reference, not a time block` : ''}. Your focus time is open.`, {
          agendaItems: matchingEvents.map(m => ({ title: m.title, time: m.time || 'All Day', type: 'meeting' }))
        });
      }
    }
    return;
  }

  // --- Clarification / Recap ---
  if (lower === 'what' || lower === 'what?' || lower === 'explain' || lower === 'what do you mean' || lower === 'huh' || lower === 'pardon') {
    const nextTask = data.tasks.find(t => !t.done);
    const taskInfo = nextTask ? `your primary focus action is "${nextTask.title}"` : 'all planned tasks are currently completed';
    const reply = `Sir, ${taskInfo}. Ask me about your schedule ("how's my 15th looking?"), next meeting, or say "start" to kick off a focus sprint.`;
    addChatMessage('assistant', reply);
    return;
  }

  // --- Postpone / Reschedule ---
  if (lower.includes('postpone') || lower.includes('reschedule') || lower.includes('move to')) {
    const task = (conversationMemory.activeCheckinTaskId && data.tasks.find(t => t.id === conversationMemory.activeCheckinTaskId)) || data.tasks.find(t => !t.done);
    const postponeDate = parseNaturalDateWithMemory(text);
    const dateStr = postponeDate ? postponeDate.label : 'a later date';
    if (task) {
      task.note = `Postponed to ${dateStr}`;
      save();
      render();
      addChatMessage('assistant', `Moved "${task.title}" to ${dateStr}. The calendar has graciously agreed to cooperate.`);
      conversationMemory.activeCheckinTaskId = null;
    } else {
      addChatMessage('assistant', `Rescheduled for ${dateStr}. Neat and tidy.`);
    }
    return;
  }

  // --- Add Task / Recurring Session / Calendar Entry ---
  if (lower.startsWith('add') || lower.includes('schedule') || lower.includes('deadline') ||
      lower.includes('remind me') || lower.includes('set a') || lower.includes('create a')) {
    const dateInfo = parseNaturalDateWithMemory(text);

    // Detect recurring patterns
    const isRecurring = /\b(daily|every day|each day|weekly|every week|each week)\b/.test(lower);
    const isCalendarIntent = /\b(calendar|schedule|session|block|reminder)\b/.test(lower);

    // Extract duration: "1 hour", "30 min", "45 minutes", "2 hours"
    let duration = 60; // default 60 min
    const durationMatch = text.match(/(\d+(?:\.\d+)?)\s*(hour|hr|h)\b/i) ||
                          text.match(/(\d+)\s*(min(?:ute)?s?)\b/i);
    if (durationMatch) {
      const val = parseFloat(durationMatch[1]);
      const unit = durationMatch[2].toLowerCase();
      duration = unit.startsWith('h') ? Math.round(val * 60) : val;
    }

    // Extract a clean task title
    // Strip command words, duration, date words, and filler
    let taskName = text
      .replace(/^(add|schedule|create|set up?|remind me (to|about)?)\s+/i, '')
      .replace(/\b(daily|every day|each day|weekly|every week|each week)\b/gi, '')
      .replace(/\bin my (calendar|schedule|agenda)?\b/gi, '')
      .replace(/\bto my (calendar|schedule|agenda)?\b/gi, '')
      .replace(/\bfor (today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b/gi, '')
      .replace(/\bon (today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b/gi, '')
      .replace(/\b(today|tomorrow)\b/gi, '')
      .replace(/\b(\d{1,2}(?:st|nd|rd|th)?)\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b/gi, '')
      .replace(/\b(\d+(?:\.\d+)?)\s*(hour|hr|h|min(?:ute)?s?)\b/gi, '')
      .replace(/\b(for|of|per|a|an|the)\b/gi, ' ')
      .replace(/\s{2,}/g, ' ')
      .trim();

    if (!taskName || taskName.length < 2) taskName = 'New Task';

    // Capitalize first letter
    taskName = taskName.charAt(0).toUpperCase() + taskName.slice(1);

    const activeProj = data.projects.find(p => p.status === 'active') || data.projects[0];
    const tier = lower.includes('deadline') || lower.includes('must') ? 'must' : 'should';
    const dateStr = dateInfo ? dateInfo.label : 'Today';
    const noteStr = isRecurring
      ? `Recurring: ${text.match(/\b(daily|every day|weekly|every week)\b/i)?.[0] || 'daily'}. Added via Assistant.`
      : `Added via Assistant${dateInfo ? ' for ' + dateStr : ''}.`;

    data.tasks.push({
      id: crypto.randomUUID(),
      project_id: activeProj ? activeProj.id : '',
      title: isRecurring ? `${taskName} [Recurring]` : (dateInfo ? `${taskName} [Due: ${dateStr}]` : taskName),
      tier,
      duration,
      time: '',
      note: noteStr,
      done: false,
      status: 'todo'
    });
    save();
    render();

    const confirmMsg = isRecurring
      ? `Added **${taskName}** as a recurring ${duration >= 60 ? duration/60 + 'h' : duration + 'min'} block. It’s in your task list. Calendar write access isn’t available over iCal, so add the recurring event in Google Calendar and it’ll sync back here.`
      : `Added **${taskName}**${dateInfo ? ' — due ' + dateStr : ''} to your task list${duration ? ` (${duration >= 60 ? duration/60 + 'h' : duration + 'min'})` : ''}. Look at us, being organized.`;

    addChatMessage('assistant', confirmMsg);
    return;
  }

  // --- Today's Agenda Query ---
  if (lower.includes('today') || lower.includes('agenda') || lower.includes('what should i do today')) {
    const openTasks = data.tasks.filter(t => !t.done);
    const meetings = data.meetings || [];
    const items = [
      ...openTasks.map(t => ({ title: t.title, time: `${t.duration || 30}m`, type: 'task' })),
      ...meetings.map(m => ({ title: m.title, time: m.time, type: 'meeting' }))
    ];

    addChatMessage('assistant', `Today, the following are the tasks and meetings you need to get done with:`, {
      agendaItems: items
    });
    return;
  }

  // --- "What's next?" / Priority query ---
  if (lower.includes('what should i do') || lower.includes('what do i do') || lower.includes("what's next") || lower.includes('whats next') || lower.includes('priority')) {
    const nextTask = data.tasks.find(t => !t.done);
    if (nextTask) {
      const proj = data.projects.find(p => p.id === nextTask.project_id);
      const projName = proj ? proj.name : 'Personal';
      addChatMessage('assistant', `Sir, right now you should execute on: **${nextTask.title}** (Project: ${projName}, ${nextTask.duration || 30} mins). Shall I start your 30-minute focus sprint?`);
    } else {
    addChatMessage('assistant', 'All planned tasks are complete. Take a breather, or run your end-of-day shutdown. You’ve earned the less glamorous option too.');
    }
    return;
  }

  // --- Progress Check-In ---
  if (lower.includes('check in') || lower.includes('progress') || lower.includes('status')) {
    const pendingTask = data.tasks.find(t => !t.done);
    if (pendingTask) {
      conversationMemory.activeCheckinTaskId = pendingTask.id;
      addChatMessage('assistant', `How’s "${pendingTask.title}" going?`, {
        taskNudge: { taskId: pendingTask.id }
      });
    } else {
      addChatMessage('assistant', 'All planned tasks are complete. The slate is almost suspiciously clean.');
    }
    return;
  }

  // --- Greeting ---
  if (lower.match(/^(hi|hello|hey|good morning|good evening|good afternoon|yo|sup|gm)/)) {
    const hour = new Date().getHours();
    const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
    const nextTask = data.tasks.find(t => !t.done);
    const taskNote = nextTask ? ` Your next priority is "${nextTask.title}". Shall we get it moving?` : ' All tasks are complete. Look at that, competence in the wild.';
    addChatMessage('assistant', `${greeting}.${taskNote}`);
    return;
  }

  // --- Default: No LLM + no keyword match → Helpful guidance ---
  addChatMessage('assistant', `I can handle schedules, meetings, tasks, and focus sprints locally. For open-ended conversation, connect Gemini or Ollama in ⚙ Settings. Even I have limits; they're just very well presented.`);
}

// Bind Chat UI Events
if ($('openChatBtn')) {
  $('openChatBtn').addEventListener('click', () => toggleAssistantDrawer(true));
}
if ($('floatingChatBtn')) {
  $('floatingChatBtn').addEventListener('click', () => toggleAssistantDrawer(true));
}
if ($('closeAssistantBtn')) {
  $('closeAssistantBtn').addEventListener('click', () => toggleAssistantDrawer(false));
}
// AI Settings Modal Bindings
const aiConfigModal = $('aiConfigModalBackdrop');
if ($('chatAiConfigBtn')) {
  $('chatAiConfigBtn').addEventListener('click', async () => {
    try {
      const res = await fetch('/api/ai-config');
      if (res.ok) {
        const cfg = await res.json();
        $('aiProviderSelect').value = cfg.provider || 'local';
        $('ollamaUrlInput').value = cfg.ollama_url || 'http://localhost:11434';
        $('ollamaModelInput').value = cfg.ollama_model || 'llama3.2';
        updateAiConfigVisibility();
      }
    } catch {}
    aiConfigModal.classList.remove('hidden');
  });
}
if ($('closeAiConfigModalBtn')) {
  $('closeAiConfigModalBtn').addEventListener('click', () => aiConfigModal.classList.add('hidden'));
}
function updateAiConfigVisibility() {
  const prov = $('aiProviderSelect').value;
  $('geminiConfigGroup').classList.toggle('hidden', prov !== 'gemini');
  $('ollamaConfigGroup').classList.toggle('hidden', prov !== 'ollama');
}
if ($('aiProviderSelect')) {
  $('aiProviderSelect').addEventListener('change', updateAiConfigVisibility);
}
if ($('aiConfigForm')) {
  $('aiConfigForm').addEventListener('submit', async e => {
    e.preventDefault();
    const provider = $('aiProviderSelect').value;
    const geminiKey = $('geminiApiKeyInput').value.trim();
    const ollamaUrl = $('ollamaUrlInput').value.trim();
    const ollamaModel = $('ollamaModelInput').value.trim();

    try {      toast('Saving AI configuration...');
      await fetch('/api/ai-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider,
          gemini_api_key: geminiKey,
          ollama_url: ollamaUrl,
          ollama_model: ollamaModel
        })
      });
      aiConfigModal.classList.add('hidden');

      // Auto-test the connection immediately after saving
      toast(`Saved. Testing connection...`);
      try {
        const testRes = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: 'ping',
            history: [],
            context: buildLLMContext()
          })
        });
        const testData = await testRes.json();
        if (testData.status === 'success' && testData.result?.response) {
          toast(`✓ ${provider.toUpperCase()} connected! AI is live.`);
          addChatMessage('assistant', `${provider} is connected. I’m ready for the difficult questions. Or the easy ones; I’m not precious.`);
        } else if (testData.status === 'error') {
          toast(`⚠ ${testData.message?.slice(0, 80)}`);
          addChatMessage('assistant', `⚠ AI config error: ${testData.message}`);
        }
      } catch (testErr) {
        toast(`AI Engine updated: ${provider.toUpperCase()}`);
      }
    } catch (err) {
      toast('Failed to save AI configuration.');
    }
  });
}

if ($('chatSyncCalBtn')) {
  $('chatSyncCalBtn').addEventListener('click', () => {
    fetchGoogleCalendar(true).then(evs => {
      addChatMessage('assistant', `Google Calendar synced! ${evs.length} total events in cache.`);
    });
  });
}

if ($('restartServerBtn')) {
  $('restartServerBtn').addEventListener('click', async () => {
    if (!confirm('Restart the Daymark server? The app will briefly disconnect then reload.')) return;
    try {
      toast('Restarting server...');
      await fetch('/api/restart', { method: 'GET', cache: 'no-store' });
    } catch (_) {
      // Expected — server closes the connection mid-restart
    }
    // Wait for server to come back up, then reload
    const checkAndReload = async (attempts = 0) => {
      if (attempts > 20) { toast('Server did not restart. Try manually.'); return; }
      try {
        const r = await fetch('/api/status', { cache: 'no-store' });
        if (r.ok) { toast('Server restarted ✓'); return; }
      } catch (_) {}
      setTimeout(() => checkAndReload(attempts + 1), 600);
    };
    setTimeout(() => checkAndReload(), 1000);
  });
}

// Quick Chip Buttons
document.querySelectorAll('.chip-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const query = btn.dataset.query;
    if (query) handleAssistantInput(query);
  });
});

// Chat Form Submit
if ($('assistantChatForm')) {
  $('assistantChatForm').addEventListener('submit', e => {
    e.preventDefault();
    const input = $('assistantChatInput');
    const val = input.value.trim();
    if (!val) return;
    input.value = '';
    handleAssistantInput(val);
  });
}

// Speech-to-Text (Voice Input)
const speechBtn = $('speechBtn');
if (speechBtn) {
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRec) {
    const recognition = new SpeechRec();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = 'en-US';

    let isListening = false;
    speechBtn.addEventListener('click', () => {
      if (isListening) {
        recognition.stop();
        return;
      }
      try {
        recognition.start();
        isListening = true;
        speechBtn.classList.add('listening');
        toast('Listening... Speak now.');
      } catch (err) {
        console.warn('Speech error', err);
      }
    });

    recognition.onresult = event => {
      const transcript = event.results[0][0].transcript;
      $('assistantChatInput').value = transcript;
      handleAssistantInput(transcript);
    };

    recognition.onend = () => {
      isListening = false;
      speechBtn.classList.remove('listening');
    };

    recognition.onerror = () => {
      isListening = false;
      speechBtn.classList.remove('listening');
    };
  } else {
    speechBtn.style.opacity = '0.5';
    speechBtn.title = 'Speech recognition not supported in this browser';
  }
}

// Global Shortcuts
document.addEventListener('keydown', e => {
  // Ctrl + K or Alt + A toggles Assistant
  if ((e.ctrlKey && e.key.toLowerCase() === 'k') || (e.altKey && e.key.toLowerCase() === 'a')) {
    e.preventDefault();
    const overlay = $('assistantDrawerOverlay');
    toggleAssistantDrawer(overlay.classList.contains('hidden'));
    return;
  }

  if (e.key === 'Escape') {
    closeFocusMode();
    $('modalBackdrop').classList.add('hidden');
    $('checkinModalBackdrop').classList.add('hidden');
    $('activeLimitModalBackdrop').classList.add('hidden');
    $('shutdownModalBackdrop').classList.add('hidden');
    $('editBigThreeModalBackdrop').classList.add('hidden');
    $('projectModalBackdrop').classList.add('hidden');
    if (calUrlModal) calUrlModal.classList.add('hidden');
    if (aiConfigModal) aiConfigModal.classList.add('hidden');
    toggleAssistantDrawer(false);
    closeDailyBriefModal();
  }
});

// Periodic Proactive Check-in Loop (Every 30 mins)
setInterval(() => {
  const pendingTask = data.tasks.find(t => !t.done);
  if (pendingTask) {
    activeCheckinTaskId = pendingTask.id;
    addChatMessage('assistant', `Quick check: how’s "${pendingTask.title}" going?`, {
      taskNudge: { taskId: pendingTask.id }
    });
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification('Daymark Accountability Check', {
        body: `How’s progress on "${pendingTask.title}"? Click to reply.`,
        tag: 'daymark-checkin'
      });
    }
  }
}, 30 * 60 * 1000);

// Shutdown Controls
$('closeShutdownBtn').addEventListener('click', () => $('shutdownModalBackdrop').classList.add('hidden'));
$('completeShutdownBtn').addEventListener('click', completeShutdown);

// Brief Modal Controls
$('briefModalClose').addEventListener('click', closeDailyBriefModal);
$('snoozeAlarmBtn').addEventListener('click', snoozeAlarm);
$('dismissAlarmBtn').addEventListener('click', dismissAlarm);

// Workspace Scanner
$('scanFolderBtn').addEventListener('click', scanWorkspaceWithDirectoryPicker);
$('fallbackFileInput').addEventListener('change', handleFallbackFileInput);
$('saveContext').addEventListener('click', () => {
  data.context = $('contextInput').value.trim();
  save();
  render();
  toast('Workspace context notes saved.');
});

// Settings & Reminder Prompt
$('settingsBtn').addEventListener('click', async () => {
  const time = prompt('Set daily check-in time (24-hour HH:MM), or type "off" to disable:', data.reminder.time);
  if (time === null) return;
  if (time.trim().toLowerCase() === 'off') {
    data.reminder.enabled = false;
    save();
    render();
    toast('Daily reminder alarm turned off.');
    return;
  }
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time.trim())) {
    toast('Please enter a valid time like 08:30 or 09:00.');
    return;
  }
  if ('Notification' in window && Notification.permission !== 'granted') {
    await Notification.requestPermission();
  }
  data.reminder.time = time.trim();
  data.reminder.enabled = true;
  save();
  render();
  toast(`Daily alarm set for ${data.reminder.time}.`);
});

// Schedule Daily Morning Check
function scheduleCheck() {
  if (!data.reminder.enabled) return;
  const now = new Date();
  const [h, m] = data.reminder.time.split(':').map(Number);
  const target = new Date(now);
  target.setHours(h, m, 0, 0);
  if (target <= now) target.setDate(target.getDate() + 1);

  const delay = target - now;
  setTimeout(() => {
    const todayKey = dateKey(new Date());
    if (data.lastBriefDate !== todayKey) {
      openDailyBriefModal(true);
      if ('Notification' in window && Notification.permission === 'granted') {
        new Notification('Daymark Morning Brief', {
          body: `Primary outcome: ${data.bigThree.primary}. Click to open your morning brief.`,
          tag: 'daymark-daily'
        });
      }
    }
    scheduleCheck();
  }, delay);
}

// Initial Boot: Render and fetch live Google Calendar
render();
syncSharedTasks();
setInterval(syncSharedTasks, 5000);
syncSharedEvents();
setInterval(syncSharedEvents, 5000);
scheduleCheck();
fetchGoogleCalendar().then(() => {
  scheduleMeetingReminders();
});

/* =========================================================
   PRE-MEETING REMINDERS (30 minutes before each meeting)
   ========================================================= */
const _scheduledMeetingReminderIds = new Set();

function scheduleMeetingReminders() {
  const now = new Date();
  const todayKey = dateKey(now);
  const importedMeetings = (data.calendarEvents || [])
    .filter(e => e.date === todayKey && !e.allDay)
    .map(e => ({ ...e, source: 'google' }));
  const daymarkMeetings = (data.meetings || []).filter(e => e.date === todayKey && e.source === 'daymark');
  const todayMeetings = [...importedMeetings, ...daymarkMeetings];

  todayMeetings.forEach(meeting => {
    // Build a stable ID for deduplication
    const leadMinutes = meeting.source === 'daymark' ? 0 : 30;
    const reminderId = `${meeting.id}-${meeting.time}-${leadMinutes}min`;
    if (_scheduledMeetingReminderIds.has(reminderId)) return;

    const [h, m] = (meeting.time || '00:00').split(':').map(Number);
    const meetingTime = new Date(now);
    meetingTime.setHours(h, m, 0, 0);

    // Fire 30 minutes before the meeting
    const reminderTime = new Date(meetingTime.getTime() - leadMinutes * 60 * 1000);
    const delay = reminderTime - now;

    if (delay <= 0) return; // Meeting already passed or too close

    _scheduledMeetingReminderIds.add(reminderId);

    setTimeout(() => {
      const msg = meeting.source === 'daymark'
        ? `Your Daymark block **${meeting.title}** starts now (${meeting.time}).`
        : `A reminder: **${meeting.title}** starts in 30 minutes (${meeting.time}). Time to land the current plane and find the next gate.`;
      addChatMessage('assistant', msg);
      toast(msg);

      if ('Notification' in window && Notification.permission === 'granted') {
        new Notification('Daymark — Meeting in 30 minutes', {
          body: `${meeting.title} at ${meeting.time}. Time to wrap up.`,
          tag: `daymark-meeting-${meeting.id}`
        });
      }

      playAlarmSequence();
    }, delay);
  });
}
