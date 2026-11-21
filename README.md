# Signal to Study

Signal to Study helps editors see how the wording of a news alert shapes what readers understand about a claim. [Explore the interactive study](https://signal-to-study.vercel.app/)

## Who measured it?

Google says its new chip cuts latency by up to half. The plain alert attributes the claim to Google. The revised alert adds two facts an editor might otherwise leave implicit: Google supplied the measurement, and nobody outside Google had verified it.

In this study, spelling out those facts changes which evidence answer is selected. The average ratings for trust and willingness to open the alert barely move.

| Across the Google and ElevenLabs stories | Plain alert | Source labeled |
| --- | ---: | ---: |
| Answers identifying the figure as the company’s own | 69 / 146 (47%) | 101 / 146 (69%) |
| Trust in the alert, out of 5 | 2.86 | 2.98 |
| Willingness to open, out of 5 | 2.83 | 2.86 |

**Where the difference comes from.** Of 100 example records, 73 met the study criteria. Each contributes one comparison per story. In **36 pairs**, the plain answer was “the alert doesn’t say” and the labeled answer was “company claim.” In **11 pairs**, those answers were reversed. The plain alert leaves outside testing unstated, so “doesn’t say” is a defensible reading.

**What to test with readers.** This worked example assumes a benefit from labeling. Even the comparison that assumes zero benefit produces a 12-point rise in this sample. A follow-up could ask “Who supplied the number?” and “Does the alert report an outside test?” separately. [Survey wording and answer key](fixtures/survey-key.json) · [Aggregate counts for both comparisons](data/summary.json)

## Run locally

Use Python 3.10 or newer. The dashboard and tests use the standard library and require no credentials.

```sh
git clone https://github.com/yihan2588/signal-to-study.git
cd signal-to-study
python3 -m unittest discover -s tests
python3 -m http.server 8000 --bind 127.0.0.1 --directory docs
```

Open [the local dashboard](http://127.0.0.1:8000/). Switch between the two alerts to see the exact wording, then expand **How to read this comparison** for the method.

## How the analysis works

- **From counts to percentages.** In the [aggregate results](data/summary.json), each version has 146 answers. For labeled alerts, 101 company attributions divided by 146 gives 69%. Dividing 435 trust points by 146 gives 2.98. The paired-answer table uses plain answers as rows and labeled answers as columns.
- **Scoring paired answers.** The [survey parser](hunchlab/survey.py) applies eligibility rules and matches each answer to its story and format. The [paired statistics](hunchlab/stats.py) compare formats within a record. The public data file contains only totals and cross-tabulations.
- **Try the design verifier.** Run the command below. It reports why changing both the news story and the format prevents a clean comparison. Repeat with `repaired.json` to see which checks the revised design passes. [Verifier inputs](docs/STUDY_INPUTS.md)

```sh
python3 -m hunchlab verify fixtures/study/claim.json fixtures/study/confounded.json
```
