// Runs view: create synchronous agent runs and read owner-scoped durable history.
import { h, clear, toast, jsonBlock, fullTime, timeAgo } from "../dom.js";
import * as api from "../api.js";
import { reportError } from "../app.js";

function runReportView(report, expanded) {
  const steps = h("div", { class: "steps" });
  for (const [index, event] of (report.tool_results || []).entries()) {
    const result = event.result || {};
    steps.append(h("details", { class: "step" },
      h("summary", null,
        h("span", { class: `badge badge-${result.ok ? "ok" : "bad"}` }, result.ok ? "ok" : "error"),
        ` ${index + 1}. ${event.tool || "tool"}`,
        result.elapsed_ms !== undefined ? h("span", { class: "muted" }, ` ${result.elapsed_ms} ms`) : null),
      h("div", { class: "step-body" },
        h("h4", null, "arguments"), jsonBlock(event.arguments ?? {}),
        result.error ? h("p", { class: "bad-text" }, result.error) : null,
        result.content !== undefined && result.content !== null
          ? h("div", null, h("h4", null, "result"), jsonBlock(result.content)) : null)));
  }
  return h("article", { class: "card run" },
    h("header", { class: "run-head" },
      h("div", null,
        h("div", { class: "run-goal" }, report.goal),
        h("div", { class: "muted small" },
          h("code", null, report.run_id), " · ", `${report.steps_used} steps`,
          report.created_at ? ` · ${timeAgo(report.created_at)}` : "")),
      h("span", { class: "muted small" }, report.final ? "finished" : "stopped")),
    h("h3", null, "Final answer"),
    h("p", { class: "final" }, report.final || "(no final answer)"),
    (report.tool_results || []).length
      ? h("div", null, h("h3", null, `Tool events (${report.tool_results.length})`), steps)
      : h("p", { class: "muted" }, "No tool calls were made."));
}

export async function renderRuns(root) {
  const goal = h("textarea", { class: "input", id: "run-goal", rows: 4, maxlength: 20000, placeholder: "Describe the goal for the agent run…" });
  const approve = h("input", { type: "checkbox", id: "approve-writes" });
  const submit = h("button", { class: "button", type: "button" }, "Start run");
  const spinner = h("span", { class: "muted" });
  const resultBox = h("div");
  const historyBox = h("div");

  const renderHistory = () => {
    clear(historyBox);
    api.listRuns().then(({ runs: items }) => {
      if (!items.length) { historyBox.append(h("p", { class: "muted" }, "No runs for this account yet.")); return; }
      for (const report of items) {
      historyBox.append(h("details", { class: "history-item" },
        h("summary", null,
          h("span", { class: "history-goal" }, report.goal.slice(0, 120)),
          h("span", { class: "muted small" }, ` ${report.steps_used} steps · ${timeAgo(report.recordedAt)}`)),
        runReportView(report, false)));
      }
    }).catch((error) => reportError(error, "could not list runs"));
  };

  submit.addEventListener("click", async () => {
    const text = goal.value.trim();
    if (text.length < 2) { toast("Goal must be at least 2 characters.", "warn"); return; }
    submit.disabled = true;
    clear(resultBox);
    spinner.textContent = "Running… POST /v1/runs is synchronous: this request stays open until the agent finishes or fails, and can take minutes depending on the model.";
    try {
      const report = await api.createRun(text, approve.checked);
      clear(resultBox).append(runReportView(report, true));
      toast("Run finished.", "ok");
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 403) {
        toast("The current credential lacks runs:write.", "bad");
      } else if (error instanceof api.ApiError && error.status === 502) {
        toast(`Agent run failed: ${error.detail}`, "bad", 9000);
      } else reportError(error, "run failed");
    } finally {
      submit.disabled = false;
      spinner.textContent = "";
      renderHistory();
    }
  });

  root.append(
    h("section", { class: "card" },
      h("h2", null, "New run"),
      h("p", { class: "muted" }, "Requires the runs:write scope. Runs execute synchronously against the configured model endpoint."),
      goal,
      h("label", { class: "check" }, approve,
        " approve writes (approve_writes). Grants the agent file-write and mutation approvals for this run. The API default is deny."),
      h("div", { class: "row" }, submit, spinner),
      resultBox),
    h("section", { class: "card" },
      h("h2", null, "Run history"), historyBox));
  renderHistory();
}
