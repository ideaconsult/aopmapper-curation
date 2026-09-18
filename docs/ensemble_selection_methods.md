# Ensemble selection by ground-truth coverage — methods writeup

Paper-ready prose (Methods section). Describes what `tasks/select_ensemble.py` does and
why. The companion `ensemble_selection_guide.md` explains how to read its outputs.

---

## Selecting a complementary model ensemble by ground-truth coverage

Individual model ranking (SMAA-2 with entropy-weighted TOPSIS as a deterministic
cross-check) identifies the single best-performing model but not a *set* of models whose
combined extractions best reproduce the curated (KEC-endorsed) relationships. Because
different models recover different subsets of the endorsed triples, a well-chosen ensemble
can achieve higher recall than any single model. We therefore frame ensemble selection as a
**maximum-coverage (set-cover) problem**.

Let *G* = {(*k*, *r*)} be the set of all curated ground-truth triples, indexed by Key Event
*k* and within-KE triple index *r*, taken from the AOP-Wiki structured annotations. For each
model *m* we determine the subset *C_m* ⊆ *G* of endorsed triples it recovers, using the
per-triple match verdict from the comparison step: a triple is recovered if it matches an
endorsed triple by exact ontology-IRI, exact normalized name, or SapBERT embedding cosine ≥
*τ* (*τ* = 0.75). The coverage of a model subset *S* is the union recall
|⋃_{m∈S} *C_m*| / |*G*|; being a union, coverage is monotonically non-decreasing in *S*.

We select models greedily: starting from the empty set, at each step we add the model that
contributes the largest number of previously-uncovered endorsed triples. This yields an
ordered **coverage curve** — cumulative fraction of endorsed triples recovered against number
of models — whose point of diminishing returns (elbow) indicates where additional models
cease to help. Greedy maximum-coverage is a standard algorithm with a
(1 − 1/*e*) ≈ 63 % worst-case optimality guarantee. We report the recommended ensemble as the
shortest prefix of this order in which every added model contributes at least a threshold
number of new endorsed triples (parameter `min_gain`, default 1).

We deliberately optimise coverage (recall of endorsed content) rather than an F-score. In
AOP curation a model triple that matches no endorsed triple is frequently a *novel candidate*
meriting curation rather than an error; penalising it as a false positive would be
inappropriate. We therefore report the count of such un-endorsed ("novel") triples as a
descriptive quantity (pooled precision) alongside coverage, not as part of the objective.

Finally we report per-KE coverage for the selected ensemble — for each KE, how many endorsed
triples were recovered and by which models — so that weakly-covered KEs and pivotal models
are identifiable. For KEs lacking curated ground truth, coverage cannot be measured; there
the overall model ranking serves as the default ensemble.

### An alternative coverage target: ontology-grounded candidate relationships

KEC endorsement is a curation snapshot, not an absolute ground truth: the endorsed set is
necessarily incomplete, and some correct relationships have simply not been curated yet.
Optimising coverage of the endorsed set alone therefore treats every plausible but
un-endorsed relationship as noise. To relax this, we offer an **alternative selection
target** (parameter `target = "kec_plus_grounded"`, off by default) that augments the
endorsed set *G* with **ontology-grounded novel relationships** on which several models
already agree.

Concretely, we take the cross-model consensus clusters — groups of near-identical triples
produced by different models — and consider those that match no endorsed triple ("novel"
clusters). A novel cluster is admitted as a target *t* if at least `grounded_min_support`
of its member triples are *ontology-grounded*, meaning both the biological-object and the
biological-process term resolve to a controlled-vocabulary concept via the dictionary /
ontology grounding step (grounding match in {`aopwiki`, `exact`}; `fuzzy` optionally
included). Requiring a support floor keeps each admitted target a genuine cross-model
*consensus* rather than a single model's grounded guess. A model *covers* such a target iff
it is a member of the cluster. Selection then proceeds by the same greedy maximum-coverage
algorithm over the union *G* ∪ {*t*}.

This is an **alternative objective, not a replacement**. Endorsed-only coverage is always
reported alongside the union coverage (the recommended subset's endorsed fraction and a
separate KEC-only coverage curve), so the reader can see exactly how much of any apparent
gain comes from genuinely endorsed relationships versus merely plausible candidates. Two
caveats are stated explicitly. First, ontology-grounded candidates typically far outnumber
endorsed triples, so the union objective is dominated by, and therefore favours breadth of,
plausible candidate relationships. Second, term-level grounding is weaker evidence than
curator endorsement: two individually valid ontology terms can still be combined into a
biologically implausible relationship. Grounded-novel coverage is thus a hypothesis-
generation signal — a ranked pool of candidate relationships for curator review — not a
correctness measure.

### Optional coupling with the model ranking

The selection can optionally be coupled to the SMAA ranking (parameter `smaa_link`, off by
default): in *tiebreak* mode, models with equal marginal coverage gain are ordered by their
SMAA first-rank probability; in *prefilter* mode, the candidate pool is restricted to the
top-*N* SMAA-ranked models before coverage selection. In the default (*none*) mode, ties are
broken deterministically by model name and all models are eligible.
