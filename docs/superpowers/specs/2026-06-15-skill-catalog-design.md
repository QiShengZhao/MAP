# Curated Skill Catalog Design

## Goal

Add a curated catalog and safe URL importer to the existing Skills page so
tenant administrators can preview and install useful public skills with one
click.

## Catalog

The catalog is a server-maintained immutable list. Each entry contains an ID,
name, description, publisher, category, source page, and a direct HTTPS URL to
one `SKILL.md` file. The first release includes selected Anthropic and OpenAI
official skills. Updating the catalog requires a code change and review.

## Import Flow

The Skills page shows catalog cards and a custom URL field. Selecting install
asks the backend to fetch the Markdown, parse YAML-style frontmatter fields
`name` and `description`, and store the complete Markdown body as the local
skill instructions. Users may preview fetched content before installation.

An existing skill with the same tenant/name is rejected with HTTP 409. Existing
skills are never silently overwritten.

## Security

- Only `https` URLs are accepted.
- DNS-resolved private, loopback, link-local, multicast, and reserved addresses
  are rejected.
- Redirects are disabled.
- Responses are limited to 256 KiB and must be Markdown or plain text.
- Imported content is stored as instructions and is never executed by the
  importer.
- Only tenant administrators may preview or install remote skills.

## UI

The Skills page gains:

- A curated catalog grid with publisher/category/source information.
- One-click install buttons with installed-state feedback.
- A custom URL importer with preview and install actions.
- The existing tenant skill table and manual editor remain available.

## Verification

Backend tests cover catalog output, parsing, safe fetching, duplicate handling,
and tenant isolation. Frontend verification covers rendering and install-state
updates. The full existing test suite must remain green.

