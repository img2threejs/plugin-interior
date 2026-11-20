# Agent-authored scene contract

The agent creates `scene-spec.json` and real `build_scene.py` after viewing the supplied image. Bootstrap does not invent these. The small [studio builder](../examples/studio/build_scene.py) is a mechanism fixture, not a fallback reconstruction; the [Oak/Linen recipe](../recipes/oak-linen/scene-spec.json) is source-hash guarded.

## Specification fields

| Field | Requirement |
|---|---|
| `schemaVersion` | Integer 1. |
| `title`, `sourceKind` | Actual scene title and photo/render/synthetic-design-reference/floorplan classification. |
| `source` | Workspace-relative pinned image under `public/`; actual PNG/JPEG/WebP extension. |
| `sourceViewLimit`, `portability` | Nonempty honest descriptions of single-view uncertainty and static/runtime/native differences. |
| `authoring.sceneBuilder` | Workspace-relative Python module exporting `build_scene(context)`; trusted executable code, not downloaded page instructions. |
| `authoring.sourceImageSize` | Actual displayed `[width,height]`, honoring source orientation; no fixed showcase aspect. |
| `authoring.room` | Authored native Z-up metre extents `x:[min,max]`, `y:[min,max]`, `height`; builder must implement them and return actual room bounds. |
| `authoring.cameraEstimate` | Source `position`, `target`, `lensMm`, `sensorWidthMm`; position must differ from target. Match through actual renders. |
| `authoring.inspectionViews` | Object of named native Z-up `position,target` poses; `source` is implicit. Choose meaningful lateral/reverse/material/cloth views for this scene. |
| `authoring.probePosition` | Authored indoor Z-up point, chosen away from geometry; production probe is 2:1 equirectangular. |
| `authoring.resourceManifest` | Optional workspace-relative actual acquisition receipt; omit for genuinely procedural scenes. |
| `excludeComponents` | Optional exact component identities, matching the pinned intake; never labels guessed by the builder. |
| `routing.objectId` | Set only for a base-owned routed child. Enables the measured D18 artifact producer at export. |
| `authoring.roomMeasurementComponents` | Required for routed export: nonempty exact component identities of the actual room shell; its exported world-vertex envelope supplies width/height/depth, not image-recovered dimensions. |
| `runtime` | `background:null` or local HDR/EXR URL, `backgroundIntensity`, optional `glassTransmission`, `variants:[]` or named finishes and optional `finishTarget`. |
| `assumptions` | Specific authored geometry/scale/material/lighting limits; do not claim measurements from one image. |

A nonempty variant list uses `{id,label,color:"#rrggbb",roughness?}`. The first entry is the authored base; others are relative linear tints for the declared finish group, not replacement material assets. Base RGB channels must be nonzero for relative recoloring. No sofa, curtains, fixed view names or exactly three variants are required.

## Builder execution

`author_scene.py draft` starts an empty Blender scene, explicitly loads the workspace module, calls `build_scene(context)`, finalizes meshes/modifiers/transforms, verifies one material UV0 layer per physical mesh, saves packed native state and actually renders. Subsequent stages reopen that master; they do not reinterpret the source image.

`context.workspace`, `.assets`, `.evidence` are `Path` objects for this project/current run. `.spec` is the parsed dictionary; `.resolve(relative)` confines data inputs/outputs to the workspace. `.helpers` lazily creates a workspace-private helper namespace; no installed-source paths or import-time scene generation.

A complete builder creates actual physical objects, material graphs, a source camera, world/lights and JSON-serializable summary with at least `roomBounds:{min,max}` in native Z-up, camera/views/components/assumptions. Use the complete linked builders as API examples, not an empty factory or a canned scene. Use a real source camera with horizontal sensor fit; ensure actual world pose and target match the declaration. No zero-vector camera templates.

## Per-object ownership and composition handoff

The parent maps separately routed objects to the same exact component identities used by the intake and builder, and passes them as `excludeComponents`. Intake exclusions must be unique declared `components`. Builders can inspect `context.exclude_components` to skip them; independently, `draft` removes every matching scene object (including hidden objects) after the builder returns and before modifier processing, native saving, rendering or baking. The master pins the intake hash and exclusion set. Later native stages refuse changes until a new draft/rebake; changing ownership for an accepted routed artifact requires a new child revision/workspace. No matching is inferred from object names or prose.

For a routed child, run the ordinary draft → bake → probe → export chain with `routing.objectId` and `authoring.roomMeasurementComponents` set in the spec. Export reimports the actual runtime GLB into a disposable Blender scene and measures world vertices. It writes `room-composition.glb` and `scene-measurements.json` in that export run's evidence directory, then root `artifact.json` with real workspace-relative file hashes, frame `m/+Y/+Z`, bottom-centre pivot, scalar `scaleEstimate:1.0`, `rig:null`, and positive `roomDimensions:{width,height,depth}` metres.

The composition GLB preserves original mesh, material and embedded image bytes, adding a scene parent translation and updating cloth origin extras. It is reimported to verify the bottom-centre pivot and unchanged shell measurements. Existing runtime GLB/manifest, viewer, portable GLB and native master stay unchanged. This is a static composition handoff, not a claim that the current viewer accepts transformed cloth roots. Missing/invalid shell geometry, excluded meshes, external GLB resources, rigs or unverifiable measurements block artifact publication. Room dimensions describe the selected authored shell envelope, not recovered real-world room dimensions. All existing delivery/material/workflow gates and independent visual/runtime review remain mandatory.

This adapter is pinned to 0.3.1 (scene-spec 1 / runtime manifest 2), one enclosed room only. The original 0.4 WIP adds house/room identity, schema-3 transport/encoding and viewer/runtime audits; none is implicitly migrated or qualified here.

## Independent mechanical replay, not terminal acceptance

`python3 tools/audit_routed_artifact.py --workspace "$CHILD" --blender-python "$IMG2_BLENDER_PYTHON"` reruns the real `interior-materials` and `interior-delivery` program gates against that exact child, then starts a separate Blender >=4.3 interpreter to reopen the actual source/runtime and composition GLBs. The existing normal audits must already have pinned current inputs; all policies, delivery contracts and assets must be inside this child. Missing Blender/core/audit receipts or stale/foreign bytes fail; no status is inferred from a user checklist.

The replay validates canonical D18 frame/pivot/room fields and actual file hashes, current intake/spec exclusion identities, embedded GLB resources, the actual exported camera transform graph, mesh/component/vertex inventory, measured shell dimensions and bottom-centre pivot. It independently regenerates the normalized GLB bytes from the original export to verify geometry/material/image preservation. Output binds `evidence.artifact:{path,sha256}` plus current spec, intake, source reference, builder, camera digest and audit inputs. It writes no evidence or workflow state.

A successful result is only `img2.interior-routed-audit`, **not** `img2.gate-verdict`; it explicitly says `terminalAcceptance:false`. No terminal gate is registered because these required independent producer contracts do not exist on this baseline:

- Artifact-bound structural turntables, self-intersection and part-coverage measurements (the replay measures vertex geometry/dimensions, not those quality criteria).
- Admitted semantic/critical-feature review of real matched reference/render pixels, binding the same artifact, camera, spec and capture setup. Workflow attestations and `compare_views.py` contact sheets do not produce this judgment.
- Actual composed-browser captures binding the delivered artifact and sidecars, proving atlas/probe integration and transformed cloth behavior. Native GLB import cannot establish browser rendering or interaction.

Each missing producer is returned explicitly; a mechanical pass cannot admit a routed child through RouteAuthority. The unchanged legacy single-room agent workflow and its existing gates remain usable. No 0.4 WIP or browser screenshots are manufactured by this audit.



## Reusable helper surface

| Helper | Purpose / identity |
|---|---|
| `material(name,color,roughness=...,metallic=...,maps=...,color_path=...,normal_strength=...)` | Actual Principled graph; map dictionary `color/normal/roughness` resolves inside W. Albedo sRGB, normal/roughness non-color; explicit UV0 normal mapping. Texture overrides the constant base input; dye the map or author a multiply graph when intended. |
| `mesh(name,vertices,faces,material,component,finish=None,tile=...)` | Actual topology with component/physical IDs and metric material UV0. |
| `box`, `tube`, `lathe`, `cushion` | Editable authoring primitives for shell/supports, pipes/seams, hollow vessels and closed lofted soft forms. Choose semantic component/finish explicitly. |
| `project_uv(obj,metres=...)` | Physical-scale local UV0; do not reuse it as the bake layer. |
| `import_model(path,component,center,height=None,width=None,yaw=...)` | Import real local glTF, preserve world affine placement, normalize UV0 and fit declared dimensions. Visually review before using. |
| `bounds(objects)`, `tag`, `light` | World bounds, semantic identity and native light construction. |
| `curtain(name,origin,rail,depth,width,closed_width,height,material,...)` | Actual 100×32 cloth, hems and hooks in an explicit horizontal orthonormal native Z-up frame; exact 50×16 solver intersections and glTF Y-up metadata. |

Preserve real geometry instead of photo cards, material UV0 instead of bake UV reuse, and identity outside a repair's scope. The packaged case shows BVH-seated piping, skewed cushions, explicit furniture contact, real openings and local garden assets; those are techniques, not mandatory furniture.

## Runtime coordinate and cloth contract

Convert native `(x,y,z)` to glTF/Three `(x,z,-y)`. Exported manifest schema 2 contains actual Y-up camera/room bounds/views, source dimensions, atlas URLs/intensities, probe/background, explicit `lighting.sun` object or null, finish groups and product links.

Cloth meshes must carry `curtain_id`, `curtain_part`, dimensions/tile/fabric width, signed `curtain_rail_axis`, `curtain_depth_axis` and `curtain_plane_origin` in glTF Y-up scene coordinates; hem/hook metadata adds `curtain_v` / `curtain_u`. Rails are horizontal and orthogonal. Closed width exceeds open width; both pinned rail endpoints remain inside room bounds. Deforming geometry must be single-user and preserve exact lattice UV intersections. Nonidentity scene roots are rejected rather than silently mis-simulated; this is not an arbitrary-rig cloth importer.

Review the full actual builder/example before running it. Workspace confinement protects file placement, not execution sandboxing: Python builders and Blender add-ons can execute code. Do not execute asset/page-provided scripts as scene specifications.
