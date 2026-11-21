# Review Flashpoint inputs for the verifier

Use the verifier for a two-arm randomized comparison with one exposure per respondent and one primary multiple-choice question. The output is an observed difference in correct-answer rates for the supplied data. A product decision also needs a review of sample size, recruitment, missing responses, and uncertainty.

## Prepare the claim and instrument

1. Copy [the example claim](../fixtures/study/claim.json). Name the approved story, control and treatment arm IDs, exact addition in the treatment, and the question, ordered choices and correct choice ID. Have the team review this record before looking at outcomes.
2. In Flashpoint, inspect the target survey version, section paths, and settings used to assign readers. Record the actual content, addition, question, and choices shown in each arm, including order. Use [the example instrument](../fixtures/study/repaired.json) as a template. Set `source.kind` to `flashpoint` and give `source.reference` a version or path reference the team can use for review. Use the real survey ID and revision. The positive integer `assignment.weights` are relative probabilities for the arms, such as `1` and `1` for an equal split. [Flashpoint survey paths and versions](https://docs.flashpoint.ai/mcp-capabilities)
3. Run `python3 -m hunchlab verify CLAIM.json INSTRUMENT.json`. For a blocked design, inspect the blockers and adjust a draft with the team. The proposal command assembles a local candidate around the approved story, preserving its base alert and using equal randomization. Review the candidate against the actual Flashpoint paths before fielding. A saved local proposal cannot be used to score responses.

## Map the first-party responses

Export responses from the fielded survey and review the exposure and revision records. Flashpoint documents response CSV exports and a response detail view with the order of blocks and questions shown. Use the available data for your project to establish each row's actual revision and exposure. If your export lacks those fields, record what is missing and use the response details or other reviewed records before scoring. [Flashpoint exports](https://docs.flashpoint.ai/surveys-import-export) · [Flashpoint response details](https://docs.flashpoint.ai/mcp-capabilities#analysis-conjoint-and-exports)

Store the normalized CSV under `flashpoint-exports/`, which is ignored by Git. Use these headers in any order.

| Field | Value |
| --- | --- |
| `study_id`, `revision` | The survey ID and fielded version for that response. |
| `sample` | `first_party` for verified first-party records. `fixture` is reserved for the offline demo. |
| `respondent_id` | A stable pseudonym unique within this comparison. Use one row per respondent. |
| `story_id`, `arm_id` | The story and version the respondent was shown, using the instrument's IDs. |
| `question_id`, `answer_id` | The primary question and selected choice, using the approved IDs. A recorded “Not sure” choice can be an ordinary incorrect answer. |

Keep the original export and mapping privately for review. Account for incomplete responses and exclusions when recording the sample. The scoring command blocks duplicate respondents, invalid exposures, missing or unexpected answers, mixed revisions, and missing arms. It checks the consistency of the supplied records and relies on the team's review of what Flashpoint fielded. The JSON verdict reports aggregates and input fingerprints.
