# Manual Checks

Use these checks after `scripts/check.sh` when validating owner-tab, translation,
and force-refresh behavior in a loaded Chromium extension.

## Multi-Tab Owner State

1. Start the backend and pair the extension.
2. Open two YouTube watch tabs with different video IDs.
3. In tab A, click `Analyze / Resume`.
4. Open the Side Panel and confirm it follows tab A's video.
5. Switch to tab B without analyzing it.
6. Confirm tab B does not clear tab A's Side Panel transcript or status.
7. In tab B, click `Analyze / Resume`.
8. Confirm the Side Panel switches to tab B only after tab B explicitly becomes owner.

## Translation Retry

1. Stop or block the configured translation provider.
2. Start analysis until a sentence translation is attempted.
3. Confirm the extension stays responsive and does not send parallel translation requests.
4. Restart the provider.
5. Revisit the same sentence or click `Analyze / Resume`.
6. Confirm failed or skipped provider results can be requested again.

## Force Re-Analyze

1. Click `Analyze / Resume` and wait for a ready state.
2. Click `Analyze / Resume` again and confirm the existing job is reused or resumed.
3. Click `Force Re-analyze`.
4. Confirm a fresh preparation job is requested and translation requests include force refresh.
