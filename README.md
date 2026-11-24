# Fort скелет public donor feed

This directory is the complete, isolated repository content for `60GramsOfCarbonMonoxide/fort-skelet-donors`. Publish **only these files** to that new public repository. Do not copy the parent Ko-fi bridge directory, private database, webhook payloads, Unity project, or deployment credentials.

The workflow reads the public, names-only feed from:

`https://zfgdvgrvuocshtxrclpj.supabase.co/functions/v1/fort-kofi/donors.json`

The intended VRChat-trusted output is:

`https://60gramsofcarbonmonoxide.github.io/fort-skelet-donors/donors.json`

The Supabase source is deployed and its public schema-1 feed has returned HTTP 200 with zero verified donors. This prepared mirror is **awaiting its first verified GitHub Pages deployment**. Repository contents and a configured world URL do not prove that the trusted output is hosted or downloadable. Confirm the workflow deployment and public JSON URL before claiming the mirror is live, and verify the world's actual download separately.

This standalone public repository contains only the mirror tooling. Ko-fi webhook verification, donor identity linking and public badge consent belong to the private backend; no account credentials, payer records or private administration documents are included here.

## Enable the isolated repository

1. Sign in to GitHub and create the public repository `fort-skelet-donors` under `60GramsOfCarbonMonoxide`. If the GitHub connector is limited to selected repositories, grant it access to this new isolated repository before using it to commit the prepared files.
2. Commit this directory's contents, preserving `.github/workflows/sync-donors.yml`, `scripts/`, `tests/`, and the action pin record. The workflow must exist on the default branch for scheduled runs.
3. In repository **Settings → Pages → Build and deployment**, select **GitHub Actions**. Do not configure a custom domain: the intended trusted host is `*.github.io`.
4. Ensure the public Supabase feed returns the documented JSON contract with HTTP 200 and `Content-Type: application/json` without any API key or authorization header. Only verified, consented donor display names may appear there.
5. Open **Actions → Sync verified donor badge feed → Run workflow** to bootstrap the first deployment.
6. Confirm the `deploy` job succeeded and the output URL serves the expected JSON. The intended URL is already saved in both Unity scenes; verify an actual VRChat download separately before claiming badge delivery works.

The jobs use GitHub's built-in, short-lived `GITHUB_TOKEN` and Pages OIDC permissions. There is **no personal access token**, Supabase key, Ko-fi secret, or manually stored Actions secret. The independent maintenance job alone has `contents: write` to update one timestamp-only heartbeat file; the feed build retains read permission. Pages is deployed directly from an artifact, rather than by committing donor data back to the repository. Required deployment permissions and the `github-pages` environment follow [GitHub's custom Pages workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Synchronization behavior

- `workflow_dispatch` supports initial deployment and manual refresh.
- The cron expression requests a run every five minutes. GitHub may delay scheduled runs; this is not an exact delivery deadline. See [GitHub's scheduled workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
- A single concurrency group and `cancel-in-progress: false` prevent an active publication from being cancelled by the next run.
- The script fetches the current **deployed Pages feed** for comparison. It does not rely on a stale checkout copy or write-generated-file commits.
- The donor list is deduplicated and sorted. Changes only to source timestamp, list order, or duplicate entries do not trigger deployment.
- When the donor set is unchanged, the prior published `generatedUtc` is retained and the deploy job is skipped.
- A missing previous Pages file (HTTP 404) bootstraps the first publication, including an empty donor list. Other network errors, redirects, bad schemas or invalid JSON fail the job and leave the last deployed artifact in place.
- Only `_site/donors.json` is uploaded. There is no public website containing donor details or payment history.

## Monthly repository maintenance

GitHub still automatically disables a public repository's scheduled workflows after 60 days without repository activity. Repeated workflow runs or Pages artifact deployments should not be relied upon as source-repository activity. This workflow therefore performs a small maintenance commit after approximately 30 days without a newer default-branch commit or heartbeat. The policy is documented in [GitHub's current schedule reference](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

The independent `maintenance` job uses its native, short-lived `GITHUB_TOKEN` and the [GitHub Contents API](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents) to update exactly `.github/donor-feed-heartbeat.json`. That file contains only schema version and UTC heartbeat date. It does not contain donor information. The standard `github-actions[bot]` committer identity follows [GitHub's checkout example](https://github.com/actions/checkout#push-a-commit-using-the-built-in-token).

Recent repository commits postpone maintenance. A missing heartbeat file does not cause a commit on every run: a recently created repository waits until its last commit is 30 days old. Empty repositories are skipped, and a concurrent heartbeat update is re-read instead of being overwritten or duplicated. The API updates one file on the current branch tree, preserving source files; checkout still uses `persist-credentials: false`. No token is stored in git configuration or a committed file.

Maintenance runs independently of Supabase availability. It does **not** change the donor feed timestamp or trigger a Pages deployment; donor membership must still change for the deploy job to run. The workflow has only schedule/manual triggers, and commits made with `GITHUB_TOKEN` do not trigger ordinary recursive workflow runs. See [GitHub's token behavior](https://docs.github.com/en/actions/concepts/security/github_token#when-github_token-triggers-workflow-runs).

If repository rules block the maintenance commit, the job will fail rather than bypass branch protection. Permit this maintenance write in the isolated repository or make a real default-branch commit manually before the inactivity limit. A workflow already disabled by GitHub must first be re-enabled from its Actions page; a disabled workflow cannot run its own heartbeat. Scheduling remains subject to GitHub service availability and policy changes.

## Public contract and validation

```json
{
  "schema": 1,
  "generatedUtc": "2026-11-24T00:00:00Z",
  "donors": ["Exact VRChat Display Name"]
}
```

Both incoming feeds must contain exactly those three keys. Extra keys, duplicate JSON keys, invalid UTF-8, non-JSON numeric constants, non-UTC timestamps, unsupported schema versions, and non-string names are rejected. Bodies and generated artifacts are capped at 512 KiB, lists at 5,000 entries, and display names at 100 UTF-16 code units to match the Udon importer. Empty names, surrounding whitespace, invalid Unicode, and embedded control/line-separator characters are rejected.

Names preserve exact case and Unicode code points. A string is always JSON data; it is never evaluated, interpolated into a shell command, used as a filename, or written into Actions output syntax. Only a locally computed `changed=true` or `changed=false` flag enters `$GITHUB_OUTPUT`. The script prints counts and status, not donor names or remote response bodies.

This mirror is solely for the cosmetic **Osteophiliac** badge. It does not authenticate a donor or grant military permissions. The private Ko-fi integration is responsible for payment verification, identity linking and consent before a name reaches its public feed. A direct public function used here must intentionally permit anonymous reads; see [Supabase's public Edge Function guidance](https://supabase.com/docs/guides/functions/auth#public-functions).

VRChat's string-loading allowlist includes GitHub Pages (`*.github.io`). A Supabase URL is not a substitute for this trusted URL when players have “Allow Untrusted URLs” disabled. See [VRChat String Loading](https://creators.vrchat.com/worlds/udon/string-loading/).

## Pinned official actions

All four action versions were checked against their official GitHub release/tag references and `action.yml` on November 24, 2026. Full commit pins and source URLs are recorded in `actions-pins.json`:

| Action | Version | Commit |
| --- | --- | --- |
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/configure-pages` | `v6.0.0` | `45bfe0192ca1faeb007ade9deae92b16b8254a0d` |
| `actions/upload-pages-artifact` | `v5.0.0` | `fc324d3547104276b827a68afc52ff2a11cc49c9` |
| `actions/deploy-pages` | `v5.0.1` | `368f82528645a54fb793d4d04e342629a3f51346` |

## Local checks

Use Python 3.10 or newer; there are no third-party packages:

```console
python -B -m unittest discover -s tests -v
```

Tests use local fixtures and fake HTTP responses. They validate semantic no-change behavior, timestamp stability, byte/name limits, strict public fields, missing-page bootstrap, failure preservation and the controlled Actions output boundary. They do not create a repository, deploy Pages, write to Supabase, or modify the Unity project.

To perform a real public-feed validation after the Supabase endpoint is live:

```console
python -B scripts/sync_donors.py --output _site/donors.json
```

Successful local validation does not prove that the hosted workflow or VRChat client has downloaded the feed. Verify the first real Actions deployment and published URL before marking hosting complete.
