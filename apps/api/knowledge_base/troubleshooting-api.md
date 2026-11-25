---
doc_id: troubleshooting-api
title: Troubleshooting API Errors
doc_type: troubleshooting
audience: [all]
version: 2026-09
---

# Troubleshooting API Errors

## 401 Unauthorized

The API token is missing, malformed or revoked. Tokens are sent in the Authorization header as
"Bearer <token>". Tokens expire after 90 days of inactivity. Create a new token in Settings >
Developer > API tokens.

## 403 Forbidden

The token is valid but does not have the scope needed for the request, or the user behind the
token lost access to the resource. Check the token scopes and the user's workspace role.

## 429 Too Many Requests

The workspace has exceeded its API rate limit:

| Plan | Rate limit |
|---|---|
| Free | 60 requests per minute |
| Pro | 600 requests per minute |
| Enterprise | 6,000 requests per minute |

Every 429 response includes a Retry-After header with the number of seconds to wait. Clients should
wait for that long and retry with exponential backoff. Enterprise customers can request a higher
limit through their success manager.

## 5xx server errors

500, 502, 503 and 504 errors are temporary. Retry with exponential backoff, starting at 1 second
and doubling up to 32 seconds. If errors continue for more than 5 minutes, check the status page and
contact support with the request ID from the X-Request-ID response header.

## Webhooks not arriving

Webhook deliveries that fail are retried 8 times over 24 hours. Endpoints must respond with a 2xx
status within 10 seconds. Delivery attempts and responses are listed in Settings > Developer >
Webhooks.
