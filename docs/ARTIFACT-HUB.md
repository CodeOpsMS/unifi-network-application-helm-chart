# Registering this chart on Artifact Hub

The chart already includes a README, license, and Artifact Hub annotations for its changes and pinned image. Register the HTTPS Helm repository to list its chart versions under your account. Artifact Hub reads the published packages; no rebuild or upload of the chart to Artifact Hub is needed. [Helm repository documentation](https://artifacthub.io/docs/topics/repositories/helm-charts/)

## Account registration

1. Sign in to [Artifact Hub](https://artifacthub.io/) and open **Control Panel → Repositories**.
2. Add a repository of kind **Helm charts** under your personal account, or select an organization you manage.
3. Choose an available repository name, for example `codeopsms-unifi`. Use this repository URL, without appending `index.yaml`:

   ```text
   https://codeopsms.github.io/unifi-network-application-helm-chart
   ```

4. After indexing, open the package and use its actual Artifact Hub URL for promotion. New published chart versions are indexed automatically. [Repository guide](https://artifacthub.io/docs/topics/repositories/)

Repository names cannot be renamed after creation. If the URL is already registered, inspect the existing entry and use the ownership-claim procedure when necessary. [Repository management FAQ](https://artifacthub.io/docs/topics/faq/#repository-management)

## Verified publisher

Send the actual repository ID displayed in your Artifact Hub control panel to the maintainer. The maintainer publishes this separate file beside the Helm index on GitHub Pages:

```yaml
# artifacthub-repo.yml — replace the placeholder with the assigned ID
repositoryID: REPLACE_WITH_ARTIFACT_HUB_REPOSITORY_ID
```

The public location must be:

```text
https://codeopsms.github.io/unifi-network-application-helm-chart/artifacthub-repo.yml
```

This metadata is independent of the tested `.tgz`. It identifies repository control; listing and the verified badge are separate steps. Verification is checked when Artifact Hub processes a changed repository index; changing only its `generated` timestamp is insufficient. [Publisher verification](https://artifacthub.io/docs/topics/repositories/#verified-publisher)

Maintainers should preserve this file on `gh-pages`. The release script retains existing repository files and deploys them with new chart versions. GitHub Pages uses Actions here, so a metadata-only branch commit also needs a Pages deployment. Do not rerun publication against an already published release or modify an existing chart archive to add account metadata.

Registering the repository and assigning its owner require your authenticated Artifact Hub account. The chart's `maintainers` field does not perform that account association. This document does not certify that a particular account has completed registration.
