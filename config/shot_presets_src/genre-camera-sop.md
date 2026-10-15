# Genre Camera-Movement SOP — Five Film Types

> Original methodology extension by jnMetaCode (MIT). Built on the
> 5-stage structure and the camera+lens aesthetic table from
> [../methodology.md](../methodology.md). The camera-movement
> conventions themselves are standard cinematography craft, not
> anyone's proprietary list — this file is our own structured,
> genre-organized phrasing of them.
>
> Best for: deciding **how the camera should move** once you know your
> genre. This is a Stage-4 (camera rules) companion — pick your genre
> block, paste the move phrasing into your prompt's camera section, and
> pair it with the suggested lens from Stage 3.
>
> **[中文版 →](./genre-camera-sop.zh.md)**

---

## How to use this with the 5-stage structure

A camera move is never the whole prompt — it plugs into **Stage 4**:

```
[Camera rules]
  Single-shot / Edited : {{one continuous take | edited across shots}}
  Move                 : {{pick from your genre block below}}
  Breathing            : "Handheld shot. Throughout, maintain an extremely
                          subtle, breath-like camera float to enhance presence."
```

Two hard rules carry over from the methodology and apply to **every**
genre below — don't drop them:

1. **Always keep the breathing line.** Even a "locked-off" horror shot
   wants the breath-float, or it reads as lifeless CG.
2. **Always state sound.** `Sound: No score. Production audio only.` —
   then enumerate the genre's signature ambient (see each block).

Each move is written as: **Move name (中文) — paste phrasing → the
effect it buys you.** The phrasing is model-agnostic; phrase tuning per
model is at the bottom.

---

## 1 · Horror / Thriller 恐怖 · 惊悚

**Camera philosophy**: the camera knows something the character doesn't.
Withhold, delay, let the frame breathe wrong.

**Lens pairing** (Stage 3): Sony Venice + Canon K-35 — low-light
high-contrast, crushed shadows, faint halation on practicals.

| Move (中文) | Paste phrasing | Effect |
|---|---|---|
| Slow creep-in (缓慢潜行推进) | "Extremely slow push-in toward the subject, almost imperceptible, no easing" | Dread accumulates; viewer leans in before the scare |
| Reveal-by-pan (横摇揭示) | "Slow lateral pan across an empty room, revealing the figure already standing in frame at the end of the move" | The threat was always there — worse than a jump-cut |
| Locked-off with off-screen sound (定镜+画外音) | "Locked-off wide on an empty doorway. Breath-float only. A sound comes from off-frame left" | Forces the viewer to police the edges of the frame |
| Low-angle stalk (低角度尾随) | "Low handheld follow behind the subject's heels, frame tilted slightly, lagging half a step" | POV of something following — never named, never shown |
| Snap-to-nothing (急摇落空) | "Fast whip-pan toward the noise, settling on empty space" | The classic false-alarm beat; primes the real scare |

**Do**: hold longer than comfortable; end shots one beat late.
**Don't**: light it evenly, or move the camera smoothly toward the
scare — smoothness kills dread.
**Ambient to enumerate**: floor creak, distant drip, a single far-off
door, the subject's own breathing.

---

## 2 · Action 动作

**Camera philosophy**: the camera is a participant, not an observer.
Weight, impact, and direction must read in one pass.

**Lens pairing** (Stage 3): simulated IMAX film camera + Panavision
C-series (35mm, f/4), motion blur added.

| Move (中文) | Paste phrasing | Effect |
|---|---|---|
| Impact-synced push (撞击同步推进) | "Camera snaps forward on the moment of impact, then settles" | Sells the hit's force; the frame feels the blow |
| Tracking sprint (跟随疾跑) | "Fast follow-tracking alongside the running subject, matching speed, foreground elements whipping past" | Velocity and stakes; foreground blur = real speed |
| Orbit on the standoff (对峙环绕) | "Slow clockwise orbit around two facing subjects, keeping both in frame" | Tension before the clash; geography stays legible |
| FPV plunge (第一视角俯冲) | "FPV free-tilting frame diving down past obstacles toward the target" | Kinetic descent; reads as the body's own motion |
| Low-angle hero rise (低角度仰拍起势) | "Low angle, slow tilt up the standing subject as they straighten" | Power and arrival without a victory pose |

**Do**: keep motion direction rhyming shot-to-shot (exit right → enter
left).
**Don't**: pile FX + blinding light + leap-into-sky on the final beat
(Rule 6) — end on weight, not fireworks.
**Ambient to enumerate**: cloth tension, metal scrape, impact thud,
low-frequency hum, debris settling.

---

## 3 · Romance 爱情

**Camera philosophy**: the camera is tender and patient. It lingers on
the small thing — a hand, a glance, the gap between two people closing.

**Lens pairing** (Stage 3): Kodak Vision3 250D 35mm + vintage primes
(Cooke S4) — warm, soft falloff, gentle grain. Or Canon EF 85mm f/1.2
for shallow intimate close-ups.

| Move (中文) | Paste phrasing | Effect |
|---|---|---|
| Breath-float close two-shot (呼吸感双人近景) | "Close two-shot, breath-float only, shallow depth of field, both faces soft" | Intimacy held still; the float keeps it alive, not posed |
| Slow drift-in to the hands (缓推至双手) | "Slow push-in from faces down to two hands almost touching" | Lets the gesture carry the emotion, no dialogue needed |
| Over-the-shoulder gaze (过肩对视) | "Long-lens over-the-shoulder, focus on the far face, foreground shoulder soft" | Captures mutual gaze in one shot — beats cutting back and forth |
| Lateral drift past a frame (横移穿过前景框) | "Slow lateral drift, a foreground element (curtain, doorway) passing through frame" | Voyeuristic tenderness; we glimpse a private moment |
| Pull-back to leave them (后拉退场) | "Slow pull-out, the two subjects growing smaller in the warm space" | The restrained ending — affection without a kiss-cam |

**Do**: let one whole shot be a single expression switch (Rule:
expression as the shot's endpoint).
**Don't**: add lens flares, floating hearts, or sappy slow-zoom on tears
— the negative-prompt prefab bans these for a reason.
**Ambient to enumerate**: soft breathing, fabric rustle, distant
street, a clock, rain on glass.

---

## 4 · Suspense / Mystery 悬疑

**Camera philosophy**: the camera frames information — what's withheld,
what's partially seen, who's watching whom.

**Lens pairing** (Stage 3): Kodak 35mm bleach-bypass — desaturated,
hard contrast, silver-cold shadows.

| Move (中文) | Paste phrasing | Effect |
|---|---|---|
| Rack focus reveal (变焦移焦揭示) | "Rack focus from the foreground object to the figure behind it" | Hands the viewer a clue in a single continuous beat |
| Surveillance high-angle (监视俯拍) | "High-angle locked-off looking down on the subject, slightly wide" | We're watching them; someone has the upper hand |
| Frame-within-frame peek (框中框窥视) | "Subject framed through a doorway / between objects, partially obscured" | Withholds — the viewer strains to see, suspicion grows |
| Slow reverse pull (缓慢后拉揭露) | "Slow pull-out revealing a second presence in the foreground we didn't know was there" | The reveal that recontextualizes the whole shot |
| Tilt-off-balance (倾斜失衡) | "Frame held at a slight dutch tilt, breath-float, never corrected" | Something is wrong but unnamed; unease without a cause |

**Do**: stage information in layers (foreground / midground /
background) and state what each layer is.
**Don't**: over-move — suspense lives in stillness and the slow reveal,
not energetic camerawork.
**Ambient to enumerate**: a ticking clock, muffled voices, footsteps in
another room, paper, a phone buzzing once.

---

## 5 · Epic / Sci-Fi 史诗 · 科幻

**Camera philosophy**: scale. The camera makes the subject small against
the world, then finds the human detail inside the vastness.

**Lens pairing** (Stage 3): simulated IMAX film camera + Panavision
C-series, anamorphic widescreen, volumetric haze.

| Move (中文) | Paste phrasing | Effect |
|---|---|---|
| Reveal crane-up (揭示升镜) | "Slow crane/boom up from the subject to reveal the full scale of the environment behind/above" | The world dwarfs the figure; awe in one continuous move |
| Slow majestic push (庄严缓推) | "Very slow push-in across a vast space toward the small distant subject" | Gravity and inevitability; the journey reads as fate |
| Lateral establishing drift (横移建立全景) | "Slow lateral drift across the wide landscape, layers passing at different speeds (parallax)" | Establishes geography and depth; parallax sells scale |
| Low-angle monument (低角度纪念碑式) | "Low angle looking up at the structure/figure against the sky, slow tilt up" | Monumentality — makes the subject feel historic |
| Hold-the-vastness (静守苍茫) | "Locked-off extreme wide, breath-float, subject occupying lower fifth of frame, held long" | The restrained epic ending — let the scale speak |

**Do**: end on the held wide, not a cut to action (Rule 6 at epic
scale).
**Don't**: cram fast cuts into the establishing — scale needs duration
to register.
**Ambient to enumerate**: wind across distance, low atmospheric hum,
distant rumble, sparse and reverberant — silence is part of scale.

---

## Quick-pick table (genre → default move + lens)

| Genre | Default opening move | Default closing move | Lens |
|---|---|---|---|
| Horror | Slow creep-in | Locked-off + off-screen sound | Sony Venice + K-35 |
| Action | Tracking sprint | Low-angle hero rise (no FX pile) | IMAX + Panavision C |
| Romance | Breath-float two-shot | Pull-back to leave them | Kodak 250D + Cooke S4 |
| Suspense | Frame-within-frame peek | Slow reverse pull reveal | Kodak bleach-bypass |
| Epic / Sci-Fi | Reveal crane-up | Hold-the-vastness | IMAX + Panavision C |

---

## Building a sequence (genre × multi-shot)

Don't pick one move — chain 3 from the same genre block so motion
rhymes. Example, **suspense 3-shot**:

```
Shot 1 — Surveillance high-angle, locked-off, subject enters a room below.
Shot 2 — Frame-within-frame peek through the doorway; a second figure
         is half-visible in the background layer.
Shot 3 — Slow reverse pull, revealing that figure was in the foreground
         all along. Hold one beat. Cut to black.
```

Keep one lens and one color grade across all three (don't mix tone
across a multi-shot edit — color drift wrecks the cut).

---

## Per-model phrasing notes

- **Seedance 2.0**: ZH or EN both fine; keep move phrasing concrete
  ("slow push-in", not "dramatic camera"); Doubao app locks 5s/10s.
- **Kling 2.x / 3.0**: best at smooth orbits and follow-tracking; one
  banned word rejects the whole prompt — keep phrasing clean.
- **Veo 3.1**: EN preferred; excels at crane/parallax establishing;
  8s/clip, extend in hops.
- **Sora 2**: fold any "no shaky cam" guardrail into the positive
  prompt — no negative field.
- **Runway Gen-4**: describe only the move you WANT; `no X` can summon
  X.

> The breathing line and `Sound: No score. Production audio only.` are
> not optional decoration — they're what separate these from generic
> "cinematic camera" prompts. Keep them in every genre.
