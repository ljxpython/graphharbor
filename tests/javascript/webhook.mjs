import assert from "node:assert/strict";
import { Client } from "@langchain/langgraph-sdk";

const client = new Client({ apiUrl: process.env.GRAPHHARBOR_URL });
const webhook = "https://callback.test/hook";
const thread = await client.threads.create();
const run = await client.runs.create(thread.thread_id, "probe", { input: { value: 1 }, webhook });
assert.equal((await client.runs.join(thread.thread_id, run.run_id)).value, 2);
assert.equal((await client.runs.wait(null, "probe", { input: { value: 2 }, webhook })).value, 3);
let frames = 0;
for await (const part of client.runs.stream(thread.thread_id, "probe", { input: { value: 3 }, webhook })) {
  assert.ok(part.event);
  frames += 1;
}
assert.ok(frames > 0);
assert.equal((await client.runs.createBatch([
  { assistantId: "probe", input: { value: 4 }, webhook },
  { assistantId: "probe", input: { value: 5 }, webhook },
])).length, 2);
const cron = await client.crons.create("probe", { schedule: "0 0 1 1 *", input: { value: 6 }, webhook });
await client.crons.update(cron.cron_id, { input: { value: 7 }, webhook });
console.log(JSON.stringify({ cron_id: cron.cron_id }));
