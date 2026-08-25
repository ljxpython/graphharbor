## 1. Official differential contract

- [x] 1.1 Add a URL-driven official-versus-GraphHarbor HTTP/OpenAPI comparator with documented dynamic-value normalization.
- [x] 1.2 Add focused tests for equal output, dynamic values, JSON mismatch and OpenAPI endpoint mismatch.
- [x] 1.3 Provide a documented command that starts no services itself and reports actionable comparison failures.
- [x] 1.4 Compare `/openapi.json` by supported path and method availability; retain HTTP media/status checks without comparing framework-generated operation metadata.
- [x] 1.5 Add a response-reference scenario format and use it to compare assistant, thread and default run-stream SSE output.

## 2. Upgrade gate

- [x] 2.1 Add a manual compatibility-upgrade workflow that starts fixed-version official `langgraph dev` and GraphHarbor against isolated PostgreSQL/Redis namespaces.
- [x] 2.2 Make the workflow run the differential comparator and retain both service logs on failure.
- [x] 2.3 Load OpenAPI exclusions from the single documented compatibility-exclusions manifest.
- [x] 2.4 Start a GraphHarbor worker in the upgrade workflow so run-stream output is actually exercised.

## 3. Version mapping

- [x] 3.1 Record the current GraphHarbor-to-LangGraph version mapping and official comparison status in the compatibility matrix.
- [x] 3.2 Extend the compatibility baseline check to validate the current mapping and document the required dependency-upgrade procedure.
- [x] 3.3 Run focused tests, baseline validation and strict OpenSpec validation.
- [x] 3.4 Align `/info` with the fixed official `langgraph dev` output and record the successful baseline date.
- [x] 3.5 Align assistant/thread creation and default run-stream SSE output with the fixed official baseline.
