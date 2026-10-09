// Saved reviews and demo snapshots. Classic script; no model calls or writes.
const CG = {data: null, at: 0, loading: false, selected: "", error: ""};

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
  const tone = status === "completed" ? "ok" : status === "running" ? "live"
    : status === "partial" || status === "invalid" || status === "failed" ? "bad" : "neutral";
  return uiBadge(esc(status), tone);
}
function cgProgress(d){
  const p = d.progress;
  let h = uiStatBand([{label:"Reviewed", value:cgNumber(p.reviewed)},
    {label:"Pending", value:cgNumber(p.pending), sub:p.pending == null ? "No saved queue" : "Includes current review"},
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
    {caption:"Unreviewed targets have zero counts. Failed stages count as ERROR; they do not count as NOT FOUND."}),
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
  return h;
}
function cgEvaluation(d){
  const e = d.evaluation;
  if (!e) return uiCard("No generated extraction metrics are available for this home.", {title:"Evaluation summary"});
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
  const picker = `<label>Saved review <select aria-label="Saved review" onchange="contractguardSelect(this.value)">
    ${d.reviews.map(r => `<option value="${esc(r.review_id)}"${r.review_id === d.selected_review_id ? " selected" : ""}>${esc(r.name)} · ${esc(r.status)}</option>`).join("")}
    </select></label>`;
  return uiCard(picker + (d.report ? `<div class="r">${renderMarkdown(d.report)}</div>` :
    uiNotice("warn", "This saved review is unavailable.")), {title:"Review report"});
}
VIEWS.reviews = function(){
  if (!CG.loading && Date.now() - CG.at > 5000) deferBg(loadContractguard);
  let h = uiNotice("note", "This page reads saved reviews from the current home. Reviewing requires the demo command or the review tool.");
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
