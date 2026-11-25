---
doc_id: troubleshooting-sync
title: Troubleshooting File Sync
doc_type: troubleshooting
audience: [all]
version: 2026-09
---

# Troubleshooting File Sync

## Files are not syncing

Work through these steps in order:

1. Check the status page for an ongoing incident.
2. Make sure the desktop app shows "Up to date" or "Syncing" and is not paused. Paused sync resumes
   from the tray menu.
3. Confirm the account is not over its storage limit. Over-limit accounts stop uploading but keep
   downloading.
4. Check the file is not larger than the plan's maximum file size: 250 MB on Free, 10 GB on Pro and
   50 GB on Enterprise.
5. Restart the desktop app. If the problem continues, collect logs (below) and contact support.

## Sync conflicts

When the same file is edited on two devices before they sync, Driftwood keeps both versions. The
second copy is renamed with "(conflicted copy)" and the device name and date. Neither version is
lost. Compare the two files and delete the one that is not needed.

## Files that never sync

Some files are always skipped: temporary files (names starting with ~$ or ending in .tmp), system
files such as .DS_Store and desktop.ini, and files whose path is longer than 260 characters on
Windows. Rename or move these files to sync them.

## Collecting sync logs

In the desktop app, open Help > Export diagnostics. This creates a ZIP file with the last 7 days of
sync logs and no file contents. Attach it to a support ticket.
