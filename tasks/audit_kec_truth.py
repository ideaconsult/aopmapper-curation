"""
audit_kec_truth.py — Ploomber task: ground-truth label-noise audit (spec §4.5).

The curated `structured` triples (from XML biological_events) are the calibration labels
for the whole quality-model / conformal plan. But they may be noisy or incomplete — the
matcher already shows only ~24% of LLM triples match them. Before trusting them as labels,
judge them: for each structured triple, ask an LLM judge whether it correctly describes the
KE (given the KE description). The verdict distribution is the de-risking metric — if a
non-trivial fraction is judged wrong/partial, calibration labels are contaminated.

Use a judge model DIFFERENT from the extractor where possible (set judge_model_key) to avoid
shared blind spots.

Ploomber:
  upstream: ["extract_kec", "parse_pydantic"]
  product:  {"json": per-triple assessments + summary, "tsv": flat per-triple}
  params:   models, model_key, judge_model_key (default model_key), limit
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from tasks.llm_judge import judge_client_from_model
from tasks.logutil import setup_logging

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["extract_kec", "parse_pydantic"]
product = None
enabled = True
models = None
model_key = None
judge_model_key = None    # defaults to model_key; set to a stronger/other model to reduce bias
limit = None              # cap number of KEs (for a quick run)
# -

logger = setup_logging(Path(product["json"]).parent / "audit_kec_truth.log")


def _load_descriptions(aop_json_path: str) -> dict:
    """uuid -> {title, description, level} from aop.json (project-standard load)."""
    from aopkb import Data
    from tasks.utils import preprocess_timestamps
    try:
        from tasks.utils import clean_html
    except ImportError:
        import re
        def clean_html(t):  # minimal fallback
            return re.sub(r"<[^>]+>", " ", t or "").replace("\xa0", " ").strip()

    with open(aop_json_path, encoding="utf-8") as f:
        raw = json.load(f)
    preprocess_timestamps(raw)
    data = Data.model_validate(raw)

    out = {}
    for ke in data.key_event:
        level = (ke.biological_organization_level.value
                 if ke.biological_organization_level else "")
        out[ke.id] = {
            "title": ke.title or "",
            "description": clean_html(ke.description) if ke.description else "",
            "level": level,
        }
    return out


def _overall(assessments: list[dict]) -> tuple[str, float | None]:
    verdicts = [a["verdict"] for a in assessments if a["verdict"] in ("correct", "partial", "wrong")]
    scored = [a["score"] for a in assessments if a.get("score") is not None]
    avg = round(sum(scored) / len(scored), 3) if scored else None
    if not verdicts:
        return "error", None
    if "wrong" in verdicts:
        return "wrong", avg
    if "partial" in verdicts:
        return "partial", avg
    return "correct", avg


if enabled:
    import time
    _t0 = time.perf_counter()
    OUT_JSON = Path(product["json"])
    OUT_TSV  = Path(product["tsv"])
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    judge_key = judge_model_key or model_key
    judge = judge_client_from_model(models, judge_key, 
                                    timeout=models[judge_key].get('timeout',1800))
    logger.info(f"Judge model: {models[judge_key]['model_llm']} @ {models[judge_key].get('base_url','http://192.168.0.252:8000/v1')}")

    with open(upstream["extract_kec"]["json"], encoding="utf-8") as f:
        kec = json.load(f)
    desc = _load_descriptions(upstream["parse_pydantic"]["json"])

    # Resume: keep already-assessed KEs
    results = {}
    if OUT_JSON.exists():
        try:
            results = json.load(open(OUT_JSON, encoding="utf-8")).get("per_ke", {})
        except (json.JSONDecodeError, AttributeError):
            results = {}
    done = set(results.keys())

    todo = [(kid, rec) for kid, rec in kec.items()
            if (rec.get("structured") and kid not in done)]
    if limit:
        todo = todo[:limit]
    logger.info(f"Auditing {len(todo)} KEs (resuming over {len(done)} done)")

    def _flush():
        counts = {}          # KE-level (overall verdict per KE)
        tcounts = {}         # triple-level (each curated triple)
        for r in results.values():
            counts[r["overall_verdict"]] = counts.get(r["overall_verdict"], 0) + 1
            for a in r.get("assessments", []):
                tcounts[a["verdict"]] = tcounts.get(a["verdict"], 0) + 1
        n = sum(counts.values()) or 1
        nt = sum(tcounts.values()) or 1
        summary = {
            "judge_model": models[judge_key]["model_llm"],
            "n_ke_audited": len(results),
            "n_triples": nt,
            "verdict_distribution": counts,
            "verdict_fraction": {k: round(v / n, 3) for k, v in counts.items()},
            "triple_verdict_distribution": tcounts,
            "triple_verdict_fraction": {k: round(v / nt, 3) for k, v in tcounts.items()},
        }
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "per_ke": results}, f, indent=2, ensure_ascii=False)
        try:
            import pandas as pd
            _tvf = summary.get("triple_verdict_fraction", {})
            pd.DataFrame([{
                "model_key":          model_key,
                "elapsed_s":          round(time.perf_counter() - _t0, 1),
                "n_ke_audited":       summary.get("n_ke_audited"),
                "audit_correct_frac": _tvf.get("correct"),
                "audit_partial_frac": _tvf.get("partial"),
                "audit_wrong_frac":   _tvf.get("wrong"),
            }]).to_csv(product["csv"], index=False)
        except Exception as _e:
            logger.warning(f"stats CSV write failed: {_e}")
        return summary

    _threads = int((models.get(model_key) or {}).get("threads",128))

    def _assess_ke(wiki_id, rec):
        info = desc.get(rec.get("uuid"), {})
        title = info.get("title") or rec.get("title", "")
        description = info.get("description", "")
        level = info.get("level") or rec.get("biological_level", "")
        assessments = []
        for idx, triple in enumerate(rec["structured"]):
            res = judge.judge_triple(title, description, level, triple)
            res["triple_index"] = idx
            res["triple"] = triple
            assessments.append(res)
        verdict, score = _overall(assessments)
        return wiki_id, {
            "uuid": rec.get("uuid"),
            "title": title,
            "n_structured": len(rec["structured"]),
            "had_description": bool(description),
            "overall_verdict": verdict,
            "overall_score": score,
            "assessments": assessments,
        }

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=_threads) as pool:
        futures = {pool.submit(_assess_ke, kid, rec): kid for kid, rec in todo}
        for i, future in enumerate(as_completed(futures), 1):
            wiki_id, record = future.result()
            results[wiki_id] = record
            logger.info(f"  [{i}/{len(todo)}] {wiki_id:<10} → {record['overall_verdict']} ({record['overall_score']})")
            if record["overall_verdict"] == "error":
                for a in record.get("assessments", []):
                    if a.get("verdict") == "error":
                        logger.warning(f"    triple {a.get('triple_index')}: {a.get('reasoning')}")
            if i % 20 == 0:
                _flush()

    summary = _flush()

    # Flat TSV (one row per structured triple)
    with open(OUT_TSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["ke_id", "title", "overall_verdict", "overall_score",
                    "triple_index", "triple_verdict", "triple_score", "reasoning",
                    "object_name", "process_name", "action_name"])
        for kid, r in results.items():
            for a in r.get("assessments", []):
                t = a.get("triple", {})
                obj = (t.get("object") or {}).get("name", "")
                prc = (t.get("process") or {}).get("name", "")
                act = (t.get("action") or {}).get("name", "")
                w.writerow([kid, r["title"], r["overall_verdict"], r["overall_score"],
                            a.get("triple_index"), a.get("verdict"), a.get("score"),
                            a.get("reasoning", ""), obj, prc, act])

    logger.info(f"GROUND-TRUTH AUDIT — KE verdicts: {summary['verdict_distribution']} "
                f"| triple verdicts: {summary['triple_verdict_distribution']}")

    # per-model assessment figure: curated-truth reliability (triple-level), this judge
    tvf = summary.get("triple_verdict_fraction")
    if tvf:
        try:
            from tasks.figviz import verdict_bar
            info = verdict_bar(tvf, model=OUT_JSON.parent.name,
                               judge_model=summary["judge_model"],
                               out_path=OUT_JSON.parent / "fig_audit_verdicts.png")
            logger.info(f"audit verdict figure → {info['out']}")
        except Exception as e:
            logger.warning(f"audit verdict figure skipped: {e}")
    try:
        import pandas as pd
        _tvf = summary.get("triple_verdict_fraction", {})
        pd.DataFrame([{
            "model_key":         model_key,
            "elapsed_s":         round(time.perf_counter() - _t0, 1),
            "n_ke_audited":      summary.get("n_ke_audited"),
            "audit_correct_frac": _tvf.get("correct"),
            "audit_partial_frac": _tvf.get("partial"),
            "audit_wrong_frac":  _tvf.get("wrong"),
        }]).to_csv(product["csv"], index=False)
    except Exception as _e:
        logger.warning(f"stats CSV write failed: {_e}")
else:
    logger.info("audit_kec_truth disabled — skipping")
