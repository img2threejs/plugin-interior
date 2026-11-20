# Tool input contracts

All JSON uses UTF-8. Paths on commands are explicit local files; no tool uploads or generates paid assets. Tools require linked img2_core API 1 and write only their own workspace artifacts/state. Audit gates recheck current inputs instead of trusting a saved pass flag.

## Intake

`init --image PATH --intake PATH --workspace PATH` requires:

```json
{
  "schemaVersion": 1,
  "sourceKind": "synthetic-design-reference",
  "authorization": "Actual permission and scope, reviewed by the project owner",
  "scale": {"unit":"m","basis":"authored-estimate","note":"Single-image scale and unseen depth are authored"},
  "components": ["room-shell","sofa","glazing"],
  "excludeComponents": ["sofa"],
  "unknowns": ["Unseen construction is not measured"],
  "interactionRequired": true
}
```

`sourceKind`: `synthetic-design-reference`, `photo`, `render`, `floorplan`. Scale basis: `authored-estimate` or `measured`; units `m`. Components are unique nonempty IDs; unknowns is an explicit array. Authorization is a declaration, not a tool-verifiable license. The pinned intake snapshot is `.img2/artifacts/interior/intake.json`.

`excludeComponents` is optional (default `[]`), contains unique exact IDs from `components`, and is pinned with the intake. The parent must map separate object ownership onto these IDs. Draft removes their real scene objects before rendering/baking; a manifest-only omission is not sufficient. A spec-level `excludeComponents`, when supplied, must match the admitted snapshot.

## Material policy

`audit_materials.py --glb PATH --policy PATH --workspace PATH` requires:

```json
{
  "schemaVersion": 1,
  "materials": [{
    "name": "Exact exported glTF material name",
    "requireBaseColorTexture": true,
    "requireNormalTexture": true,
    "requireRoughnessTexture": true,
    "minRoughnessFactor": 0.8,
    "maxMetallicFactor": 0,
    "maxSheenColorFactor": 0.2,
    "texCoord": 0
  }]
}
```

Only `name` is mandatory per material; declare checks intentionally, not by assumed defaults. Names must be unique and select actual exported materials. Scalar bounds are finite in [0,1], `texCoord` a nonnegative integer. glTF defaults still apply when a factor/extension is absent. The audit checks texture resources, selected primitive UV presence and texture-transform coordinate overrides; it is not the Khronos validator or a texture pixel decoder. For example, roughnessFactor 1 with a low-valued roughness image can still be glossy.

Output: `.img2/artifacts/interior/material-audit.json`. Gate mode: `--workspace PATH --gate`; it is read-only and rejects changed GLB/policy/receipt inputs.

## Delivery contract

`audit_delivery.py --contract PATH --workspace PATH` requires `schemaVersion:1`, `root`, `assets`, `runtime`, `portable`, `native`, and `limitations`.

- `root`: explicit asset directory; relative paths resolve against the contract file. An absolute existing project root is allowed for read-only inspection without large asset copies.
- Each `assets` row: unique nonempty `id`, `role`, POSIX relative `path`, exact expected 64-hex `sha256`.
- Roles: exactly one each `source`, `runtime`, `portable`, `native`; any number of `sidecar` rows.
- All resolved files must stay under root; duplicate paths/IDs, symlink escapes, URLs and missing bytes fail.
- `runtime`: `{"asset":"runtime-id","sidecars":["atlas-id","probe-id"]}`. Reference every sidecar; no undeclared/foreign-role dependency.
- `portable`: `{"asset":"portable-id","limitations":["Static appearance snapshot; no browser cloth physics"]}`.
- `native`: `{"asset":"native-id"}`.
- `limitations`: nonempty strings declaring global assumptions/transport/device limits.

Use observed SHA256s, not invented example digests. The tool cannot establish their provenance or consumer/visual acceptance. Runtime and portable must not be the same file/bytes under two names.

Output: `.img2/artifacts/interior/delivery-audit.json`. Gate mode: `--workspace PATH --gate`; changing the contract or an asset invalidates the saved audit.

## Routed mechanical audit (not a terminal gate)

After the normal material/delivery audits have pinned current inputs in the exact child:

```sh
python3 tools/audit_routed_artifact.py --workspace "$CHILD" --artifact artifact.json --spec scene-spec.json --blender-python "$IMG2_BLENDER_PYTHON"
```

`--blender-python` may instead come from `IMG2_BLENDER_PYTHON`; absence fails rather
than skipping measurements. Requires actual Blender >=4.3 and linked img2 core for
the real subprocess audits. Artifact paths/files, intake, reference, builder,
policies, delivery root/contracts/assets must remain inside this child with no
symlink aliases. Existing general-purpose delivery audits still support external
asset roots; this routed audit deliberately does not.

Exit 0 means only the mechanical scope passed; exit 1 means unavailable/invalid/
stale evidence. JSON kind is `img2.interior-routed-audit`, with exact artifact SHA,
actual independent Blender measurements and rerun program audit reports.
`terminalAcceptance:false` and `missingTerminalProducers` remain explicit even on
success. No editable manifest or old workflow acceptance can fill these gaps.
See [scene contract](scene-contract.md#independent-mechanical-replay-not-terminal-acceptance).


## Local decision snapshots (no provider calls)

`python3 tools/interior_adapter.py --workspace W --spec scene-spec.json --request decision-request.json`
prints a frozen quality-review context, not an advisory or acceptance. The request has exactly
`group`, `subject:{runId,objectId,revision,passId}`, `candidates`, `evidence`, `requirements`,
`policyVersion`, `requestedModel`. The programmatic `authoring.decisions.decision_context`
also supports `build-strategy` and `repair` when the trusted host supplies separately verified
`available_dependencies` and `authorized_sources` sets. The CLI cannot grant these facts and
refuses construction snapshots. The subject pass is the unlocked native pass (`camera`,
`geometry`, `materials`, `lighting`, `interaction`); the base independently enforces cursor/lock.

Evidence maps roles to local relative files. Required: `source`, `spec`, `builder`, `intake`;
review/repair also require `gate-report`, `camera`, `render`. Add each required view/crop
under its own role. Actual bytes are hashed, never fabricated or accepted by this adapter.
`requirements` carries the base's `criticalFeatures` and `requiredViews`; missing visual
evidence stays unevaluated by the base's admission policy.

Strategy/repair expose one to three candidates with exactly `id`, `passId`, `components`,
`operations`, `fieldPaths`, `materialSources`, `dependencies`, `rigHandoff`. The shared plan
identifier syntax and pass-specific operation/field allowlists are enforced. Components must
be admitted and not separately owned. Each material source is a real bound SHA-256 also in
the trusted host's independent authorization set; a provenance claim or filename is not
authorization. Resource operations require nonempty sources. Native plans require a `blender`
dependency already verified by the host; missing availability is not inferred. `rigHandoff`
must be explicitly false: this static interior adapter has no verified skeleton handoff.
Quality-review candidates remain empty. Arbitrary code/argv, old private plan fields,
threshold edits, unknown dependencies and excluded-component mutations are rejected.

Application blocker: native scene-spec 1 uses top-level `authoring`/`runtime`, not the base
`StrategyDecision`'s componentTree-only delta format. No fictitious componentTree is added:
a base-native-spec application adapter is required before these snapshots can authorize an
interior trial. The existing safe agent authoring workflow remains unchanged. The shared
base retains repair diagnosis, pre-spec consultation, immutable applications/trials,
Jev/login/consent/shared budget, and canonical review authority.


## Review and exit codes

`interior_workflow.py record --stage NAME --decision accept|reject|request-input|stop --reviewer NAME --note TEXT --evidence PATH` (repeat evidence flag for multiple files). Stages: `intake,camera,geometry,materials,lighting,interaction,delivery,review`. Evidence resolves inside the workspace and is hash-pinned. `revise --from NAME --reason TEXT` retains history and invalidates the dependent suffix. `next`/`report` are read-only.

Audits/gates: 0 pass, 1 evaluated failure, 2 malformed/error. A gate prints exactly one `img2.gate-verdict` envelope. The real runner produces `img2.gate-run` and stops on a blocking non-pass. Workflow `next` uses 3 for an explicit input/stop block. A passing gate does not authenticate a reviewer or inspect screenshot meaning.
