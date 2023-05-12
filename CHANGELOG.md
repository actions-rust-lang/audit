# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.7] - 2023-05-12

* Update `cargo-audit` to 0.17.6

## [1.1.6] - 2023-03-24

* Update `cargo-audit` to 0.17.5

## [1.1.5] - 2022-12-22

* Fix duplicate issues for yanked crates.

    The previous version introduced a bug where existing issues were not properly detected.
    This only affected issues for yanked crates.
    Now duplicate issues will no longer be created.

## [1.1.4] - 2022-12-22

* Handle warnings without any associated advisory.

    This occurs for yanked crates, where the `advisory` field is `null` in the JSON output.
    Now a message is shown that the crate and version is yanked.

## [1.1.3] - 2022-12-05

* Fix the path to the cargo installation directory to fix caching.

## [1.1.2] - 2022-11-09

### Changed

* Update `cargo-audit` to 0.17.4 which fixes checking for yanked crates.

## [1.1.1] - 2022-10-13

### Changed

* Switch from set-output to $GITHUB_OUTPUT to avoid warning
    https://github.blog/changelog/2022-10-11-github-actions-deprecating-save-state-and-set-output-commands/

## [1.1.0] - 2022-08-14

### Added

* Present aliases for the RustSec ID and related advisories in the overview table (#1).

### Changed

* Setting `denyWarnings` will now pass `--deny warnings` to cargo audit.

## [1.0.1] - 2022-08-09

### Added

* Create proper release tags.

## [1.0.0] - 2022-08-09

Initial Version
