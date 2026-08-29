"""
API layer.

Every endpoint that touches case data does three things in the same order:
check the caller's permission, write an audit entry, then answer. There is no
path through this file that returns case data without both.
"""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import case_intake, pdf_report
from .dashboard import build as build_dashboard
from .auth import (Principal, current_user, require, audit, read_audit,
                   verify_chain, encryption_status)
from .graph.build import build_graph
from .graph.analytics import NetworkAnalytics
from .graph.anomaly import AnomalyDetector
from .graph.store import (CypherExportStore, JsonExportStore, export_graph,
                          describe_backends)
from .integrations import status as integrations_status, fetch as integrations_fetch
from .nlq import QueryParser
from .pipeline.ocr import capabilities as ocr_capabilities
from .report import ReportBuilder
from .summarise import default_summariser, status as summariser_status

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(title="Criminal Network Analysis System",
              description="AI-assisted analysis of multi-source investigation data. "
                          "All data in this deployment is synthetic.",
              version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


class State:
    graph = None
    analytics = None
    findings = None
    parser = None
    reports = None
    summariser = None


S = State()


def load(force=False):
    if S.graph is None or force:
        S.graph = build_graph()
        S.analytics = NetworkAnalytics(S.graph)
        S.findings = AnomalyDetector(S.graph, S.analytics).run_all()
        S.parser = QueryParser(S.graph)
        S.summariser = default_summariser()
        S.reports = ReportBuilder(S.graph, S.analytics, S.findings, S.summariser)
    return S


@app.on_event("startup")
def _startup():
    load()


# ------------------------------------------------------------------ helpers

def serialise_graph(cg, analytics, node_filter=None, min_confidence=0.0,
                    edge_types=None, redact=False):
    infl = {r["id"]: r for r in analytics.influence()}
    comm = analytics.communities()["assignment"]
    nodes, edges = [], []
    for n, d in cg.G.nodes(data=True):
        if node_filter is not None and n not in node_filter:
            continue
        item = {
            "id": n, "label": d.get("label"), "type": d.get("type"),
            "community": comm.get(n),
            "influence": infl.get(n, {}).get("score"),
            "rank": infl.get(n, {}).get("rank"),
            "prior_cases": d.get("prior_cases"),
            "lat": d.get("lat"), "lon": d.get("lon"),
            "aliases": d.get("aliases", []),
            "phones": ["[redacted]"] * len(d.get("phones", [])) if redact else d.get("phones", []),
            "accounts": [] if redact else d.get("accounts", []),
            "record_checked": "prior_cases" in d,
        }
        nodes.append(item)
    keep = {n["id"] for n in nodes}
    for a, b, d in cg.G.edges(data=True):
        if a not in keep or b not in keep:
            continue
        if d["confidence"] < min_confidence:
            continue
        if edge_types and not set(d["types"]) & set(edge_types):
            continue
        edges.append({
            "source": a, "target": b, "types": d["types"],
            "weight": round(d["weight"], 3), "confidence": d["confidence"],
            "observations": d["observations"],
            "first_seen": d["first_seen"], "last_seen": d["last_seen"],
            "independent_sources": sorted({s["source_type"] for s in d["sources"]}),
        })
    return {"nodes": nodes, "edges": edges}


# ------------------------------------------------------------------ routes

@app.get("/api/me")
def me(user: Principal = Depends(current_user)):
    return user.dict()


@app.get("/api/stats")
def stats(user: Principal = Depends(current_user)):
    require(user, "graph:read")
    audit(user, "VIEW_STATS")
    s = load()
    out = s.graph.stats()
    out["findings"] = len(s.findings)
    out["high_severity"] = len([f for f in s.findings if f["severity"] == "high"])
    out["communities"] = s.analytics.communities()["count"]
    stamps = [d[k] for _, _, d in s.graph.G.edges(data=True)
              for k in ("first_seen", "last_seen") if d.get(k)]
    out["timeline"] = {"min": min(stamps)[:10] if stamps else None,
                       "max": max(stamps)[:10] if stamps else None,
                       "undated_edges": sum(1 for _, _, d in s.graph.G.edges(data=True)
                                            if not d.get("first_seen"))}
    out["capabilities"] = {
        "ocr": ocr_capabilities(),
        "summariser": summariser_status(),
        "graph_backends": describe_backends(),
        "audit_security": encryption_status(),
        "pdf_export": pdf_report.available(),
        "integrations": [a["adapter"] for a in integrations_status()["adapters"]],
    }
    return out


@app.get("/api/graph")
def graph(min_confidence: float = 0.0,
          edge_types: str | None = None,
          types: str | None = None,
          user: Principal = Depends(current_user)):
    require(user, "graph:read")
    audit(user, "VIEW_GRAPH", {"min_confidence": min_confidence,
                               "edge_types": edge_types, "types": types})
    s = load()
    node_filter = None
    if types:
        wanted = set(types.split(","))
        node_filter = {n for n, d in s.graph.G.nodes(data=True) if d["type"] in wanted}
    return serialise_graph(s.graph, s.analytics, node_filter, min_confidence,
                           edge_types.split(",") if edge_types else None,
                           redact=not user.can("entity:read"))


@app.get("/api/dashboard")
def dashboard(user: Principal = Depends(current_user)):
    require(user, "graph:read")
    audit(user, "VIEW_DASHBOARD")
    s = load()
    return build_dashboard(s.graph, s.analytics, s.findings)


@app.get("/api/entities")
def entities(user: Principal = Depends(current_user)):
    require(user, "entity:read")
    audit(user, "LIST_ENTITIES")
    s = load()
    return [{"id": n, "label": d.get("label"), "type": d.get("type"),
             "aliases": d.get("aliases", []), "phones": d.get("phones", []),
             "prior_cases": d.get("prior_cases")}
            for n, d in s.graph.G.nodes(data=True)]


@app.get("/api/entity/{node_id}")
def entity(node_id: str, user: Principal = Depends(current_user)):
    require(user, "entity:read")
    s = load()
    if node_id not in s.graph.G:
        raise HTTPException(404, "No such entity")
    audit(user, "VIEW_ENTITY", {"node": node_id,
                                "label": s.graph.G.nodes[node_id].get("label")})
    return s.reports.subject_report(node_id)


@app.get("/api/influencers")
def influencers(limit: int = 15, user: Principal = Depends(current_user)):
    require(user, "graph:read")
    audit(user, "VIEW_INFLUENCERS", {"limit": limit})
    return load().analytics.influence()[:limit]


@app.get("/api/communities")
def communities(user: Principal = Depends(current_user)):
    require(user, "graph:read")
    audit(user, "VIEW_COMMUNITIES")
    s = load()
    c = s.analytics.communities()
    return {"count": c["count"], "modularity": c["modularity"],
            "groups": [[{"id": n, "label": s.graph.G.nodes[n].get("label"),
                         "type": s.graph.G.nodes[n].get("type")} for n in g]
                       for g in c["groups"]]}


@app.get("/api/path")
def path(a: str, b: str, cutoff: int = 5, user: Principal = Depends(current_user)):
    require(user, "evidence:read")
    s = load()
    if a not in s.graph.G or b not in s.graph.G:
        raise HTTPException(404, "Unknown entity")
    audit(user, "TRACE_PATH", {"from": a, "to": b, "cutoff": cutoff})
    return s.reports.link_report(a, b, cutoff)


@app.get("/api/evidence")
def evidence(a: str, b: str, user: Principal = Depends(current_user)):
    """The evidence trail behind a single edge - the 'why do you say this?' view."""
    require(user, "evidence:read")
    s = load()
    if not s.graph.G.has_edge(a, b):
        raise HTTPException(404, "No such link")
    audit(user, "VIEW_EVIDENCE", {"a": a, "b": b})
    e = s.graph.G[a][b]
    out = []
    for src in e["sources"]:
        doc = s.graph.raw["documents"].get(src["source_id"], {})
        out.append({**src, "document": doc.get("record")})
    return {"a": a, "a_label": s.graph.G.nodes[a].get("label"),
            "b": b, "b_label": s.graph.G.nodes[b].get("label"),
            "types": e["types"], "confidence": e["confidence"],
            "observations": e["observations"],
            "independent_sources": sorted({s_["source_type"] for s_ in e["sources"]}),
            "sources": out}


@app.get("/api/findings")
def findings(severity: str | None = None, user: Principal = Depends(current_user)):
    require(user, "graph:read")
    audit(user, "VIEW_FINDINGS", {"severity": severity})
    fs = load().findings
    return [f for f in fs if not severity or f["severity"] == severity]


@app.get("/api/corroboration")
def corroboration(min_level: int = 2, user: Principal = Depends(current_user)):
    require(user, "graph:read")
    audit(user, "VIEW_CORROBORATION")
    return [c for c in load().analytics.corroboration()
            if c["corroboration_level"] >= min_level]


class Ask(BaseModel):
    q: str


@app.post("/api/query")
def query(body: Ask, user: Principal = Depends(current_user)):
    require(user, "query:run")
    s = load()
    parsed = s.parser.parse(body.q)
    audit(user, "NL_QUERY", {"question": body.q, "intent": parsed.intent,
                             "interpretation": parsed.interpretation})
    result = None
    if parsed.intent == "path":
        result = s.reports.link_report(parsed.params["a"], parsed.params["b"],
                                       parsed.params.get("cutoff", 5))
    elif parsed.intent == "neighbourhood":
        nb = s.analytics.neighbourhood(parsed.params["node"], parsed.params["hops"])
        result = serialise_graph(s.graph, s.analytics, set(nb["nodes"]))
        result["center"] = parsed.params["node"]
    elif parsed.intent == "influencers":
        result = s.analytics.influence()[:parsed.params.get("limit", 10)]
    elif parsed.intent == "anomalies":
        result = s.findings
    elif parsed.intent == "communities":
        result = s.analytics.communities()["groups"]
    elif parsed.intent == "transactions":
        rows = [r for r in s.graph.raw["txn_rows"]
                if int(r["amount_inr"]) >= parsed.params.get("min_amount", 0)]
        node = parsed.params.get("node")
        if node:
            label = s.graph.G.nodes[node].get("label")
            names = {label, *(s.graph.G.nodes[node].get("aliases") or [])}
            accts = set(s.graph.G.nodes[node].get("accounts") or [])
            rows = [r for r in rows
                    if r["from_name"] in names or r["to_name"] in names
                    or r["from_account"] in accts or r["to_account"] in accts]
        result = sorted(rows, key=lambda r: -int(r["amount_inr"]))[:100]
    elif parsed.intent == "calls":
        node = parsed.params.get("node")
        result = [c for c in s.analytics.corroboration() if "CALLED" in c["types"]
                  and (node is None or node in (c["a"], c["b"]))]
    elif parsed.intent == "entity":
        result = s.reports.subject_report(parsed.params["node"])
    return {"parsed": parsed.dict(), "result": result}


@app.get("/api/report/overview")
def overview(user: Principal = Depends(current_user)):
    require(user, "report:generate")
    audit(user, "GENERATE_OVERVIEW_REPORT")
    return load().reports.case_overview()


@app.get("/api/audit")
def audit_log(limit: int = Query(200, le=1000), user: Principal = Depends(current_user)):
    require(user, "audit:read")
    audit(user, "VIEW_AUDIT_LOG")
    return read_audit(limit)


@app.post("/api/reload")
def reload_data(user: Principal = Depends(current_user)):
    require(user, "data:ingest")
    audit(user, "RELOAD_PIPELINE")
    load(force=True)
    return {"status": "reloaded", **S.graph.stats()}


# --------------------------------------------------------------- case intake

@app.get("/api/case/schema")
def case_schema(user: Principal = Depends(current_user)):
    """What the intake forms need to send — lets the UI build itself from
    the same source of truth the pipeline reads, instead of duplicating it."""
    require(user, "graph:read")
    return {
        "quick_add": {k: {"file": v[0], "required_fields": v[3]}
                     for k, v in case_intake.JSON_SOURCES.items()},
        "bulk_json": {k: {"file": v[0], "id_field": v[1]}
                     for k, v in case_intake.JSON_SOURCES.items()},
        "bulk_csv": {k: {"file": v[0], "columns": v[1]}
                    for k, v in case_intake.CSV_SOURCES.items()},
    }


class QuickAddBody(BaseModel):
    kind: str
    fields: dict


@app.post("/api/case/quick-add")
def case_quick_add(body: QuickAddBody, user: Principal = Depends(current_user)):
    """
    File one new document — an FIR, a surveillance log, a social-media tip —
    as it comes in. Any investigator can do this. The pipeline reruns
    immediately so the new document's entities, links and any anomalies it
    triggers show up right away.
    """
    require(user, "data:submit")
    try:
        rec = case_intake.quick_add(body.kind, body.fields)
    except case_intake.IntakeError as e:
        raise HTTPException(400, str(e))
    id_field = case_intake.JSON_SOURCES[body.kind][1]
    before = len(S.findings) if S.findings is not None else 0
    load(force=True)
    audit(user, "QUICK_ADD_RECORD", {"kind": body.kind, "id": rec[id_field]})
    after = len(S.findings)
    return {"status": "added", "record": rec, "stats": S.graph.stats(),
            "findings_before": before, "findings_after": after,
            "new_findings": max(0, after - before)}


@app.post("/api/case/new")
def case_new(user: Principal = Depends(current_user)):
    """
    Wipe every source file and start a blank case. Restricted to admins, same
    as bulk upload/replace - this is that operation applied to all six
    sources at once instead of one at a time.
    """
    require(user, "data:ingest")
    result = case_intake.clear_all()
    load(force=True)
    audit(user, "NEW_CASE", result)
    return {"status": "cleared", "result": result, "stats": S.graph.stats()}


@app.post("/api/case/upload")
async def case_upload(kind: str = Form(...), mode: str = Form("append"),
                      file: UploadFile = File(...),
                      user: Principal = Depends(current_user)):
    """
    Load or replace a whole source file — e.g. a fresh CDR dump, or the full
    set of six files for a brand-new case. mode='replace' swaps the active
    case entirely; mode='append' merges new records into what's there,
    deduping by id. Restricted to admins, same as /api/reload.
    """
    require(user, "data:ingest")
    raw = await file.read()
    try:
        if kind == "scan":
            saved = case_intake.save_scan(file.filename or "scan.png", raw)
            result = {"kind": "scan", "file": saved}
        else:
            result = case_intake.bulk_upload(kind, raw, mode)
    except case_intake.IntakeError as e:
        raise HTTPException(400, str(e))
    before = len(S.findings) if S.findings is not None else 0
    load(force=True)
    audit(user, "BULK_UPLOAD", {"kind": kind, "mode": mode,
                                "filename": file.filename, **result})
    after = len(S.findings)
    return {"status": "uploaded", "result": result, "stats": S.graph.stats(),
            "findings_before": before, "findings_after": after,
            "new_findings": max(0, after - before)}


@app.get("/api/pipeline")
def pipeline(user: Principal = Depends(current_user)):
    """Transparency view: what the extractor found and which merges were applied."""
    require(user, "entity:read")
    audit(user, "VIEW_PIPELINE")
    s = load()
    return {
        "extractor": s.graph.raw["extractor"],
        "mentions": [m.dict() for m in s.graph.raw["mentions"]][:400],
        "resolution_decisions": s.graph.raw["resolution_decisions"][:200],
        "phone_ownership": s.graph.raw["ownership_evidence"],
        "documents": [{"id": k, "source_type": v["source_type"],
                       "script": v.get("script", "latin")}
                      for k, v in s.graph.raw["documents"].items()],
        "ocr": s.graph.raw.get("ocr", {}),
        "summariser": summariser_status(),
    }


@app.get("/api/document/{doc_id:path}")
def document(doc_id: str, user: Principal = Depends(current_user)):
    require(user, "evidence:read")
    s = load()
    doc = s.graph.raw["documents"].get(doc_id)
    if not doc:
        raise HTTPException(404, "No such source document")
    audit(user, "VIEW_DOCUMENT", {"document": doc_id})
    return {"id": doc_id, **doc}


# ------------------------------------------------------- reports as PDF

def _evidence_rows(report: dict, subject_label: str) -> list[dict]:
    return [{"pair": f"{subject_label} — {l['label']}",
             "relationship": ", ".join(l["relationship"]).lower().replace("_", " "),
             "sources": ", ".join(l["independent_sources"]),
             "confidence": l["confidence"]}
            for l in report.get("links", [])]


def _pdf_or_503(builder, filename: str, user: Principal, what: str):
    if not pdf_report.available():
        raise HTTPException(
            503, "PDF export needs reportlab:  pip install reportlab")
    data = builder()
    audit(user, "EXPORT_PDF", {"document": what, "bytes": len(data)})
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="{filename}"'})


@app.get("/api/report/overview.pdf")
def overview_pdf(user: Principal = Depends(current_user)):
    require(user, "report:generate")
    s = load()
    rep = s.reports.case_overview()

    def build():
        pdf = pdf_report.CaseFilePdf(user.name, user.role, user.badge, user.unit)
        rows = [{"pair": f"{c['a_label']} — {c['b_label']}",
                 "relationship": ", ".join(c["types"]).lower().replace("_", " "),
                 "sources": ", ".join(c["independent_sources"]),
                 "confidence": c["confidence"]}
                for c in s.analytics.corroboration()[:22]]
        return pdf.render("Network analysis summary", rep["markdown"], rows,
                          [f for f in s.findings if f["severity"] == "high"])
    return _pdf_or_503(build, "network-analysis-summary.pdf", user, "case overview")


@app.get("/api/entity/{node_id}/report.pdf")
def entity_pdf(node_id: str, user: Principal = Depends(current_user)):
    require(user, "report:generate")
    s = load()
    if node_id not in s.graph.G:
        raise HTTPException(404, "No such entity")
    rep = s.reports.subject_report(node_id)

    def build():
        pdf = pdf_report.CaseFilePdf(user.name, user.role, user.badge, user.unit)
        return pdf.render(f"Subject profile — {rep['label']}", rep["markdown"],
                          _evidence_rows(rep, rep["label"]), rep["findings"])
    safe = "".join(c for c in rep["label"] if c.isalnum() or c in " -_").strip()
    return _pdf_or_503(build, f"subject-{safe or node_id}.pdf", user,
                       f"subject profile {node_id}")


@app.get("/api/path.pdf")
def path_pdf(a: str, b: str, cutoff: int = 5,
             user: Principal = Depends(current_user)):
    require(user, "report:generate")
    s = load()
    if a not in s.graph.G or b not in s.graph.G:
        raise HTTPException(404, "Unknown entity")
    rep = s.reports.link_report(a, b, cutoff)
    la, lb = s.graph.G.nodes[a].get("label"), s.graph.G.nodes[b].get("label")

    def build():
        pdf = pdf_report.CaseFilePdf(user.name, user.role, user.badge, user.unit)
        rows = []
        for p in rep.get("paths", [])[:2]:
            for leg in p["legs"]:
                rows.append({"pair": f"{leg['from_label']} — {leg['to_label']}",
                             "relationship": ", ".join(leg["types"]).lower().replace("_", " "),
                             "sources": " | ".join(
                                 f"{x['source_type']}:{x['source_id']}"
                                 for x in leg["sources"][:3]),
                             "confidence": leg["confidence"]})
        return pdf.render(f"Connection analysis — {la} and {lb}",
                          rep["markdown"], rows)
    return _pdf_or_503(build, "connection-analysis.pdf", user,
                       f"connection {a}->{b}")


# ------------------------------------------------------- system surfaces

@app.get("/api/audit/verify")
def audit_verify(user: Principal = Depends(current_user)):
    require(user, "audit:read")
    audit(user, "VERIFY_AUDIT_CHAIN")
    return verify_chain()


@app.get("/api/security")
def security(user: Principal = Depends(current_user)):
    require(user, "graph:read")
    return {"audit": encryption_status(),
            "roles": {r: sorted(p) for r, p in __import__(
                "app.auth", fromlist=["ROLES"]).ROLES.items()},
            "your_role": user.role,
            "your_permissions": sorted(user.permissions)}


@app.get("/api/integrations")
def integrations(user: Principal = Depends(current_user)):
    require(user, "graph:read")
    audit(user, "VIEW_INTEGRATIONS")
    return integrations_status()


@app.get("/api/integrations/{key}/preview")
def integration_preview(key: str, authorisation: str = "",
                        user: Principal = Depends(current_user)):
    require(user, "data:ingest")
    audit(user, "FETCH_EXTERNAL_SOURCE",
          {"adapter": key, "authorisation": authorisation})
    return integrations_fetch(key, authorisation=authorisation)


@app.get("/api/export/cypher")
def export_cypher(user: Principal = Depends(current_user)):
    require(user, "data:ingest")
    audit(user, "EXPORT_CYPHER")
    import tempfile
    s = load()
    path = os.path.join(tempfile.gettempdir(), "graph.cypher")
    info = export_graph(s.graph, CypherExportStore(path))
    with open(path, encoding="utf-8") as f:
        body = f.read()
    return Response(content=body, media_type="text/plain",
                    headers={"Content-Disposition":
                             'attachment; filename="graph.cypher"',
                             "X-Export-Nodes": str(info["nodes"]),
                             "X-Export-Edges": str(info["edges"])})


@app.get("/api/ocr")
def ocr_view(user: Principal = Depends(current_user)):
    require(user, "entity:read")
    audit(user, "VIEW_OCR_STATUS")
    return load().graph.raw.get("ocr", {})


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))
