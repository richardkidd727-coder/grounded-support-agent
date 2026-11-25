---
doc_id: troubleshooting-login
title: Troubleshooting Sign-in Problems
doc_type: troubleshooting
audience: [all]
version: 2026-09
---

# Troubleshooting Sign-in Problems

## Forgotten password

1. Go to the sign-in page and choose "Forgot password".
2. Enter the account email. A reset link is sent within 5 minutes and is valid for 1 hour.
3. If the email does not arrive, check the spam folder and confirm the address is the one on the
   account. Reset emails come from no-reply@driftwood.example.

Password reset links can be requested at most 5 times per hour.

## "Account locked" message

After 10 failed sign-in attempts the account is locked for 30 minutes. Waiting is the fastest fix.
Support cannot unlock an account early unless the customer's identity has been verified and the
failed attempts were not suspicious.

## Single sign-on (SAML) errors
<!-- audience: enterprise -->

Enterprise workspaces that use SAML single sign-on show "SSO configuration error" when the identity
provider certificate has expired or the user is not assigned to the Driftwood app in the identity
provider. The workspace administrator should:

1. Check that the user is assigned to the Driftwood application in their identity provider.
2. Upload the renewed certificate in Settings > Security > SSO if it has expired.
3. Use the SSO test tool on the same page to see the exact error from the identity provider.

Administrators can always sign in with their backup admin password if SSO is broken.

## Desktop app keeps asking to sign in

If the desktop app signs out repeatedly, the system clock is usually wrong. Correct the computer's
date and time, then sign in again. If it continues, sign out from Settings > Account in the app and
reinstall the latest version.
