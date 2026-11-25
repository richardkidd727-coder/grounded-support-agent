---
doc_id: escalation-policy
title: Escalation to a Human Agent
doc_type: policy
audience: [all]
version: 2026-09
---

# Escalation to a Human Agent

## When a conversation must be escalated

The AI assistant must hand the conversation to a human agent by creating an escalation ticket when
any of the following is true:

- The customer asks to speak to a person.
- A refund or credit is above the limit the assistant is allowed to issue.
- The question is not covered by Driftwood's policies, or the assistant is not confident its
  answer is supported by them.
- The customer reports possible account compromise, data loss or a security incident.
- The customer mentions legal action, a regulator or a law enforcement request.
- Identity verification has failed three times.
- An Enterprise customer reports a P1 or P2 issue.

## Ticket priority

| Situation | Priority |
|---|---|
| Security incident, data loss, Enterprise P1 | Urgent |
| Refund or credit above limit, Enterprise P2, legal mention | High |
| Customer asked for a human, unanswered policy question | Normal |
| Feature requests and general feedback | Low |

## What an escalation ticket must contain

Every ticket must include a short summary of the problem, the customer's plan, whether identity was
verified, what the assistant already tried, and the customer's preferred contact method. The
customer is told the ticket number and the expected response time for their plan.

## Expected response after escalation

Escalated tickets follow the response targets in the Support Hours policy for the customer's plan.
Urgent tickets are routed immediately to the on-call team regardless of plan.
