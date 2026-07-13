---
name: audiocraft
description: Route Meta AudioCraft work to the relevant MusicGen, AudioGen, EnCodec, installation, optimization, or troubleshooting reference without loading the full umbrella guide.
license: MIT
metadata:
  hermes:
    category: mlops/models
    tags: [audio, musicgen, audiogen, encodec]
---

# AudioCraft

Load only the reference required by the request:

- Complete examples and workflows: `references/full-audiocraft-guide.md`
- Advanced conditioning and generation: `references/advanced-usage.md`
- Installation/runtime failures: `references/troubleshooting.md`

Before generation, identify the requested capability:

- MusicGen: text, melody, style, stereo, or continuation;
- AudioGen: sound effects and environmental audio;
- EnCodec: neural compression and tokenization.

Verify model availability, device, memory, sample rate, duration and output
format before a costly run. Keep generated artifacts outside the skill folder.
