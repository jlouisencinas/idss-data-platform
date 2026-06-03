// ============================================================
//  LKL DATABASE AUTOMATION — Google Apps Script
//  Updated: added PENDING_PRISM_UPDATE queue for new agents
// ============================================================

function doPost(e) {
  processAndFreezeProductionData();
  return ContentService.createTextOutput("Database updated successfully");
}

function processAndFreezeProductionData() {
  Logger.log("Starting full process...");
  updateUnitColumnFromDatabase();
  deleteDelistedAgents();          // remove agents marked "*" (delisted) from Database
  appendUnknownAgentsToDatabase();
  freezeCurrentMonthValues();
  freezeMonthlyRecCount();
  sendBranchSummaryEmail();
  Logger.log("All steps completed.");
}

/* =====================================================
   STEP 1: Load latest CSV and update UNIT column
===================================================== */
function updateUnitColumnFromDatabase() {
  Logger.log("Starting updateUnitColumnFromDatabase");

  const FOLDER_ID = '1O00CGI9zSzsbGK4S_n3bTAehJ-TUcvrX';
  const RAW_SHEET_NAME = 'CLEANED_RAW';
  const DB_SHEET_NAME = 'Database 2026';

  const ss = SpreadsheetApp.getActiveSpreadsheet();

  const rawSheet =
    ss.getSheetByName(RAW_SHEET_NAME) || ss.insertSheet(RAW_SHEET_NAME);
  rawSheet.clearContents();

  const dbSheet = ss.getSheetByName(DB_SHEET_NAME);
  if (!dbSheet) {
    throw new Error(`Sheet not found: ${DB_SHEET_NAME}`);
  }

  /* ---- Build AGENT CODE → UNIT map ---- */
  const dbData = dbSheet.getDataRange().getValues();
  const headers = dbData[0];

  const codeIdx = headers.indexOf("AGENT CODE");
  const unitIdx = headers.indexOf("UM NAME");

  if (codeIdx === -1 || unitIdx === -1) {
    throw new Error("Required columns missing in Database 2026");
  }

  const unitMap = {};
  for (let i = 1; i < dbData.length; i++) {
    const code = String(dbData[i][codeIdx]).trim().replace(/\s+/g, '');
    const unit = String(dbData[i][unitIdx]).trim();
    if (code) unitMap[code] = unit;
  }

  Logger.log(`Loaded ${Object.keys(unitMap).length} unit mappings`);

  /* ---- Get latest CSV ---- */
  const folder = DriveApp.getFolderById(FOLDER_ID);
  const files = folder.getFilesByType(MimeType.CSV);

  let latestFile = null;
  let latestTime = 0;

  while (files.hasNext()) {
    const file = files.next();
    const created = file.getDateCreated().getTime();
    if (created > latestTime) {
      latestTime = created;
      latestFile = file;
    }
  }

  if (!latestFile) {
    Logger.log("No CSV file found.");
    return;
  }

  Logger.log(`Processing file: ${latestFile.getName()}`);

  /* ---- Parse CSV and update UNIT ---- */
  const csv = Utilities.parseCsv(
    latestFile.getBlob().getDataAsString("utf-8")
  );

  const output = [csv[0]];

  for (let i = 1; i < csv.length; i++) {
    const row = csv[i];
    const agentCode = String(row[1]).trim().replace(/\s+/g, '');

    if (row[0] === "UNKNOWN" && unitMap[agentCode]) {
      row[0] = unitMap[agentCode];
    }
    output.push(row);
  }

  /* ---- Sort by AGENT CODE ---- */
  const header = output.shift();
  output.sort((a, b) => Number(a[1]) - Number(b[1]));
  output.unshift(header);

  rawSheet
    .getRange(1, 1, output.length, output[0].length)
    .setValues(output);

  Logger.log(`CLEANED_RAW updated with ${output.length - 1} records`);
}

/* =====================================================
   STEP 1b: Delete DELISTED agents from Database 2026.
   Agents whose name ends with "*" in the report are
   delisted. We detect them in CLEANED_RAW (col C = AGENT
   NAME, col B = AGENT CODE) and delete their entire row
   from Database 2026, matching by AGENT CODE (falling
   back to AGENT NAME when the code is blank).
   Rows are deleted bottom-to-top so indices don't shift.
===================================================== */
function deleteDelistedAgents() {
  Logger.log("Checking for delisted (*) agents...");

  const ss       = SpreadsheetApp.getActiveSpreadsheet();
  const rawSheet = ss.getSheetByName("CLEANED_RAW");
  const dbSheet  = ss.getSheetByName("Database 2026");

  if (!rawSheet || !dbSheet) {
    Logger.log("deleteDelistedAgents: required sheets missing — skipping.");
    return;
  }

  // ── 1. Collect delisted codes + names from CLEANED_RAW ─────────────────────
  // CLEANED_RAW columns: A=UNIT(0), B=AGENT CODE(1), C=AGENT NAME(2)
  const rawData = rawSheet.getDataRange().getValues();
  const stripStar = function (s) {
    return String(s).trim().replace(/\s*\*+\s*$/, "").trim().toUpperCase();
  };

  const delistedCodes = {};  // normalized code -> true
  const delistedNames = {};  // normalized name (no *) -> true

  for (var i = 1; i < rawData.length; i++) {
    var nameCell = String(rawData[i][2]).trim();
    if (!nameCell || !/\*\s*$/.test(nameCell)) continue;  // only names ending with *
    var code = String(rawData[i][1]).trim();
    if (code) delistedCodes[code] = true;
    delistedNames[stripStar(nameCell)] = true;
  }

  var totalFlagged = Object.keys(delistedCodes).length + Object.keys(delistedNames).length;
  if (Object.keys(delistedNames).length === 0) {
    Logger.log("deleteDelistedAgents: no delisted (*) agents in this report.");
    return;
  }
  Logger.log("deleteDelistedAgents: delisted codes=" +
    JSON.stringify(Object.keys(delistedCodes)) +
    " names=" + JSON.stringify(Object.keys(delistedNames)));

  // ── 2. Find matching rows in Database 2026 ─────────────────────────────────
  const dbData      = dbSheet.getDataRange().getValues();
  const dbHeaders   = dbData[0];
  const agentCodeCol = dbHeaders.indexOf("AGENT CODE");
  const agentNameCol = dbHeaders.indexOf("AGENT NAME");

  if (agentCodeCol === -1 || agentNameCol === -1) {
    Logger.log("deleteDelistedAgents: AGENT CODE/NAME column missing — skipping.");
    return;
  }

  const rowsToDelete = [];  // 1-indexed sheet rows
  for (var r = 1; r < dbData.length; r++) {
    var dbCode = String(dbData[r][agentCodeCol]).trim();
    var dbName = stripStar(dbData[r][agentNameCol]);
    if ((dbCode && delistedCodes[dbCode]) || (dbName && delistedNames[dbName])) {
      rowsToDelete.push(r + 1);  // +1 → 1-indexed sheet row
    }
  }

  if (rowsToDelete.length === 0) {
    Logger.log("deleteDelistedAgents: no matching Database rows (already removed?).");
    return;
  }

  // ── 3. Delete bottom-to-top so row indices don't shift ─────────────────────
  rowsToDelete.sort(function (a, b) { return b - a; });
  rowsToDelete.forEach(function (rowNum) { dbSheet.deleteRow(rowNum); });

  Logger.log("deleteDelistedAgents: deleted " + rowsToDelete.length +
    " row(s) from Database 2026 (flagged " + Object.keys(delistedNames).length + " agent(s)).");
}

/* =====================================================
   STEP 2: Append new (UNKNOWN) agents to Database
   + queue them in PENDING_PRISM_UPDATE
===================================================== */
function appendUnknownAgentsToDatabase() {
  Logger.log("Appending UNKNOWN agents + updating UNIT column as FOR_DB_UPDATE...");

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const rawSheet = ss.getSheetByName("CLEANED_RAW");
  const dbSheet = ss.getSheetByName("Database 2026");

  if (!rawSheet || !dbSheet) throw new Error("Sheets missing");

  const rawData = rawSheet.getDataRange().getValues();
  const dbData = dbSheet.getDataRange().getValues();
  const dbHeaders = dbData[0];

  const agentCodeCol = dbHeaders.indexOf("AGENT CODE");
  const agentNameCol = dbHeaders.indexOf("AGENT NAME");
  const unitColInDB = dbHeaders.indexOf("BRANCH");

  if (agentCodeCol === -1 || agentNameCol === -1 || unitColInDB === -1) {
    throw new Error("Required columns not found in Database sheet");
  }

  const existingCodes = new Set(
    dbData.slice(1).map(r => String(r[agentCodeCol]).trim())
  );

  const rowsToInsert = [];
  const rowsToMark = [];

  // --- STEP 1: Validate bottom row ---
  const lastRowIndex = rawData.length - 1;
  const lastUnit = String(rawData[lastRowIndex][0]).trim();

  if (lastUnit !== "UNKNOWN") {
    Logger.log("Bottom row is not UNKNOWN. Skipping process.");
    return;
  }

  // --- STEP 2: Process contiguous UNKNOWN rows from bottom ---
  for (let i = lastRowIndex; i > 0; i--) {
    const row = rawData[i];
    const unit = String(row[0]).trim();
    const code = String(row[1]).trim();
    const name = String(row[2]).trim();

    if (unit !== "UNKNOWN") break;

    if (code && !existingCodes.has(code)) {
      const newRow = new Array(dbHeaders.length).fill("");
      newRow[agentCodeCol] = code;
      newRow[agentNameCol] = name;
      newRow[unitColInDB] = "FOR_DB_UPDATE";

      rowsToInsert.push(newRow);
      existingCodes.add(code);
      rowsToMark.push(i + 1);
    }
  }

  // --- STEP 3: Insert into Database ---
  if (rowsToInsert.length > 0) {
    function getLastFilledRow(sheet, col) {
      const values = sheet.getRange(2, col + 1, sheet.getMaxRows() - 1).getValues();
      for (let i = values.length - 1; i >= 0; i--) {
        if (String(values[i][0]).trim() !== "") return i + 2;
      }
      return 1;
    }

    const startRow = getLastFilledRow(dbSheet, agentCodeCol) + 1;

    dbSheet
      .getRange(startRow, 1, rowsToInsert.length, rowsToInsert[0].length)
      .setValues(rowsToInsert);

    Logger.log(`Inserted ${rowsToInsert.length} UNKNOWN agents with BRANCH="FOR_DB_UPDATE"`);

    // --- STEP 3b: Queue new agents in PENDING_PRISM_UPDATE ---
    // This sheet is read by the Claude in Chrome workflow to know
    // which agents still need their details fetched from Prism.
    let pendingSheet = ss.getSheetByName("PENDING_PRISM_UPDATE");
    if (!pendingSheet) {
      pendingSheet = ss.insertSheet("PENDING_PRISM_UPDATE");
    }
    pendingSheet.clearContents();

    // Header row
    pendingSheet.getRange(1, 1, 1, 3).setValues([
      ["AGENT CODE", "AGENT NAME", "STATUS"]
    ]);

    // Pending agent rows — STATUS starts as "PENDING"
    const pendingRows = rowsToInsert.map(r => [
      r[agentCodeCol],
      r[agentNameCol],
      "PENDING"
    ]);
    pendingSheet.getRange(2, 1, pendingRows.length, 3).setValues(pendingRows);

    // Style the header for visibility
    pendingSheet.getRange(1, 1, 1, 3)
      .setBackground("#c9000a")
      .setFontColor("#ffffff")
      .setFontWeight("bold");

    Logger.log(`Queued ${pendingRows.length} agents in PENDING_PRISM_UPDATE`);

  } else {
    Logger.log("No new UNKNOWN agents to insert.");

    // Even with no new agents, we may need to REBUILD the pending queue.
    // Scenario: a previous run added agents with BRANCH="FOR_DB_UPDATE" but
    // PENDING_PRISM_UPDATE was cleared before PRISM automation finished.
    const pendingSheet = ss.getSheetByName("PENDING_PRISM_UPDATE");

    // Check whether any PENDING rows already exist in the queue
    let hasPending = false;
    if (pendingSheet) {
      const pendingData = pendingSheet.getDataRange().getValues();
      hasPending = pendingData.slice(1).some(r => String(r[2]).trim() === "PENDING");
    }

    if (hasPending) {
      // PRISM automation is still running — leave the sheet alone
      Logger.log("PENDING_PRISM_UPDATE has unprocessed agents — skipping clear.");
    } else {
      // No active pending rows — check Database for agents still tagged FOR_DB_UPDATE
      const forUpdateAgents = dbData.slice(1).filter(
        r => String(r[unitColInDB]).trim() === "FOR_DB_UPDATE"
      );

      if (forUpdateAgents.length > 0) {
        // Rebuild PENDING_PRISM_UPDATE so PRISM automation can pick them up
        const sheet = pendingSheet || ss.insertSheet("PENDING_PRISM_UPDATE");
        sheet.clearContents();

        sheet.getRange(1, 1, 1, 3).setValues([["AGENT CODE", "AGENT NAME", "STATUS"]]);

        const pendingRows = forUpdateAgents.map(r => [
          r[agentCodeCol],
          r[agentNameCol],
          "PENDING"
        ]);
        sheet.getRange(2, 1, pendingRows.length, 3).setValues(pendingRows);

        sheet.getRange(1, 1, 1, 3)
          .setBackground("#c9000a")
          .setFontColor("#ffffff")
          .setFontWeight("bold");

        Logger.log(`Rebuilt PENDING_PRISM_UPDATE with ${pendingRows.length} FOR_DB_UPDATE agent(s).`);
      } else {
        // No FOR_DB_UPDATE agents and no pending rows — safe to clear any stale sheet
        if (pendingSheet) {
          pendingSheet.clearContents();
          Logger.log("Cleared completed PENDING_PRISM_UPDATE sheet (all done, nothing pending).");
        }
      }
    }
  }

  // --- STEP 4: Batch update CLEANED_RAW ---
  if (rowsToMark.length > 0) {
    const lastRow = rawSheet.getLastRow();
    for (let i = 0; i < rowsToMark.length; i++) {
      const rowNum = rowsToMark[i];
      if (rowNum <= lastRow) {
        rawSheet.getRange(rowNum, 1).setValue("FOR_DB_UPDATE");
      }
    }
    Logger.log(`Updated ${rowsToMark.length} rows to FOR_DB_UPDATE in CLEANED_RAW`);
  } else {
    Logger.log("No rows to mark in CLEANED_RAW.");
  }
}

/* =====================================================
   STEP 3: Freeze current month values into Database
===================================================== */
function freezeCurrentMonthValues() {
  Logger.log("Starting freezeCurrentMonthValues");

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const dbSheet = ss.getSheetByName("Database 2026");
  const rawSheet = ss.getSheetByName("CLEANED_RAW");

  if (!dbSheet || !rawSheet) {
    throw new Error("Required sheets missing.");
  }

  /* ---- Detect month ---- */
  const rawDate = rawSheet.getRange("O1").getValue();
  const month =
    Utilities.formatDate(rawDate, ss.getSpreadsheetTimeZone(), "MMM")
      .toUpperCase();

  Logger.log(`Detected report month: ${month}`);

  /* ---- Column lookup ---- */
  const headers = dbSheet.getRange(1, 1, 1, dbSheet.getLastColumn()).getValues()[0];

  const colIndex = header =>
    headers.indexOf(header) + 1;

  const ccCol  = colIndex(`${month} CC`);
  const apeCol = colIndex(`${month} APE`);
  const ytdCol = colIndex("YTD IDSS");
  const napCol = colIndex("NAP IDSS");

  if ([ccCol, apeCol, ytdCol, napCol].some(c => c === 0)) {
    SpreadsheetApp.getUi().alert("One or more required columns are missing.");
    return;
  }

  /* ---- Build RAW lookup map ---- */
  const rawData = rawSheet.getDataRange().getValues();
  const rawMap = {};

  for (let i = 1; i < rawData.length; i++) {
    rawMap[rawData[i][1]] = rawData[i];
  }

  /* ---- Prepare values ---- */
  const agentCodes = dbSheet
    .getRange("G2:G")
    .getValues()
    .flat()
    .filter(Boolean);

  const cc  = [];
  const ape = [];
  const ytd = [];
  const nap = [];

  agentCodes.forEach(code => {
    const r = rawMap[code];
    cc.push([r  ? r[5] : 0]);
    ape.push([r ? r[6] : 0]);
    ytd.push([r ? r[7] : 0]);
    nap.push([r ? r[9] : 0]);
  });

  const startRow = 2;
  const count = agentCodes.length;

  dbSheet.getRange(startRow, ccCol,  count, 1).setValues(cc);
  dbSheet.getRange(startRow, apeCol, count, 1).setValues(ape);
  dbSheet.getRange(startRow, ytdCol, count, 1).setValues(ytd);
  dbSheet.getRange(startRow, napCol, count, 1).setValues(nap);

  Logger.log(`Frozen values written for ${count} agents`);
}

/* =====================================================
   STEP 3b: Compute and freeze <Month> REC per agent.
   Replicates the sheet formula:
     =IFERROR(COUNTIFS(E:E, G:G, J:J,">="&monthStart, J:J,"<="&monthEnd), 0)
   For each agent (col G = AGENT CODE), counts how many
   other agents list them as recruiter (col E = RECRUITER
   CODE) AND have a DATE APPOINTED (col J) within the
   current report month. Writes results to "<Month> REC".
   No more LOV sheet or hand-maintained formulas needed.
===================================================== */
function freezeMonthlyRecCount() {
  Logger.log("Starting freezeMonthlyRecCount");

  const ss       = SpreadsheetApp.getActiveSpreadsheet();
  const dbSheet  = ss.getSheetByName("Database 2026");
  const rawSheet = ss.getSheetByName("CLEANED_RAW");

  if (!dbSheet || !rawSheet) {
    Logger.log("freezeMonthlyRecCount: required sheets missing — skipping.");
    return;
  }

  // ── Derive month + full month window from the IDSS report date ───────────────
  // CLEANED_RAW!O1 holds the IDSS report date (e.g. May 16, 2026).
  // Window = 1st → last day of that month, regardless of which day it is.
  // No LOV sheet dependency — the window is always the complete calendar month.
  const rawDate = rawSheet.getRange("O1").getValue();
  if (!(rawDate instanceof Date)) {
    Logger.log("freezeMonthlyRecCount: CLEANED_RAW!O1 is not a date — skipping.");
    return;
  }
  const tz         = ss.getSpreadsheetTimeZone();
  const month      = Utilities.formatDate(rawDate, tz, "MMM").toUpperCase();        // "MAY"
  const year       = parseInt(Utilities.formatDate(rawDate, tz, "yyyy"), 10);       // 2026
  const monthNum   = parseInt(Utilities.formatDate(rawDate, tz, "M"),    10);       // 5 (1-based)
  const lastDay    = new Date(year, monthNum, 0).getDate();                         // last day of month
  const monthStartStr = year + String(monthNum).padStart(2, "0") + "01";           // "20260501"
  const monthEndStr   = year + String(monthNum).padStart(2, "0") +
                        String(lastDay).padStart(2, "0");                           // "20260531"
  Logger.log("freezeMonthlyRecCount: IDSS date " +
    Utilities.formatDate(rawDate, tz, "MMM d, yyyy") +
    " → window " + monthStartStr + " – " + monthEndStr);

  // ── Locate required columns by header name (resilient to reordering) ────────
  const data    = dbSheet.getDataRange().getValues();
  const headers = data[0];

  // RECRUITER NAME    — the recruiter's first name stored on each agent's row
  // FIRST NAME BASIS  — this agent's own first name (dedicated column)
  // DATE APPOINTED    — used to filter agents appointed within the month window
  // Matching: RECRUITER NAME value == FIRST NAME BASIS value → same person
  const recruiterNameCol = headers.indexOf("RECRUITER NAME");   // col F
  const firstNameCol     = headers.indexOf("FIRST NAME BASIS"); // dedicated column
  const aptDateCol       = headers.indexOf("DATE APPOINTED");   // col J
  const recCol           = headers.indexOf(month + " REC");     // e.g. "MAY REC"

  if (recruiterNameCol === -1 || firstNameCol === -1 || aptDateCol === -1) {
    Logger.log("freezeMonthlyRecCount: missing column(s) — " +
      "RECRUITER NAME=" + recruiterNameCol +
      ", FIRST NAME BASIS=" + firstNameCol +
      ", DATE APPOINTED=" + aptDateCol + " — skipping.");
    return;
  }
  if (recCol === -1) {
    Logger.log('freezeMonthlyRecCount: "' + month + ' REC" column not found — skipping.');
    return;
  }

  // ── Helper: convert a date value to YYYYMMDD string in spreadsheet timezone ─
  const toDateStr = function(v) {
    var d = (v instanceof Date && !isNaN(v.getTime())) ? v
          : (typeof v === "string" && v.trim() ? new Date(v) : null);
    if (!d || isNaN(d.getTime())) return null;
    return Utilities.formatDate(d, tz, "yyyyMMdd");
  };

  // ── Pass 1: count recruits per recruiter ─────────────────────────────────────
  // For every agent whose DATE APPOINTED falls in the month window,
  // increment the count keyed by their RECRUITER NAME value.
  const recCounts = {}; // recruiterName (uppercased) → recruit count
  for (var i = 1; i < data.length; i++) {
    var recruiterName = String(data[i][recruiterNameCol]).trim().toUpperCase();
    if (!recruiterName) continue;

    var apptStr = toDateStr(data[i][aptDateCol]);
    if (!apptStr) continue;

    if (apptStr >= monthStartStr && apptStr <= monthEndStr) {
      recCounts[recruiterName] = (recCounts[recruiterName] || 0) + 1;
    }
  }

  // ── Pass 2: write count to the matching recruiter row ────────────────────────
  // Each agent has a FIRST NAME BASIS value. If that value matches a key in
  // recCounts (i.e. their first name appears as a RECRUITER NAME for agents
  // appointed this month), write the count to their <Month> REC cell.
  var recValues = [];
  for (var j = 1; j < data.length; j++) {
    var firstName = String(data[j][firstNameCol]).trim().toUpperCase();
    recValues.push([recCounts[firstName] || 0]);
  }

  var count = data.length - 1;
  dbSheet.getRange(2, recCol + 1, count, 1).setValues(recValues);
  SpreadsheetApp.flush(); // commit before sendBranchSummaryEmail reads the sheet

  var sample = Object.keys(recCounts).slice(0, 5).map(function(k) {
    return k + "=" + recCounts[k];
  }).join(", ");
  Logger.log("freezeMonthlyRecCount: wrote " + month + " REC for " + count +
    " agents. " + Object.keys(recCounts).length + " recruiter(s) found." +
    (sample ? " Sample: " + sample : " — check RECRUITER NAME values vs AGENT NAME words."));
}

/* =====================================================
   STEP 4: Email the branch production summary
   Ranked by MTD APE. Computed from Database 2026 using
   the current report month detected from CLEANED_RAW!O1.
   Metrics per branch:
     - MTD APE        = sum of "<MONTH> APE"
     - YTD APE        = sum of "YTD IDSS"
     - Recruits       = sum of "<MONTH> REC"
     - Manpower       = total agents in branch
     - Active         = agents with "<MONTH> APE" > 0
     - Activity Ratio = Active / Manpower (%)
===================================================== */
function sendBranchSummaryEmail() {
  Logger.log("Starting sendBranchSummaryEmail");

  // First recipient is for testing. Add more to the array as needed.
  const RECIPIENTS = [
    "jlouisencinas@gmail.com",
    //  "plukfloroespiritu@gmail.com", "plukruthgutierrez@gmail.com",
  ];

  const NON_BRANCH = new Set(["", "FOR_DB_UPDATE", "UNKNOWN"]);

  const ss       = SpreadsheetApp.getActiveSpreadsheet();
  const dbSheet  = ss.getSheetByName("Database 2026");
  const rawSheet = ss.getSheetByName("CLEANED_RAW");

  if (!dbSheet || !rawSheet) {
    Logger.log("sendBranchSummaryEmail: required sheets missing — skipping.");
    return;
  }

  // ── Detect month + date (same source the freeze step uses) ──────────────────
  const rawDate = rawSheet.getRange("O1").getValue();
  if (!(rawDate instanceof Date)) {
    Logger.log("sendBranchSummaryEmail: CLEANED_RAW!O1 is not a date — skipping.");
    return;
  }
  const tz             = ss.getSpreadsheetTimeZone();
  const month          = Utilities.formatDate(rawDate, tz, "MMM").toUpperCase();
  const reportDateLabel = Utilities.formatDate(rawDate, tz, "MMMM d, yyyy");
  const generatedAt    = Utilities.formatDate(new Date(), tz, "MMM d, yyyy 'at' h:mm a z");

  // ── Locate columns by header (resilient to reordering) ─────────────────────
  const data    = dbSheet.getDataRange().getValues();
  const headers = data[0];
  const idx     = (name) => headers.indexOf(name);

  const branchCol = idx("BRANCH");
  const apeCol    = idx(month + " APE");
  const recCol    = idx(month + " REC");
  const ytdCol    = idx("YTD IDSS");

  if (branchCol === -1 || apeCol === -1 || ytdCol === -1) {
    Logger.log("sendBranchSummaryEmail: missing required column(s) — skipping.");
    return;
  }
  if (recCol === -1) Logger.log('"' + month + ' REC" column not found — recruits will show 0.');

  const num = (v) => { const n = parseFloat(String(v).replace(/,/g, "")); return isNaN(n) ? 0 : n; };

  // ── Aggregate per branch ────────────────────────────────────────────────────
  const stats = {};
  for (let i = 1; i < data.length; i++) {
    const branch = String(data[i][branchCol]).trim();
    if (NON_BRANCH.has(branch)) continue;
    if (!stats[branch]) stats[branch] = { mtdApe: 0, ytdApe: 0, recruits: 0, manpower: 0, active: 0 };
    const s   = stats[branch];
    const ape = num(data[i][apeCol]);
    s.manpower++;
    s.mtdApe   += ape;
    s.ytdApe   += num(data[i][ytdCol]);
    s.recruits += recCol !== -1 ? num(data[i][recCol]) : 0;
    if (ape > 0) s.active++;
  }

  const branches = Object.keys(stats).sort((a, b) => stats[b].mtdApe - stats[a].mtdApe);
  if (branches.length === 0) { Logger.log("No branch rows found — skipping email."); return; }

  // ── Totals ──────────────────────────────────────────────────────────────────
  const totals = { mtdApe: 0, ytdApe: 0, recruits: 0, manpower: 0, active: 0 };
  branches.forEach(function(b) {
    const s = stats[b];
    totals.mtdApe   += s.mtdApe;
    totals.ytdApe   += s.ytdApe;
    totals.recruits += s.recruits;
    totals.manpower += s.manpower;
    totals.active   += s.active;
  });

  // ── Formatters ──────────────────────────────────────────────────────────────
  const reportYear  = Utilities.formatDate(rawDate, tz, "yyyy");

  const peso = function(n) {
    return Math.round(n).toLocaleString("en-PH");
  };
  const pct = function(active, manpower) {
    return manpower > 0 ? ((active / manpower) * 100).toFixed(1) + "%" : "&mdash;";
  };
  const ratioColor = function(r) {
    return r >= 50 ? "#16a34a" : r >= 30 ? "#d97706" : "#dc2626";
  };
  // Activity % shown as a colored dot + number (no fill pill — cleaner/professional)
  const ratioBadge = function(active, manpower) {
    if (manpower === 0) return '<span style="color:#9ca3af;">&mdash;</span>';
    const r   = (active / manpower) * 100;
    const col = ratioColor(r);
    return '<table cellpadding="0" cellspacing="0" style="margin:0 auto;"><tr>' +
      '<td style="padding-right:5px;vertical-align:middle;">' +
        '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' + col + ';"></span>' +
      '</td>' +
      '<td style="vertical-align:middle;">' +
        '<span style="font-size:13px;font-weight:700;color:' + col + ';">' + r.toFixed(1) + '%</span>' +
      '</td>' +
    '</tr></table>';
  };
  // Rank badge: gold / silver / bronze / outlined grey
  const rankBadge = function(i) {
    if (i === 0) return '<span style="display:inline-block;width:28px;height:28px;border-radius:50%;background:#F59E0B;color:#fff;font-size:12px;font-weight:800;text-align:center;line-height:28px;">1</span>';
    if (i === 1) return '<span style="display:inline-block;width:28px;height:28px;border-radius:50%;background:#94A3B8;color:#fff;font-size:12px;font-weight:800;text-align:center;line-height:28px;">2</span>';
    if (i === 2) return '<span style="display:inline-block;width:28px;height:28px;border-radius:50%;background:#B45309;color:#fff;font-size:12px;font-weight:800;text-align:center;line-height:28px;">3</span>';
    return '<span style="display:inline-block;width:28px;height:28px;border-radius:50%;border:2px solid #CBD5E1;color:#64748B;font-size:12px;font-weight:700;text-align:center;line-height:24px;">' + (i + 1) + '</span>';
  };

  // ── KPI Cards ───────────────────────────────────────────────────────────────
  // 2×2 table — no CSS transforms needed, works in all email clients on mobile
  const kpiCard = function(label, valueHtml, accentColor) {
    return '<td width="50%" style="padding:4px;">' +
      '<div style="background:#ffffff;border-top:3px solid ' + accentColor + ';border-radius:6px;' +
      'padding:12px 14px;border:1px solid #e2e8f0;border-top-width:3px;">' +
        '<div style="font-size:9px;font-weight:700;letter-spacing:0.8px;color:#94A3B8;' +
        'text-transform:uppercase;margin-bottom:8px;white-space:nowrap;">' + label + '</div>' +
        '<div style="font-size:18px;font-weight:800;color:#0f172a;line-height:1;">' + valueHtml + '</div>' +
      '</div></td>';
  };

  const overallR     = totals.manpower > 0 ? (totals.active / totals.manpower) * 100 : 0;
  const ratioDisplay = '<span style="color:' + ratioColor(overallR) + ';">' + pct(totals.active, totals.manpower) + '</span>';

  const kpiRow =
    '<table width="100%" cellpadding="0" cellspacing="0">' +
    '<tr>' +
      kpiCard("Total MTD APE",     peso(totals.mtdApe),                 "#1B3A8C") +
      kpiCard("Total YTD APE",     peso(totals.ytdApe),                 "#2563EB") +
    '</tr>' +
    '<tr>' +
      kpiCard(month + " Recruits", String(Math.round(totals.recruits)), "#059669") +
      kpiCard("Activity Ratio",    ratioDisplay,                        "#7C3AED") +
    '</tr>' +
    '</table>';

  // ── Branch rows ─────────────────────────────────────────────────────────────
  const maxMtdApe = stats[branches[0]].mtdApe || 1;
  const BAR_PX    = 130;
  let rows = "";

  branches.forEach(function(b, i) {
    const s     = stats[b];
    const barW  = Math.round((s.mtdApe / maxMtdApe) * BAR_PX);
    const rowBg = i % 2 === 0 ? "#ffffff" : "#f8fafc";

    const barHtml = barW > 0
      ? '<td width="' + barW + '" height="5" bgcolor="#1B3A8C" style="font-size:0;border-radius:3px 0 0 3px;"> </td>' +
        '<td width="' + (BAR_PX - barW) + '" height="5" bgcolor="#e2e8f0" style="font-size:0;border-radius:0 3px 3px 0;"> </td>'
      : '<td width="' + BAR_PX + '" height="5" bgcolor="#e2e8f0" style="font-size:0;border-radius:3px;"> </td>';

    rows +=
      '<tr style="background:' + rowBg + ';">' +
      // Rank
      '<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;text-align:center;white-space:nowrap;">' +
        rankBadge(i) +
      '</td>' +
      // Branch name
      '<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;">' +
        '<span style="font-size:14px;font-weight:700;color:#0f172a;">' + b + '</span>' +
      '</td>' +
      // MTD APE + inline bar
      '<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;width:170px;">' +
        '<div style="font-size:14px;font-weight:700;color:#0f172a;margin-bottom:7px;">' + peso(s.mtdApe) + '</div>' +
        '<table cellpadding="0" cellspacing="0"><tr>' + barHtml + '</tr></table>' +
      '</td>' +
      // YTD APE
      '<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;text-align:right;font-size:13px;font-weight:500;color:#475569;white-space:nowrap;">' +
        peso(s.ytdApe) +
      '</td>' +
      // Recruits — clean bold number, no pill
      '<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;text-align:center;font-size:14px;font-weight:800;color:#1d4ed8;">' +
        Math.round(s.recruits) +
      '</td>' +
      // Manpower
      '<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;text-align:center;">' +
        '<span style="font-size:14px;font-weight:600;color:#0f172a;">' + s.manpower + '</span>' +
      '</td>' +
      // Active
      '<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;text-align:center;">' +
        '<span style="font-size:14px;font-weight:700;color:#0f172a;">' + s.active + '</span>' +
      '</td>' +
      // Activity %
      '<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;text-align:center;">' +
        ratioBadge(s.active, s.manpower) +
      '</td>' +
      '</tr>';
  });

  // Totals row
  const totalRow =
    '<tr style="background:#fafbfc;">' +
    '<td style="padding:12px 10px;border-top:2px solid #1B3A8C;"></td>' +
    '<td style="padding:12px 10px;border-top:2px solid #1B3A8C;">' +
      '<span style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:0.8px;">All Branches</span>' +
    '</td>' +
    '<td style="padding:12px 10px;border-top:2px solid #1B3A8C;">' +
      '<div style="font-size:14px;font-weight:800;color:#1B3A8C;">' + peso(totals.mtdApe) + '</div>' +
    '</td>' +
    '<td style="padding:12px 10px;border-top:2px solid #1B3A8C;text-align:right;font-size:13px;font-weight:800;color:#475569;white-space:nowrap;">' +
      peso(totals.ytdApe) +
    '</td>' +
    '<td style="padding:12px 10px;border-top:2px solid #1B3A8C;text-align:center;font-size:14px;font-weight:800;color:#1d4ed8;">' +
      Math.round(totals.recruits) +
    '</td>' +
    '<td style="padding:12px 10px;border-top:2px solid #1B3A8C;text-align:center;">' +
      '<span style="font-size:14px;font-weight:800;color:#0f172a;">' + totals.manpower + '</span>' +
    '</td>' +
    '<td style="padding:12px 10px;border-top:2px solid #1B3A8C;text-align:center;">' +
      '<span style="font-size:14px;font-weight:800;color:#0f172a;">' + totals.active + '</span>' +
    '</td>' +
    '<td style="padding:12px 10px;border-top:2px solid #1B3A8C;text-align:center;">' +
      ratioBadge(totals.active, totals.manpower) +
    '</td>' +
    '</tr>';

  // ── Full HTML ────────────────────────────────────────────────────────────────
  const html =
    // Media queries: supported by Gmail iOS/Android app, Apple Mail, Samsung Mail.
    // .kc  = KPI card cells → 2×2 grid on mobile
    // .ew  = outer wrapper  → tighter padding on mobile
    // .hd  = header inner   → less padding on mobile
    // .rdp = report-date pill column → hidden on mobile (saves header space)
    // .kw  = KPI band       → tighter padding on mobile
    // .rp  = rankings panel → tighter padding on mobile
    // .bt  = branch table   → smaller font/padding on mobile
    // .ft  = footer         → tighter padding on mobile
    '<style>' +
    '@media only screen and (max-width:600px){' +
    '.ew{padding:14px 6px!important}' +
    '.hd{padding:18px 14px 14px!important}' +
    '.rdp{display:none!important}' +
    '.kw{padding:14px 10px!important}' +
    '.rp{padding:16px 10px 18px!important}' +
    '.bt th{padding:8px 4px!important;font-size:9px!important}' +
    '.bt td{padding:8px 4px!important;font-size:11px!important}' +
    '.ft{padding:12px 14px!important}' +
    '.ttl{font-size:19px!important}' +
    '}' +
    '</style>' +

    '<div class="ew" style="background:#E9EEF4;padding:28px 16px;font-family:Arial,Helvetica,sans-serif;">' +
    '<div style="max-width:680px;margin:0 auto;">' +

    // ── Header ──
    '<div style="background:#1B3A8C;border-radius:10px 10px 0 0;padding:0;">' +
      '<div style="background:rgba(255,255,255,0.12);height:4px;border-radius:10px 10px 0 0;"></div>' +
      '<div class="hd" style="padding:26px 32px 24px;">' +
        '<table width="100%" cellpadding="0" cellspacing="0"><tr>' +
          '<td style="vertical-align:top;">' +
            '<div style="font-size:10px;font-weight:700;letter-spacing:2.5px;' +
            'color:rgba(255,255,255,0.45);text-transform:uppercase;margin-bottom:6px;">Pru Life UK</div>' +
            '<div class="ttl" style="font-size:24px;font-weight:800;color:#ffffff;line-height:1.15;margin-bottom:6px;">' +
              'Branch Production Summary' +
            '</div>' +
            '<div style="font-size:12px;font-weight:600;color:rgba(255,255,255,0.7);margin-bottom:2px;">' +
              'Lazurite Keystone Life Area' +
            '</div>' +
            // Report date shown inline on mobile (pill is hidden)
            '<div style="font-size:11px;font-weight:600;color:rgba(255,255,255,0.6);margin-top:8px;">' +
              reportDateLabel +
            '</div>' +
          '</td>' +
          '<td class="rdp" style="vertical-align:middle;text-align:right;padding-left:20px;">' +
            '<table cellpadding="0" cellspacing="0" style="margin-left:auto;">' +
              '<tr><td style="background:rgba(255,255,255,0.12);border:1px solid rgba(255,255,255,0.2);' +
              'border-radius:8px;padding:10px 16px;text-align:center;white-space:nowrap;">' +
                '<div style="font-size:9px;font-weight:700;letter-spacing:1.5px;' +
                'color:rgba(255,255,255,0.55);text-transform:uppercase;margin-bottom:4px;">Report Date</div>' +
                '<div style="font-size:13px;font-weight:700;color:#ffffff;">' + reportDateLabel + '</div>' +
              '</td></tr>' +
            '</table>' +
          '</td>' +
        '</tr></table>' +
      '</div>' +
    '</div>' +

    // ── KPI Cards ──
    '<div class="kw" style="background:#EEF2F7;padding:20px 24px;border-left:1px solid #d1d9e0;border-right:1px solid #d1d9e0;">' +
      kpiRow +
    '</div>' +

    // ── Rankings Table ──
    '<div class="rp" style="background:#ffffff;padding:24px 28px 28px;' +
    'border-left:1px solid #d1d9e0;border-right:1px solid #d1d9e0;">' +

      '<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px;"><tr>' +
        '<td style="vertical-align:middle;">' +
          '<span style="font-size:11px;font-weight:800;letter-spacing:1.5px;' +
          'color:#64748b;text-transform:uppercase;">Branch Performance Rankings</span>' +
        '</td>' +
        '<td style="text-align:right;vertical-align:middle;">' +
          '<span style="font-size:11px;color:#94a3b8;font-style:italic;">Ranked by MTD APE &nbsp;&mdash;&nbsp; ' + month + ' ' + reportYear + '</span>' +
        '</td>' +
      '</tr></table>' +

      // overflow-x lets the table scroll horizontally on narrow screens
      '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;">' +
      '<table class="bt" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;min-width:540px;">' +
        '<thead>' +
          '<tr style="border-bottom:2px solid #e2e8f0;">' +
            '<th width="44"  style="padding:10px 10px;text-align:center;font-size:10px;font-weight:700;letter-spacing:0.5px;color:#64748b;text-transform:uppercase;white-space:nowrap;">#</th>' +
            '<th width="110" style="padding:10px 10px;text-align:left;font-size:10px;font-weight:700;letter-spacing:0.5px;color:#64748b;text-transform:uppercase;">Branch</th>' +
            '<th width="158" style="padding:10px 10px;text-align:left;font-size:10px;font-weight:700;letter-spacing:0.5px;color:#64748b;text-transform:uppercase;">MTD APE</th>' +
            '<th width="108" style="padding:10px 10px;text-align:right;font-size:10px;font-weight:700;letter-spacing:0.5px;color:#64748b;text-transform:uppercase;white-space:nowrap;">YTD APE</th>' +
            '<th width="54"  style="padding:10px 10px;text-align:center;font-size:10px;font-weight:700;letter-spacing:0.5px;color:#64748b;text-transform:uppercase;white-space:nowrap;">REC</th>' +
            '<th width="76"  style="padding:10px 10px;text-align:center;font-size:10px;font-weight:700;letter-spacing:0.5px;color:#64748b;text-transform:uppercase;white-space:nowrap;">Manpower</th>' +
            '<th width="62"  style="padding:10px 10px;text-align:center;font-size:10px;font-weight:700;letter-spacing:0.5px;color:#64748b;text-transform:uppercase;white-space:nowrap;">Active</th>' +
            '<th width="68"  style="padding:10px 10px;text-align:center;font-size:10px;font-weight:700;letter-spacing:0.5px;color:#64748b;text-transform:uppercase;white-space:nowrap;">Activity</th>' +
          '</tr>' +
        '</thead>' +
        '<tbody>' + rows + totalRow + '</tbody>' +
      '</table>' +
      '</div>' +
    '</div>' +

    // ── Footer ──
    '<div class="ft" style="background:#F8FAFC;border:1px solid #d1d9e0;border-top:none;' +
    'border-radius:0 0 10px 10px;padding:14px 28px;">' +
      '<table width="100%" cellpadding="0" cellspacing="0"><tr>' +
        '<td style="font-size:11px;color:#94a3b8;line-height:2;">' +
          '<strong style="color:#64748b;">Activity Ratio</strong>' +
          ' = active agents (MTD APE &gt; 0) &divide; total manpower' +
          ' &nbsp;&nbsp;' +
          '<span style="color:#16a34a;font-size:9px;">&#9679;</span> &ge;50%&nbsp;&nbsp;' +
          '<span style="color:#d97706;font-size:9px;">&#9679;</span> 30&ndash;49%&nbsp;&nbsp;' +
          '<span style="color:#dc2626;font-size:9px;">&#9679;</span> &lt;30%' +
          '<br>' +
          'Generated ' + generatedAt + ' &nbsp;&middot;&nbsp; IDSS Data Platform' +
        '</td>' +
      '</tr></table>' +
    '</div>' +

    '</div></div>';

  const subject = "[LKL] Branch Production Summary — " + reportDateLabel;

  MailApp.sendEmail({ to: RECIPIENTS.join(","), subject: subject, htmlBody: html });
  Logger.log("sendBranchSummaryEmail: sent to " + RECIPIENTS.join(", ") + " (" + branches.length + " branches).");
}

/* =====================================================
   TEST: Run this function directly from the Apps Script
   editor to send a test email without triggering the
   full pipeline. Requires CLEANED_RAW to have data
   from the last run (so the date cell O1 is populated).
===================================================== */
function testBranchSummaryEmail() {
  sendBranchSummaryEmail();
}

/* =====================================================
   HELPER: Mark a single agent as done in the queue
   Called automatically by the Claude workflow after
   each successful Prism fetch.
   Usage: pass ?agentCode=XXXXXXXX to the web app URL
===================================================== */
function markAgentDone(agentCode) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const pendingSheet = ss.getSheetByName("PENDING_PRISM_UPDATE");
  if (!pendingSheet) return;

  const data = pendingSheet.getDataRange().getValues();
  for (let i = 1; i < data.length; i++) {
    if (String(data[i][0]).trim() === String(agentCode).trim()) {
      pendingSheet.getRange(i + 1, 3).setValue("DONE");
      Logger.log(`Marked agent ${agentCode} as DONE`);
      return;
    }
  }
}

/* =====================================================
   HELPER: Get all PENDING agents (for debugging /
   manual checks in the script editor)
===================================================== */
function getPendingAgents() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const pendingSheet = ss.getSheetByName("PENDING_PRISM_UPDATE");
  if (!pendingSheet) {
    Logger.log("PENDING_PRISM_UPDATE sheet does not exist.");
    return [];
  }

  const data = pendingSheet.getDataRange().getValues();
  const pending = data.slice(1).filter(r => r[2] === "PENDING");
  Logger.log(`Pending agents: ${JSON.stringify(pending)}`);
  return pending;
}
