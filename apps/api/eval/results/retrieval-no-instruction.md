# Retrieval evaluation

- Embedding model: `BAAI/bge-small-en-v1.5`
- Query instruction: off
- Chunker: `structure-v2:350:60`
- Retrieval: hybrid (pgvector cosine + Postgres FTS), RRF, 20 candidates per retriever, top 5
- Cases: 30 in scope, 12 out of scope

## Ranking (in-scope cases)

| Hit@1 | Hit@3 | Hit@5 | MRR |
|---|---|---|---|
| 90% | 100% | 100% | 0.944 |

## Confidence gate (top cosine similarity)

- In scope: min 0.610 / median 0.741 / max 0.882
- Out of scope: min 0.431 / median 0.566 / max 0.723
- Best *single* threshold: 0.69 - accepts 83% of answerable questions, rejects 92% of out-of-scope ones (balanced accuracy 88%). The two distributions overlap, so a single cut-off must trade wrong answers against needless escalations.

### Three-band gate (used by the agent)

| Band | Similarity | Action |
|---|---|---|
| Reject | < 0.61 | Out of scope, no LLM call |
| Uncertain | 0.61 - 0.73 | Grounding judge decides |
| Confident | >= 0.73 | Answer, grounded on retrieved chunks |

- Answerable questions sent to the judge: 47% (none rejected outright)
- Out-of-scope questions rejected outright: 58%; sent to the judge: 42% (none accepted outright)

## Per case

| Case | Tier | In scope | Rank | Top sim | Gate band | Top section |
|---|---|---|---|---|---|---|
| refund-pro-monthly-window | pro | yes | 1 | 0.882 | confident | Refund Policy > Pro plan > Monthly Pro subscriptions |
| refund-pro-annual | pro | yes | 1 | 0.720 | judge | Refund Policy > Pro plan > Annual Pro subscriptions |
| refund-enterprise-limit | enterprise | yes | 2 | 0.752 | confident | Refund Policy > General rules |
| refund-free-accidental-upgrade | free | yes | 2 | 0.712 | judge | Refund Policy > Non-refundable items |
| refund-overage | unverified | yes | 1 | 0.788 | confident | Refund Policy > Non-refundable items |
| refund-timing | pro | yes | 1 | 0.765 | confident | Refund Policy > General rules |
| plans-compare | unverified | yes | 1 | 0.696 | judge | Subscription Plans > Pro plan |
| plans-max-file-size | pro | yes | 1 | 0.649 | judge | Troubleshooting File Sync > Files that never sync |
| billing-failed-payment | pro | yes | 1 | 0.727 | judge | Billing and Payments > Failed payments |
| billing-vat | pro | yes | 1 | 0.794 | confident | Billing and Payments > Invoices and receipts |
| cancel-pro-renewal | pro | yes | 1 | 0.746 | confident | Cancellation and Downgrades > Cancelling a Pro subscription |
| enterprise-notice | enterprise | yes | 1 | 0.786 | confident | Enterprise Contracts and Renewals > Renewals |
| enterprise-expired | enterprise | yes | 1 | 0.723 | judge | Enterprise Contracts and Renewals > Contract status |
| enterprise-outage-credit | enterprise | yes | 1 | 0.612 | judge | Uptime SLA and Service Credits > Credit amounts |
| support-free-hours | free | yes | 1 | 0.834 | confident | Support Hours and Response Times > Free plan support |
| support-p1-response | enterprise | yes | 1 | 0.695 | judge | Support Hours and Response Times > Enterprise priority levels |
| security-lost-2fa | unverified | yes | 1 | 0.649 | judge | Account Security and Identity Verification > Lost two-factor device |
| security-verify | unverified | yes | 1 | 0.737 | confident | Account Security and Identity Verification > Verifying identity before account changes |
| account-billing-email | pro | yes | 1 | 0.865 | confident | Billing and Payments > Changing the billing email |
| data-export | pro | yes | 3 | 0.674 | judge | Troubleshooting File Sync > Files that never sync |
| data-retention | pro | yes | 1 | 0.862 | confident | Data Export and Retention > Retention after cancellation or deletion |
| privacy-residency | enterprise | yes | 1 | 0.763 | confident | Privacy and Personal Data Requests > Data residency |
| privacy-gdpr-delete | unverified | yes | 1 | 0.729 | judge | Privacy and Personal Data Requests > Personal data requests |
| escalation-human | unverified | yes | 1 | 0.610 | judge | Escalation to a Human Agent > When a conversation must be escalated |
| login-password | unverified | yes | 1 | 0.701 | judge | Troubleshooting Sign-in Problems > Forgotten password |
| login-sso | enterprise | yes | 1 | 0.783 | confident | Troubleshooting Sign-in Problems > Single sign-on (SAML) errors |
| sync-conflict | unverified | yes | 1 | 0.755 | confident | Troubleshooting File Sync > Sync conflicts |
| api-429 | pro | yes | 1 | 0.728 | judge | Troubleshooting API Errors > 429 Too Many Requests |
| api-401 | pro | yes | 1 | 0.811 | confident | Troubleshooting API Errors > 401 Unauthorized |
| api-webhooks | pro | yes | 1 | 0.744 | confident | Troubleshooting API Errors > Webhooks not arriving |
| oos-weather | pro | no | - | 0.500 | reject | Escalation to a Human Agent > Ticket priority |
| oos-taxes | pro | no | - | 0.623 | judge | Billing and Payments > Taxes |
| oos-poem | unverified | no | - | 0.452 | reject | Troubleshooting API Errors > 5xx server errors |
| oos-capital | unverified | no | - | 0.431 | reject | Account Management > Changing the workspace name |
| oos-laptop | pro | no | - | 0.458 | reject | Account Security and Identity Verification > Two-factor authentication |
| oos-translate | unverified | no | - | 0.508 | reject | Support Hours and Response Times > Support channels by plan |
| oos-stock | enterprise | no | - | 0.675 | judge | Uptime SLA and Service Credits > Uptime commitment |
| near-miss-netflix | pro | no | - | 0.672 | judge | Cancellation and Downgrades > Cancelling a Pro subscription |
| near-miss-gym-refund | pro | no | - | 0.723 | judge | Refund Policy > Non-refundable items |
| near-miss-iphone-reset | unverified | no | - | 0.586 | reject | Account Security and Identity Verification > Lost two-factor device |
| near-miss-usb | free | no | - | 0.625 | judge | Data Export and Retention > Exporting your data |
| near-miss-router | pro | no | - | 0.546 | reject | Troubleshooting API Errors > 429 Too Many Requests |
