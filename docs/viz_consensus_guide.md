# Reading the consensus triple visualisation (`viz_consensus.html`)

## What it is

`viz_consensus.html` is a self-contained interactive report produced by
`tasks/viz_consensus.py` after the cross-model pipeline runs.  It shows, for
each Key Event (KE), which curated ground-truth triples were confirmed by LLM
models, which were missed, and which LLM-extracted triples are novel candidates
not yet in the curated set.

The page is a small static shell that `fetch()`es a companion
`viz_consensus.json` and renders it client-side, so it **must be served over
http** — browsers block `fetch` on `file://`. Double-clicking the HTML will
show "Could not load viz_consensus.json".

To view it locally, serve the product directory and open the page from there:

```bash
uv run python -m http.server 8000 --directory products_202606/<crossmodel>
```

then open `http://localhost:8000/viz_consensus.html` (both `viz_consensus.html`
and `viz_consensus.json` must be in the served directory — the task writes
them side by side). The same applies when browsing output from the AOPMapper
server or a vite dev/preview server — anything that serves the folder over
http works.

---

## Restricting to the recommended ensemble subset

By default the report shows **all** models. To instead show only the recommended ensemble
subset (from the `select_ensemble` task — see
[ensemble_selection_guide.md](ensemble_selection_guide.md)), set in `env.consensus.yaml`:

```yaml
viz_restrict_to_ensemble: true
```

When on, `viz_consensus` reads `recommended_models` from the ensemble summary and:
- drops cluster members from models outside the subset (and any cluster left empty);
- recomputes each cluster's support, the total/consensus cluster counts, and the support
  histogram over the subset;
- GT triples that only non-subset models confirmed correctly fall back to "not found".

So the page shows exactly what the selected ensemble produces. The header is labelled
*"recommended ensemble subset — N models"* and the title gets a *(recommended subset)*
suffix so it is unmistakable which view you are looking at. The `select_ensemble` task is an
upstream of `viz_consensus`, so it builds first. Turn the flag back to `false` for the
all-models view (unchanged behaviour).

Note: the ensemble subset can be selected against either target (`ensemble.target` in
`env.consensus.yaml`; see [ensemble_selection_guide.md](ensemble_selection_guide.md)). With
`kec_plus_grounded`, the recommended subset may include models chosen for their
ontology-grounded *novel* triples, not KEC recall alone — so the restricted view reflects
that broader objective.

---

## Page structure

```
Header bar
  "N models | M clusters total, K consensus (support≥2) | τ=0.75 | SapBERT"

Model panel  ▸  collapsible; lists the models (short names, full name on hover)
                — labelled "Recommended ensemble" when restricted to the subset

Support distribution chips
  sup=1: 624   sup=2: 183   sup=3: 90   …

[KE block]  ▸  one collapsible per KE
[KE block]
…
```

KEs are sorted by number of GT triples (most GT first), so KEs with the
richest curated annotation appear at the top.

---

## KE block

```
▶  KE1000  |  Activation of PPARα  |  2/3 GT confirmed  1 novel  5 clusters
```

The header summarises at a glance:
- **N/M GT confirmed** — how many curated GT triples were found by at least
  one model consensus cluster (green = some found; red = none found)
- **N novel** — how many consensus clusters produced triples not in the GT
  (blue)
- **N clusters** — total clusters across all categories

---

## Inside a KE block

The table has one row per curated GT triple, followed by a "novel" row.

### GT confirmed row (green background)

```
GT #0 | PPARα | molecular binding | increased | ✓ confirmed (1 cluster)
         └─ confirmed by  7/11 ████████░░  gemma-4-31B, gpt-oss-120b, …  name  score=0.847
               object (repr.) | process (repr.) | action
               PPARα          | molecular bind. | increased
               [member triples with logprobs ▸]
```

Each GT triple that was found by at least one model is shown alongside the
confirming cluster(s):
- **Support bar** — fraction of all models that contributed an equivalent
  triple; colour-coded dark green ≥ 75 %, olive ≥ 50 %, amber ≥ 33 %,
  orange < 33 %
- **Tier badge** — best match tier across cluster members: `iri` (IRI exact),
  `name` (normalised name exact), or `embedding` (SapBERT cosine ≥ τ)
- **score** — similarity score of the best member match to this GT triple
- **Representative triple** — the canonical form chosen by the clustering
  algorithm (centroid/best-IRI triple)

Expanding "member triples with logprobs" shows a per-model table with the
exact wording used by each model, per-slot log-probability bars, and the
individual match tier and score for that model.

If multiple clusters all match the same GT triple, they are stacked (this is
rare but can happen when a model uses very different phrasing that still
exceeds τ).

### GT miss row (amber background)

```
GT #2 | liver cells | apoptosis | increased | ✗ not found by any model
```

A GT triple that no model produced an equivalent for.  This is a **recall
miss** at the ensemble level — stronger evidence of incompleteness than a
single-model miss, since all models failed to extract it.

### Novel row (blue background)

```
novel (no GT match) — 2 cluster(s)
  novel  3/11 ███░░░░░░░  gemma-4-26B, gemma-4-31B, gpt-oss-120b
  novel  1/11 █░░░░░░░░░  LFM2.5-8B
```

Clusters whose members did not match any GT triple in any model.  These are
**candidate novel triples**: the models agree on a relationship that is not
yet curated in AOP-Wiki.  Higher support = more models independently produced
the same relationship = stronger signal.

---

## Are any triples dropped?

No.  Every triple extracted by every model ends up in exactly one cluster.
`cluster_ke_triples` assigns each triple to the best-matching existing cluster
or creates a new singleton (support=1).  The `min_support = 2` threshold only
affects the summary counter `n_consensus_clusters`; all clusters — including
singletons — appear in the JSON and in the viz.

The support distribution at the top of the page shows how many clusters fall
in each support bucket.  The `sup=1` count is the singleton total: triples
that no other model produced an equivalent for.  They appear as low-support
novel clusters or as low-confidence confirming clusters.

---

## What to look for

| Pattern | Interpretation |
|---|---|
| GT row confirmed with support ≥ 75 % | Strong ensemble signal; high-confidence re-confirmation of curated triple |
| GT row confirmed with support < 33 % | Weak signal; only 1–2 models found it |
| GT miss row | No model found this relationship — possible wording mismatch, KE description gap, or genuinely hard to extract |
| Novel cluster with high support | Multiple models agree on a new relationship; candidate for GT expansion |
| Novel singleton (support = 1) | One model extracted something unusual; low confidence, needs manual check |
| KE has only novel clusters, no GT | Models extracted relationships but none correspond to the curated set |

---

## Relationship to other reports

| File | What it shows |
|---|---|
| `viz_consensus.html` | **This file.** Cross-model GT coverage and novel candidates |
| `{model}/viz_kec_matching.html` | Per-model triple-vs-GT comparison (tier, score, recall misses per model) |
| `crossmodel_pairwise.tsv` | Jaccard pairwise agreement between all model pairs |
| `figures/fig_pairwise_agreement.png` | Biclustered heatmap of pairwise Jaccard scores |
| `figures/fig_ke_triples_heatmap.png` | Per-KE × model extraction count with model row-clustering |

---

## Technical notes

**Cluster formation** — Two triples are merged if, for each slot (object,
process), their SapBERT cosine similarity exceeds τ = 0.75.  The action slot
is matched by name only (antonyms like "increased"/"decreased" are close in
embedding space).  Merging is greedy and deterministic.

**GT matching** — Each model's match status (tier + score + which GT triple
index) is pre-computed by `compare_kec.py` and stored in
`kec_match_report.json`.  `viz_consensus.py` loads these reports and joins on
the `triple_idx` field stored in each cluster member, giving a formal,
threshold-based match verdict rather than a heuristic name-overlap check.

**KE sort order** — KEs are sorted by number of GT triples (most first), so
richly annotated KEs appear first and poorly annotated or GT-free KEs appear
at the bottom.
