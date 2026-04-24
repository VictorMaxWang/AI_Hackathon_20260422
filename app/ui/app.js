(function (globalScope, factory) {
  const api = factory();

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }

  if (globalScope) {
    globalScope.GuardedOpsOperatorPanel = api;
  }

  if (typeof window !== "undefined" && typeof document !== "undefined") {
    const boot = function () {
      api.boot(document);
    };

    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", boot, { once: true });
    } else {
      boot();
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const SECTION_DEFINITIONS = [
    ["intent_normalized", "请求归一化"],
    ["plan_summary", "计划摘要"],
    ["risk_hits", "风险命中"],
    ["scope_preview", "范围预览"],
    ["confirmation_basis", "确认依据"],
    ["execution_evidence", "执行证据"],
    ["result_assertion", "结果断言"],
    ["residual_risks_or_next_step", "残余风险 / 下一步"],
  ];

  const STATUS_LABELS = {
    success: "Success",
    completed: "Completed",
    pending_confirmation: "Pending Confirmation",
    refused: "Refused",
    failed: "Failed",
    cancelled: "Cancelled",
    confirmed: "Confirmed",
    skipped: "Skipped",
    unknown: "Unknown",
  };

  const PREFLIGHT_LABELS = {
    ready: "Ready",
    pending: "Pending",
    blocked: "Blocked",
    not_required: "Not Required",
    not_available: "Not Available",
  };

  const STATUS_TONES = {
    success: "ready",
    completed: "ready",
    confirmed: "ready",
    pending_confirmation: "pending",
    pending: "pending",
    cancelled: "pending",
    skipped: "pending",
    refused: "blocked",
    failed: "blocked",
    mismatch: "blocked",
    required: "pending",
    info: "info",
    unknown: "neutral",
    neutral: "neutral",
    not_required: "neutral",
    not_available: "neutral",
  };

  function boot(doc) {
    const form = doc.querySelector("#operator-form");
    const input = doc.querySelector("#operator-request");
    const button = doc.querySelector("#submit-request");
    const statusText = doc.querySelector("#request-status");

    if (!form || !input || !button || !statusText) {
      return;
    }

    if (form.dataset.bound === "true") {
      return;
    }

    form.dataset.bound = "true";
    form.addEventListener("submit", async function (event) {
      event.preventDefault();

      const rawUserInput = String(input.value || "").trim();
      if (!rawUserInput) {
        statusText.textContent = "请输入自然语言请求。";
        return;
      }

      setBusy(input, button, true);
      statusText.textContent = "正在读取 orchestrator 输出…";

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

        const viewModel = createViewModel(payload, rawUserInput);
        renderViewModel(doc, viewModel);
        statusText.textContent = "Operator Panel 已更新";
      } catch (error) {
        const viewModel = createFailureViewModel(rawUserInput, error);
        renderViewModel(doc, viewModel);
        statusText.textContent = "请求失败";
      } finally {
        setBusy(input, button, false);
      }
    });
  }

  function createFailureViewModel(rawUserInput, error) {
    const message = firstText(error && error.message, "请求失败");
    return {
      userInput: firstText(rawUserInput, "-"),
      status: "failed",
      statusLabel: STATUS_LABELS.failed,
      statusTone: "blocked",
      riskLevel: "-",
      riskTone: "neutral",
      riskReasons: [],
      confidenceText: "Unavailable",
      confidenceTone: "neutral",
      confidenceSource: "",
      blastRadius: normalizeBlastRadius({}),
      policySimulator: normalizePolicySimulator({}),
      explanationSections: [],
      timelineEntries: [
        {
          source: "client",
          title: "render_failed",
          summary: message,
          status: "failed",
          tone: "blocked",
          meta: [],
          evidenceRefs: [],
        },
      ],
      preflightItems: [],
      confirmation: {
        visible: false,
        required: false,
        status: "not_required",
        statusLabel: PREFLIGHT_LABELS.not_required,
        tone: "neutral",
        summary: "",
        text: "",
        evidenceRefs: [],
      },
      refusal: {
        visible: false,
        reason: "",
        safeAlternative: "",
        evidenceRefs: [],
      },
      recovery: {
        visible: false,
        why: "",
        failureType: "",
        safeNextSteps: [],
        diagnostics: [],
        flags: [],
      },
      residualNextStep: {
        summary: message,
        evidenceRefs: [],
      },
    };
  }

  function createViewModel(payload, rawUserInput) {
    const source = asObject(payload);
    const operatorPanel = hasArray(asObject(source.operator_panel).explanation_sections)
      ? asObject(source.operator_panel)
      : buildFallbackPanel(source, rawUserInput);

    const status = firstText(operatorPanel.status, "unknown");
    const riskLevel = firstText(operatorPanel.risk_level, "unknown");
    const confidence = normalizeConfidence(operatorPanel.confidence);
    const confirmation = normalizeConfirmation(asObject(operatorPanel.confirmation));
    const refusal = normalizeRefusal(asObject(operatorPanel.refusal));
    const recovery = normalizeRecovery(asObject(operatorPanel.recovery));
    const residual = normalizeResidual(asObject(operatorPanel.residual_next_step));

    return {
      userInput: firstText(operatorPanel.user_input, rawUserInput, "-"),
      status: status,
      statusLabel: formatStatusLabel(status),
      statusTone: toneForStatus(status),
      riskLevel: riskLevel,
      riskTone: toneForRisk(riskLevel),
      riskReasons: uniqueStrings(operatorPanel.risk_reasons),
      confidenceText: formatConfidence(confidence),
      confidenceTone: confidence === null ? "neutral" : "info",
      confidenceSource: firstText(operatorPanel.confidence_source),
      blastRadius: normalizeBlastRadius(asObject(operatorPanel.blast_radius_preview)),
      policySimulator: normalizePolicySimulator(asObject(operatorPanel.policy_simulator)),
      explanationSections: normalizeExplanationSections(operatorPanel.explanation_sections),
      timelineEntries: normalizeTimelineEntries(operatorPanel.timeline_entries),
      preflightItems: normalizePreflightItems(operatorPanel.preflight_items),
      confirmation: confirmation,
      refusal: refusal,
      recovery: recovery,
      residualNextStep: residual,
    };
  }

  function buildFallbackPanel(payload, rawUserInput) {
    const intent = asObject(payload.intent);
    const risk = asObject(payload.risk);
    const plan = asObject(payload.plan);
    const execution = asObject(payload.execution);
    const result = asObject(payload.result);
    const explanationCard = asObject(payload.explanation_card);
    const evidenceChain = asObject(payload.evidence_chain);
    const recovery = asObject(payload.recovery);
    const timeline = asList(payload.timeline);
    const environment = asObject(payload.environment);
    const status = firstText(result.status, plan.status, execution.status, "unknown");
    const planStatus = firstText(plan.status, "unknown");
    const resultStatus = firstText(result.status, execution.status, "unknown");
    const confirmationText = firstText(result.confirmation_text, risk.confirmation_text);
    const requiresConfirmation = Boolean(risk.requires_confirmation);

    return {
      user_input: firstText(rawUserInput, intent.raw_user_input, "-"),
      status: status,
      risk_level: firstText(risk.risk_level, "unknown"),
      risk_reasons: uniqueStrings(risk.reasons),
      confidence: normalizeConfidence(intent.confidence),
      confidence_source: firstText(intent.confidence_source, risk.confidence_source),
      blast_radius_preview: asObject(payload.blast_radius_preview),
      policy_simulator: asObject(payload.policy_simulator),
      explanation_sections: SECTION_DEFINITIONS.map(function (item) {
        const key = item[0];
        const label = item[1];
        const section = asObject(explanationCard[key]);
        return {
          key: key,
          label: label,
          summary: firstText(section.summary, "-"),
          evidence_refs: uniqueStrings(section.evidence_refs),
        };
      }),
      timeline_entries: hasArray(timeline) && timeline.length
        ? timeline.map(function (entry, index) {
            const item = asObject(entry);
            const statusValue = firstText(item.status, "unknown");
            return {
              source: "timeline",
              title: firstText(item.intent, item.step_id, "timeline"),
              summary: firstText(item.result_summary, statusValue),
              status: statusValue,
              severity: toneForStatus(statusValue),
              stage: firstText(item.intent, "timeline"),
              timestamp: firstText(item.timestamp),
              risk_level: firstText(item.risk),
              evidence_refs: uniqueStrings(item.refs),
              step_id: firstText(item.step_id, index + 1),
            };
          })
        : asList(evidenceChain.events).map(function (entry, index) {
            const item = asObject(entry);
            const details = asObject(item.details);
            return {
              source: "evidence",
              title: firstText(item.title, item.stage, "evidence"),
              summary: firstText(
                details.result_summary,
                details.summary,
                details.error,
                details.status,
                item.title,
                item.stage
              ),
              status: firstText(details.status, details.result_status),
              severity: firstText(item.severity, "info"),
              stage: firstText(item.stage, "evidence"),
              timestamp: firstText(item.timestamp),
              risk_level: firstText(details.risk_level, details.risk),
              evidence_refs: uniqueStrings(item.refs),
              step_id: firstText(details.step_id, index + 1),
            };
          }),
      preflight_items: [
        {
          key: "intent_parsed",
          label: "Intent parsed",
          status: asList(evidenceChain.events).some(function (entry) {
            return firstText(asObject(entry).stage).toLowerCase() === "parse";
          })
            ? "ready"
            : "not_available",
          summary: firstText(intent.intent)
            ? "识别到可解释 intent。"
            : "未发现 parse evidence。",
          evidence_refs: [],
        },
        {
          key: "policy_bound",
          label: "Policy bound",
          status: firstText(risk.risk_level) ? "ready" : "not_available",
          summary: firstText(risk.risk_level)
            ? "风险决策已绑定到当前请求。"
            : "未发现 policy evidence。",
          evidence_refs: [],
        },
        {
          key: "plan_ready",
          label: "Plan ready",
          status: planStatus === "pending_confirmation"
            ? "pending"
            : ["refused", "unsupported", "failed"].indexOf(planStatus) >= 0
              ? "blocked"
              : "ready",
          summary: "计划状态：" + planStatus + "。",
          evidence_refs: [],
        },
        {
          key: "confirmation_gate",
          label: "Confirmation gate",
          status: !requiresConfirmation
            ? "not_required"
            : resultStatus === "pending_confirmation" || planStatus === "pending_confirmation"
              ? "pending"
              : planStatus === "confirmed" || resultStatus === "success"
                ? "ready"
                : "blocked",
          summary: !requiresConfirmation
            ? "该请求不需要额外确认。"
            : confirmationText
              ? "确认文本已绑定。"
              : "等待确认状态。",
          evidence_refs: [],
        },
        {
          key: "environment_ready",
          label: "Environment ready",
          status: firstText(environment.status) === "ok"
            ? "ready"
            : firstText(environment.status) === "error"
              ? "blocked"
              : "not_available",
          summary: "环境状态：" + firstText(environment.status, "not_collected") + "。",
          evidence_refs: [],
        },
      ],
      confirmation: {
        required: requiresConfirmation,
        status: !requiresConfirmation
          ? "not_required"
          : resultStatus === "pending_confirmation" || planStatus === "pending_confirmation"
            ? "pending_confirmation"
            : planStatus === "confirmed" || resultStatus === "success"
              ? "confirmed"
              : "required",
        text: confirmationText,
        summary: firstText(asObject(explanationCard.confirmation_basis).summary),
        evidence_refs: uniqueStrings(asObject(explanationCard.confirmation_basis).evidence_refs),
      },
      refusal: {
        is_refused: status === "refused" || planStatus === "refused",
        reason: firstText(result.error, plan.reason, asObject(explanationCard.risk_hits).summary),
        safe_alternative: firstText(risk.safe_alternative),
        evidence_refs: uniqueStrings(asObject(explanationCard.risk_hits).evidence_refs),
      },
      recovery: {
        available: Object.keys(recovery).length > 0,
        failure_type: firstText(recovery.failure_type),
        why_it_failed: firstText(recovery.why_it_failed),
        safe_next_steps: uniqueStrings(recovery.safe_next_steps),
        suggested_readonly_diagnostics: uniqueStrings(
          recovery.suggested_readonly_diagnostics
        ),
        requires_confirmation_for_recovery: Boolean(
          recovery.requires_confirmation_for_recovery
        ),
        can_retry_safely: Boolean(recovery.can_retry_safely),
      },
      residual_next_step: {
        summary: firstText(
          asObject(explanationCard.residual_risks_or_next_step).summary,
          "-"
        ),
        evidence_refs: uniqueStrings(
          asObject(explanationCard.residual_risks_or_next_step).evidence_refs
        ),
      },
    };
  }

  function normalizeExplanationSections(value) {
    return asList(value).map(function (entry) {
      const item = asObject(entry);
      return {
        key: firstText(item.key, "section"),
        label: firstText(item.label, item.key, "Section"),
        summary: firstText(item.summary, "-"),
        evidenceRefs: uniqueStrings(item.evidence_refs),
      };
    });
  }

  function normalizeTimelineEntries(value) {
    return asList(value).map(function (entry, index) {
      const item = asObject(entry);
      const stage = firstText(item.stage, item.source, "evidence");
      const status = firstText(item.status);
      const severity = firstText(item.severity, item.tone, "info");
      const tone = toneForTimeline(status, severity);
      const meta = [
        firstText(item.source),
        firstText(stage),
        formatTimestamp(item.timestamp),
        firstText(item.risk_level) ? "risk " + firstText(item.risk_level) : "",
      ].filter(Boolean);

      return {
        index: index + 1,
        title: firstText(item.title, "timeline"),
        summary: firstText(item.summary, "-"),
        status: status,
        tone: tone,
        meta: meta,
        evidenceRefs: uniqueStrings(item.evidence_refs),
      };
    });
  }

  function normalizePreflightItems(value) {
    return asList(value).map(function (entry) {
      const item = asObject(entry);
      const status = firstText(item.status, "not_available");
      return {
        key: firstText(item.key, "preflight"),
        label: firstText(item.label, item.key, "Preflight"),
        status: status,
        statusLabel: PREFLIGHT_LABELS[status] || formatStatusLabel(status),
        tone: toneForStatus(status),
        summary: firstText(item.summary, "-"),
        evidenceRefs: uniqueStrings(item.evidence_refs),
      };
    });
  }

  function normalizeBlastRadius(value) {
    return {
      visible:
        Boolean(firstText(value.summary)) ||
        asList(value.facts).length > 0 ||
        asList(value.impacts).length > 0 ||
        uniqueStrings(value.protected_paths).length > 0 ||
        uniqueStrings(value.notes).length > 0,
      scenario: firstText(value.scenario, "general"),
      summary: firstText(value.summary, "-"),
      facts: asList(value.facts).map(function (entry) {
        const item = asObject(entry);
        return {
          label: firstText(item.label, "Fact"),
          value: firstText(item.value, "-"),
        };
      }),
      impacts: asList(value.impacts).map(function (entry) {
        const item = asObject(entry);
        return {
          label: firstText(item.label, "Impact"),
          value: firstText(item.value, "-"),
          precision: firstText(item.precision, "conservative"),
        };
      }),
      protectedPaths: uniqueStrings(value.protected_paths),
      notes: uniqueStrings(value.notes),
    };
  }

  function normalizePolicySimulator(value) {
    return {
      visible:
        asList(value.matched_rules).length > 0 ||
        Boolean(firstText(value.scope_summary)) ||
        Boolean(firstText(value.policy_version)) ||
        Boolean(firstText(value.target_fingerprint)),
      riskLevel: firstText(value.risk_level, "unknown"),
      riskTone: toneForRisk(firstText(value.risk_level, "unknown")),
      allow: readBoolean(value.allow),
      requiresConfirmation: readBoolean(value.requires_confirmation),
      policyVersion: firstText(value.policy_version, "-"),
      scopeSummary: firstText(value.scope_summary, "-"),
      targetFingerprint: firstText(value.target_fingerprint, "-"),
      matchedRules: asList(value.matched_rules).map(function (entry) {
        const item = asObject(entry);
        return {
          ruleId: firstText(item.rule_id, "unknown.rule"),
          outcome: firstText(item.outcome, "deny"),
          summary: firstText(item.summary, "-"),
          tone: firstText(item.outcome) === "allow"
            ? "ready"
            : firstText(item.outcome) === "confirm"
              ? "pending"
              : "blocked",
        };
      }),
      deniedBecause: uniqueStrings(value.denied_because),
      confirmationBecause: uniqueStrings(value.requires_confirmation_because),
      safeAlternative: firstText(value.safe_alternative),
    };
  }

  function normalizeConfirmation(value) {
    const status = firstText(value.status, "not_required");
    return {
      visible:
        Boolean(value.required) ||
        status !== "not_required" ||
        Boolean(firstText(value.text)) ||
        Boolean(firstText(value.summary)),
      required: Boolean(value.required),
      status: status,
      statusLabel: formatStatusLabel(status),
      tone: toneForStatus(status),
      summary: firstText(value.summary, "当前请求无确认依据。"),
      text: firstText(value.text),
      evidenceRefs: uniqueStrings(value.evidence_refs),
    };
  }

  function normalizeRefusal(value) {
    return {
      visible: Boolean(value.is_refused),
      reason: firstText(value.reason, "-"),
      safeAlternative: firstText(value.safe_alternative),
      evidenceRefs: uniqueStrings(value.evidence_refs),
    };
  }

  function normalizeRecovery(value) {
    const failureType = firstText(value.failure_type);
    return {
      visible: Boolean(value.available),
      failureType: failureType,
      why: firstText(value.why_it_failed, "-"),
      safeNextSteps: uniqueStrings(value.safe_next_steps),
      diagnostics: uniqueStrings(value.suggested_readonly_diagnostics),
      flags: [
        value.requires_confirmation_for_recovery
          ? "Recovery requires confirmation"
          : "",
        value.can_retry_safely ? "Retry can stay bounded" : "Retry not yet safe",
      ].filter(Boolean),
    };
  }

  function normalizeResidual(value) {
    return {
      summary: firstText(value.summary, "-"),
      evidenceRefs: uniqueStrings(value.evidence_refs),
    };
  }

  function renderViewModel(doc, viewModel) {
    const panel = query(doc, "#operator-panel");
    panel.hidden = false;

    setText(doc, "#user-input-text", viewModel.userInput);
    setPill(doc, "#status-badge", viewModel.statusLabel, viewModel.statusTone);
    setPill(doc, "#risk-badge", viewModel.riskLevel, viewModel.riskTone);
    setPill(doc, "#confidence-badge", viewModel.confidenceText, viewModel.confidenceTone);

    const confidenceSource = query(doc, "#confidence-source");
    confidenceSource.hidden = !viewModel.confidenceSource;
    confidenceSource.textContent = viewModel.confidenceSource
      ? "Confidence source: " + viewModel.confidenceSource
      : "";

    renderTags(query(doc, "#risk-reasons"), viewModel.riskReasons, "warning");
    renderBlastRadius(doc, viewModel.blastRadius);
    renderExplanationSections(query(doc, "#explanation-list"), viewModel.explanationSections);
    renderTimeline(query(doc, "#timeline-list"), viewModel.timelineEntries);
    renderPreflight(query(doc, "#preflight-list"), viewModel.preflightItems);
    renderPolicySimulator(doc, viewModel.policySimulator);
    renderConfirmation(doc, viewModel.confirmation);
    renderRefusal(doc, viewModel.refusal);
    renderRecovery(doc, viewModel.recovery);
    renderResidual(doc, viewModel.residualNextStep);
  }

  function renderExplanationSections(container, sections) {
    replaceChildren(
      container,
      sections.length
        ? sections.map(function (section) {
            const item = createElement("article", "explanation-item");
            const title = createElement("h3", "explanation-title", section.label);
            const summary = createElement("p", "explanation-summary", section.summary);
            item.appendChild(title);
            item.appendChild(summary);
            item.appendChild(createRefs(section.evidenceRefs));
            return item;
          })
        : [createElement("p", "empty-state", "暂无 explanation card。")]
    );
  }

  function renderTimeline(container, entries) {
    replaceChildren(
      container,
      entries.length
        ? entries.map(function (entry) {
            const item = createElement("li", "timeline-item");
            item.dataset.tone = entry.tone;

            const top = createElement("div", "timeline-top");
            top.appendChild(createElement("h3", "timeline-title", entry.title));
            top.appendChild(createMetaList(entry.meta));

            item.appendChild(top);
            item.appendChild(createElement("p", "timeline-summary", entry.summary));
            if (entry.status) {
              const statusWrap = createElement("div", "timeline-meta");
              statusWrap.appendChild(createMetaChip("status " + entry.status));
              item.appendChild(statusWrap);
            }
            item.appendChild(createRefs(entry.evidenceRefs));
            return item;
          })
        : [createElement("li", "empty-state", "暂无 timeline。")]
    );
  }

  function renderPreflight(container, items) {
    replaceChildren(
      container,
      items.length
        ? items.map(function (item) {
            const node = createElement("li", "check-item");
            node.dataset.status = item.status;

            const head = createElement("div", "check-head");
            head.appendChild(createElement("span", "check-mark"));

            const labelWrap = createElement("div");
            labelWrap.appendChild(createElement("h3", "check-title", item.label));
            labelWrap.appendChild(createElement("p", "meta-text", item.statusLabel));
            head.appendChild(labelWrap);

            node.appendChild(head);
            node.appendChild(createElement("p", "check-summary", item.summary));
            node.appendChild(createRefs(item.evidenceRefs));
            return node;
          })
        : [createElement("li", "empty-state", "暂无 preflight。")]
    );
  }

  function renderBlastRadius(doc, blastRadius) {
    const panel = query(doc, "#blast-radius-panel");
    panel.hidden = !blastRadius.visible;
    setText(doc, "#blast-radius-summary", blastRadius.summary);
    renderDetailGrid(
      query(doc, "#blast-radius-facts"),
      blastRadius.facts,
      "No scoped facts available."
    );
    renderImpactList(
      query(doc, "#blast-radius-impacts"),
      blastRadius.impacts,
      "No impact items available."
    );
    renderTags(
      query(doc, "#blast-radius-paths"),
      blastRadius.protectedPaths,
      "critical",
      "No protected paths flagged"
    );
    renderCompactList(
      query(doc, "#blast-radius-notes"),
      blastRadius.notes,
      "No extra preview notes."
    );
  }

  function renderPolicySimulator(doc, policySimulator) {
    const panel = query(doc, "#policy-simulator-panel");
    panel.hidden = !policySimulator.visible;
    setPill(
      doc,
      "#policy-risk-badge",
      policySimulator.riskLevel,
      policySimulator.riskTone
    );
    setText(doc, "#policy-scope-summary", policySimulator.scopeSummary);
    setText(
      doc,
      "#policy-version",
      "Policy version: " + firstText(policySimulator.policyVersion, "-")
    );
    setText(doc, "#policy-fingerprint", policySimulator.targetFingerprint);
    renderRuleList(
      query(doc, "#policy-rules"),
      policySimulator.matchedRules,
      "No matched rules recorded."
    );
    renderCompactList(
      query(doc, "#policy-denied"),
      policySimulator.deniedBecause,
      "No deny reasons."
    );
    renderCompactList(
      query(doc, "#policy-confirmation-reasons"),
      policySimulator.confirmationBecause,
      "No confirmation reasons."
    );
    setText(
      doc,
      "#policy-safe-alternative",
      policySimulator.safeAlternative
        ? "Safe alternative: " + policySimulator.safeAlternative
        : "No safe alternative provided."
    );
  }

  function renderConfirmation(doc, confirmation) {
    const panel = query(doc, "#confirmation-panel");
    panel.hidden = !confirmation.visible;
    panel.dataset.tone = confirmation.tone;
    setText(doc, "#confirmation-summary", confirmation.summary);
    setText(doc, "#confirmation-text", firstText(confirmation.text, "当前没有确认文本。"));
  }

  function renderRefusal(doc, refusal) {
    const panel = query(doc, "#refusal-panel");
    panel.hidden = !refusal.visible;
    panel.dataset.tone = "blocked";
    setText(doc, "#refusal-reason", refusal.reason || "-");
    setText(
      doc,
      "#safe-alternative",
      refusal.safeAlternative
        ? "安全替代建议：" + refusal.safeAlternative
        : "未提供安全替代建议。"
    );
  }

  function renderRecovery(doc, recovery) {
    const panel = query(doc, "#recovery-panel");
    panel.hidden = !recovery.visible;
    panel.dataset.tone = recovery.visible ? "pending" : "neutral";
    setText(
      doc,
      "#recovery-why",
      recovery.failureType
        ? "Failure type: " + recovery.failureType + ". " + recovery.why
        : recovery.why
    );
    renderCompactList(query(doc, "#recovery-steps"), recovery.safeNextSteps, "暂无 safe next steps。");
    renderCompactList(
      query(doc, "#recovery-diagnostics"),
      recovery.diagnostics,
      "暂无 read-only diagnostics。"
    );
    renderTags(query(doc, "#recovery-flags"), recovery.flags, "warning");
  }

  function renderResidual(doc, residualNextStep) {
    setText(doc, "#residual-summary", residualNextStep.summary || "-");
    renderRefsInto(query(doc, "#residual-refs"), residualNextStep.evidenceRefs);
  }

  function renderDetailGrid(container, rows, fallbackText) {
    replaceChildren(
      container,
      rows.length
        ? rows.map(function (row) {
            const item = createElement("div", "detail-row");
            item.appendChild(createElement("span", "detail-label", row.label));
            item.appendChild(createElement("span", "detail-value", row.value));
            return item;
          })
        : [createElement("p", "empty-state", fallbackText)]
    );
  }

  function renderImpactList(container, items, fallbackText) {
    replaceChildren(
      container,
      items.length
        ? items.map(function (item) {
            const node = createElement("li", "impact-item");
            const top = createElement("div", "timeline-top");
            top.appendChild(createElement("h3", "check-title", item.label));
            top.appendChild(createPrecisionChip(item.precision));
            node.appendChild(top);
            node.appendChild(createElement("p", "check-summary", item.value));
            return node;
          })
        : [createElement("li", "", fallbackText)]
    );
  }

  function renderRuleList(container, items, fallbackText) {
    replaceChildren(
      container,
      items.length
        ? items.map(function (item) {
            const node = createElement("li", "rule-item");
            const top = createElement("div", "timeline-top");
            top.appendChild(createElement("h3", "check-title", item.ruleId));
            top.appendChild(createOutcomeChip(item.outcome, item.tone));
            node.appendChild(top);
            node.appendChild(createElement("p", "check-summary", item.summary));
            return node;
          })
        : [createElement("li", "", fallbackText)]
    );
  }

  function renderCompactList(container, values, fallbackText) {
    replaceChildren(
      container,
      values.length
        ? values.map(function (value) {
            return createElement("li", "", value);
          })
        : [createElement("li", "", fallbackText)]
    );
  }

  function renderTags(container, values, tone, fallbackText) {
    replaceChildren(
      container,
      values.length
        ? values.map(function (value) {
            const tag = createElement("span", "tag", value);
            if (tone) {
              tag.dataset.tone = tone;
            }
            return tag;
          })
        : [createElement("span", "tag", fallbackText || "No flagged reasons")]
    );
  }

  function renderRefsInto(container, refs) {
    replaceChildren(container, createRefs(refs).childNodes);
  }

  function createRefs(refs) {
    const wrapper = createElement("div", "refs");
    uniqueStrings(refs).forEach(function (ref) {
      wrapper.appendChild(createElement("span", "ref-chip", ref));
    });
    return wrapper;
  }

  function createMetaList(values) {
    const wrapper = createElement("div", "timeline-meta");
    values.forEach(function (value) {
      wrapper.appendChild(createMetaChip(value));
    });
    return wrapper;
  }

  function createMetaChip(value) {
    return createElement("span", "meta-chip", value);
  }

  function createPrecisionChip(value) {
    const chip = createElement(
      "span",
      "meta-chip precision-chip",
      firstText(value, "conservative")
    );
    chip.dataset.tone = firstText(value, "conservative") === "bounded" ? "ready" : "pending";
    return chip;
  }

  function createOutcomeChip(label, tone) {
    const chip = createElement("span", "meta-chip precision-chip", firstText(label, "deny"));
    chip.dataset.tone = tone || "neutral";
    return chip;
  }

  function replaceChildren(container, nodes) {
    container.replaceChildren();
    asList(nodes).forEach(function (node) {
      container.appendChild(node);
    });
  }

  function setText(doc, selector, value) {
    query(doc, selector).textContent = firstText(value, "-");
  }

  function setPill(doc, selector, label, tone) {
    const element = query(doc, selector);
    element.textContent = firstText(label, "-");
    element.dataset.tone = tone || "neutral";
  }

  function setBusy(input, button, busy) {
    input.disabled = busy;
    button.disabled = busy;
  }

  function formatStatusLabel(status) {
    const lowered = firstText(status, "unknown").toLowerCase();
    return STATUS_LABELS[lowered] || lowered.replace(/_/g, " ");
  }

  function toneForRisk(riskLevel) {
    const normalized = firstText(riskLevel).toUpperCase();
    if (normalized === "S0") {
      return "ready";
    }
    if (normalized === "S1" || normalized === "S2") {
      return "pending";
    }
    if (normalized === "S3") {
      return "blocked";
    }
    return "neutral";
  }

  function toneForStatus(status) {
    const lowered = firstText(status, "neutral").toLowerCase();
    return STATUS_TONES[lowered] || "neutral";
  }

  function toneForTimeline(status, severity) {
    if (status) {
      return toneForStatus(status);
    }
    return toneForStatus(severity);
  }

  function formatConfidence(value) {
    if (value === null) {
      return "Unavailable";
    }
    return Math.round(value * 100) + "%";
  }

  function formatTimestamp(value) {
    const raw = firstText(value);
    if (!raw) {
      return "";
    }

    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) {
      return raw;
    }

    return parsed.toISOString().replace("T", " ").replace(".000Z", "Z");
  }

  function normalizeConfidence(value) {
    if (value === null || value === undefined || value === "") {
      return null;
    }
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1) {
      return null;
    }
    return parsed;
  }

  function readBoolean(value) {
    return value === true;
  }

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function asList(value) {
    if (Array.isArray(value)) {
      return value;
    }
    if (value === null || value === undefined) {
      return [];
    }
    if (value && typeof value.length === "number" && typeof value !== "string") {
      return Array.prototype.slice.call(value);
    }
    return [value];
  }

  function hasArray(value) {
    return Array.isArray(value);
  }

  function uniqueStrings(value) {
    const seen = [];
    asList(value).forEach(function (entry) {
      const text = firstText(entry);
      if (text && seen.indexOf(text) < 0) {
        seen.push(text);
      }
    });
    return seen;
  }

  function firstText() {
    for (let index = 0; index < arguments.length; index += 1) {
      const value = arguments[index];
      if (value === null || value === undefined) {
        continue;
      }
      const text = String(value).trim();
      if (text) {
        return text;
      }
    }
    return "";
  }

  function query(doc, selector) {
    const node = doc.querySelector(selector);
    if (!node) {
      throw new Error("Missing UI node: " + selector);
    }
    return node;
  }

  function createElement(tagName, className, text) {
    const element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    if (text !== undefined) {
      element.textContent = text;
    }
    return element;
  }

  return {
    SECTION_DEFINITIONS: SECTION_DEFINITIONS,
    boot: boot,
    createFailureViewModel: createFailureViewModel,
    createViewModel: createViewModel,
    buildFallbackPanel: buildFallbackPanel,
  };
});
