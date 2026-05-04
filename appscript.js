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
  appendUnknownAgentsToDatabase();
  freezeCurrentMonthValues();
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

    // Clear any stale PENDING_PRISM_UPDATE from a previous run
    const pendingSheet = ss.getSheetByName("PENDING_PRISM_UPDATE");
    if (pendingSheet) {
      pendingSheet.clearContents();
      Logger.log("Cleared stale PENDING_PRISM_UPDATE sheet.");
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
