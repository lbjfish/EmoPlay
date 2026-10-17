# T3 — childhood-photo edit after accepted identity reference

## Evidence lock and input precondition

Target/cutoff: `T3` at `2026-10-15T04:12:00Z`. This fulfills promise `P1`:
send the childhood photo. The relevant allowed evidence says that the
fictional character lived with her grandmother in Wutongqiao through primary
school; as a second-grade child she wore red overalls under a very large
huangge tree outside her grandmother's home while hugging a large yellow local
mixed-breed dog named Wangcai. The dog was larger than the child and has a
healed missing notch on its own left ear.

Use the childhood scene only as a fictional, benign reconstruction; it is not
an original family photo and must not claim that a real child or real family
was photographed. `m047` through `m068` are future-locked for T3. Do not add
later story, clothing, location, fandom, relationship, or adult-life facts.

Before dispatch, meet the same four verified-reference conditions in
`T1_after_reference_edit.md`. The input selector must report only
human-accepted assets whose role is `reference`; an accepted adult T1 scene
candidate remains ineligible. Stop if any non-reference image is present in
the outgoing input list.

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
image_inputs: [one_to_four_human_accepted_reference_images_only]
mask: absent
candidate_role_after_success: candidate
human_review_required: true
```

## Final production prompt

```text
Edit the supplied human-accepted fictional identity reference image(s) into
one wholesome, fictional childhood family-album-style snapshot. Do not depict
a real person or a real child; do not identify, name, imitate, or claim a
photograph of anyone real. Preserve only gentle, age-appropriate continuity
from the anchor: a round child face, monolid eyes, and a small natural mole at
the tail of the child's own left eyebrow. Do not shrink, retouch, or paste an
adult face onto a child.

Show an elementary-school child, about second-grade age, outdoors under a
very large huangge tree outside her grandmother's home in Wutongqiao. She wears
red overalls and naturally hugs or leans against Wangcai, a calm large yellow
local mixed-breed dog that is visibly bigger than she is. Wangcai has a small,
healed missing notch on the dog's own left ear; it must look non-graphic and
natural. The child and dog are safe, relaxed, and fully clothed. Give the
child an ordinary, age-appropriate, non-distinct haircut rather than importing
the adult long ponytail or later uneven bangs.

Make it a modest, unbranded, slightly soft family-album snapshot without a
printed date, caption, school identifier, location sign, logo, watermark, or
readable incidental text. No adult jewelry, makeup, glasses, adult body shape,
sexualization, distress, violence, celebrity likeness, extra limbs, duplicate
ears, or mirrored left/right markers. Do not add present-day/adult-life details
or any fact from after the locked cutoff.
```

## Human PASS/FAIL rubric

PASS only when all items hold:

- The file decodes as a 1024×1024 PNG and registered path, size, and SHA-256
  match the reviewed bytes.
- It is plainly a fictional, age-appropriate second-grade child rather than a
  de-aged adult; no real-child claim, school identifier, printed date, or
  personal data appears.
- The child has a natural round face, monolid eyes, and the subject-left
  eyebrow-tail mole without pasted-adult artifacts. There is no adult earring,
  adult makeup, adult glasses, yellow hoodie, or T1 haircut.
- Red overalls, the large huangge tree, the grandmother-home context, and the
  calm yellow dog larger than the child all appear naturally. The dog's own
  left ear has one small healed missing notch, not a graphic injury.
- The image fulfils the promised childhood-photo scene without future leakage,
  logo/text/watermark, unsafe content, anatomy defects, or false historicity.
- A human accepts the exact verified hash with a concrete reason before it is
  treated as final media.

FAIL and stop on any age/identity discontinuity, adult-face scaling, missing
dog/tree/red-overalls anchor, wrong ear/mole orientation, unsafe child/animal
depiction, future detail, or hash/format mismatch. Retain the candidate and
the human reason; do not silently generate a replacement.
