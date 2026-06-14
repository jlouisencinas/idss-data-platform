// ─────────────────────────────────────────────────────────────────────────────
// apps-script/idss_listener_realtime.gs
//
// IDSS Branch Report Listener — Real-time (Gmail Push Notifications)
//
// Gmail pushes an instant notification to Google Cloud Pub/Sub the moment a
// new email arrives. Pub/Sub forwards it to this Apps Script Web App, which
// checks if all 4 branches are received for the same date and fires GitHub.
//
// Typical latency: 2-5 seconds after the email lands in Gmail.
//
// ── ONE-TIME SETUP ────────────────────────────────────────────────────────────
//
// STEP 1 — Google Cloud Pub/Sub (do this once in console.cloud.google.com)
//   a. Open your Google Cloud project (or create a free one)
//   b. APIs & Services → Enable "Cloud Pub/Sub API"
//   c. Pub/Sub → Topics → Create topic:  idss-gmail-push
//   d. On that topic → Subscriptions → Create subscription:
//        • Name:              idss-gmail-sub
//        • Delivery type:     Push
//        • Endpoint URL:      ← leave blank for now (fill after Step 3)
//   e. IAM → Grant role "Pub/Sub Publisher" to:
//        gmail-api-push@system.gserviceaccount.com
//        (this lets Gmail publish to your topic)
//
// STEP 2 — Enable Gmail Advanced Service in this script
//   Editor → Extensions → Advanced Google Services → Gmail API → Enable
//
// STEP 3 — Deploy as Web App
//   Editor → Deploy → New Deployment
//        • Type:        Web App
//        • Execute as:  Me (jlouisencinas@gmail.com)
//        • Who can access: Anyone
//   Copy the Web App URL (looks like https://script.google.com/macros/s/AKfycb.../exec)
//
// STEP 4 — Finish Pub/Sub subscription
//   Go back to the Pub/Sub subscription you created in Step 1d
//   Edit → Endpoint URL → paste the Web App URL from Step 3 → Save
//
// STEP 5 — Add Script Properties
//   Editor → Project Settings → Script Properties → Add:
//        GITHUB_TOKEN   →  ghp_xxxx  (fine-grained PAT, Actions: read+write)
//        PUBSUB_TOPIC   →  projects/YOUR_GCP_PROJECT_ID/topics/idss-gmail-push
//
// STEP 6 — Activate Gmail watch
//   Run  setupGmailWatch()  once from the editor. Approve auth prompts.
//   (A 6-day renewal trigger is created automatically.)
//
// ─────────────────────────────────────────────────────────────────────────────

const CONFIG = {
  expectedBranches: 4,
  githubRepo:       'jlouisencinas/idss-data-platform',
  workflowFile:     'idss-pipeline.yml',
  githubBranch:     'main',
  alertEmail:       'jlouisencinas@gmail.com',
  stateTtlDays:     3
};


// ─── Push entry point ─────────────────────────────────────────────────────────

/**
 * Pub/Sub calls this the moment a new email arrives in Gmail.
 * Must return HTTP 200 quickly — heavy work happens synchronously but fast.
 */
function doPost(e) {
  try {
    // Pub/Sub wraps payload as base64 inside message.data
    const envelope  = JSON.parse(e.postData.contents);
    const gmailData = JSON.parse(
      Utilities.newBlob(Utilities.base64Decode(envelope.message.data))
        .getDataAsString()
    );
    // gmailData = { emailAddress: "jlouisencinas@gmail.com", historyId: "12345" }
    _onGmailHistoryUpdate(String(gmailData.historyId));

  } catch (err) {
    Logger.log('doPost error: ' + err);
  }

  // Always acknowledge — Pub/Sub retries on non-200
  return ContentService.createTextOutput('OK').setMimeType(ContentService.MimeType.TEXT);
}


// ─── Core logic ───────────────────────────────────────────────────────────────

function _onGmailHistoryUpdate(newHistoryId) {
  // Prevent concurrent executions from racing on state + GitHub trigger
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(8000)) {
    Logger.log('Could not acquire lock — skipping duplicate execution.');
    return;
  }

  try {
    const props  = PropertiesService.getScriptProperties();
    const token  = props.getProperty('GITHUB_TOKEN');
    const lastId = props.getProperty('last_history_id');

    if (!lastId) {
      props.setProperty('last_history_id', newHistoryId);
      return;
    }

    // Fetch only messages ADDED to INBOX since our last known history point
    let historyPage;
    try {
      historyPage = Gmail.Users.History.list('me', {
        startHistoryId: lastId,
        historyTypes:   ['messageAdded'],
        labelId:        'INBOX'
      });
    } catch (err) {
      Logger.log('History.list error: ' + err);
      props.setProperty('last_history_id', newHistoryId);
      return;
    }

    // Advance cursor regardless of whether there were matching messages
    props.setProperty('last_history_id', newHistoryId);

    const records = (historyPage && historyPage.history) ? historyPage.history : [];
    if (!records.length) return;

    // Load state: { "20260614": ["ESPERA BRANCH", "POLARIS LIFE INS. AGENCY INC.", ...] }
    const state = JSON.parse(props.getProperty('branch_state') || '{}');
    _pruneStaleState(state);

    const datesToTrigger = new Set();

    for (const record of records) {
      for (const added of (record.messagesAdded || [])) {
        const msg = GmailApp.getMessageById(added.message.id);
        if (msg) _processMessage(msg, state, datesToTrigger);
      }
    }

    props.setProperty('branch_state', JSON.stringify(state));

    // Trigger GitHub for each completed date
    for (const date of datesToTrigger) {
      const branches = state[date];
      const ok = _triggerGitHub(token, date, branches);
      if (ok) delete state[date];
    }

    // Final save after successful triggers removed their dates
    props.setProperty('branch_state', JSON.stringify(state));

  } finally {
    lock.releaseLock();
  }
}


function _processMessage(message, state, datesToTrigger) {
  const subject = message.getSubject();

  // Match: "Branch Production Reports as of 20260614 (ESPERA BRANCH)"
  const match = subject.match(
    /Branch Production Reports as of (\d{8})(?:\s+\((.+?)\))?/i
  );
  if (!match) return;

  const date   = match[1];                       // "20260614"
  const branch = (match[2] || 'unknown').trim(); // "ESPERA BRANCH"

  if (!state[date]) state[date] = [];

  if (!state[date].includes(branch)) {
    state[date].push(branch);
    Logger.log(`[${date}] +${branch}  →  ${state[date].length}/${CONFIG.expectedBranches}`);
  }

  if (state[date].length >= CONFIG.expectedBranches) {
    datesToTrigger.add(date);
  }
}


function _triggerGitHub(token, date, branches) {
  const url =
    `https://api.github.com/repos/${CONFIG.githubRepo}` +
    `/actions/workflows/${CONFIG.workflowFile}/dispatches`;

  try {
    const res = UrlFetchApp.fetch(url, {
      method:             'post',
      contentType:        'application/json',
      headers: {
        'Authorization':        `Bearer ${token}`,
        'Accept':               'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'
      },
      payload:            JSON.stringify({ ref: CONFIG.githubBranch }),
      muteHttpExceptions: true
    });

    const code = res.getResponseCode();

    if (code === 204) {
      Logger.log(`✅ GitHub triggered for ${date}  [${branches.join(', ')}]`);
      return true;
    }

    const body = `HTTP ${code}: ${res.getContentText()}`;
    Logger.log('❌ ' + body);
    GmailApp.sendEmail(
      CONFIG.alertEmail,
      `[IDSS] ⚠️ GitHub trigger failed — ${date}`,
      body
    );
    return false;

  } catch (err) {
    Logger.log('❌ Exception: ' + err);
    GmailApp.sendEmail(
      CONFIG.alertEmail,
      `[IDSS] ⚠️ GitHub trigger exception — ${date}`,
      String(err)
    );
    return false;
  }
}


function _pruneStaleState(state) {
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - CONFIG.stateTtlDays);
  const cutoffStr = Utilities.formatDate(cutoff, 'Asia/Manila', 'yyyyMMdd');
  for (const date of Object.keys(state)) {
    if (date < cutoffStr) {
      Logger.log('Pruning stale date: ' + date);
      delete state[date];
    }
  }
}


// ─── Setup & maintenance ──────────────────────────────────────────────────────

/**
 * Run ONCE (after deploying as Web App) to tell Gmail to push notifications
 * to your Pub/Sub topic whenever a new email arrives.
 * Creates a 6-day renewal trigger automatically (Gmail watch expires after 7 days).
 */
function setupGmailWatch() {
  const props      = PropertiesService.getScriptProperties();
  const pubsubTopic = props.getProperty('PUBSUB_TOPIC');

  if (!pubsubTopic) {
    Logger.log('❌ Set PUBSUB_TOPIC in Script Properties first.');
    Logger.log('   Format: projects/YOUR_PROJECT_ID/topics/idss-gmail-push');
    return;
  }

  const response = Gmail.Users.watch({ labelIds: ['INBOX'], topicName: pubsubTopic }, 'me');

  props.setProperty('last_history_id', String(response.historyId));
  Logger.log('Gmail watch active.');
  Logger.log('  historyId : ' + response.historyId);
  Logger.log('  expires   : ' + new Date(Number(response.expiration)));

  // Remove any existing renewal trigger to avoid duplicates
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'renewGmailWatch')
    .forEach(t => ScriptApp.deleteTrigger(t));

  // Renew every 6 days (Gmail watch expires after 7)
  ScriptApp.newTrigger('renewGmailWatch')
    .timeBased()
    .everyDays(6)
    .create();

  Logger.log('✅ Setup complete. Renewal trigger created (every 6 days).');
}

/** Called automatically every 6 days to keep the Gmail watch alive. */
function renewGmailWatch() {
  const props      = PropertiesService.getScriptProperties();
  const pubsubTopic = props.getProperty('PUBSUB_TOPIC');
  const response   = Gmail.Users.watch({ labelIds: ['INBOX'], topicName: pubsubTopic }, 'me');
  Logger.log('Gmail watch renewed. Expires: ' + new Date(Number(response.expiration)));
}

/** Stop receiving push notifications (disables the listener). */
function stopGmailWatch() {
  Gmail.Users.stop('me');
  Logger.log('Gmail watch stopped.');
}


// ─── Debug utilities ──────────────────────────────────────────────────────────

/** Show which branches have arrived for each pending date. */
function viewState() {
  const state = JSON.parse(
    PropertiesService.getScriptProperties().getProperty('branch_state') || '{}'
  );
  const dates = Object.keys(state);
  if (!dates.length) { Logger.log('State is empty — no pending dates.'); return; }
  for (const date of dates) {
    Logger.log(`${date}: [${state[date].join(', ')}]  (${state[date].length}/${CONFIG.expectedBranches})`);
  }
}

/** Wipe all tracked state (use if something gets stuck). */
function resetState() {
  PropertiesService.getScriptProperties().deleteProperty('branch_state');
  Logger.log('State cleared.');
}

/** Fire the GitHub workflow manually to verify your token works. */
function testGitHubTrigger() {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) { Logger.log('Set GITHUB_TOKEN in Script Properties first.'); return; }
  _triggerGitHub(token, 'MANUAL_TEST', ['test branch']);
}

/**
 * Simulate a push notification using recent unread emails.
 * Useful for testing doPost() logic without waiting for a real email.
 */
function testWithRecentEmails() {
  const threads = GmailApp.search(
    'subject:"Branch Production Reports as of" newer_than:7d'
  );
  const state = {};
  const toTrigger = new Set();

  for (const thread of threads) {
    for (const message of thread.getMessages()) {
      _processMessage(message, state, toTrigger);
    }
  }

  Logger.log('Simulated state: ' + JSON.stringify(state));
  Logger.log('Dates ready to trigger: ' + JSON.stringify([...toTrigger]));
}
