import assert from "node:assert/strict";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { Client } from "@langchain/langgraph-sdk";

const [url, assistantId, directory, state] = process.argv.slice(2);
const client = new Client({ apiUrl: url });
const running = state === "running";
const results = [];

async function marker(path) {
  const deadline = Date.now() + 15000;
  while (!existsSync(path)) {
    assert(Date.now() < deadline, `marker timeout: ${path}`);
    await sleep(10);
  }
}

for (const action of ["interrupt", "rollback"]) {
  for (const wait of [false, true]) {
    const control = join(directory, `js-${action}-${wait}`);
    mkdirSync(control);
    const thread = await client.threads.create();
    const run = await client.runs.create(thread.thread_id, assistantId, {
      input: { marker_directory: control }, streamResumable: true,
    });
    if (running) {
      await marker(join(control, "entered"));
      const abort = new AbortController();
      const stream = client.runs.joinStream(thread.thread_id, run.run_id, {
        cancelOnDisconnect: false, signal: abort.signal,
      });
      const consuming = (async () => {
        try {
          for await (const frame of stream) assert(frame.event);
        } catch (error) {
          if (!abort.signal.aborted) throw error;
        }
      })();
      await sleep(100);
      abort.abort();
      await consuming;
      assert(!existsSync(join(control, "cleaning")), "disconnect cancelled the run");
    }
    let responded = false;
    const request = client.runs.cancel(thread.thread_id, run.run_id, wait, action)
      .then(() => { responded = true; });
    if (running) {
      await marker(join(control, "cleaning"));
      await sleep(100);
      if (wait) assert(!responded, "SDK wait returned before cleanup");
      writeFileSync(join(control, "release"), "");
    }
    await request;
    if (running) await marker(join(control, "stopped"));
    if (action === "interrupt") {
      await client.runs.join(thread.thread_id, run.run_id);
      const frames = [];
      for await (const frame of client.runs.joinStream(thread.thread_id, run.run_id)) {
        frames.push(frame.event);
      }
      assert.equal((await client.runs.get(thread.thread_id, run.run_id)).status, "interrupted");
      assert.equal((await client.runs.list(thread.thread_id)).length, 1);
    }
    results.push({ sdk: "javascript-1.9.28", state, action, wait, passed: true });
  }
}
process.stdout.write(JSON.stringify(results) + "\n");
