# Visual prompt package — execution templates only

## Status and boundary

This directory contains the reviewed prompt briefs used to prepare visual
operations. Generated media lives under `outputs/runs/`; a brief alone never
proves success and never replaces a hash-bound human decision in storage.

The source contract is the committed SceneSpecs and the current Azure image
configuration. No credential, endpoint, local user path, or raw provider
response belongs in this package.

## Locked wire profile

Use this same profile for the one reference generation and for each permitted
edit. It is intentionally aligned with `config.creator.example.toml`.

```yaml
provider: azure_openai
model: gpt-image-2
wire_parameters:
  n: 1
  size: 1024x1024
  quality: high
  output_format: png
wire_omissions:
  - output_compression
timeout_seconds: 900
transport: curl_ipv4
request_count: exactly_one_per_authorized_operation
automatic_retry: false
```

The `wire_omissions` entry is a locked choice, not an unspecified default:
PNG is the selected lossless format, so no `output_compression` value is sent.
Do not send `null`, `100`, or another value while `output_format` remains
`png`; the adapter forwards supplied values verbatim. Switching to JPEG or
WebP to use a compression value is a separate compatibility/configuration
decision and is outside this packet.

The implementation's existing Azure v1 transport/authentication contract must
remain in force. Do not put an endpoint or credential into a prompt, request
record, shell argument, or repository file.

## Candidate and input rules

- A successful real-provider image is still only a `candidate`; it requires a
  fresh human accept decision tied to its verified SHA-256 before it is final.
- A placeholder, fixture, prompt-only result, missing file, malformed result,
  or output with unknown billing state is never a usable reference or final
  image.
- Every current chapter image is an edit operation, not text-only generation.
  Each must receive
  one to four human-accepted visual-reference asset bytes as multipart
  `image[]` inputs, with no mask.
- Record each input asset's relative path, asset ID, SHA-256, media type,
  size, and human decision ID before dispatch. Re-hash it immediately before
  dispatch. Do not use an unaccepted candidate or an untracked file.
- The current application selects up to four accepted, verified image assets
  whose role is exactly `reference`. Accepted scene candidates are not
  valid edit inputs. Before dispatch, confirm the recorded input list contains
  only those reference-role assets and no mask.

## Common safety and continuity rules

- All people are entirely fictional, text-designed characters. Do not name,
  resemble, imitate, or claim to depict a real person.
- Use neutral, non-sexualized, non-violent casual-photo language. No branded
  products, logos, watermarks, or readable incidental text. T6 has one explicit
  exception: the paper must contain exactly `阿杰 10.16` and no added text.
- Preserve a person's own left/right orientation. In an ordinary non-mirrored
  front-facing image, the subject's left appears on the viewer's right; human
  review must check the subject's orientation rather than assume the screen.
- Never invent facts from later messages. Each scene file states its UTC
  cutoff and locked message range. Treat the SceneSpec, rather than this
  prompt package, as the authoritative record when they differ.

## Stop conditions shared by all operations

Stop before dispatch if any required human decision, verified input hash,
configuration invariant, explicit paid-call authorization, or unresolved prior
run is missing. Stop after the one attempt if it times out, loses transport,
returns 5xx, has unknown billing, returns malformed/no image data, or produces
a policy refusal. Reconcile and record the result; do not automatically retry,
switch provider, substitute a placeholder, or call a scene from a text-only
brief. A human visual FAIL is also a stop: preserve the candidate and its
reason, then obtain separate authorization before a changed next attempt.

## Current brief inventory

- `reference_identity.md`: fictional adult identity anchor.
- `T1_after_reference_edit.md`: restaurant selfie.
- `T2_background.md`: morning metro background for the voice chapter.
- `T3_childhood_edit.md`: childhood photo with Wangcai.
- `T4_support_cafe.md`: fan support cafe photo.
- `T5_background.md`: late-night home background for the voice chapter.
- `T6_verification_selfie.md`: exact-paper-text selfie.
