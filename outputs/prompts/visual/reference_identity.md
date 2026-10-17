# REFERENCE_VISUAL — one fictional identity-reference generation

## Preflight

This is a production template only. It may be used for one newly authorized
reference operation after the earlier timed-out operation with unknown billing
has been reconciled as a separate historical event. It must not be treated as
a retry of that operation. The input source is the reference SceneSpec at
`outputs/runs/run_d42588a930dc46f4a46af51185a0b39d/reference-visual/reference_spec.json`.

Evidence scope: `persona:identity` only. It is a timeless adult identity
anchor, so it must not import the later T1 haircut/yellow hoodie/restaurant or
the T3 childhood/dog/tree facts.

## Request shape

```yaml
kind: image
operation: generate
provider: azure_openai
model: gpt-image-2
parameters:
  n: 1
  size: 1024x1024
  quality: high
  output_format: png
  # output_compression is deliberately omitted from the wire request for PNG.
image_inputs: []
mask: absent
candidate_role_after_success: reference
human_review_required: true
```

## Final production prompt

```text
Create exactly one natural, casual smartphone portrait of a wholly fictional,
text-designed adult Chinese woman. This is an original fictional identity
anchor, not a portrait of a real person and not an imitation of anyone.

Show a relaxed chest-up phone photograph in neutral soft daylight against a
plain, unbranded indoor background. The subject is a slim adult woman with a
round face, monolid eyes, a subtle smile with natural under-eye fullness, and
a small mole at the tail of her own left eyebrow. Her hair is chestnut brown,
long to the chest, and gathered in a low ponytail. She wears a simple loose
neutral-colored sweatshirt. She normally wears contact lenses in this neutral
portrait; do not add black-frame glasses.

Preserve this asymmetric identity marker precisely: only her own left ear has
a piercing and it wears one small silver crescent-moon earring. Make that ear
visible; do not add an earring or piercing to her own right ear. Use a
non-mirrored orientation so her own left side is unambiguous.

One person only. Natural anatomy and hands; no extra fingers, duplicate ears,
mirrored facial markers, beauty-advertising styling, watermark, logo, readable
text, date, identifiable place, celebrity likeness, or real-person reference.
Do not add any scene-specific, time-specific, or conversation-derived fact
beyond the identity attributes above. Keep the image safe, ordinary, and
non-sexualized.
```

## Human PASS/FAIL rubric

PASS only when all items hold:

- The file decodes as a 1024×1024 PNG and its recorded size/SHA-256 match the
  bytes reviewed.
- It has exactly one clearly adult, fictional subject; no real-person claim,
  text, logo, watermark, or identifiable location is present.
- The round face, monolid eyes, small mole at the subject's left eyebrow tail,
  chestnut long low ponytail, and the single silver crescent on the subject's
  left ear are all natural and unambiguous.
- There is no right-ear earring/piercing, unwanted glasses, extra limbs,
  duplicated/mirrored markers, or scene-specific future detail.
- The reviewer writes a concrete acceptance reason and accepts the exact
  current SHA-256 through the human review flow.

FAIL (and stop) for any missing/mirrored anchor, anatomy defect, unintended
real-person resemblance claim, unsafe content, scene leakage, unreadable or
wrong-format file, or uncertain identity continuity. A FAIL is a recorded
human rejection of this candidate, not permission to silently regenerate.
