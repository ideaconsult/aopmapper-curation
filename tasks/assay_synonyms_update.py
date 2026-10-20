"""
assay_synonyms_update.py — Ploomber task: keep resources/assay_synonyms.json current as
the assay consensus changes between releases.

Reads the per-KE consensus clusters straight from viz_assay_consensus_json (NOT the Solr
output, which is built FROM the map this task maintains), counts n_ke / n_rec per
punct-normalised assay name WITHOUT the synonym map (no circularity), embeds the names with
SapBERT, and folds every pair with cosine >= assay_synonym_threshold into the map by STAR
grouping (a canonical absorbs only its direct neighbours; chains never drift), minus the
hard VETO (digit / roman numeral, Greek / subunit and antonym differences are never merged).
The logic is tasks/assay_synonym_merge.py (shared with scripts/propose_technique_merges.py).

Existing canonicals and variants are preserved and the task is idempotent: with an unchanged
consensus a second run changes nothing. Every new merge is appended to
resources/assay_synonyms_applied.tsv (variant, canonical, sim, risky) for audit, and a run
that adds merges logs a WARNING so a release that moves the map is visible. Risky-flagged
pairs are merged (the threshold alone decides) but recorded.

Ploomber:
  upstream: ["ke_graph"]   (current-release KE set, same gate as assay_consensus2solr)
  product:  {"nb", "synonyms", "applied", "stats"}
  params:   viz_assay_consensus_json, assay_min_support, assay_synonym_threshold,
            assay_synonym_veto, embedding_model
assay_consensus2solr takes its synonym map from this task's `synonyms` product.
"""
from __future__ import annotations

import json
from pathlib import Path

from tasks.assay_synonym_merge import default_embed, run_update, techniques_from_viz
from tasks.logutil import setup_logging
from tasks.utils import get_kid

# ── Ploomber task header ──────────────────────────────────────────────────────
# + tags=["parameters"]
upstream = ["ke_graph"]
product = None
viz_assay_consensus_json: str = ""
assay_min_support: int = 2
assay_synonym_threshold: float = 0.90
assay_synonym_veto: bool = True
embedding_model: str = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
# -

logger = setup_logging(Path(product["stats"]).parent / "assay_synonyms_update.log")

with open(upstream["ke_graph"]["lookup"], encoding="utf-8") as f:
    lookup = json.load(f)
current_ke_ids = {
    get_kid(lookup, uuid, "key-event-reference")
    for uuid in lookup.get("key-event-reference", {})
}
current_ke_ids.discard(None)

with open(viz_assay_consensus_json, encoding="utf-8") as f:
    viz = json.load(f)

techs = techniques_from_viz(viz, current_ke_ids, int(assay_min_support))
logger.info(f"{len(techs)} distinct normalised assay names over {len(current_ke_ids)} KEs "
            f"(support >= {assay_min_support})")

stats = run_update(
    techs, default_embed(embedding_model),
    Path(product["synonyms"]), Path(product["applied"]),
    threshold=float(assay_synonym_threshold), veto=bool(assay_synonym_veto))

out = Path(product["stats"])
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump(stats, f, indent=2, ensure_ascii=False)
