# Operator Profile Recognition Card

The Operator Profile Recognition Card is the identity record the Eagle carries for the person it is searching for. It lets the onboard computer distinguish **the** missing person from any other person it detects, without a network connection.

## The assumption

**The individual being searched for has a full face scan on file before they go missing.** We propose that the scan is taken at a point of contact that already exists: a backcountry permit, team or club registration, or the onboarding of operators and personnel who work in remote terrain. When a search begins, the card for that person is loaded onto the Eagle before launch.

Without a card, the Eagle still detects and reports every human it finds (heat, camera, sound); it cannot say whether that human is the person being sought. The card adds that answer.

## What the card contains

| Field | Source | Purpose |
|---|---|---|
| Face signatures | Full face scan (live, about 10 s, head turning) plus recent photos | 512-number ArcFace embeddings for face verification |
| Appearance description | Short text, e.g. "red jacket, blue pack" | Appearance matching when the face is too small or turned away |
| Appearance signatures | Person crops from the photos (optional) | Same, from images |
| Enrolment date and source | Recorded at enrolment | Audit; cards should be refreshed when they age |

The card holds signatures, not photographs. Signatures cannot be turned back into a picture of the face. It is one file on the device (`~/eagle/target_profile.npz`) and is never committed to this repository.

## Enrolment

Run on the Eagle's computer (`eagle-kit/apps/enroll_target.py`):

```bash
python apps/enroll_target.py --photos ~/target_photos --live --description "red jacket, blue pack" --out ~/eagle/target_profile.npz
python apps/enroll_target.py --verify --profile ~/eagle/target_profile.npz   # live face score
```

- **Photos**: iPhone HEIC, JPEG and PNG are read; duplicate files are counted once.
- **Same-person check**: every photo face is compared with the others; a face that does not match the group (for example a bystander in a group photo) is dropped and listed (`--same-person-cos`, default 0.30).
- **Live scan**: the person faces the Eagle's camera and turns their head slowly; sharp, well-lit frames across head angles are kept. Scanning on the same camera that will search gives the best match.
- `--add-to` appends further live scans to an existing card, for example in the final camera position.

## How the card is used during a search

1. The camera finds people (YOLO on the Hailo-8); each is tracked.
2. For each tracked person, face matching runs on a background thread so the video never waits: SCRFD finds the face, ArcFace computes its signature, and it is compared with the card.
3. Each person gets a match level:

| Level | Meaning |
|---|---|
| 0 | No match (or face not visible) |
| 1, 2 | Appearance match (clothing / appearance) |
| 3 | Face verified against the card |

4. **Identity only raises priority.** Fusion still reports every detected human; a missed match cannot suppress a report. The match level is carried in the existing class byte of the 48-byte encrypted FIX message, so rescuers see "person" versus "very likely the missing person".

On the demonstration screen a verified match shows as a green **TARGET** box and banner; other people show as **PERSON** and "not the target".

## Measured so far

Indoor test, one enrolled person, Camera Module 3 standard lens (measured, 2026-11-08):

- 42 photo files: 16 duplicates skipped, 26 faces kept; all 26 agreed with each other (lowest cosine 0.60 to the group).
- Live scan: 20 of 34 frames kept; mean cosine 0.46 to the photo faces (lowest 0.40). Unrelated people typically score below about 0.15 (published ArcFace behaviour, not yet measured on our own team).
- A person walking quickly across the view was missed at first; face matching was moved off the camera thread and is checked several times per second.

Not yet measured: score separation between the enrolled person and other people at 1, 3 and 6 m; outdoor light; night (infrared) faces.

## Limits

- **Range**: face verification needs a face about 40 pixels wide. With the standard lens that is roughly up to 13 m; with the Wide lens roughly 7 m. Beyond that, appearance matching applies, which is weaker.
- **Angle and blur**: side views and motion blur lower the score. A person who is lost, injured or signalling is usually still or facing the Eagle, which is the case the system is designed for.
- **Changed appearance**: different clothing defeats appearance matching; it does not affect face verification.
- **Not airtight on its own**: identity is supporting evidence, which is why it never decides alone whether a person is reported.

## Privacy and licences

- A card is created only for the person being searched for, with their prior consent at enrolment. It stays on the device and should be deleted after the mission.
- No photo of any person is committed to this repository.
- The InsightFace pretrained face models are licensed for non-commercial research use. A commercially licensed or self-trained model is required before any commercial deployment.
