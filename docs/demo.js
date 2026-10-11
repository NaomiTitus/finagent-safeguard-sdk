/* Run the real linter in the browser.
 *
 * The package is written into Pyodide's virtual filesystem from
 * demo-bundle.json, which tools/build_demo.py generates and gates. Nothing is
 * reimplemented here: this file collects input, calls scan_source and
 * _render_annotation, and renders what comes back.
 *
 * The one piece of logic that is not the package's is the exit status, which
 * lives in the linter's main() and is tied to argv. Reproduced here from its
 * one rule -- advisory signals do not fail a build unless --strict -- and
 * ADVISORY_SIGNALS is read from the package rather than hardcoded, so the
 * copy cannot disagree about which signals those are.
 */

const EXAMPLES = [
  {
    name: 'money + PII',
    why: 'The common case: a function whose identifiers name both an amount and a person.',
    code: `import bank_client


def transfer_funds(iban, amount, customer_id):
    bank_client.execute_transfer(iban, amount)
`,
  },
  {
    // Relabelled after checking: the linter finds NOTHING here. Both methods
    // take only `self`, so no identifier matches and no signal fires. Showing
    // it as a linter success would have been a lie; showing it as the AST
    // rule's blind spot is the honest version and explains why layer 4 runs
    // in parallel rather than downstream.
    name: 'what the linter misses',
    why: 'Exit 0 — the AST rule is silent. Both methods take only self, so no identifier matches. The difference between them lives in the body, which is what the classifier reads.',
    code: `import xml.etree.ElementTree as ET


class SepaTransfer:
    def _create_header(self):
        """Create the GrpHdr node with the initiating party."""
        Nm_node = ET.Element('Nm')
        Nm_node.text = self._config['name']
        return Nm_node

    def _create_PmtInf_node(self):
        """Create the blank payment information nodes."""
        ED = dict()
        ED['Nm_Dbtr_Node'] = ET.Element('Nm')
        ED['IBAN_DbtrAcct_Node'] = ET.Element('IBAN')
        return ED
`,
  },
  {
    // Also relabelled. This DOES flag -- "charge" is in the money vocabulary,
    // so the function's own name matches -- while a human would label it no.
    // That is the tool over-flagging, which is the behaviour a reviewer should
    // see rather than one hidden behind a tooltip claiming otherwise.
    name: 'a false positive',
    why: 'Flagged, and a human would say no: the name contains "charge", but it asserts against a mock. Over-flagging is the cost of a rule that never guesses — which is why a human confirms.',
    code: `def test_charge_is_rejected(self, http_client_mock):
    http_client_mock.stub_request('post', '/v1/charges')
    assert charge(amount=4200, currency='eur').status == 'failed'
`,
  },
  {
    name: 'a Nordic identifier',
    why: 'No money anywhere — a national identifier is enough on its own.',
    code: `def lookup_customer(personnummer: str) -> dict:
    return registry.fetch(personnummer)
`,
  },
  {
    name: 'nothing to flag',
    why: 'Exit 0. A linter that finds something in every file gets switched off.',
    code: `def greet(greeting: str) -> str:
    return greeting.strip().capitalize()
`,
  },
];

const $ = (id) => document.getElementById(id);
const src = $('src');
const out = $('out');
const boot = $('boot');
let pyodide = null;

const esc = (s) =>
  String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

function setStatus(text, state) {
  boot.textContent = text;
  boot.dataset.state = state;
}

/* ---------------------------------------------------------------- examples */
EXAMPLES.forEach((ex, i) => {
  const b = document.createElement('button');
  b.textContent = ex.name;
  b.title = ex.why;
  b.setAttribute('aria-pressed', String(i === 0));
  b.addEventListener('click', () => {
    src.value = ex.code;
    document
      .querySelectorAll('#examples button')
      .forEach((o) => o.setAttribute('aria-pressed', String(o === b)));
    if (pyodide) scan(false);
  });
  $('examples').appendChild(b);
});
src.value = EXAMPLES[0].code;

/* ------------------------------------------------------------------- boot */
async function init() {
  try {
    setStatus('loading python…', 'loading');
    pyodide = await loadPyodide({
      indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/',
    });

    setStatus('loading the linter…', 'loading');
    const bundle = await (await fetch('demo-bundle.json')).json();
    // Written into the virtual filesystem under the package's real layout, so
    // the imports in the source resolve exactly as they do on disk.
    for (const [path, text] of Object.entries(bundle)) {
      const dir = path.slice(0, path.lastIndexOf('/'));
      pyodide.FS.mkdirTree(dir);
      pyodide.FS.writeFile(path, text);
    }
    pyodide.runPython(`import sys; sys.path.insert(0, "")`);
    pyodide.runPython(`
from pathlib import Path
from finagent_safeguard.cli.linter import (
    ADVISORY_SIGNALS, UnparseableSource, scan_source, _render_annotation,
)
`);

    setStatus('ready — runs locally', 'ready');
    $('scan').disabled = false;
    $('fix').disabled = false;
    scan(false);
  } catch (err) {
    setStatus('failed to load', 'error');
    out.innerHTML =
      `<pre class="ctx">  The linter could not load in this browser.\n\n  ${esc(err)}\n\n` +
      `  Everything it does is reproducible offline:\n` +
      `    pip install -e . &amp;&amp; finagent-lint src/</pre>`;
  }
}

/* ------------------------------------------------------------------- scan */
function scan(withEdit) {
  if (!pyodide) return;
  pyodide.globals.set('_source', src.value);
  let result;
  try {
    result = pyodide.runPython(`
import json
try:
    _found = scan_source(_source, Path("demo.py"))
    _payload = {
        "ok": True,
        "advisory": sorted(ADVISORY_SIGNALS),
        "findings": [
            {
                "function": f.function,
                "line": f.insert_line,
                "signal": f.signal,
                "vocabularies": list(f.vocabularies),
                "indent": f.indent,
                "annotation": _render_annotation(f),
            }
            for f in _found
        ],
    }
except UnparseableSource as exc:
    _payload = {"ok": False, "error": str(exc)}
except SyntaxError as exc:
    _payload = {"ok": False, "error": f"that is not valid Python: {exc}"}
json.dumps(_payload)
`);
  } catch (err) {
    out.innerHTML = `<pre class="ctx">  ${esc(err)}</pre>`;
    return;
  }
  render(JSON.parse(result), withEdit);
}

function render(payload, withEdit) {
  if (!payload.ok) {
    out.innerHTML =
      `<pre class="ctx">  ${esc(payload.error)}</pre>` +
      `<div class="exit"><b>exit 2</b> — the tool did not work, which is a ` +
      `different thing from finding nothing.</div>`;
    return;
  }

  const advisory = new Set(payload.advisory);
  const findings = payload.findings;
  const counted = findings.filter((f) => !advisory.has(f.signal)).length;
  const advisoryCount = findings.length - counted;

  if (!findings.length) {
    out.innerHTML =
      `<pre class="ctx">  Nothing to flag.\n\n  No identifier named money or a person, no risky\n` +
      `  annotation, and nothing reaching a bank client.</pre>` +
      `<div class="exit clean"><b>exit 0</b> — clean.</div>`;
    return;
  }

  let html = '';
  for (const f of findings) {
    const isAdvisory = advisory.has(f.signal);
    html +=
      `<div class="finding">` +
      `<b>demo.py:${f.line}: ${esc(f.function)}</b>` +
      `<span class="tag${isAdvisory ? ' advisory' : ''}">${esc(f.signal)}</span>` +
      (f.vocabularies.length
        ? `<span class="tag">${esc(f.vocabularies.join('+'))}</span>`
        : '') +
      (isAdvisory
        ? `<div class="ctx" style="font-size:13px;margin-top:6px">advisory — ` +
          `reported, but does not fail a build without <code>--strict</code></div>`
        : '') +
      `</div>`;
  }

  if (withEdit) {
    // The annotation, shown with the indentation --fix would apply, so what is
    // on screen is what would be written rather than a tidied version of it.
    const blocks = findings
      .map((f) =>
        f.annotation
          .map((l) => `<span class="ins">+${esc(f.indent + l)}</span>`)
          .join('\n')
      )
      .join('\n<span class="ctx"> </span>\n');
    html +=
      `<pre>${blocks}\n<span class="ctx"> def …  (the function, unchanged)</span></pre>`;
  }

  const verb = counted ? 'exit 1' : 'exit 0';
  html +=
    `<div class="exit${counted ? '' : ' clean'}"><b>${verb}</b> — ` +
    `${counted} actionable, ${advisoryCount} advisory. ` +
    (counted
      ? `A build fails here until a developer has decided.`
      : `Advisory only; use <code>--strict</code> to fail on these too.`) +
    `</div>`;

  out.innerHTML = html;
}

$('scan').addEventListener('click', () => scan(false));
$('fix').addEventListener('click', () => scan(true));
src.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    e.preventDefault();
    scan(false);
  }
});

init();
