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
    success: "成功",
    completed: "已完成",
    pending_confirmation: "等待确认",
    refused: "已拒绝",
    failed: "失败",
    cancelled: "已取消",
    confirmed: "已确认",
    skipped: "已跳过",
    unknown: "未知",
    unsupported: "不支持",
  };

  const PREFLIGHT_LABELS = {
    ready: "就绪",
    pending: "等待中",
    blocked: "已阻断",
    not_required: "无需确认",
    not_available: "不可用",
  };

  const LABEL_TRANSLATIONS = {
    "Intent": "意图",
    "Risk level": "风险等级",
    "Preview quality": "预览质量",
    "Target user": "目标用户",
    "Known or predicted home": "已知或预计主目录",
    "Owned files": "归属文件",
    "Sessions and processes": "会话与进程",
    "Home path": "主目录路径",
    "Privilege boundary": "权限边界",
    "base_path": "基础路径",
    "max_results": "最大结果数",
    "max_depth": "最大深度",
    "Fact": "事实",
    "Impact": "影响",
    "Section": "分区",
    "Preflight": "预检",
  };

  const CODE_TRANSLATIONS = {
    query_disk_usage: "磁盘使用查询",
    query_memory_usage: "内存使用查询",
    query_port: "端口查询",
    query_process: "进程查询",
    search_file: "文件搜索",
    file_search: "文件搜索",
    env_probe: "环境探测",
    dangerous_request: "高风险请求",
    unsupported: "不支持",
    unknown: "未知",
    conservative: "保守",
    bounded: "受限",
    allow: "允许",
    deny: "拒绝",
    confirm: "需要确认",
    parse: "解析",
    plan: "计划",
    policy: "策略",
    confirmation: "确认",
    result: "结果",
    evidence: "证据",
    timeline: "时间线",
    info: "信息",
    warning: "警告",
    tool_call: "工具调用",
    ok: "正常",
  };

  const TEXT_TRANSLATIONS = {
    "recognized read-only operation": "已识别为只读操作",
    "Scope is limited to the current guarded request envelope.": "范围限制在当前受控请求内。",
    "Only currently available planner, policy, and result metadata are included.": "仅包含当前可用的计划、策略与结果元数据。",
    "No additional execution or probing is performed for this preview.": "本次预览不会执行额外操作或探测。",
    "No scoped facts available.": "暂无范围内事实。",
    "No impact items available.": "暂无影响项。",
    "No protected paths flagged": "未标记受保护路径",
    "No extra preview notes.": "暂无额外预览说明。",
    "No matched rules recorded.": "暂无命中规则记录。",
    "No deny reasons.": "暂无拒绝原因。",
    "No confirmation reasons.": "暂无确认原因。",
    "No safe alternative provided.": "未提供安全替代建议。",
    "No flagged reasons": "暂无标记原因",
    "Recovery requires confirmation": "恢复操作需要确认",
    "Retry can stay bounded": "重试可保持在受控范围内",
    "Retry not yet safe": "暂不适合重试",
    "The request stayed outside the guarded policy boundary.": "该请求超出受控策略边界。",
    "Rephrase the request as a bounded read-only diagnostic.": "请将请求改写为受限只读诊断。",
    "Submit a fresh guarded request after narrowing the target.": "缩小目标范围后重新提交受控请求。",
    "Inspect the latest evidence and risk reasons.": "检查最新证据与风险原因。",
    "Confirm the target still exists before trying again.": "重试前确认目标仍然存在。",
    "Recovery guidance has been attached for the next bounded request.": "已为下一次受限请求附加恢复建议。",
    "Earlier steps completed; inspect the latest state before follow-up.": "前序步骤已完成；继续前请检查最新状态。",
    "Review the evidence timeline before any follow-up action.": "执行后续动作前先查看证据时间线。",
    "Use a bounded lookup to confirm current target state.": "使用受限查询确认当前目标状态。",
    "Allows recognized read-only requests within bounded scope.": "允许受限范围内的只读请求。",
    "intent:query_disk_usage": "意图：磁盘使用查询",
    "intent:query_memory_usage": "意图：内存使用查询",
    "policy: policy_decision": "策略：策略决策",
    "\"policy: policy_decision\"": "策略：策略决策",
    "awaiting exact confirmation": "等待精确确认",
    "policy denied this request": "策略拒绝该请求",
    "bounded readonly plan": "受限只读计划",
  };

  const PREFLIGHT_TITLE_LABELS = {
    "Intent parsed": "意图已解析",
    "Policy bound": "策略已绑定",
    "Plan ready": "计划就绪",
    "Confirmation gate": "确认门",
    "Environment ready": "环境就绪",
  };

  const TIMELINE_TITLE_LABELS = {
    intent_parsed: "意图解析",
    plan_evaluated: "计划评估",
    policy_decision: "策略决策",
    confirmation_not_required: "无需确认",
    confirmation_state: "确认状态",
    final_result: "最终结果",
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
      statusText.textContent = "正在读取编排器输出…";

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
        statusText.textContent = "控制面已更新";
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
      confidenceText: "不可用",
      confidenceTone: "neutral",
      confidenceSource: "",
      answerSummary: emptyAnswerSummary(),
      blastRadius: normalizeBlastRadius({}),
      policySimulator: normalizePolicySimulator({}),
      explanationSections: [],
      timelineEntries: [
        {
          source: "client",
          title: "渲染失败",
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
    const answerSummary = normalizeAnswerSummary(source, operatorPanel, status, riskLevel);

    return {
      userInput: firstText(operatorPanel.user_input, rawUserInput, "-"),
      status: status,
      statusLabel: formatStatusLabel(status),
      statusTone: toneForStatus(status),
      riskLevel: riskLevel,
      riskTone: toneForRisk(riskLevel),
      riskReasons: uniqueStrings(operatorPanel.risk_reasons).map(localizeText),
      confidenceText: formatConfidence(confidence),
      confidenceTone: confidence === null ? "neutral" : "info",
      confidenceSource: firstText(operatorPanel.confidence_source),
      answerSummary: answerSummary,
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
            ? "识别到可解释意图。"
            : "未发现解析证据。",
          evidence_refs: [],
        },
        {
          key: "policy_bound",
          label: "Policy bound",
          status: firstText(risk.risk_level) ? "ready" : "not_available",
          summary: firstText(risk.risk_level)
            ? "风险决策已绑定到当前请求。"
            : "未发现策略证据。",
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

  function normalizeAnswerSummary(payload, operatorPanel, status, riskLevel) {
    const source = asObject(payload);
    const result = asObject(source.result);
    const execution = asObject(source.execution);
    const finalStatus = firstText(status, result.status, execution.status).toLowerCase();

    if (["success", "completed"].indexOf(finalStatus) < 0) {
      return emptyAnswerSummary();
    }

    const answer = firstText(
      answerTextFromExplanation(source.explanation),
      summarizeResultData(result.data),
      summarizeExecutionResults(execution.results)
    );
    if (!answer || answer === "-") {
      return emptyAnswerSummary();
    }

    const intentName = firstText(asObject(source.intent).intent, asObject(operatorPanel.intent).intent);
    const meta = uniqueStrings([
      formatStatusLabel(finalStatus),
      firstText(riskLevel),
      answerKindLabel(intentName),
    ].filter(Boolean));

    return {
      visible: true,
      text: localizeText(answer),
      meta: meta,
    };
  }

  function emptyAnswerSummary() {
    return {
      visible: false,
      text: "",
      meta: [],
    };
  }

  function answerTextFromExplanation(value) {
    const text = stripEvidenceSuffix(firstText(value));
    if (!text) {
      return "";
    }

    const diskMatch = text.match(/当前共检测到[^\n。]*。/);
    if (diskMatch) {
      return diskMatch[0];
    }

    const memoryMatch = text.match(/当前内存总量[^\n。]*。/);
    if (memoryMatch) {
      return memoryMatch[0];
    }

    const summaryMatch = text.match(/摘要[:：]\s*([^\n]+)/);
    if (summaryMatch) {
      return stripEvidenceSuffix(summaryMatch[1]);
    }

    const lines = text.split(/\r?\n/).map(function (line) {
      return stripEvidenceSuffix(line);
    }).filter(Boolean);
    if (lines.length > 1) {
      return "";
    }
    return lines[0] || text;
  }

  function stripEvidenceSuffix(value) {
    return firstText(value).replace(/\s*\[evidence:[^\]]+\]\s*$/i, "").trim();
  }

  function summarizeResultData(value) {
    const data = asObject(value);
    const direct = firstText(data.answer, data.summary, data.message, data.output);
    if (direct) {
      return answerTextFromExplanation(direct);
    }
    return firstText(summarizeDiskUsageData(data), summarizeMemoryUsageData(data));
  }

  function summarizeExecutionResults(value) {
    const results = asList(value);
    for (let index = 0; index < results.length; index += 1) {
      const item = asObject(results[index]);
      const direct = firstText(item.answer, item.summary, item.result_summary);
      if (direct) {
        return answerTextFromExplanation(direct);
      }
      const dataSummary = summarizeResultData(item.data);
      if (dataSummary) {
        return dataSummary;
      }
    }
    return "";
  }

  function summarizeDiskUsageData(data) {
    const filesystems = asList(data.filesystems).map(asObject).filter(function (item) {
      return Object.keys(item).length > 0;
    });
    if (!filesystems.length) {
      return "";
    }

    let tightest = filesystems[0];
    filesystems.forEach(function (item) {
      if (percentValue(item.use_percent) > percentValue(tightest.use_percent)) {
        tightest = item;
      }
    });

    const count = firstText(data.count, filesystems.length);
    const mountedOn = firstText(tightest.mounted_on, tightest.mount, tightest.mountpoint, "未知挂载点");
    const usePercent = firstText(tightest.use_percent, tightest.use, tightest.percent, "未知");
    const available = firstText(tightest.available, tightest.avail, tightest.free, "未知");
    return "当前共检测到 " + count + " 个挂载点；最紧张的是 " + mountedOn
      + "，使用率 " + usePercent + "，可用空间 " + available + "。";
  }

  function summarizeMemoryUsageData(data) {
    const total = byteCount(data.total_bytes);
    const used = byteCount(data.used_bytes);
    const available = byteCount(data.available_bytes);
    if (total === null || used === null || available === null) {
      return "";
    }

    const usedPercent = formatPercent(data.used_percent);
    let summary = "当前内存总量 " + formatBytes(total)
      + "，已用 " + formatBytes(used)
      + "（" + usedPercent + "），可用 " + formatBytes(available);

    const processes = asList(data.top_processes).map(asObject).filter(function (item) {
      return Object.keys(item).length > 0;
    });
    if (processes.length > 0) {
      const first = processes[0];
      const command = firstText(first.command, first.process_name, "未知进程");
      const pid = firstText(first.pid, "未知");
      const memoryBytes = byteCount(first.memory_bytes);
      summary += "；内存占用最高的进程是 " + command + "（PID " + pid;
      if (memoryBytes !== null) {
        summary += "，占用 " + formatBytes(memoryBytes);
      }
      summary += "）";
    } else if (firstText(data.process_error)) {
      summary += "；进程排行暂不可用：" + firstText(data.process_error);
    }

    return summary + "。";
  }

  function byteCount(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatBytes(value) {
    let number = Number(value);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let index = 0;
    while (number >= 1024 && index < units.length - 1) {
      number /= 1024;
      index += 1;
    }
    if (index === 0) {
      return String(Math.round(number)) + " " + units[index];
    }
    return number.toFixed(1) + " " + units[index];
  }

  function formatPercent(value) {
    const text = firstText(value, "未知");
    if (text === "未知") {
      return text;
    }
    return text.endsWith("%") ? text : text + "%";
  }

  function percentValue(value) {
    const parsed = Number(firstText(value).replace("%", ""));
    return Number.isFinite(parsed) ? parsed : -1;
  }

  function answerKindLabel(intentName) {
    const readonlyIntents = [
      "query_disk_usage",
      "query_memory_usage",
      "query_port",
      "query_process",
      "search_files",
      "file_search",
    ];
    return readonlyIntents.indexOf(firstText(intentName)) >= 0
      ? "只读查询"
      : localizeCode(intentName);
  }

  function normalizeExplanationSections(value) {
    return asList(value).map(function (entry) {
      const item = asObject(entry);
      return {
        key: firstText(item.key, "section"),
        label: localizeLabel(firstText(item.label, item.key, "Section")),
        summary: localizeText(firstText(item.summary, "-")),
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
        localizeText(item.source),
        localizeText(stage),
        formatTimestamp(item.timestamp),
        firstText(item.risk_level) ? "风险等级 " + firstText(item.risk_level) : "",
      ].filter(Boolean);

      return {
        index: index + 1,
        title: localizeTimelineTitle(firstText(item.title, "timeline")),
        summary: localizeText(firstText(item.summary, "-")),
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
        label: localizePreflightLabel(firstText(item.label, item.key, "Preflight")),
        status: status,
        statusLabel: PREFLIGHT_LABELS[status] || formatStatusLabel(status),
        tone: toneForStatus(status),
        summary: localizeText(firstText(item.summary, "-")),
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
      summary: localizeText(firstText(value.summary, "-")),
      facts: asList(value.facts).map(function (entry) {
        const item = asObject(entry);
        return {
          label: localizeLabel(firstText(item.label, "Fact")),
          value: localizeDetailValue(firstText(item.value, "-"), firstText(item.label, "Fact")),
        };
      }),
      impacts: asList(value.impacts).map(function (entry) {
        const item = asObject(entry);
        return {
          label: localizeLabel(firstText(item.label, "Impact")),
          value: localizeText(firstText(item.value, "-")),
          precision: firstText(item.precision, "conservative"),
        };
      }),
      protectedPaths: uniqueStrings(value.protected_paths),
      notes: uniqueStrings(value.notes).map(localizeText),
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
      scopeSummary: localizeText(firstText(value.scope_summary, "-")),
      targetFingerprint: firstText(value.target_fingerprint, "-"),
      matchedRules: asList(value.matched_rules).map(function (entry) {
        const item = asObject(entry);
        return {
          ruleId: firstText(item.rule_id, "unknown.rule"),
          outcome: firstText(item.outcome, "deny"),
          summary: localizeText(firstText(item.summary, "-")),
          tone: firstText(item.outcome) === "allow"
            ? "ready"
            : firstText(item.outcome) === "confirm"
              ? "pending"
              : "blocked",
        };
      }),
      deniedBecause: uniqueStrings(value.denied_because).map(localizeText),
      confirmationBecause: uniqueStrings(value.requires_confirmation_because).map(localizeText),
      safeAlternative: localizeText(firstText(value.safe_alternative)),
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
      summary: localizeText(firstText(value.summary, "当前请求无确认依据。")),
      text: localizeText(firstText(value.text)),
      evidenceRefs: uniqueStrings(value.evidence_refs),
    };
  }

  function normalizeRefusal(value) {
    return {
      visible: Boolean(value.is_refused),
      reason: localizeText(firstText(value.reason, "-")),
      safeAlternative: localizeText(firstText(value.safe_alternative)),
      evidenceRefs: uniqueStrings(value.evidence_refs),
    };
  }

  function normalizeRecovery(value) {
    const failureType = firstText(value.failure_type);
    return {
      visible: Boolean(value.available),
      failureType: localizeText(failureType),
      why: localizeText(firstText(value.why_it_failed, "-")),
      safeNextSteps: uniqueStrings(value.safe_next_steps).map(localizeText),
      diagnostics: uniqueStrings(value.suggested_readonly_diagnostics).map(localizeText),
      flags: [
        value.requires_confirmation_for_recovery
          ? "Recovery requires confirmation"
          : "",
        value.can_retry_safely ? "Retry can stay bounded" : "Retry not yet safe",
      ].filter(Boolean).map(localizeText),
    };
  }

  function normalizeResidual(value) {
    return {
      summary: localizeText(firstText(value.summary, "-")),
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
      ? "置信度来源：" + viewModel.confidenceSource
      : "";

    renderTags(query(doc, "#risk-reasons"), viewModel.riskReasons, "warning");
    renderAnswerSummary(doc, viewModel.answerSummary);
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

  function renderAnswerSummary(doc, answerSummary) {
    const summary = answerSummary || emptyAnswerSummary();
    const panel = query(doc, "#answer-summary-panel");
    panel.hidden = !summary.visible;
    setText(doc, "#answer-summary-text", summary.text);
    renderTags(query(doc, "#answer-summary-meta"), summary.meta || [], "info");
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
        : [createElement("p", "empty-state", "暂无解释卡。")]
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
              statusWrap.appendChild(createMetaChip("状态 " + formatStatusLabel(entry.status)));
              item.appendChild(statusWrap);
            }
            item.appendChild(createRefs(entry.evidenceRefs));
            return item;
          })
        : [createElement("li", "empty-state", "暂无时间线。")]
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
        : [createElement("li", "empty-state", "暂无预检项。")]
    );
  }

  function renderBlastRadius(doc, blastRadius) {
    const panel = query(doc, "#blast-radius-panel");
    panel.hidden = !blastRadius.visible;
    setText(doc, "#blast-radius-summary", blastRadius.summary);
    renderDetailGrid(
      query(doc, "#blast-radius-facts"),
      blastRadius.facts,
      "暂无范围内事实。"
    );
    renderImpactList(
      query(doc, "#blast-radius-impacts"),
      blastRadius.impacts,
      "暂无影响项。"
    );
    renderTags(
      query(doc, "#blast-radius-paths"),
      blastRadius.protectedPaths,
      "critical",
      "未标记受保护路径"
    );
    renderCompactList(
      query(doc, "#blast-radius-notes"),
      blastRadius.notes,
      "暂无额外预览说明。"
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
      "策略版本：" + firstText(policySimulator.policyVersion, "-")
    );
    setText(doc, "#policy-fingerprint", policySimulator.targetFingerprint);
    renderRuleList(
      query(doc, "#policy-rules"),
      policySimulator.matchedRules,
      "暂无命中规则记录。"
    );
    renderCompactList(
      query(doc, "#policy-denied"),
      policySimulator.deniedBecause,
      "暂无拒绝原因。"
    );
    renderCompactList(
      query(doc, "#policy-confirmation-reasons"),
      policySimulator.confirmationBecause,
      "暂无确认原因。"
    );
    setText(
      doc,
      "#policy-safe-alternative",
      policySimulator.safeAlternative
        ? "安全替代建议：" + policySimulator.safeAlternative
        : "未提供安全替代建议。"
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
        ? "失败类型：" + recovery.failureType + "。" + recovery.why
        : recovery.why
    );
    renderCompactList(query(doc, "#recovery-steps"), recovery.safeNextSteps, "暂无安全下一步。");
    renderCompactList(
      query(doc, "#recovery-diagnostics"),
      recovery.diagnostics,
      "暂无只读诊断。"
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
          item.appendChild(createElement("span", "detail-label", localizeLabel(row.label)));
          item.appendChild(createElement("span", "detail-value", localizeText(row.value)));
          return item;
        })
        : [createElement("p", "empty-state", localizeText(fallbackText))]
    );
  }

  function renderImpactList(container, items, fallbackText) {
    replaceChildren(
      container,
      items.length
        ? items.map(function (item) {
            const node = createElement("li", "impact-item");
            const top = createElement("div", "timeline-top");
            top.appendChild(createElement("h3", "check-title", localizeLabel(item.label)));
            top.appendChild(createPrecisionChip(item.precision));
            node.appendChild(top);
            node.appendChild(createElement("p", "check-summary", localizeText(item.value)));
            return node;
          })
        : [createElement("li", "", localizeText(fallbackText))]
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
            node.appendChild(createElement("p", "check-summary", localizeText(item.summary)));
            return node;
          })
        : [createElement("li", "", localizeText(fallbackText))]
    );
  }

  function renderCompactList(container, values, fallbackText) {
    replaceChildren(
      container,
      values.length
        ? values.map(function (value) {
            return createElement("li", "", localizeText(value));
          })
        : [createElement("li", "", localizeText(fallbackText))]
    );
  }

  function renderTags(container, values, tone, fallbackText) {
    replaceChildren(
      container,
      values.length
        ? values.map(function (value) {
            const tag = createElement("span", "tag", localizeText(value));
            if (tone) {
              tag.dataset.tone = tone;
            }
            return tag;
          })
        : [createElement("span", "tag", localizeText(fallbackText || "No flagged reasons"))]
    );
  }

  function renderRefsInto(container, refs) {
    replaceChildren(container, createRefs(refs).childNodes);
  }

  function createRefs(refs) {
    const wrapper = createElement("div", "refs");
    uniqueStrings(refs).forEach(function (ref) {
      wrapper.appendChild(createElement("span", "ref-chip", localizeText(ref)));
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
    return createElement("span", "meta-chip", localizeText(value));
  }

  function createPrecisionChip(value) {
    const chip = createElement(
      "span",
      "meta-chip precision-chip",
      localizeText(firstText(value, "conservative"))
    );
    chip.dataset.tone = firstText(value, "conservative") === "bounded" ? "ready" : "pending";
    return chip;
  }

  function createOutcomeChip(label, tone) {
    const chip = createElement("span", "meta-chip precision-chip", localizeText(firstText(label, "deny")));
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

  function localizeLabel(value) {
    const text = firstText(value);
    return LABEL_TRANSLATIONS[text] || localizeText(text);
  }

  function localizeCode(value) {
    const text = firstText(value);
    const lowered = text.toLowerCase();
    return CODE_TRANSLATIONS[text] || STATUS_LABELS[lowered] || PREFLIGHT_LABELS[lowered] || text;
  }

  function localizeDetailValue(value, label) {
    const normalizedLabel = firstText(label).toLowerCase();
    if (normalizedLabel === "intent") {
      return localizeCode(value);
    }
    return localizeText(value);
  }

  function localizeTimelineTitle(value) {
    const text = firstText(value);
    return TIMELINE_TITLE_LABELS[text] || localizeText(text);
  }

  function localizePreflightLabel(value) {
    const text = firstText(value);
    return PREFLIGHT_TITLE_LABELS[text] || localizeText(text);
  }

  function localizeText(value) {
    const text = firstText(value);
    if (!text) {
      return "";
    }

    const exact = TEXT_TRANSLATIONS[text] || CODE_TRANSLATIONS[text];
    if (exact) {
      return exact;
    }

    const intentScopeMatch = text.match(/^Intent scope for (.+)\.$/);
    if (intentScopeMatch) {
      return "意图范围：" + localizeCode(intentScopeMatch[1]) + "。";
    }

    const policyVersionMatch = text.match(/^Policy version: (.+)$/);
    if (policyVersionMatch) {
      return "策略版本：" + policyVersionMatch[1];
    }

    const safeAlternativeMatch = text.match(/^Safe alternative: (.+)$/);
    if (safeAlternativeMatch) {
      return "安全替代建议：" + localizeText(safeAlternativeMatch[1]);
    }

    const failureTypeMatch = text.match(/^Failure type: ([^.]+)\. (.+)$/);
    if (failureTypeMatch) {
      return "失败类型：" + localizeText(failureTypeMatch[1]) + "。" + localizeText(failureTypeMatch[2]);
    }

    return text
      .replace(/最终结果为 success。/g, "最终结果为成功。")
      .replace(/为 success/g, "为成功")
      .replace(/recognized read-only operation/g, "已识别为只读操作")
      .replace(/query_disk_usage/g, "磁盘使用查询")
      .replace(/query_memory_usage/g, "内存使用查询")
      .replace(/policy_decision/g, "策略决策")
      .replace(/evidence_chain/g, "证据链")
      .replace(/环境状态：ok/g, "环境状态：正常")
      .replace(/requires_write=False/g, "需要写入：否")
      .replace(/requires_write=True/g, "需要写入：是")
      .replace(/\bnot_required\b/g, "无需确认")
      .replace(/\bpending_confirmation\b/g, "等待确认")
      .replace(/\bsuccess\b/g, "成功")
      .replace(/\bready\b/g, "就绪")
      .replace(/\bfailed\b/g, "失败")
      .replace(/\brefused\b/g, "已拒绝");
  }

  function formatStatusLabel(status) {
    const lowered = firstText(status, "unknown").toLowerCase();
    return STATUS_LABELS[lowered] || PREFLIGHT_LABELS[lowered] || localizeText(lowered.replace(/_/g, " "));
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
      return "不可用";
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
