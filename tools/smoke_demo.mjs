/* Does the browser demo actually run? Headless Pyodide, same code path.
 *
 *     npm install pyodide@0.26.4 && node tools/smoke_demo.mjs
 *
 * Why this exists
 * ---------------
 * The demo shipped broken. tests/test_demo.py checked that every example's
 * caption matched real behaviour and that the bundle was not stale -- the data
 * was right and the integration was never executed. The page loaded, said
 * "loading the linter…", and stopped: emscripten's filesystem would not resolve
 * the relative paths the bundle was written to, so it failed with ErrnoError 44
 * before a line of Python ran.
 *
 * No Python test could have caught that, because the failure was in the glue.
 * This runs the glue.
 *
 * It deliberately mirrors docs/demo.js rather than importing it -- demo.js is a
 * browser script with no module boundary, and a refactor that broke the real
 * page while leaving a shared helper working would be the mistake one layer
 * down. The cost is that the two must be changed together; the Pyodide version
 * is asserted against the one the page pins, so at least that cannot drift
 * silently.
 */

import { loadPyodide, version as pyodideVersion } from 'pyodide';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const fail = (msg) => {
  console.error(`  FAIL  ${msg}`);
  process.exitCode = 1;
};
let checks = 0;
const ok = (msg) => {
  checks += 1;
  console.log(`  ok    ${msg}`);
};

/* The page pins a Pyodide version. If the installed one differs, this smoke
 * test is exercising a runtime the visitor will never load. */
const html = readFileSync(join(ROOT, 'docs/index.html'), 'utf8');
const pinned = html.match(/pyodide\/v([\d.]+)\/full\/pyodide\.js/);
if (!pinned) fail('docs/index.html does not pin a Pyodide version');
else if (pinned[1] !== pyodideVersion)
  fail(`page pins Pyodide ${pinned[1]}, this test installed ${pyodideVersion}`);
else ok(`Pyodide ${pyodideVersion} matches the version the page pins`);

const bundle = JSON.parse(readFileSync(join(ROOT, 'docs/demo-bundle.json'), 'utf8'));
const pyodide = await loadPyodide();

/* Exactly what demo.js does. Absolute paths and an explicit sys.path root --
 * the two things whose absence broke it. */
const PKG = '/pkg';
pyodide.FS.mkdirTree(PKG);
for (const [path, text] of Object.entries(bundle)) {
  const full = `${PKG}/${path}`;
  pyodide.FS.mkdirTree(full.slice(0, full.lastIndexOf('/')));
  pyodide.FS.writeFile(full, text);
}
pyodide.runPython(`import sys; sys.path.insert(0, "${PKG}")`);
pyodide.runPython(`
from pathlib import Path
from finagent_safeguard.cli.linter import (
    ADVISORY_SIGNALS, UnparseableSource, scan_source, _render_annotation,
)
`);
ok('the package imports under Pyodide');

/* Every example from the page, scanned for real. The captions are checked in
 * tests/test_demo.py against CPython; this checks the same inputs survive the
 * browser runtime, which is a different claim. */
const js = readFileSync(join(ROOT, 'docs/demo.js'), 'utf8');
const examples = [...js.matchAll(/name: '([^']+)',\n\s+why: '([^']*)',\n\s+code: `([\s\S]*?)`,/g)];
if (!examples.length) fail('no examples parsed out of docs/demo.js');

for (const [, name, why, code] of examples) {
  pyodide.globals.set('_source', code);
  let payload;
  try {
    payload = JSON.parse(
      pyodide.runPython(`
import json
_found = scan_source(_source, Path("demo.py"))
json.dumps({
    "findings": [
        {"signal": f.signal, "annotation": _render_annotation(f)} for f in _found
    ],
    "advisory": sorted(ADVISORY_SIGNALS),
})
`)
    );
  } catch (err) {
    fail(`"${name}" raised under Pyodide: ${err}`);
    continue;
  }

  const advisory = new Set(payload.advisory);
  const counted = payload.findings.filter((f) => !advisory.has(f.signal)).length;
  const exit = counted ? 1 : 0;

  if (/xit 0/.test(why) && exit !== 0) {
    fail(`"${name}" claims exit 0, got ${exit}`);
  } else if (/Flagged/.test(why) && exit !== 1) {
    fail(`"${name}" claims it is flagged, got exit ${exit}`);
  } else {
    ok(`"${name}" -> exit ${exit}, ${payload.findings.length} finding(s)`);
  }

  /* The annotation is what --fix would write. An empty one would render a
   * blank panel, which reads as "nothing to do" rather than as a bug. */
  for (const f of payload.findings) {
    if (!f.annotation.length) fail(`"${name}" produced an empty annotation`);
    else if (!f.annotation[0].startsWith('@regulated_tool('))
      fail(`"${name}" annotation does not begin with the decorator`);
  }
}

/* Unparseable input must reach the page as a message, not as a thrown error
 * that leaves the panel showing the previous result. */
pyodide.globals.set('_source', 'def broken(:\n');
const broke = pyodide.runPython(`
_verdict = "not raised"
try:
    scan_source(_source, Path("demo.py"))
except (UnparseableSource, SyntaxError):
    _verdict = "handled"
_verdict
`);
if (broke === 'handled') ok('unparseable input raises something the page catches');
else fail(`unparseable input returned ${broke}`);

console.log(
  process.exitCode
    ? `\n  ${checks} checks passed, and at least one failed.`
    : `\n  ${checks} checks passed. The demo runs.`
);
