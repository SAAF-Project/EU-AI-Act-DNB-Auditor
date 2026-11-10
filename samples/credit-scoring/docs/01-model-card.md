# Model card - RetailCredit Score v3 (provided by ScoreCo B.V.)

*Synthetic document for the SAAF hackathon.*

## Purpose
RetailCredit Score v3 estimates the probability of default within 12 months for consumer loan applicants and converts it to a score between 300 and 850.

## Training data
Trained on 1.2 million historical loan applications from Dutch lenders between 2015 and 2023. Features include income, existing debt, BKR registration status, payment history, employment type, age band and postcode area.

## Performance
On the 2023 hold-out set the model reached a Gini coefficient of 0.61. Performance is reviewed by ScoreCo once per year.

## Limitations
The model has not been validated for self-employed applicants or applicants with less than two years of credit history. Postcode area may act as a proxy for ethnic origin; ScoreCo has not performed a bias examination on this feature.

## Explainability
For each score the model returns the top three contributing features (reason codes). Reason codes are available to credit officers in the loan origination screen.
