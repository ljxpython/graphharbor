import { Client } from "@langchain/langgraph-sdk";

const baseUrl = (process.env.GRAPHHARBOR_URL || "http://127.0.0.1:31296").replace(/\/$/, "");
const client = new Client({ apiUrl: baseUrl });
const suffix = `js-${Date.now()}`;

const assistant = await client.assistants.create({
  graphId: "assistant",
  name: "graphharbor-js-contract",
  metadata: { suite: suffix },
});
const thread = await client.threads.create({
  graphId: "assistant",
  metadata: { suite: suffix },
});

try {
  const fetchedAssistant = await client.assistants.get(assistant.assistant_id);
  if (fetchedAssistant.assistant_id !== assistant.assistant_id) {
    throw new Error("assistant lookup returned a different id");
  }
  const assistants = await client.assistants.search({ graphId: "assistant", metadata: { suite: suffix } });
  if (!assistants.some((item) => item.assistant_id === assistant.assistant_id)) {
    throw new Error("assistant search did not return created assistant");
  }
  if ((await client.assistants.count({ graphId: "assistant", metadata: { suite: suffix } })) !== 1) {
    throw new Error("assistant count did not return one");
  }
  if ((await client.assistants.getVersions(assistant.assistant_id)).length !== 1) {
    throw new Error("assistant versions did not return the initial version");
  }
  await client.assistants.setLatest(assistant.assistant_id, 1);
  await client.assistants.getGraph(assistant.assistant_id);
  await client.assistants.getSchemas(assistant.assistant_id);
  await client.assistants.getSubgraphs(assistant.assistant_id);
  await client.assistants.update(assistant.assistant_id, { metadata: { updated: true } });
  const fetchedThread = await client.threads.get(thread.thread_id);
  if (fetchedThread.thread_id !== thread.thread_id) {
    throw new Error("thread lookup returned a different id");
  }
  const count = await client.threads.count({ metadata: { suite: suffix } });
  if (count !== 1) throw new Error(`expected one thread, got ${count}`);
  if (!(await client.threads.search({ metadata: { suite: suffix } })).some((item) => item.thread_id === thread.thread_id)) {
    throw new Error("thread search did not return created thread");
  }
  await client.threads.update(thread.thread_id, { metadata: { updated: true } });
  await client.threads.updateState(thread.thread_id, { values: { value: 1 } });
  await client.threads.getState(thread.thread_id);
  await client.threads.getHistory(thread.thread_id, { limit: 5 });
  const copied = await client.threads.copy(thread.thread_id);
  await client.threads.patchState(copied.thread_id, { patched: true });

  const run = await client.runs.create(thread.thread_id, assistant.assistant_id, {
    input: { value: 1 },
  });
  const fetchedRun = await client.runs.get(thread.thread_id, run.run_id);
  if (fetchedRun.run_id !== run.run_id) throw new Error("run lookup returned a different id");
  if (!(await client.runs.list(thread.thread_id)).some((item) => item.run_id === run.run_id)) {
    throw new Error("run list did not return created run");
  }
  const batch = await client.runs.createBatch([
    { assistantId: assistant.assistant_id, threadId: thread.thread_id, input: { value: 2 } },
    { assistantId: assistant.assistant_id, threadId: thread.thread_id, input: { value: 3 } },
  ]);
  if (batch.length !== 2) throw new Error("run batch did not create two runs");
  await client.runs.cancelMany({ runIds: batch.map((item) => item.run_id), status: "pending" });
  await client.runs.cancel(thread.thread_id, run.run_id, false, "interrupt");
  const cancelled = await client.runs.get(thread.thread_id, run.run_id);
  if (cancelled.status !== "interrupted") {
    throw new Error(`cancelled run status was ${cancelled.status}`);
  }
  await client.runs.delete(thread.thread_id, run.run_id);
  for (const item of batch) await client.runs.delete(thread.thread_id, item.run_id);

  const cron = await client.crons.create(assistant.assistant_id, {
    schedule: "* * * * *",
    input: { value: 1 },
  });
  const crons = await client.crons.search({ assistantId: assistant.assistant_id });
  if (!crons.some((item) => item.cron_id === cron.cron_id)) {
    throw new Error("cron search did not return created cron");
  }
  if ((await client.crons.count({ assistantId: assistant.assistant_id })) !== 1) {
    throw new Error("cron count did not return one");
  }
  await client.crons.update(cron.cron_id, { enabled: false, schedule: "*/5 * * * *" });
  const threadCron = await client.crons.createForThread(thread.thread_id, assistant.assistant_id, {
    schedule: "*/10 * * * *",
  });
  await client.crons.delete(threadCron.cron_id);
  await client.crons.delete(cron.cron_id);
  await client.threads.prune([copied.thread_id], { strategy: "delete" });
  console.log(JSON.stringify({ ok: true, assistant_id: assistant.assistant_id, thread_id: thread.thread_id }));
} finally {
  await client.threads.delete(thread.thread_id);
  await client.assistants.delete(assistant.assistant_id);
}
