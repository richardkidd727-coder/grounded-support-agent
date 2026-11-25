# Retrieval evaluation

- Embedding model: `BAAI/bge-small-en-v1.5`
- Query instruction: on
- Chunker: `structure-v2:350:60`
- Retrieval: hybrid (pgvector cosine + Postgres FTS), RRF, 20 candidates per retriever, top 5
- Cases: 30 in scope, 12 out of scope

## Ranking (in-scope cases)

| Hit@1 | Hit@3 | Hit@5 | MRR |
|---|---|---|---|
| 90% | 100% | 100% | 0.950 |

## Confidence gate (top cosine similarity)

- In scope: min 0.583 / median 0.728 / max 0.878
- Out of scope: min 0.403 / median 0.564 / max 0.698
- Best *single* threshold: 0.70 - accepts 77% of answerable questions, rejects 100% of out-of-scope ones (balanced accuracy 88%). The two distributions overlap, so a single cut-off must trade wrong answers against needless escalations.

### Three-band gate (used by the agent)

| Band | Similarity | Action |
|---|---|---|
| Reject | < 0.58 | Out of scope, no LLM call |
| Uncertain | 0.58 - 0.70 | Grounding judge decides |
| Confident | >= 0.70 | Answer, grounded on retrieved chunks |

- Answerable questions sent to the judge: 23% (none rejected outright)
- Out-of-scope questions rejected outright: 50%; sent to the judge: 50% (none accepted outright)

## Per case

| Case | Tier | In scope | Rank | Top sim | Gate band | Top section |
|---|---|---|---|---|---|---|
| refund-pro-monthly-window | pro | yes | 1 | 0.878 | confident | Refund Policy > Pro plan > Monthly Pro subscriptions |
| refund-pro-annual | pro | yes | 1 | 0.703 | confident | Refund Policy > Pro plan > Annual Pro subscriptions |
| refund-enterprise-limit | enterprise | yes | 2 | 0.704 | confident | Refund Policy > General rules |
| refund-free-accidental-upgrade | free | yes | 2 | 0.704 | confident | Refund Policy > Non-refundable items |
| refund-overage | unverified | yes | 1 | 0.786 | confident | Refund Policy > Non-refundable items |
| refund-timing | pro | yes | 1 | 0.764 | confident | Refund Policy > General rules |
| plans-compare | unverified | yes | 1 | 0.671 | judge | Subscription Plans > Pro plan |
| plans-max-file-size | pro | yes | 1 | 0.611 | judge | Troubleshooting File Sync > Files that never sync |
| billing-failed-payment | pro | yes | 1 | 0.728 | confident | Billing and Payments > Failed payments |
| billing-vat | pro | yes | 1 | 0.800 | confident | Billing and Payments > Invoices and receipts |
| cancel-pro-renewal | pro | yes | 1 | 0.725 | confident | Cancellation and Downgrades > Cancelling a Pro subscription |
| enterprise-notice | enterprise | yes | 1 | 0.773 | confident | Enterprise Contracts and Renewals > Renewals |
| enterprise-expired | enterprise | yes | 1 | 0.709 | confident | Enterprise Contracts and Renewals > Contract status |
| enterprise-outage-credit | enterprise | yes | 1 | 0.583 | judge | Uptime SLA and Service Credits > Credit amounts |
| support-free-hours | free | yes | 1 | 0.822 | confident | Support Hours and Response Times > Free plan support |
| support-p1-response | enterprise | yes | 1 | 0.668 | judge | Support Hours and Response Times > Enterprise priority levels |
| security-lost-2fa | unverified | yes | 1 | 0.667 | judge | Account Security and Identity Verification > Lost two-factor device |
| security-verify | unverified | yes | 1 | 0.732 | confident | Account Security and Identity Verification > Verifying identity before account changes |
| account-billing-email | pro | yes | 1 | 0.869 | confident | Billing and Payments > Changing the billing email |
| data-export | pro | yes | 2 | 0.678 | judge | Troubleshooting File Sync > Files that never sync |
| data-retention | pro | yes | 1 | 0.862 | confident | Data Export and Retention > Retention after cancellation or deletion |
| privacy-residency | enterprise | yes | 1 | 0.752 | confident | Privacy and Personal Data Requests > Data residency |
| privacy-gdpr-delete | unverified | yes | 1 | 0.728 | confident | Privacy and Personal Data Requests > Personal data requests |
| escalation-human | unverified | yes | 1 | 0.620 | judge | Escalation to a Human Agent > When a conversation must be escalated |
| login-password | unverified | yes | 1 | 0.719 | confident | Troubleshooting Sign-in Problems > Forgotten password |
| login-sso | enterprise | yes | 1 | 0.788 | confident | Troubleshooting Sign-in Problems > Single sign-on (SAML) errors |
| sync-conflict | unverified | yes | 1 | 0.764 | confident | Troubleshooting File Sync > Sync conflicts |
| api-429 | pro | yes | 1 | 0.717 | confident | Troubleshooting API Errors > 429 Too Many Requests |
| api-401 | pro | yes | 1 | 0.801 | confident | Troubleshooting API Errors > 401 Unauthorized |
| api-webhooks | pro | yes | 1 | 0.747 | confident | Troubleshooting API Errors > Webhooks not arriving |
| oos-weather | pro | no | - | 0.502 | reject | Escalation to a Human Agent > Ticket priority |
| oos-taxes | pro | no | - | 0.614 | judge | Billing and Payments > Taxes |
| oos-poem | unverified | no | - | 0.408 | reject | Data Export and Retention > Exporting your data |
| oos-capital | unverified | no | - | 0.403 | reject | Troubleshooting API Errors > 5xx server errors |
| oos-laptop | pro | no | - | 0.462 | reject | Account Security and Identity Verification > Two-factor authentication |
| oos-translate | unverified | no | - | 0.475 | reject | Support Hours and Response Times > Support channels by plan |
| oos-stock | enterprise | no | - | 0.647 | judge | Uptime SLA and Service Credits > Uptime commitment |
| near-miss-netflix | pro | no | - | 0.679 | judge | Cancellation and Downgrades > Cancelling a Pro subscription |
| near-miss-gym-refund | pro | no | - | 0.698 | judge | Refund Policy > Pro plan > Monthly Pro subscriptions |
| near-miss-iphone-reset | unverified | no | - | 0.595 | judge | Account Security and Identity Verification > Lost two-factor device |
| near-miss-usb | free | no | - | 0.589 | judge | Data Export and Retention > Exporting your data |
| near-miss-router | pro | no | - | 0.539 | reject | Troubleshooting API Errors > 429 Too Many Requests |
