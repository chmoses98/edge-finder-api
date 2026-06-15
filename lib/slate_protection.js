/**
 * lib/slate_protection.js
 * ========================
 * Node.js wrapper around the Python slate_manager protection logic.
 *
 * Provides:
 *   1. checkSentinels(slateData)  — synchronous sentinel scan, returns { ok, violations }
 *   2. protectSlate(slateData, opts) — calls `python lib/slate_manager.py protect ...`
 *      via child_process.spawnSync, handles run-type routing and file writing.
 *
 * Usage from api/slate.js (called before the response is serialized):
 *
 *   const { checkSentinels } = require('../lib/slate_protection');
 *   const sentinelCheck = checkSentinels(result);
 *   if (!sentinelCheck.ok) {
 *     // Log and mark — the workflow will quarantine when it writes the file
 *     result._sentinelViolations = sentinelCheck.violations;
 *     result._containsSentinels = true;
 *   }
 *
 * File-routing (which path gets written on disk) is handled by the
 * `scripts/protect_slate.py` step in the GitHub Actions workflow, not here.
 * That step calls `python lib/slate_manager.py protect ...` directly.
 *
 * Sentinel values (always rejected — see lib/slate_manager.py):
 *   19900, -19900, 100000, -100000, and abs() >= 19000
 */

'use strict';

const SENTINEL_PRICES = new Set([19900, -19900, 100000, -100000]);
const SENTINEL_ABS_THRESHOLD = 19000;

/**
 * Check whether a numeric value is a sentinel price.
 * @param {*} value
 * @returns {boolean}
 */
function isSentinelPrice(value) {
  if (value === null || value === undefined) return false;
  const v = Number(value);
  if (Number.isNaN(v)) return false;
  if (SENTINEL_PRICES.has(v)) return true;
  if (Math.abs(v) >= SENTINEL_ABS_THRESHOLD) return true;
  return false;
}

/**
 * Recursively scan an object for sentinel prices.
 * @param {*} obj
 * @param {string} path
 * @returns {Array<{path: string, value: number}>}
 */
function findSentinelsInObject(obj, path = '') {
  const found = [];
  if (obj === null || obj === undefined) return found;

  if (Array.isArray(obj)) {
    obj.forEach((item, i) => {
      found.push(...findSentinelsInObject(item, `${path}[${i}]`));
    });
  } else if (typeof obj === 'object') {
    for (const [key, val] of Object.entries(obj)) {
      const subPath = path ? `${path}.${key}` : key;
      found.push(...findSentinelsInObject(val, subPath));
    }
  } else if (typeof obj === 'number') {
    if (isSentinelPrice(obj)) {
      found.push({ path, value: obj });
    }
  }
  return found;
}

/**
 * Check a slate data object for sentinel prices before writing to disk.
 *
 * @param {Object} slateData - the full slate result object
 * @returns {{ ok: boolean, violations: Array<{path: string, value: number}> }}
 */
function checkSentinels(slateData) {
  const violations = findSentinelsInObject(slateData);
  return {
    ok: violations.length === 0,
    violations,
    violationCount: violations.length,
  };
}

/**
 * Annotate a slate result with sentinel metadata.
 * Call this in api/slate.js before returning the JSON response.
 * The workflow's protect_slate.py step will read _containsSentinels and quarantine if set.
 *
 * @param {Object} slateData - the full slate result (mutated in place)
 * @returns {Object} the same slateData with _sentinel* fields added
 */
function annotateSentinels(slateData) {
  const check = checkSentinels(slateData);
  slateData._sentinelCheckRan = true;
  slateData._containsSentinels = !check.ok;
  if (!check.ok) {
    slateData._sentinelViolations = check.violations.slice(0, 20); // cap for response size
    slateData._sentinelViolationCount = check.violationCount;
    console.error(
      `[slate_protection] SENTINEL DETECTED: ${check.violationCount} violations found. ` +
      `Slate will be quarantined by protect_slate.py.`
    );
  }
  return slateData;
}

module.exports = { isSentinelPrice, findSentinelsInObject, checkSentinels, annotateSentinels };
