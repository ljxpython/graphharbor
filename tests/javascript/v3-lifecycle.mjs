import assert from "node:assert/strict";
import { Client } from "@langchain/langgraph-sdk";

const client = new Client({ apiUrl: process.env.GRAPHHARBOR_URL || "http://127.0.0.1:31397" });
const row = await client.threads.create();
const stream = client.threads.stream(row.thread_id, { assistantId: "v3_tool" });
const timeout = setTimeout(() => { console.error("lifecycle consumer timed out"); process.exitCode = 1; void stream.close(); }, 25000);
try {
  const children = [];
  const childOutputs = [];
  const childMessages = [];
  const raw = [];
  const subscription = await stream.subscribe(["checkpoints", "custom"]);
  const consumeRaw = (async () => { for await (const event of subscription) raw.push(event); })();
  const consume = (async () => {
    for await (const child of stream.subgraphs) {
      children.push(child);
      childOutputs.push(child.output);
      childMessages.push((async () => {
        const messages = [];
        for await (const message of child.messages) messages.push(await message);
        return messages;
      })());
    }
  })();
  await stream.run.start({ input: { messages: [] } });
  const output = await stream.output;
  await consume;
  assert.equal(children.length, 2);
  assert.equal(new Set(children.map(child => JSON.stringify(child.namespace))).size, 2);
  assert.deepEqual(new Set(children.map(child => child.cause?.tool_call_id)), new Set(["dispatch-alpha", "dispatch-beta"]));
  assert.ok(output);
  const values = await Promise.all(childOutputs);
  const messages = await Promise.all(childMessages);
  await consumeRaw;
  assert.ok(values.every(value => JSON.stringify(value).includes("child-result")));
  assert.ok(messages.every(value => JSON.stringify(value).includes("child-result")));
  const checkpoints = raw.filter(event => event.method === "checkpoints").length;
  if (process.env.REQUIRE_CHECKPOINTS !== "0") assert.ok(checkpoints);
  const custom = raw.filter(event => event.method === "custom" && event.params.data.fixture === "dispatch").length;
  if (process.env.REQUIRE_CHECKPOINTS !== "0") assert.ok(custom);
  console.log(JSON.stringify({ children: children.length, cause: "verified", output: "resolved", checkpoints, custom }));
} finally {
  clearTimeout(timeout);
  await stream.close();
}

const projectionRow = await client.threads.create();
const projection = client.threads.stream(projectionRow.thread_id, { assistantId: "v3_projection" });
const projectionTimeout = setTimeout(() => { process.exitCode = 1; void projection.close(); }, 25000);
try {
  const results = [];
  const toolEvents = [];
  projection.onEvent(event => { if (event.method === "tools") toolEvents.push(event); });
  const consume = (async () => {
    for await (const tool of projection.toolCalls) results.push({ id: tool.id, output: await tool.output });
  })();
  await projection.run.start({ input: { messages: [{ role: "user", content: "call delegate" }] } });
  await projection.output;
  await consume;
  assert.equal(new Set(results.map(result => result.id)).size, 1);
  assert.ok(results.every(result => JSON.stringify(result.output).includes("child-result")));
  assert.equal(toolEvents.filter(event => event.params.data.event === "tool-started").length, 1);
  assert.equal(toolEvents.filter(event => event.params.data.event === "tool-finished").length, 1);
  // SDK 1.9.28 yields the same tool handle twice on both official and GraphHarbor.
  // Keep that observable; do not hide the consumer issue by deleting server events.
  console.log(JSON.stringify({ toolProjection: "verified", yieldedHandles: results.length, uniqueToolCalls: 1 }));
} finally {
  clearTimeout(projectionTimeout);
  await projection.close();
}

for (const scenario of ["failed", "interrupted"]) {
  const thread = await client.threads.create();
  const session = client.threads.stream(thread.thread_id, { assistantId: "v3_edge" });
  const lifecycle = [];
  session.onEvent(event => { if (event.method === "lifecycle") lifecycle.push(event); });
  const timer = setTimeout(() => { console.error(`${scenario} timed out`); process.exitCode = 1; void session.close(); }, 25000);
  try {
    await session.run.start({ input: { scenario } });
    if (scenario === "failed") {
      await session.output;
      const failure = lifecycle.find(event => event.params.data.event === "failed" && event.params.namespace.length === 0);
      assert.ok(failure, "root failure must reach the SDK");
      assert.match(failure.params.data.error, /v3 fixture failure/);
    } else {
      await session.output;
      assert.equal(session.interrupted, true);
      assert.equal(session.interrupts.length, 1);
      const pending = session.interrupts[0];
      assert.ok(pending.interruptId);
      await session.input.respond({ interrupt_id: pending.interruptId, namespace: pending.namespace, response: true });
      assert.ok(await session.output);
    }
    console.log(JSON.stringify({ scenario, sdk: "verified" }));
  } finally {
    clearTimeout(timer);
    await session.close();
  }
}
