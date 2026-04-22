(function () {
  const form = document.querySelector("#chat-form");
  const input = document.querySelector("#raw-user-input");
  const sendButton = document.querySelector("#send-button");
  const statusText = document.querySelector("#request-status");
  const resultPanel = document.querySelector("#result-panel");
  const riskLevel = document.querySelector("#risk-level");
  const riskReasons = document.querySelector("#risk-reasons");
  const environmentSummary = document.querySelector("#environment-summary");
  const executionStatus = document.querySelector("#execution-status");
  const explanationText = document.querySelector("#explanation-text");
  const resultJson = document.querySelector("#result-json");

  form.addEventListener("submit", async function (event) {
    event.preventDefault();

    const rawUserInput = input.value.trim();
    if (!rawUserInput) {
      statusText.textContent = "请输入只读运维请求";
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

      renderResult(payload);
      statusText.textContent = "已返回";
    } catch (error) {
      resultPanel.hidden = false;
      riskLevel.textContent = "-";
      riskReasons.textContent = "-";
      environmentSummary.textContent = "-";
      executionStatus.textContent = "failed";
      explanationText.textContent = error.message || "请求失败";
      resultJson.textContent = "-";
      statusText.textContent = "请求失败";
    } finally {
      setBusy(false);
    }
  });

  function renderResult(payload) {
    const risk = payload.risk || {};
    const environment = payload.environment || {};
    const snapshot = environment.snapshot || {};
    const execution = payload.execution || {};

    resultPanel.hidden = false;
    riskLevel.textContent = risk.risk_level || "-";
    riskReasons.textContent = Array.isArray(risk.reasons) && risk.reasons.length
      ? risk.reasons.join("；")
      : "-";
    environmentSummary.textContent = formatEnvironment(environment, snapshot);
    executionStatus.textContent = execution.status || payload.result?.status || "-";
    explanationText.textContent = payload.explanation || "-";
    resultJson.textContent = JSON.stringify(payload.result || {}, null, 2);
  }

  function formatEnvironment(environment, snapshot) {
    if (environment.status && environment.status !== "ok") {
      return `${environment.status}: ${environment.reason || environment.error || "未采集"}`;
    }

    const parts = [
      snapshot.hostname ? `主机 ${snapshot.hostname}` : null,
      snapshot.distro ? `系统 ${snapshot.distro}` : null,
      snapshot.current_user ? `用户 ${snapshot.current_user}` : null,
      snapshot.connection_mode ? `模式 ${snapshot.connection_mode}` : null,
    ].filter(Boolean);

    return parts.length ? parts.join("，") : "未采集";
  }

  function setBusy(isBusy) {
    sendButton.disabled = isBusy;
    input.disabled = isBusy;
  }
})();
