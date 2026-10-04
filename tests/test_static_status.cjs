const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.join(__dirname, "..");

function extractScript(html) {
  const m = html.match(/<script>\s*([\s\S]*?)<\/script>/);
  if (!m) throw new Error("no inline <script> block found");
  return m[1];
}

function makeHarness(source) {
  const alerts = [];
  const fetches = [];
  const sandbox = {
    alert: (msg) => alerts.push(String(msg)),
    confirm: () => true,
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      const key = opts.headers && opts.headers.apikey;
      if (!key) {
        const body = JSON.stringify({
          message: "No API key found in request",
          hint: "No `apikey` request header or url param was found.",
        });
        return { ok: false, status: 401, text: async () => body };
      }
      return { ok: true, status: 204, text: async () => "" };
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: "inline-script.js" });
  if (typeof sandbox.cmd !== "function") throw new Error("cmd() not defined by inline script");
  return { dash: sandbox.cmd(), alerts, fetches };
}

function makeJob() {
  return {
    id: "test-id",
    status: "New",
    job_title: "Test Engineer",
    company: "Acme",
    location: "Remote",
    track: "eng",
    match_score: "",
    date: "",
    url: "https://example.com",
    _dup_flag: false,
  };
}

test("markApplied on committed public/index.html persists status without alert", async () => {
  const html = fs.readFileSync(path.join(ROOT, "public", "index.html"), "utf8");
  const { dash, alerts, fetches } = makeHarness(extractScript(html));
  const job = makeJob();
  dash.jobs = [job];
  await dash.markApplied(job);
  assert.equal(fetches.length, 1);
  assert.equal(fetches[0].opts.method, "PATCH");
  assert.deepEqual(alerts, []);
  assert.equal(job.status, "Applied");
  const headers = fetches[0].opts.headers;
  assert.ok(headers.apikey);
  assert.equal(headers.Authorization, `Bearer ${headers.apikey}`);
  assert.equal(JSON.parse(fetches[0].opts.body).status, "Applied");
});

test("markApplied on app/static/index.html rendered with configured key succeeds", async () => {
  const tpl = fs.readFileSync(path.join(ROOT, "app", "static", "index.html"), "utf8");
  const html = tpl
    .replaceAll("__SUPABASE_URL__", "https://test.supabase.co")
    .replaceAll("__SUPABASE_KEY__", "test-anon-key");
  const { dash, alerts, fetches } = makeHarness(extractScript(html));
  const job = makeJob();
  dash.jobs = [job];
  await dash.markApplied(job);
  assert.equal(fetches.length, 1);
  assert.equal(fetches[0].opts.method, "PATCH");
  assert.deepEqual(alerts, []);
  assert.equal(job.status, "Applied");
  const headers = fetches[0].opts.headers;
  assert.ok(headers.apikey);
  assert.equal(headers.Authorization, `Bearer ${headers.apikey}`);
  assert.equal(JSON.parse(fetches[0].opts.body).status, "Applied");
});
