---
doc_id: data-export-and-retention
title: Data Export and Retention
doc_type: policy
audience: [all]
version: 2026-09
---

# Data Export and Retention

## Exporting your data

Every plan can export its data at any time. Exports include all files, folder structure, comments
and version history within the plan's history window.

- **Free and Pro**: export from Settings > Workspace > Export. Driftwood prepares a ZIP archive and
  emails a download link, valid for 7 days. Large exports can take up to 24 hours.
- **Enterprise**: in addition to the ZIP export, administrators can use the bulk export API, which
  streams files directly to a customer-owned S3 or Azure storage bucket.

## Retention after cancellation or deletion

| Plan | Data kept after cancellation or deletion |
|---|---|
| Free | 30 days |
| Pro | 60 days |
| Enterprise | 90 days, or as set in the contract |

During this period the data can be restored by reactivating the account. After it ends, the data
is permanently deleted from primary storage and removed from backups within a further 35 days.

## Deleted files

Files deleted by a user go to the workspace trash and can be restored for 30 days on Free, 180 days
on Pro and for the full version-history window on Enterprise. Emptying the trash is permanent.

## Legal hold

Enterprise administrators can place users or folders on legal hold, which prevents permanent
deletion until the hold is released. Legal hold is not available on Free or Pro.
