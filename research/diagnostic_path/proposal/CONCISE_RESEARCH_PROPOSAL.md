# Learning Diagnostic Navigation from Symptom Locations to Defect Origins

## Motivation and objective

Repository-level fault localization aims to identify code responsible for a reported issue. Existing agents are commonly trained and evaluated using their final predicted locations. Although this supervision can encourage effective exploration, correct localization does not establish whether an agent understands how the defect produces the observed failure.

Issue reports may provide a useful starting point: a function mentioned in a traceback, a public operation exhibiting incorrect behavior, or code that exposes a failure. We call this a **symptom-associated function**, rather than an “interface function,” because it need not be a public interface. Such functions may be easier to retrieve from the report, while the underlying defect originates elsewhere.

This research investigates whether an agent can learn to navigate from a symptom-associated function toward the defect origin while constructing a **diagnostic path**: an evidence-supported explanation linking the two through relevant program dependencies, conditions, and state transformations. The diagnostic path is distinct from the agent’s complete exploration history, which may contain detours and rejected hypotheses.

The central hypothesis is that learning these relationships may improve localization and provide useful context for automated program repair (APR) and bug reproduction test (BRT) generation. However, whether diagnostic-path learning provides benefits beyond accurate target-code identification remains an open question.

## Research questions

### RQ1: Does diagnostic-path information provide value beyond identifying the target code?

We will investigate whether diagnostic paths improve localization learning, repair success, or reproduction-test quality compared with providing accurate locations and relevant source code alone. This question examines whether understanding the failure mechanism offers additional value, rather than assuming that richer explanations necessarily improve downstream performance.

### RQ2: How can reliable reference diagnostic paths be constructed?

Training and evaluation require reference explanations connecting symptom locations to defect origins. The challenge is to distinguish a plausible structural route from a supported failure mechanism. Reference annotations must identify relevant transitions, their supporting evidence, and an appropriate level of detail. They must also accommodate alternative valid explanations rather than assume that every issue has one uniquely correct path. Developer-edited functions and executed call chains may provide evidence, but do not independently establish diagnostic correctness.

### RQ3: What supervision is appropriate for learning diagnostic navigation?

We will investigate whether outcome-based rewards—such as correct localization or the quality of the final diagnostic explanation—are sufficient, or whether intermediate process rewards provide additional benefits. This includes determining what constitutes meaningful diagnostic progress and how intermediate feedback can encourage useful exploration without rewarding unsupported explanations or enforcing one prescribed investigation sequence.

## Scope and intended contribution

An initial controlled setting may provide a known symptom location or failing-test entry point, allowing the study to isolate diagnostic navigation from starting-point retrieval.

The intended contribution is a clearer formulation and empirical evaluation of diagnostic-path learning, together with an investigation of reference-label construction and suitable training supervision. The research will establish whether learning the relationships between symptoms and defect origins is a useful capability beyond endpoint localization, and under which conditions it benefits APR or BRT generation.
