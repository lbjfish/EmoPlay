# T1 — edit after accepted identity reference

## Evidence lock and input precondition

Target/cutoff: `T1` at `2026-10-14T11:43:00Z`. Use only the T1 SceneSpec and
facts visible at or before that cutoff: the lunchtime chest-length-hair cut
became clavicle-length hair with uneven bangs (her own left fringe is shorter),
the yellow hoodie, rainy Hangzhou after work, the restaurant meal, the crowded
entrance-side table, and her happy-but-slightly-embarrassed agreement to show
the new bangs. The trigger intent is a selfie while eating, to show the bangs.

`m023` through `m068` are future-locked for T1. Do not use any reply after the
target moment or infer events after the meal. No fact from the locked range may
enter the image.

Before dispatch, verify all of the following:

1. One to four reference-image assets have a human `accept` decision, are
   listed in the current checkpoint's accepted assets, are technically eligible,
   and pass a fresh path/size/SHA-256 verification.
2. The input selector reports only assets whose role is `reference`. An
   accepted T1/T3 scene candidate is never a valid anchor.
3. Each accepted asset is supplied as a binary multipart `image[]` input with
   its verified original filename/content type; no mask is supplied.
4. The reference review record, input asset IDs, relative paths, hashes, and
   decision IDs are available for the run audit.

If any condition is false, do not downgrade this to a text-only generation.

## Request shape

```yaml
kind: image
operation: edit
provider: azure_openai
model: gpt-image-2
parameters:
  n: 1
  size: 1024x1024
  quality: high
  output_format: png
  # output_compression is deliberately omitted from the wire request for PNG.
image_inputs: [one_to_four_human_accepted_reference_images]
mask: absent
candidate_role_after_success: candidate
human_review_required: true
```

## Final production prompt

```text
Edit the supplied human-accepted fictional identity reference image(s) into
one plausible casual phone selfie. Preserve the same entirely fictional
person's stable facial identity: round face, monolid eyes, small mole at the
tail of her own left eyebrow, and the single silver crescent earring on her
own left ear only. Preserve a natural adult appearance. This is not a real
person and must not resemble, name, or imitate any real individual.

Scene at the locked target moment: she is seated at the entrance-side table of
a busy, unbranded casual restaurant in rainy Hangzhou after work, taking a
slightly self-conscious but happy front-camera selfie while eating. Frame her
face and the top of a simple bowl of warm soup naturally; keep other diners
indistinct and non-identifiable. She wears the evidence-supported yellow
oversized hoodie with no logo or text.

Apply the time-specific hair change: chestnut-brown clavicle-length hair with
uneven, choppy bangs. Her own left side of the fringe is visibly shorter than
her own right side. Do not retain the reference's chest-length low ponytail.
Use a non-mirrored orientation so the subject's own left/right markers remain
auditable.

One adult subject only. No readable menu/sign text, no brand, no watermark,
no celebrity likeness, no extra fingers/ears, no mirrored mole or earring, and
no glamor-advertising treatment. Do not add any fact outside the locked
evidence at or before the target UTC. Keep the food and restaurant depiction
ordinary, safe, and non-graphic.
```

## Human PASS/FAIL rubric

PASS only when all items hold:

- The bytes are a 1024×1024 PNG; path, size, and SHA-256 match the registered
  candidate and provider/run audit.
- The stable face markers match the accepted reference without being mirrored:
  round face, monolid eyes, left eyebrow-tail mole, and exactly one silver
  crescent on the subject's left ear.
- The adult haircut is clavicle-length with conspicuously uneven bangs whose
  subject-left side is shorter; it is not the long reference ponytail.
- The yellow oversized hoodie, modest bowl-of-soup restaurant selfie, rainy
  after-work atmosphere, and happy/embarrassed expression fit the pre-cutoff
  evidence. Other diners are not identifiable.
- There is no post-cutoff story leakage, invented brand/text, extra anatomy,
  explicit/unsafe material, or claim that the candidate is already final.
- A human accepts the verified exact hash with a concrete reason; only then may
  it enter an accepted-assets checkpoint.

FAIL and stop on any continuity mismatch, wrong left/right orientation,
text/logo/watermark, post-cutoff detail, anatomy issue, or uncertain reference
match. Preserve the rejected candidate and reason; a new paid operation needs
separate authorization and a documented single-variable change.
