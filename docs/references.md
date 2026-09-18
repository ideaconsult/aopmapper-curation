BRIDGE: Benchmarking Large Language Models for Understanding Real-world Clinical Practice Text https://arxiv.org/abs/2504.19467 

SOTA
Adverse Outcome Pathways (AOPs) require models that integrate chemical, biological, and toxicological information. While general commercial and biomedical models are typically used for this task, specialized frameworks utilizing Retrieval-Augmented Generation (RAG) tied to AOP databases (like AOP-Smart and LLMao) are demonstrating the highest effectiveness. [1, 2, 3, 4, 5]
Specific large language models and architectures excelling in these areas include:
1. General-Purpose Leaders in Biomedical Reasoning
GPT-4 & GPT-4o (OpenAI): Recognized for strong zero-shot reasoning, literature mining, and comprehension of complex biomedical literature.
Gemini Pro & Med-PaLM 2 (Google): These models have excelled on benchmarks for medical and scientific question-answering. [1, 2, 3, 5]
2. Specialized Biomedical & Clinical Models
MedGPT & BioGPT: Domain-specific medical models that excel in clinical decision support, safety, and understanding complex biomedical relationships. [1, 2, 3, 4]
SciSpacy: Often deployed alongside primary LLMs for advanced Named Entity Recognition (NER), helping extract Key Events (KEs) and Molecular Initiating Events (MIEs) from literature. [1, 2, 3]
3. AOP-Specific AI Systems
AOP-Smart: A RAG-enhanced framework designed specifically to interact with AOP knowledge graphs (like AOP-Wiki) and retrieve specific key events. [1]
LLMao: A capstone project leveraging RAG to parse and query the Environmental Protection Agency's AOP database. [1]
CoTox: A Chain-of-Thought reasoning framework that integrates biological information (like pathway annotations) with chemical structure to predict organ-specific toxicity. [1]
Why these models work best for AOPs
Building an AOP is traditionally a time-consuming manual process that requires extracting causal links from massive amounts of fragmented scientific text. The best AOP pipelines do not rely solely on an LLM's basic parametric knowledge; instead, they combine powerful models like GPT-4 and BioBERT with a RAG pipeline that searches specific AOP ontologies and databases. This approach prevents hallucinations and ensures pathway traceability. [1, 2, 3, 4, 5]
To compare various general and specialized models on medical and scientific tasks, you can review performance data on the Open Medical-LLM Leaderboard or review the AIMultiple Healthcare LLM Evaluation. [1, 2]
https://huggingface.co/Tonic/med-gpt-oss-20b

https://huggingface.co/microsoft/biogpt
https://github.com/VHP4Safety/aop-builder
https://github.com/VHP4Safety/AOP-Suite
AOP-Smart is an advanced Retrieval-Augmented Generation (RAG) framework designed to reduce large language model (LLM) hallucinations in adverse outcome pathway (AOP) analysis. It leverages XML data from the AOP-Wiki to build a specialized knowledge base and integrates external evidence for enhanced, reliable mechanistic reasoning. [1, 2]
Actionable Details & Project Status
Architecture: The system utilizes question-related key entities (KEs) and key event relationships (KERs) to execute joint retrieval and knowledge expansion. [1]
Current Availability: At this time, AOP-Smart is described in academic literature and pre-print publications (such as on arXiv and ResearchGate). [1, 2]
GitHub Repository: As of June 2026, a dedicated or official public open-source repository named "AOP-Smart" has not been indexed or published. Researchers looking for AOP computational tools can refer to the VHP4Safety AOP-Suite or the AOP Builder Monorepo for related open-source efforts in the field. [1]
https://github.com/BeckResearchLab/llmao
https://github.com/dmis-lab/CoTox

I. State-of-the-Art Evaluation Frameworks and Benchmarks
The current SOTA recognizes that structured extraction requires highly specialized benchmarks. General reasoning evaluations are poor proxies for this skill, as formatting constraints can actually interfere with a model's reasoning capabilities.

Comprehensive Multi-Format Benchmarks: Frameworks like StructEval have emerged as major standards. StructEval tests LLMs on their ability to generate and convert among 21 different non-renderable (JSON, YAML, CSV) and renderable (HTML, React, SVG) structured formats (Yang et al., 2025). It explicitly separates the evaluation into generation tasks (text to structure) and conversion tasks (structure to structure), finding that generating structures from natural language is significantly harder than converting between formats.

Modality-Agnostic Value Accuracy (SOB): Newer frameworks, such as the Structured Output Benchmark (SOB), span multiple source modalities (native text, OCR'd PDFs, and audio transcripts). They focus heavily on exact leaf-value matches—evaluating if the values inside the structure faithfully represent the source material, which is where many frontier models still struggle.

Multimodal Structured Output (SO-Bench): For Multimodal LLMs processing visual data like dense tables, charts, or UI screens, benchmarks evaluate the model's ability to pull structured elements strictly from the visual input, penalizing models that hallucinate values based on their pre-training rather than reading the provided image.

Domain-Specific Extraction Pipelines: Evaluation is often highly tailored to specific domains with complex structural dependencies. For instance, in automated clinical meta-analyses, benchmarks assess LLMs on extracting varying data types (e.g., statistical results versus risk-of-bias characteristics) to determine what level of automation is safe and reliable (Li et al., 2025). Similarly, benchmarks like Struct-Bench evaluate the quality of structured natural language generation in specialized scenarios, such as creating privacy-preserving synthetic data represented by context-free grammars (Wang et al., 2025).


II. Key Evaluation Metrics
You can't rely on standard NLP metrics like ROUGE or BLEU for data extraction. SOTA evaluation pipelines decompose scoring into rigorous, targeted dimensions:

Schema Adherence / Compliance: Does the output compile? Does it exactly match the required JSON schema, Pydantic model, or Context-Free Grammar?

Value-Level Accuracy (Fidelity): This is the ultimate test. Advanced evaluators break down errors into a specific taxonomy:

Value deviation/error: The data type is correct, but the extracted content is wrong, miscalculated, or hallucinatory.

Type error: The schema expected an integer, but the model provided a stringified number or narrative text.

Structural Correctness: Testing if nested arrays, lists, or hierarchical graphs map to the correct semantic relationships from the raw text (e.g., grouping the correct side-effects to the correct medication in a medical record).

III. State-of-the-Art Extraction Methodologies (The "Plus")
To actually achieve high scores on these benchmarks, relying on simple "Please output JSON" zero-shot prompts is outdated. Here is how modern systems are extracting data reliably:

Constrained Decoding: This is the current gold standard for the structural side. Frameworks force the LLM's inference engine to only generate tokens that adhere strictly to a predefined JSON Schema or grammar. The model physically cannot output a syntactically invalid JSON, ensuring 100% schema compliance and freeing the model to focus on value accuracy.

Decoupled Extraction (Semantic vs. Formatting): Forcing an LLM to extract data and format it simultaneously can degrade its reasoning performance. A SOTA workflow often separates these concerns: it first prompts the LLM to extract the raw information into a text scratchpad, and then uses a second pass (or a cheaper, faster model) strictly to map that extracted text into the target JSON structure.

Multi-LLM Ensembles and Consensus: For critical tasks where field omissions are costly, SOTA systems use an ensemble of different frontier models. The outputs are systematically parsed into standardized columns, and a consensus mechanism (like majority voting) locks in the structured data, flagging any malformed outputs or disagreements for human review.

Code-Augmented Reasoning: For highly structured inputs like tables, models are increasingly prompted to write execution code (e.g., Python or SQL) to retrieve, aggregate, or format the data, rather than trying to parse and map it strictly within their context window using natural language alone.

References
Li, L., Mathrani, A., & Susnjak, T. (2025). What level of automation is "good enough"? A benchmark of large language models for meta-analysis data extraction. arXiv. https://doi.org/10.48550/arxiv.2507.15152
Cited by: 4

Wang, S., Raunak, V., Backurs, A., Reis, V., Zhou, P., Chen, S., Yang, L., Lin, Z., Yekhanin, S., & Fanti, G. (2025). Struct-Bench: A Benchmark for Differentially Private Structured Text Generation. arXiv. https://doi.org/10.48550/arxiv.2509.10696
Cited by: 7

Yang, J., Jiang, D., He, L., Siu, S., Zhang, Y., Liao, D., Li, Z., Zeng, H., Jia, Y., Wang, H., Schneider, B., Ruan, C., Ma, W., Lyu, Z., Wang, Y., Lu, Y., Do, Q. D., Jiang, Z., Nie, P., & Chen, W. (2025). StructEval: Benchmarking LLMs' Capabilities to Generate Structural Outputs. arXiv. https://doi.org/10.48550/arxiv.2505.20139
Cited by: 22


I. SOTA Semantic Evaluation MethodologiesWhen evaluating semantic correctness, rigid comparisons against a "ground truth" JSON fail because language is inherently variable (e.g., extracting "programmed cell death" instead of "apoptosis"). SOTA evaluation pipelines handle this through several advanced techniques:1. Tiered Hybrid Semantic MatchingInstead of binary pass/fail grading, cutting-edge workflows (like the one detailed in your provided document) evaluate extracted data through escalating tiers of semantic leniency:Tier 1 (Exact): Checking for exact Ontology Reference (IRI) or exact string name matches.Tier 2 (Embedding Similarity): If exact matches fail, projecting both the extracted value and the ground truth into a vector space using a domain-specific embedder (e.g., SapBERT). A match is granted if the cosine similarity meets a specific threshold ($\ge \tau$).Tier 3 (LLM-as-a-Judge): Using a separate LLM to resolve borderline embedding cases.2. Atomic Claim Extraction & QAG (Question-Answer Generation)Modern evaluation frameworks (like DeepEval's metrics or Opik's "Meaning Match") do not compare JSON objects directly. Instead, they flatten the extracted JSON into atomic claims. The evaluator then uses a strictly constrained LLM-as-a-judge to read the original source text and answer a binary question for each claim: "Does the source text explicitly support this value?" This measures Faithfulness (no hallucinations) and Answer Relevancy independent of formatting.3. Cross-Model Consensus & Ground-Truth AuditingA major realization in recent benchmarks is that human-curated ground truth is often flawed or incomplete.Consensus Support: SOTA pipelines evaluate extraction quality on unannotated text by running a panel of 10–20 different models. If multiple distinct models independently extract the exact same semantic triple, that "consensus support" acts as a ground-truth-free trust signal.Label-Noise Auditing: Before evaluating an LLM against a human dataset, SOTA systems employ an LLM judge to audit the human annotations, categorizing them into correct, partial, or wrong to calibrate the evaluation baseline.II. SOTA Evaluation MetricsGeneral NLP metrics (ROUGE, BLEU) are useless for structured data. Recent benchmarks like LLMStructBench (2026) and ExtractBench decompose semantic evaluation into highly specific error taxonomies:Value Error (Hallucination): The model populated the correct Pydantic field with the correct data type, but the information is completely fabricated or contradicts the text.Value Deviation: The model extracted the correct semantic concept, but the phrasing differs slightly from the reference. Evaluated using Levenshtein distance or semantic embedding similarity.Coercible Mismatch: The semantics are correct, but the underlying type is slightly off (e.g., outputting the string "42" instead of the integer 42). SOTA evaluators grant partial credit here rather than a binary fail.III. Actual Extraction Methodology SOTA (The "Plus")To achieve high scores on these semantic benchmarks, relying purely on zero-shot prompting with a Pydantic schema is no longer enough.Token Log-Probability Calibration (Confidence Gating): Instead of trusting whatever the LLM outputs, SOTA pipelines extract the token-level log-probabilities (logprobs) from the model's forward pass. For fields requiring a controlled vocabulary (e.g., biological actions like "increased" vs. "decreased"), pipelines calculate the classification margin—the probability gap between the chosen token and the runner-up. This margin is a highly predictive signal of semantic correctness, allowing systems to automatically route low-confidence extractions to a human reviewer.Decoupled Semantic Scratchpads: Forcing an LLM to simultaneously reason about text, find entities, and map them to a complex nested schema degrades semantic accuracy. The current best practice is a two-pass pipeline:Prompt the model to extract all relevant facts into an unstructured "scratchpad."Pass that scratchpad back to the model (or a cheaper, faster model) strictly to map the extracted facts into the target Pydantic schema.