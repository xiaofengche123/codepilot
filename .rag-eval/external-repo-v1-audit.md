# External repository label audit packet

Status: `AWAITING_INDEPENDENT_DEVELOPER`

This packet fixes a blind 3-of-12 (25%) sample, one item per repository. The reviewer should inspect the pinned source revisions and labels without reading `external-repo-v1-results-2026-09-02.json` or the statistical conclusions first.

## Reviewer declaration

- Reviewer name:
- Role / affiliation:
- Review date:
- I did not create these labels: [ ]
- I reviewed the pinned source revisions below: [ ]
- Signature or approval reference:

## EXT-I02 — itsdangerous

- Commit: `672971d66a2ef9f85151e53283113f33d642dabd`
- Query: Where does signature verification try rotated secret keys newest first?
- Required: `src/itsdangerous/signer.py:227-242`
- Supporting: `src/itsdangerous/signer.py:175-180`
- Query accurately describes the code: [ ] yes [ ] no
- Required label is necessary and correctly bounded: [ ] yes [ ] no
- Supporting label is helpful but not necessary: [ ] yes [ ] no
- Proposed correction / notes:

## EXT-M03 — markupsafe

- Commit: `b2e4d9c7687be25695fffbe93a37622302b24fb1`
- Query: How does percent formatting escape tuple, mapping, and scalar values in Markup?
- Required: `src/markupsafe/__init__.py:154-165`
- Supporting: `src/markupsafe/__init__.py:357-381`
- Query accurately describes the code: [ ] yes [ ] no
- Required label is necessary and correctly bounded: [ ] yes [ ] no
- Supporting label is helpful but not necessary: [ ] yes [ ] no
- Proposed correction / notes:

## EXT-C04 — click

- Commit: `36baa15ff831b939a22bc527cd76ce653ef6f66d`
- Query: How does BadParameter choose a parameter hint and format its invalid value message?
- Required: `src/click/exceptions.py:114-156`
- Supporting: `src/click/exceptions.py:19-23`
- Query accurately describes the code: [ ] yes [ ] no
- Required label is necessary and correctly bounded: [ ] yes [ ] no
- Supporting label is helpful but not necessary: [ ] yes [ ] no
- Proposed correction / notes:

## Audit disposition

- Accepted without changes: [ ]
- Accepted with corrections recorded in a new dataset version: [ ]
- Rejected: [ ]
- Audited labels accepted / reviewed:
- Agreement rate:
- Final notes:

Do not edit `external-repo-v1.json` after evaluation. Any accepted correction must create `external-repo-v2.json`, preserving v1 and its results.
