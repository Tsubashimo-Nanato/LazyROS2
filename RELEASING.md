# Releasing LazyROS2

Releases are built only from a reviewed, clean `main`. `VERSION` is the single version source; `lazy --version`, changelog heading, annotated tag, archive directory and GitHub release must agree.

## Repository settings

After `CI / required` has appeared once, configure the `main` ruleset to require pull requests, resolved conversations, strict `CI / required`, linear history, and no force-push or deletion. With a single maintainer, required approvals may remain zero. Enable squash merge only and delete merged branches automatically.

Protect `v*` tags from update and deletion, and restrict tag creation to the maintainer. These rules are repository settings and cannot be enforced by files in this tree.

## Prepare the release

1. Confirm all release-gate documents and tests are present.
2. Set `VERSION` to the intended stable `MAJOR.MINOR.PATCH` value; project metadata declares version as dynamic so this remains the only version literal.
3. Move user-visible entries from `[Unreleased]` to the dated version in `CHANGELOG.md`.
4. Run the full core, shell, installer, PTY, mocked-tool and ROS integration suites.
5. Install the candidate tar into a temporary HOME, exercise `lazy --version`, then uninstall and confirm a sentinel survives.
6. Merge the release pull request into `main` and verify the required check on that exact commit.

## Tag and publish

Create a signed annotated tag with the authenticated maintainer identity:

```sh
version=$(cat VERSION)
git switch main
git pull --ff-only
git status --short
git tag -s "v$version" -m "LazyROS2 $version"
git push origin "v$version"
```

The release workflow checks that:

- the ref is an annotated tag and GitHub reports its signature as verified;
- tag and `VERSION` match;
- no release with that tag already exists;
- the archive comes from the tagged commit and excludes local-only files;
- the tar includes `SOURCE_REF` and `SOURCE_COMMIT`;
- `SHA256SUMS` verifies before upload.
- the exact tar installs into a temporary HOME, reports the expected version, uninstalls, and preserves an external sentinel.

The build job has read-only repository permission and uploads only the verified tar and checksum. A separate publish job downloads those artifacts and is the only job receiving `contents: write`, `id-token: write`, and `attestations: write`; it publishes `lazyros2-<version>.tar.gz`, `SHA256SUMS`, and a GitHub artifact attestation.

## Verify the published release

Download the assets into an empty directory and run:

```sh
sha256sum --check SHA256SUMS
gh attestation verify "lazyros2-${version}.tar.gz" --repo Tsubashimo-Nanato/LazyROS2
tar -tzf "lazyros2-${version}.tar.gz" | sed -n '1,20p'
```

Install the downloaded archive in a temporary HOME on at least one supported Ubuntu/ROS combination. Do not replace or upload assets after publication; publish a new patch version if the archive is wrong.

## Rollback

Do not move or overwrite a release tag. If a release is unsafe, mark it clearly in the release notes, fix forward on a new branch, and publish a new patch version. Users can manually reinstall a previously verified release with `--allow-downgrade`; the installer preserves the prior version until the new transaction commits.
