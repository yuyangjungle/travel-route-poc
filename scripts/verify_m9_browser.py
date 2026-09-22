"""Browser regressions for M9 consent and startup races; no model calls.

Run the local server, then:
  py -3.12 scripts/verify_m9_browser.py --url http://127.0.0.1:8765

Requires the separately installed agent-browser CLI + browser, not a new Python
dependency. The intent endpoint is blocked at the network layer and replaced by
a synthetic browser fixture. Optimizer calls exercise the real local service.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse
from urllib.request import urlopen
import uuid


def browser_executable() -> str:
    found = shutil.which("agent-browser")
    if not found:
        raise RuntimeError("Install agent-browser and its browser before running this check.")
    if os.name == "nt" and Path(found).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        # Invoke the actual executable directly, avoiding cmd.exe interpretation
        # of selector/JSON arguments in npm's Windows shim.
        candidates = list((Path(found).parent / "node_modules/agent-browser/bin").glob("agent-browser-win32-*.exe"))
        if len(candidates) != 1:
            raise RuntimeError("Could not locate the agent-browser native Windows executable.")
        return str(candidates[0])
    return found


class Browser:
    def __init__(self, executable: str, init_script: Path):
        self.command = [executable, "--session", "m9-regression-" + uuid.uuid4().hex[:10],
                        "--json"]
        self.init_script = init_script
        self.started = False

    def run(self, *args: str, script: str | None = None) -> dict:
        launch = [] if self.started else ["--init-script", str(self.init_script)]
        self.started = True
        # Windows browser daemons can inherit pipe handles from their launcher.
        # Regular temporary files avoid waiting for a long-lived daemon to close
        # stdout after the short CLI process has already exited.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            result = subprocess.run([*self.command, *launch, *args], input=script, stdout=stdout, stderr=stderr,
                                    text=True, encoding="utf-8", errors="replace", timeout=40)
            stdout.seek(0)
            output = stdout.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(output)
        except ValueError:
            raise RuntimeError(f"agent-browser {args[0]} did not return JSON (exit {result.returncode}).") from None
        if result.returncode or not payload.get("success"):
            raise RuntimeError(f"agent-browser {args[0]} failed: {payload.get('error', 'unknown error')}")
        return payload.get("data", {})

    def evaluate(self, script: str):
        return self.run("eval", "--stdin", script=script).get("result")

    def wait_for(self, expression: str) -> None:
        self.evaluate("""(async () => {
          const deadline = Date.now() + 15000;
          while (!(EXPRESSION)) {
            if (Date.now() > deadline) throw new Error('Timed out waiting for regression condition');
            await new Promise(resolve => setTimeout(resolve, 30));
          }
          return true;
        })()""".replace("EXPRESSION", expression))

    def assert_js(self, expression: str, message: str) -> None:
        if self.evaluate(f"Boolean({expression})") is not True:
            raise AssertionError(message)

    def activate(self, selector: str, *, checked: bool | None = None) -> None:
        # This regression concerns browser event propagation and response races,
        # not pointer placement during the page's smooth scrolling animation.
        # DOM activation fires checkbox input/change events in the real browser.
        self.evaluate("""(() => {
          const element = document.querySelector(SELECTOR);
          if (!element || element.disabled || !element.getClientRects().length)
            throw new Error('Regression control is absent, disabled or hidden');
          const checked = CHECKED;
          if (checked === null || element.checked !== checked) element.click();
          return true;
        })()""".replace("SELECTOR", json.dumps(selector)).replace("CHECKED", json.dumps(checked)))


def fixture_script(config: dict) -> str:
    config = dict(config)
    config["ai_available"] = True
    fields = dict(config["scenarios"][0]["fields"])
    fields["allow_self_transfer"] = True
    draft = {
        "contract_version": "m9-intent-v2", "fields": fields,
        "summary": "浏览器回归用合成旅行意图，允许自行转机。",
        "assumptions": ["这是离线意图夹具，不是模型评估结果。"],
        "unresolved_mentions": ["当前不支持自行转机，需要明确同意改为受保护联程。"],
        "missing_fields": [], "model": "synthetic-browser-fixture",
    }
    return """(() => {
      const config = CONFIG;
      const draft = DRAFT;
      const originalFetch = window.fetch.bind(window);
      const jsonResponse = value => new Response(JSON.stringify(value), {
        status: 200, headers: {'Content-Type': 'application/json'}
      });
      const test = window.__m9Regression = {
        bootstrapWaiting: false, releaseBootstrap: null,
        optimizeBodies: [], releaseOptimize: [], intentCalls: 0, downloads: 0, errors: []
      };
      window.addEventListener('error', event => test.errors.push(event.message));
      window.addEventListener('unhandledrejection', event => test.errors.push(String(event.reason)));
      const createObjectURL = URL.createObjectURL.bind(URL);
      URL.createObjectURL = blob => {test.downloads++; return createObjectURL(blob);};
      window.fetch = async (input, options) => {
        const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
        if (path === '/api/bootstrap') {
          test.bootstrapWaiting = true;
          await new Promise(resolve => {test.releaseBootstrap = resolve;});
          return jsonResponse(config);
        }
        if (path === '/api/intent') {
          test.intentCalls++;
          return jsonResponse(draft);
        }
        if (path === '/api/optimize') {
          test.optimizeBodies.push(JSON.parse(options.body));
          const gate = new Promise(resolve => {test.releaseOptimize.push(resolve);});
          const response = await originalFetch(input, options);
          // Buffer the actual local optimizer response so delayed display is
          // deterministic even when the optimizer itself completes immediately.
          const content = await response.text();
          await gate;
          return new Response(content, {status: response.status, headers: {'Content-Type': 'application/json'}});
        }
        return originalFetch(input, options);
      };
    })();""".replace("CONFIG", json.dumps(config, ensure_ascii=False)).replace("DRAFT", json.dumps(draft, ensure_ascii=False))


def verify(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use a local HTTP development server; remote sites are not supported.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Use the local server origin without credentials, paths or query parameters.")
    url = url.rstrip("/")
    with urlopen(url + "/api/bootstrap", timeout=15) as response:
        config = json.load(response)
    checks: list[str] = []
    with tempfile.TemporaryDirectory(prefix="travel-m9-browser-") as temp:
        init_script = Path(temp) / "fixture.js"
        init_script.write_text(fixture_script(config), encoding="utf-8")
        browser = Browser(browser_executable(), init_script)
        try:
            browser.run("open", "about:blank")
            # Even if the fetch fixture stops working, there must be no paid call.
            browser.run("network", "route", "**/api/intent", "--abort")
            browser.run("open", url)
            browser.wait_for("window.__m9Regression?.bootstrapWaiting")
            browser.activate("#mode-manual")
            # An initially disabled button or a safe loading guard are both valid.
            if browser.evaluate("document.querySelector('#start-manual').disabled") is False:
                browser.activate("#start-manual")
            browser.assert_js("window.__m9Regression.errors.length === 0", "Manual action during loading threw an exception.")
            browser.assert_js("document.querySelector('#confirmation').hidden", "Loading unexpectedly created a draft.")
            checks.append("manual_action_before_bootstrap_is_safe")

            browser.evaluate("window.__m9Regression.releaseBootstrap(); true")
            browser.wait_for("document.querySelectorAll('.scenario-button').length === 4")
            browser.run("snapshot", "-i")
            browser.activate("#mode-ai")
            browser.run("fill", "#travel-text", "浏览器测试：2027年10月从浦东出发，海岛旅行，也允许自行转机。")
            browser.activate("#extract")
            browser.wait_for("document.querySelector('#accept-protected-only') !== null")
            browser.assert_js("window.__m9Regression.intentCalls === 1", "Synthetic intent fixture did not handle extraction.")

            browser.activate("#accept-protected-only", checked=True)
            browser.activate("#confirmed", checked=True)
            browser.activate("#optimize")
            browser.wait_for("window.__m9Regression.releaseOptimize.length === 1")
            browser.activate("#accept-protected-only", checked=False)
            browser.assert_js("!document.querySelector('#confirmed').checked", "Withdrawing protected-only consent did not clear confirmation.")
            browser.evaluate("window.__m9Regression.releaseOptimize[0](); true")
            browser.wait_for("!document.querySelector('#optimize').disabled")
            browser.assert_js("document.querySelector('#results').hidden", "A stale in-flight result rendered after consent withdrawal.")
            checks.append("consent_withdrawal_invalidates_in_flight_result")

            browser.activate("#accept-protected-only", checked=True)
            browser.activate("#confirmed", checked=True)
            browser.activate("#optimize")
            browser.wait_for("window.__m9Regression.releaseOptimize.length === 2")
            browser.evaluate("window.__m9Regression.releaseOptimize[1](); true")
            browser.wait_for("!document.querySelector('#optimize').disabled")
            browser.assert_js("!document.querySelector('#results').hidden && document.querySelectorAll('.route-card').length > 0", "Reconfirmed conditions did not display real optimizer results.")
            browser.activate("#accept-protected-only", checked=False)
            browser.assert_js("document.querySelector('#results').hidden && !document.querySelector('#confirmed').checked", "Withdrawing consent did not clear completed results and confirmation.")
            # The button is hidden from users; invoking its handler proves the
            # cached export was also cleared, beyond simply hiding the results.
            browser.evaluate("document.querySelector('#download-results').click(); true")
            browser.assert_js("window.__m9Regression.downloads === 0", "A stale result remains downloadable after withdrawal.")
            browser.assert_js("window.__m9Regression.optimizeBodies.every(body => body.confirmed === true && body.fields.allow_self_transfer === false)", "Confirmed protected-only choice was not sent exactly.")
            browser.assert_js("window.__m9Regression.errors.length === 0", "Browser reported an uncaught exception.")
            checks.append("consent_withdrawal_clears_completed_result_and_export")
            return {"status": "passed", "checks": checks, "model_calls": 0,
                    "optimizer_calls": 2, "intent_source": "synthetic intercepted fixture"}
        except Exception:
            diagnostic = browser.evaluate("""({
              intent_fixture_calls: window.__m9Regression?.intentCalls,
              optimizer_calls: window.__m9Regression?.optimizeBodies?.length,
              browser_errors: window.__m9Regression?.errors,
              extract_disabled: document.querySelector('#extract')?.disabled,
              text_length: document.querySelector('#travel-text')?.value.length,
              fixture_present: window.fetch.toString().includes('intentCalls'),
              app_busy: typeof state === 'undefined' ? null : state.busy,
              app_ai_available: typeof state === 'undefined' ? null : state.config?.ai_available,
              input_error: document.querySelector('#input-error')?.textContent,
              confirmation_error: document.querySelector('#confirm-error')?.textContent
            })""")
            print(json.dumps({"status": "failed", "diagnostic": diagnostic}, ensure_ascii=False))
            raise
        finally:
            browser.run("close")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    print(json.dumps(verify(args.url), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
