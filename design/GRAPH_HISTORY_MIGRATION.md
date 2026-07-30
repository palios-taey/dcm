# DCM graph-history migration contract

Status: operational contract; this repository does not yet ship an automated migration
command.

DCM history is provenance. Moving a deployment to a different Neo4j database must preserve
that history exactly and must not copy, alter, or delete unrelated graph namespaces.

## Scope

The portable DCM namespace currently consists of:

- `:DCMSession` nodes;
- `:DCMContribution` nodes; and
- `(:DCMContribution)-[:IN]->(:DCMSession)` relationships.

All node properties must be preserved, including historical contributions that do not have a
current `seq` property. Session IDs, contribution IDs, timestamps, peer claims, server presence
stamps, concern/resolution fields, status, and final artifacts are provenance and must not be
recomputed.

## Non-destructive procedure

1. Identify the source and destination from explicit runtime configuration. Do not depend on
   a compiled-in host, port, database name, or credential.
2. Freeze DCM writes or take a transactionally consistent source snapshot. Record the freeze
   boundary and source database identity without recording secrets.
3. Inventory source counts for sessions, contributions, and `IN` relationships. Record
   per-session contribution counts and the complete constraint list.
4. Inventory the destination and compute the intersection of session IDs and contribution IDs
   before importing.
5. Abort on any identifier collision whose canonical property digest differs. Identical
   records may be skipped only when both node properties and relationship endpoints match.
6. Export only the DCM namespace. Never export all labels from a co-resident database.
7. Import into a staging destination or isolated transaction first. Preserve all properties
   and relationship endpoints exactly.
8. Recreate or verify the current DCM uniqueness constraints only after collision analysis.
9. Run the acceptance checks below.
10. Point readers and then writers at the destination through configuration. Keep the source
    snapshot unchanged through the rollback window.

There is no cleanup or source deletion step in this contract.

## Canonical digest

Migration tooling must create a deterministic digest for every session:

- canonical JSON of the session properties;
- followed by each contribution in `(seq, created, contrib_id)` order;
- including the complete contribution property map; and
- including the contribution-to-session relationship endpoint.

Property keys are sorted and strings are encoded as UTF-8. Missing properties and empty values
must remain distinguishable. A deployment may choose a standard cryptographic hash, but the
algorithm and tool version must be recorded with the receipt.

## Acceptance checks

A migration is accepted only when:

- destination session count equals the source count plus documented, pre-existing destination
  sessions;
- destination contribution count and `IN` relationship count satisfy the same equation;
- every source session ID exists at the destination;
- every source per-session digest matches the destination digest;
- there are no orphan contributions or cross-session `IN` relationships;
- current uniqueness constraints exist without rewriting historical records;
- `mesh.read_session` can read every migrated session;
- open/closed status and final artifacts match exactly; and
- unrelated destination label and relationship counts are unchanged.

The receipt records before/after counts, digest algorithm, mismatches (zero for acceptance),
source freeze boundary, configuration cutover, and rollback observation.

## Failure and rollback

Any count, digest, endpoint, constraint, or read failure is a full stop. Restore the prior
connection configuration and retain both source and failed destination for analysis. Do not
repair provenance by renumbering contributions, changing identifiers, or dropping collisions.

An automated migration command remains future work. Until it exists and has production
receipts, use database-native snapshot/export tooling plus an independently reviewed,
namespace-scoped import procedure that satisfies this contract.
