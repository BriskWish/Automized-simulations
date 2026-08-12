# Offline LLM Evaluation Matrix

`matrix.py` is the source of truth for the next LLM evaluation phase.  It is
declarative and network-free: importing it does not create a client, read a
prompt, use credentials, or dispatch a tool.

The matrix currently specifies four test families:

- One safe route for every `willy.errors.ErrorKind`, including bounded recovery,
  policy rejection, confirmation, fork, artifact validation, and escalation.
- Configuration proposals, revisions, ambiguity, validation failures, input
  audit failures, and confirmation-required changes.
- Fake completion and fake executor protocol cases for tool name, arguments,
  layer boundary, authorization, loop limit, malformed response, timeout,
  transport, rate limit, credential, budget, circuit, and cancellation paths.
- Future acceptance metrics for error routing, tool calling, configuration
  accuracy, latency, and public-report redaction.

The current pytest module only validates coverage and safety properties of
these specifications.  A later opt-in runner may bind a scripted fake client
to this matrix.  Live model evaluation remains separate from the default test
suite and must not be enabled while prompt contracts are changing.

For an explicit live run, use `python3 -m tests.llm_eval.live_runner`.  It
uses the configured model with the production layer prompts and fake tools,
then writes a redacted report.  Legacy completion scores and actual tool
calling are separate acceptance gates: a score-only pass is not a tool-calling
acceptance pass.
