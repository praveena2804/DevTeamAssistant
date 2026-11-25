# Evaluation: brd_analyst.system v1

| Field | Value |
|---|---|
| Prompt hash | `b76bde95ce7564cf01ca0bcb878f819254b6ae5b5821c45c39731e125eb2523a` |
| Model | `qwen2.5:3b` (digest `357c53fb659c…`) |
| Dataset | `smoke` (`44b0487acd01…`), 3 cases |
| Created | 2026-11-25T14:02:24+00:00 |

Scores are 0 to 1 (1 = best). Pass thresholds are set per agent part (D-12).

| Case | Schema valid | Scores | Time |
|---|---|---|---|
| `lib-goal-scope` | yes | checklist_recall 1.00, citation_validity 1.00, no_forbidden_terms 1.00, schema_valid 1.00, term_recall 1.00 | 48.8 s |
| `lib-roles-features` | yes | checklist_recall 1.00, citation_validity 1.00, no_forbidden_terms 1.00, schema_valid 1.00, term_recall 1.00 | 56.4 s |
| `lib-rules-acceptance` | yes | checklist_recall 1.00, citation_validity 1.00, no_forbidden_terms 1.00, schema_valid 1.00, term_recall 1.00 | 52.9 s |

**Totals:** cases 3, checklist_recall 1, citation_validity 1, no_forbidden_terms 1, schema_valid 1, term_recall 1
