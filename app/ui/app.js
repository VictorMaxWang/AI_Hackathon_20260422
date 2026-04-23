(function () {
  const form = document.querySelector("#chat-form");
  const input = document.querySelector("#raw-user-input");
  const sendButton = document.querySelector("#send-button");
  const statusText = document.querySelector("#request-status");
  const resultPanel = document.querySelector("#result-panel");
  const userInputText = document.querySelector("#user-input-text");
  const riskCard = document.querySelector("#risk-card");
  const riskLevel = document.querySelector("#risk-level");
  const riskReasons = document.querySelector("#risk-reasons");
  const planStatus = document.querySelector("#plan-status");
  const planReason = document.querySelector("#plan-reason");
  const executionStatus = document.querySelector("#execution-status");
  const confirmationPanel = document.querySelector("#confirmation-panel");
  const confirmationText = document.querySelector("#confirmation-text");
  const refusalPanel = document.querySelector("#refusal-panel");
  const refusalReason = document.querySelector("#refusal-reason");
  const safeAlternative = document.querySelector("#safe-alternative");
  const planSteps = document.querySelector("#plan-steps");
  const explanationText = document.querySelector("#explanation-text");
  const resultSummary = document.querySelector("#result-summary");
  const resultJson = document.querySelector("#result-json");

  form.addEventListener("submit", async function (event) {
    event.preventDefault();

    const rawUserInput = input.value.trim();
    if (!rawUserInput) {
      statusText.textContent = "请输入运维请求或精确确认语";
      return;
    }

    setBusy(true);
    statusText.textContent = "处理中";

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ raw_user_input: rawUserInput }),
      });

      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || "请求失败");
      }

      renderResult(payload, rawUserInput);
      statusText.textContent = "已返回";
    } catch (error) {
      resultPanel.hidden = false;
      resultPanel.dataset.state = "failed";
      userInputText.textContent = rawUserInput;
      riskCard.dataset.risk = "";
      riskLevel.textContent = "-";
      riskReasons.textContent = "-";
      planStatus.textContent = "-";
      planReason.textContent = "-";
      executionStatus.textContent = "failed";
      confirmationPanel.hidden = true;
      refusalPanel.hidden = true;
      renderPlanSteps([]);
      explanationText.textContent = error.message || "请求失败";
      resultSummary.textContent = "请求失败";
      resultJson.textContent = "-";
      statusText.textContent = "请求失败";
    } finally {
      setBusy(false);
    }
  });

  function renderResult(payload, rawUserInput) {
    const intent = payload.intent || {};
    const risk = payload.risk || {};
    const plan = payload.plan || {};
    const execution = payload.execution || {};
    const result = payload.result || {};
    const status = result.status || plan.status || execution.status || "-";
    const confirmation = result.confirmation_text || risk.confirmation_text || "";

    resultPanel.hidden = false;
    resultPanel.dataset.state = normalizeState(status);
    userInputText.textContent = rawUserInput || intent.raw_user_input || "-";
    riskCard.dataset.risk = risk.risk_level || "";
    riskLevel.textContent = risk.risk_level || "-";
    riskReasons.textContent = Array.isArray(risk.reasons) && risk.reasons.length
      ? risk.reasons.join("；")
      : "-";
    planStatus.textContent = plan.status || "-";
    planReason.textContent = plan.reason || "-";
    executionStatus.textContent = execution.status || result.status || "-";

    confirmationPanel.hidden = status !== "pending_confirmation" || !confirmation;
    confirmationText.textContent = confirmation || "-";

    refusalPanel.hidden = status !== "refused" && plan.status !== "refused";
    refusalReason.textContent = result.error || plan.reason || formatReasons(risk.reasons);
    safeAlternative.textContent = risk.safe_alternative
      ? `安全替代方案：${risk.safe_alternative}`
      : "安全替代方案：-";

    renderPlanSteps(Array.isArray(plan.steps) ? plan.steps : []);
    explanationText.textContent = payload.explanation || "-";
    resultSummary.textContent = summarizeResult(result, execution);
    resultJson.textContent = JSON.stringify(result, null, 2);
  }

  function renderPlanSteps(steps) {
    planSteps.replaceChildren();

    if (!steps.length) {
      const emptyItem = document.createElement("li");
      emptyItem.textContent = "无执行步骤";
      planSteps.appendChild(emptyItem);
      return;
    }

    for (const step of steps) {
      const item = document.createElement("li");
      const toolName = step.tool_name || "unknown_tool";
      const args = step.args && Object.keys(step.args).length
        ? ` ${JSON.stringify(step.args)}`
        : "";
      item.textContent = `${toolName}${args}`;
      planSteps.appendChild(item);
    }
  }

  function summarizeResult(result, execution) {
    const status = result.status || execution.status || "-";

    if (status === "pending_confirmation") {
      return "等待用户输入精确确认语，尚未执行工具。";
    }

    if (status === "refused") {
      return result.error ? `已拒绝：${result.error}` : "已拒绝，未执行工具。";
    }

    if (status === "success") {
      const dataStatus = result.data && result.data.status ? result.data.status : "completed";
      return result.tool_name
        ? `${result.tool_name} 返回成功，状态：${dataStatus}`
        : `执行成功，状态：${dataStatus}`;
    }

    if (status === "failed") {
      return result.error ? `执行失败：${result.error}` : "执行失败。";
    }

    if (status === "cancelled") {
      return "待确认操作已取消，未执行工具。";
    }

    return `当前状态：${status}`;
  }

  function normalizeState(status) {
    if (["pending_confirmation", "refused", "success", "failed", "cancelled"].includes(status)) {
      return status;
    }
    return "neutral";
  }

  function formatReasons(reasons) {
    return Array.isArray(reasons) && reasons.length ? reasons.join("；") : "-";
  }

  function setBusy(isBusy) {
    sendButton.disabled = isBusy;
    input.disabled = isBusy;
  }
})();
