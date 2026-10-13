// Saved reports, human decisions and demonstration snapshots. Classic script.
const CG = {data: null, at: 0, loading: false, selected: "", error: "", drafts: {}, busy: false, decisionError: ""};

async function loadContractguard(background = false){
  if (CG.loading) return;
  CG.loading = true;
  const selected = CG.selected;
  try {
    const query = selected ? `?review_id=${encodeURIComponent(selected)}` : "";
    const res = await fetch(`/api/contractguard${query}`, background ? {headers: BG} : undefined);
    if (!res.ok) throw new Error("Saved reviews could not be read.");
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    if (selected === CG.selected){ CG.data = data; CG.at = Date.now(); CG.error = ""; }
  } catch(e){ CG.error = "Saved reviews are temporarily unavailable."; CG.at = Date.now(); }
  finally { CG.loading = false; }
  if (activeView === "reviews") render();
}
function contractguardSelect(id){
  CG.selected = id; CG.at = 0; CG.data = null;
  loadContractguard(false);
}
const cgNumber = n => n == null ? "—" : esc(Number(n).toLocaleString());
const cgScore = n => n == null ? "—" : esc(Number(n).toFixed(4));
function cgStatus(status){
  const tone = status === "completed" ? "ok" : status === "pending_review" || status === "approved" ? "warn" : status === "running" ? "live"
    : status === "partial" || status === "invalid" || status === "failed" ? "bad" : "neutral";
  return uiBadge(esc(status), tone);
}
function cgProgress(d){
  const p = d.progress;
  let h = uiStatBand([{label:"Stored reviews", value:cgNumber(p.reviewed)},
    {label:"Pending", value:cgNumber(p.pending), sub:p.pending == null ? "No saved queue" : "Awaiting review or storage"},
    {label:"Incomplete", value:cgNumber(p.failed)}, {label:"Status", value:cgStatus(p.status)}]);
  h += uiCard(`<p>Current contract: ${esc(p.current || "No review is running.")}</p>` +
    uiTable(["Contract", "Status"], p.contracts.map(r => [esc(r.name), cgStatus(r.status)]),
      {empty:"No saved reviews exist in this home."}), {title:"Review progress"});
  return h;
}
function cgRisk(d){
  return uiCard(uiTable(["Clause", "HIGH", "MEDIUM", "LOW", "NOT FOUND", "ERROR"],
    Object.entries(d.risk_matrix).map(([name, values]) => [esc(name),
      ...["HIGH", "MEDIUM", "LOW", "NOT_FOUND", "ERROR"].map(k => cgNumber(values[k]))]),
    {caption:"Pending reports use generated assessments; approved reports use accepted assessments. Rejected reports are excluded. Failed stages count as ERROR; they do not count as NOT FOUND."}),
    {title:"Risk matrix"});
}
function cgMemory(d){
  const counts = d.memory_counts;
  let h = counts ? uiStatBand([{label:"Semantic entries", value:cgNumber(counts.semantic_entries)},
    {label:"Review episodes", value:cgNumber(counts.episodic_entries)},
    {label:"Clause procedures", value:cgNumber(counts.procedural_skills)}]) :
    uiNotice("warn", "Local memory counts are unavailable.");
  h += uiCard(uiTable(["Reviewed", "Semantic", "Episodic", "Procedures", "Retrieval events", "Retrieved items"],
    d.memory_growth.map(r => [r.reviewed, r.semantic_entries, r.episodic_entries,
      r.procedural_skills, r.retrieval_events, r.retrieved_items].map(cgNumber)),
    {empty:"No memory progression was recorded. Run the demo to capture snapshots.",
      caption:"These snapshots record state after each review. Memory growth does not establish quality improvement."}),
    {title:"Memory growth"});
  const records = d.domain_memory || [];
  if (records.length){
    h += uiCard(uiTable(["Clause", "Kind", "State", "Role / policy", "Revision", "Actions"], records.map(r => {
      const a = r.applicability;
      const actions = r.state === "candidate" ? uiButton("Approve", {onclick:`contractguardMemoryDialog('${esc(r.id)}','approve')`}) :
        r.state === "approved" ? uiButton("Revoke", {level:"destructive", onclick:`contractguardMemoryDialog('${esc(r.id)}','revoke')`}) + " " +
          uiButton("Replace", {onclick:`contractguardMemoryDialog('${esc(r.id)}','supersede')`}) : "";
      const scoped = ["review","correction","experience"].includes(r.kind);
      return [esc(r.clause_type), esc(r.kind), cgStatus(r.state), esc(`${a.role || (scoped ? "Unknown" : "General")} / ${a.policy_version || (scoped ? "Unknown policy" : "No policy constraint")}`),
        cgNumber(r.revision), `<details><summary>Inspect</summary><pre>${esc(JSON.stringify(r, null, 2))}</pre></details>` + actions];
    }), {caption:"Only applicable approved records guide a review. Older records remain inspectable; lifecycle decisions make no model call."}),
      {title:"Scoped memory", action:uiButton("Propose guidance", {onclick:"contractguardMemoryDialog('','propose')"})});
  }
  return h;
}
function cgEvaluation(d){
  const e = d.evaluation;
  const q = d.quality_report;
  if (q) return uiCard(`<p>Execution: ${esc(q.execution_kind)} · ${esc(q.execution_status || q.engineering)}.</p>
    <p>Quality: ${esc(q.quality.status)}. ${esc((q.quality.reasons || []).join(" "))}</p>
    <p>History: ${esc(q.history_origin)}. Human annotation required: ${q.human_annotation_required ? "yes" : "no"}.</p>` +
    uiTable(["Metric", "Value", "Scored support"], Object.entries(q.metrics).filter(([,v]) => v && typeof v === "object" && "support" in v)
      .map(([name,v]) => [esc(name), cgScore(v.value == null ? v.f1 : v.value), cgNumber(v.support)]),
      {caption:"Unscored criteria remain unverified. Scripted scores verify engineering behavior."}) +
    uiTable(["Judge criterion", "Mean opinion", "Support", "Calibration"], Object.entries(q.metrics.judge_opinions || {})
      .map(([name,v]) => [esc(name), cgScore(v.mean), cgNumber(v.support), esc(v.calibration)]),
      {empty:"No model judge was requested."}), {title:"Upgrade evaluation"});
  if (!e) return uiCard("Quality: not evaluated. No extraction metrics are available for this home.", {title:"Evaluation summary"});
  const t = e.telemetry;
  return uiCard(uiTable(["Aggregation", "Precision", "Recall", "F1"],
    ["micro", "macro"].map(k => [esc(k), cgScore(e[k].precision), cgScore(e[k].recall), cgScore(e[k].f1)])) +
    uiStatBand([{label:"Annotated contracts", value:cgNumber(e.contracts)},
      {label:"Memory mode", value:esc(d.mode)}, {label:"Mean seconds", value:cgScore(t.mean_latency_seconds)},
      {label:"Input tokens", value:cgNumber(t.input_tokens)}, {label:"Output tokens", value:cgNumber(t.output_tokens)},
      {label:"Model calls", value:cgNumber(t.llm_calls)}]) +
    `<p>Scored targets: ${e.scored_targets.map(esc).join(", ")}.</p>
     <p>Matching: ${esc(e.matcher.rule)} at ${cgScore(e.matcher.threshold)}.
     Extraction failures: ${cgNumber(e.extraction_failed_pairs)}.
     Risk failures: ${cgNumber(e.risk_failed_pairs)}.
     Estimated USD: ${t.estimated_cost_usd == null ? "unavailable" : cgScore(t.estimated_cost_usd)}.</p>
     <p>Risk quality is not scored by CUAD. These results describe this saved sample.</p>`, {title:"Evaluation summary"});
}
function cgReport(d){
  if (!d.reviews.length) return uiCard("No report has been saved.", {title:"Review report"});
  const picker = `<label class="fld">Saved review <select aria-label="Saved review" onchange="contractguardSelect(this.value)">
    ${d.reviews.map(r => `<option value="${esc(r.review_id)}"${r.review_id === d.selected_review_id ? " selected" : ""}>${esc(r.name)} · ${esc(r.status)} · ${esc(r.review_kind)}</option>`).join("")}
    </select></label>`;
  const human = d.human_review;
  let actions = "";
  if (human && human.status === "pending_review"){
    actions = uiNotice("warn", "This report awaits human review. Confirmed findings will enter review memory.") +
      uiButton("Confirm and save", {level:"primary", onclick:"contractguardReview('confirm')"}) + " " +
      uiButton("Correct and confirm", {onclick:"contractguardReview('correct')"}) + " " +
      uiButton("Reject", {level:"destructive", onclick:"contractguardReview('reject')"});
  } else if (human && human.status === "approved"){
    actions = uiNotice("warn", "Your decision was saved. Retry to finish storing the approved findings.") +
      uiButton("Retry saving", {level:"primary", onclick:"contractguardRetry()", attrs:CG.busy ? "disabled" : ""});
  } else if (human && human.decision){
    actions = uiNotice(human.status === "completed" ? "ok" : "note",
      `${esc(human.status)} · ${esc(human.reviewer)} · ${esc(human.timestamp)}<p>${esc(human.note)}</p>`);
  }
  if (human) actions += `<details><summary>Source contract</summary><div class="mdcode"><pre>${esc(human.source_text)}</pre></div></details>`;
  if (human && human.review_context){
    actions += `<details><summary>Review context and required checks</summary><pre>${esc(JSON.stringify(human.review_context, null, 2))}</pre>` +
      `<pre>${esc(JSON.stringify(human.working_context, null, 2))}</pre></details>`;
  }
  const generated = d.report ? `<div class="r">${renderMarkdown(d.report)}</div>` :
    uiNotice("warn", "This saved review is unavailable.");
  const report = human && human.approved_report ?
    `<h3>Approved findings</h3><div class="r">${renderMarkdown(human.approved_report)}</div>
    <details><summary>Original generated report</summary>${generated}</details>` : generated;
  return uiCard(picker + actions + report, {title:"Review report"});
}

function contractguardReview(decision){
  const h = CG.data && CG.data.human_review;
  if (!h || h.status !== "pending_review" || CG.busy) return;
  let draft = CG.drafts[h.review_id];
  if (!draft || draft.revision !== h.revision){
    draft = {review_id:h.review_id, revision:h.revision, decision, reviewer:"", note:"", source_text:h.source_text,
      findings:h.findings.map(f => ({clause_type:f.clause_type, found:f.status === "found",
        evidence:f.extraction.spans.map(span => {
          let start = -1, occurrence = -1;
          do { start = h.source_text.indexOf(span.text, start + 1); occurrence++; } while(start >= 0 && start < span.start);
          return {text:span.text, occurrence};
        }), risk_level:f.risk ? f.risk.risk_level : "LOW",
        reasoning:f.risk ? f.risk.reasoning : f.extraction.rationale,
        recommendation:f.risk ? f.risk.recommendation : "Add appropriate protections."}))};
    CG.drafts[h.review_id] = draft;
  }
  draft.decision = decision;
  contractguardDialog(draft);
}
function contractguardDialog(draft){
  let fields = `<label class="fld">Reviewer label <input id="cg-reviewer" maxlength="200" value="${esc(draft.reviewer)}"
    oninput="contractguardField('reviewer',this.value)"></label>
    <label class="fld">Review note <textarea class="editor" maxlength="4000" oninput="contractguardField('note',this.value)">${esc(draft.note)}</textarea></label>`;
  if (draft.decision === "correct") fields += draft.findings.map((f,i) => uiCard(
    `<label class="fld">Clause presence <select aria-label="${esc(f.clause_type)} presence" onchange="contractguardFinding(${i},'found',this.value === 'true')">
      <option value="true"${f.found ? " selected" : ""}>Found</option><option value="false"${!f.found ? " selected" : ""}>Not found</option></select></label>
    <p>Found clauses require exact source quotations. For repeated text, enter its zero-based occurrence.</p>` +
    f.evidence.map((quote,j) => `<label class="fld">Source quotation ${j+1}<textarea class="editor" maxlength="4000"
      oninput="contractguardQuote(${i},${j},'text',this.value)">${esc(quote.text)}</textarea></label>
      <label class="fld">Occurrence <input type="number" min="0" value="${quote.occurrence == null ? "" : esc(quote.occurrence)}"
      oninput="contractguardQuote(${i},${j},'occurrence',this.value === '' ? null : Number(this.value))"></label>` +
      uiButton("Remove quotation", {level:"tertiary", onclick:`contractguardEvidence(${i},${j})`})).join("") +
    uiButton("Add quotation", {level:"tertiary", onclick:`contractguardEvidence(${i},-1)`}) +
    `<label class="fld">Risk level <select aria-label="${esc(f.clause_type)} risk" onchange="contractguardFinding(${i},'risk_level',this.value)">
      ${["HIGH","MEDIUM","LOW"].map(level => `<option${level === f.risk_level ? " selected" : ""}>${level}</option>`).join("")}</select></label>
    <label class="fld">Reasoning <textarea class="editor" maxlength="4000" oninput="contractguardFinding(${i},'reasoning',this.value)">${esc(f.reasoning)}</textarea></label>
    <label class="fld">Recommendation <textarea class="editor" maxlength="4000" oninput="contractguardFinding(${i},'recommendation',this.value)">${esc(f.recommendation)}</textarea></label>`,
    {title:esc(f.clause_type)})).join("");
  openDialog(`<h2>${draft.decision === "reject" ? "Reject report" : draft.decision === "correct" ? "Correct and confirm" : "Confirm and save"}</h2>
    <details><summary>Source contract</summary><div class="mdcode"><pre>${esc(draft.source_text)}</pre></div></details>
    <div id="cg-decision-error" role="alert"></div><fieldset id="cg-review-fields" style="border:none;min-width:0">${fields}</fieldset>
    <div class="dialog-foot">${uiButton("Cancel", {onclick:"closeDialog()"})}
    ${uiButton(draft.decision === "reject" ? "Reject report" : "Save decision", {level:"primary", onclick:"contractguardSubmit()"})}</div>`, {wide:true});
  CG.draft = draft;
}
function contractguardField(key,value){ if (!CG.busy) CG.draft[key] = value; }
function contractguardFinding(i,key,value){ if (!CG.busy) CG.draft.findings[i][key] = value; }
function contractguardQuote(i,j,key,value){ if (!CG.busy) CG.draft.findings[i].evidence[j][key] = value; }
function contractguardEvidence(i,j){
  if (CG.busy) return;
  const evidence = CG.draft.findings[i].evidence;
  if (j < 0 && evidence.length < 20) evidence.push({text:"", occurrence:null});
  else if (j >= 0) evidence.splice(j,1);
  contractguardDialog(CG.draft);
}
async function contractguardSubmit(){
  if (CG.busy || !CG.draft) return;
  const d = CG.draft;
  const payload = {review_id:d.review_id, revision:d.revision, decision:d.decision, reviewer:d.reviewer, note:d.note};
  if (d.decision === "correct") payload.findings = d.findings.map(f => ({...f,
    evidence:f.found ? f.evidence : [], risk_level:f.found ? f.risk_level : null,
    recommendation:f.found ? f.recommendation : null}));
  await contractguardSave(payload, true);
}
async function contractguardRetry(){
  if (!CG.busy && CG.data.human_review) await contractguardSave(CG.data.human_review.submission, false);
}
async function contractguardSave(payload, dialog){
  CG.busy = true;
  const currentDialog = dialog ? document.querySelector("dialog[open]") : null;
  if (currentDialog) currentDialog.querySelectorAll("button,fieldset").forEach(el => el.disabled = true);
  try {
    const res = await fetch("/api/contractguard/review", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    if (!res.ok) throw new Error("The review decision could not be saved.");
    const result = await res.json();
    if (result.error) throw new Error(result.error);
    delete CG.drafts[payload.review_id];
    if (currentDialog && currentDialog.open) currentDialog.close();
    CG.decisionError = "";
    CG.at = 0;
  } catch(e){
    if (currentDialog && currentDialog.open) currentDialog.querySelector("#cg-decision-error").innerHTML = uiNotice("failed",esc(e.message));
    else CG.decisionError = e.message;
  } finally {
    CG.busy = false;
    if (currentDialog) currentDialog.querySelectorAll("button,fieldset").forEach(el => el.disabled = false);
  }
  await loadContractguard(false);
}

function contractguardMemoryDialog(id, action){
  if (CG.busy) return;
  const record = (CG.data.domain_memory || []).find(r => r.id === id);
  if (action !== "propose" && !record) return;
  CG.memoryDecision = {id, action, revision:record ? record.revision : null};
  let fields = `<label class="fld">Reviewer label <input id="cg-memory-reviewer" maxlength="200"></label>
    <label class="fld">Reason <textarea class="editor" id="cg-memory-reason" maxlength="4000"></textarea></label>`;
  if (action === "propose"){
    const clauses = [...new Set(CG.data.domain_memory.map(r => r.clause_type))];
    fields += `<label class="fld">Guidance kind <select id="cg-memory-kind"><option value="policy">Organizational policy</option><option value="experience">Experience rule</option></select></label>
      <label class="fld">Clause <select id="cg-memory-clause">${clauses.map(c => `<option>${esc(c)}</option>`).join("")}</select></label>
      <label class="fld">Guidance <textarea class="editor" id="cg-memory-guidance" maxlength="40000"></textarea></label>`;
    for (const key of ["project","contract_type","represented_party","role","jurisdiction","policy_version"])
      fields += `<label class="fld">${esc(key.replaceAll("_"," "))} <input id="cg-memory-${key}" maxlength="200"></label>`;
    fields += `<label class="fld">Valid from <input type="date" id="cg-memory-from"></label>
      <label class="fld">Valid until <input type="date" id="cg-memory-until"></label>
      <label class="fld">Regression case identities (comma separated; required for experience rules)
      <input id="cg-memory-regressions" maxlength="4000"></label>`;
  } else if (action === "supersede"){
    const candidates = CG.data.domain_memory.filter(r => r.state === "candidate" && r.kind === record.kind && r.clause_type === record.clause_type);
    fields += `<label class="fld">Replacement candidate <select id="cg-memory-replacement">${candidates.map(r => `<option value="${esc(r.id)}">${esc(r.id.slice(0,12))}</option>`).join("")}</select></label>`;
  }
  openDialog(`<h2>${esc(action)} memory</h2><div id="cg-memory-error" role="alert"></div>
    <fieldset id="cg-memory-fields">${fields}</fieldset><div class="dialog-foot">${uiButton("Cancel", {onclick:"closeDialog()"})}
    ${uiButton("Save decision", {level:"primary", onclick:"contractguardMemorySubmit()"})}</div>`, {wide:true});
}
async function contractguardMemorySubmit(){
  if (CG.busy || !CG.memoryDecision) return;
  const d = CG.memoryDecision;
  const get = key => document.getElementById(`cg-memory-${key}`).value.trim();
  let payload = {action:d.action, reviewer:get("reviewer"), reason:get("reason")};
  if (d.action === "propose"){
    const context = {};
    for (const key of ["project","contract_type","represented_party","role","jurisdiction","policy_version"])
      if (get(key)) context[key] = get(key);
    payload = {...payload, kind:get("kind"), clause_type:get("clause"), guidance:get("guidance"), context,
      valid_from:get("from") || null, valid_until:get("until") || null,
      regression_cases:get("regressions").split(",").map(v => v.trim()).filter(Boolean)};
  } else {
    payload = {...payload, memory_id:d.id, revision:d.revision};
    if (d.action === "supersede") payload.replacement_id = get("replacement");
  }
  const dialog = document.querySelector("dialog[open]");
  CG.busy = true;
  if (dialog) dialog.querySelectorAll("button,fieldset").forEach(el => el.disabled = true);
  try {
    const res = await fetch("/api/contractguard/memory", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Memory decision could not be saved.");
    if (dialog && dialog.open) dialog.close();
    CG.at = 0;
  } catch(e){
    if (dialog && dialog.open) dialog.querySelector("#cg-memory-error").innerHTML = uiNotice("failed",esc(e.message));
  } finally {
    CG.busy = false;
    if (dialog) dialog.querySelectorAll("button,fieldset").forEach(el => el.disabled = false);
  }
  await loadContractguard(false);
}

VIEWS.reviews = function(){
  if (!CG.loading && Date.now() - CG.at > 5000) deferBg(loadContractguard);
  let h = uiNotice("note", "Generate reports with the review tool, then confirm, correct or reject them here. Confirmation stores approved findings without a model call.");
  if (CG.decisionError) h += uiNotice("failed", esc(CG.decisionError));
  if (CG.error) h += uiNotice("failed", esc(CG.error));
  const d = CG.data;
  if (!d) return h + uiCard("Reading saved reviews…");
  if (d.execution_kind === "offline_smoke") h += uiNotice("warn",
    "Offline scripted demonstration. Scores verify wiring; live quality and memory improvement remain unmeasured.");
  if (d.execution_kind === "live") h += uiNotice("note", "Live sample results do not establish general legal accuracy.");
  for (const error of d.errors) h += uiNotice("warn", esc(error));
  if (d.truncated) h += uiNotice("warn", "This view is limited to 200 saved reviews.");
  return h + cgProgress(d) + cgRisk(d) + cgMemory(d) + cgEvaluation(d) + cgReport(d);
};
