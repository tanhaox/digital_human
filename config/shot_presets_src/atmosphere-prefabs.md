# Atmosphere Prefabs — Reusable Visual-Quality Blocks

> Source: organized from Mx-Shell's publicly shared prompt materials
> and Douyin livestream content (March/May 2026). These are the
> reusable "atmosphere & quality" paragraph templates he uses across
> different works, categorized by genre.
>
> **How to use**: copy the matching block, paste it into your own
> prompt's atmosphere section.
>
> **[中文版 →](./atmosphere-prefabs.zh.md)**

---

## 1. Dark Tokusatsu / Battle-Damaged Aesthetic

```
Visual base: Anamorphic widescreen cinematic. Simulated IMAX film
             camera, paired with Panavision C-series lens (35mm focal,
             f/4 aperture).
Color & tone: Low-saturation grey-blue dominant. Shadow info compressed
              but detail preserved. Subtle edge bloom, moderate film grain.
Style core: Realistic dark battle-damaged aesthetic. Emphasize the
            fusion of biological texture and alien tech. Build an
            oppressive, heavy, live-action sensation of physical pain.
```

**Best for**: transformation sequences, mecha, post-apocalyptic heroes.

---

## 2. Hollywood Teal-and-Orange / Hard Sci-Fi Cyber

```
Visual style: Cyberpunk, hardcore sci-fi, cinematic, hyperreal,
              live-action shoot, wasteland industrial.
Color & tone: Hollywood teal-and-orange. Cold orange-grey and dark cyan
              creating an oppressive, cold, unknown atmosphere.
              High-intensity neon orange and cyan blue. Extreme
              cool-warm contrast is the core visual signature.
Lighting: Dark low-key, high-contrast. Shadow info compressed but
          detail preserved. Subtle edge bloom, moderate film grain.
          Overall visibility is extremely low.
Ambient light: Dense volumetric fog scatters faint cold light.
Light sources: Glowing props as dynamic light sources — produce strong
               rim light and lens flares.
Camera body: Sony Venice cinema camera with Canon K-35 series lenses.
             Handheld shoot, with subtle floating throughout.
```

**Best for**: cyberpunk, action combat, mech-vs-monster confrontation.

---

## 3. 1960s Atompunk / Retro-Future

```
Style core: Atompunk, cinematic, hyperreal, photoreal, live-action
            shoot, no game-CG feel.
Visual base: Anamorphic widescreen cinematic. IMAX film camera with
             Panavision C-series lens (motion blur added).
Color & tone: 1960s retro-sci-fi atompunk aesthetic. Retro warm-orange
              + sea-salt blue high-contrast palette. Film grain texture,
              retro wide-angle lens, low-saturation retro film LUT.
              Natural daylight illumination — large floor-to-ceiling
              windows let in soft side-light, casting gentle shadows on
              the terrazzo floor. Overall lighting transmits evenly,
              highlights don't blow out, shadow detail preserved,
              tonal transitions natural — creating a quiet, light-and-
              shadow ambience with a subtle retro soft-glow texture.
Reference: *Fallout* (the TV series) aesthetic + Pixar-style character
           motion control.
```

**Best for**: zombie + beach villa, retro hotel, mid-century reversal
of expectation (zombie inside luxury setting).

---

## 4. Shaw Brothers Hong Kong / Steampunk Wuxia

```
Shaw Brothers cinema style. Steampunk meets classical wuxia. 1980s
Hong Kong color martial-arts film style. Retro HK noir. Mechanical
wuxia. Epic composition. Deep shadows. Kodak 35mm vintage film,
bleach-bypass developed. Soft glow on highlights. Rich texture.
Live-action shoot, photorealistic cinematic feel.

Color & light: Non-naturalistic dramatic artificial lighting. Cold
               dominant palette — slate-grey, silver-white, dark red.
               Candlelight warmth reflects on the face. Hard cinematic
               light, clear chiaroscuro on face and metal.
```

**Best for**: classical-East-meets-steampunk, wuxia + machinery.

---

## 5. Heavy Mech / Industrial Epic

```
Camera body: Simulated IMAX film camera with Panavision C-series lens.
             Frame rate 24, simulated motion blur.
Color & tone: Hollywood teal-and-orange, low saturation. Shadow info
              compressed but detail preserved. Subtle edge bloom,
              moderate film grain.
Style core: Heavy mech cinema aesthetic. Photorealistic, sci-fi epic,
            heavy mecha, heavy-industry mechanical aesthetic fused
            with advanced tech — retain raw mechanical feel and weight.
Core VFX: Hollywood-grade visual effects.
```

**Best for**: humanoid mecha, mechanical transformations, scaled-up
confrontations.

---

## 6. Commercial Portrait (for image-gen reference photos)

```
Style: "portrait photography". Reference uploaded photo. Generate a
       hyperreal commercial portrait of a {{age}} {{ethnicity}}
       {{gender}}, close shot. Features and face shape 100% match
       reference. Preserve minor facial blemishes. Light scattered
       loose strands of hair for a relaxed feel. No accessories.
Clothing: {{deep black t-shirt / ...}}
Background: Soft white blurred gradient.
Light: Soft side-light to bring out facial dimensionality.
Skin: Warm healthy tone with natural fine luster. Detail enhancement
      at 100%, disable over-smoothing.
Color: Refined warm tones. Edges sharp and clean — no blur, jaggies,
       or ghosting.
Camera: Canon EF 85mm f/1.2.
Aspect: 3:4.
```

**Best for**: converting a "raw selfie" into a high-quality reference
photo that survives Seedance's face moderation.

---

## 7. Product Shot (weapons / armor / gear)

```
Generate a {{category}} product photo. Grey background, preserve
metallic texture. {{Part A color}}. {{Part B}} carries
{{tech-feel / vintage / motif}} engraving.

Subject: {{Specific description: shape / material / features}}
Composition: {{Diagonal / Centered / Rule-of-thirds}}, with
             {{low-angle / level / high-angle}} POV.
Primary palette: {{Background}} + {{Subject}} + {{High-contrast accent}}
Light: Side cold light highlights metal texture. {{Self-luminous part}}
       creates warm-tone highlights. Background is weak ambient light.
       Overall: high-contrast, dark-key atmosphere.
Style: Photoreal, strong metal materiality, fine light-and-shadow,
       light film grain for atmosphere.
Materials: {{Specific surface details 1}} / {{texture 2}} /
           {{glow effect}} all need precise rendering.
Feel: Cold, hardcore, futuristic cyberpunk weapon aesthetic.

--ar 16:9 --v 6 --style raw
```

**Best for**: weapons, armor, wrist-controllers, single-prop image
generation via MJ/GPT before feeding into video.

---

## 8. Three-View (character / mech)

```
Reference uploaded person photo. Design a {{style}} {{category}}
for them. Produce front, side, and back three-views. Maintain
subject consistency across angles. Each angle shows full body.
Photoreal cinematic style.

Design: {{biomimetic / humanoid robot / ...}} — high-tech, super
        detailed layering, precise mechanical structure, {{material}},
        {{paint scheme}}. Surface has fine scratches.

Lighting: Enhance ambient occlusion + contact shadows in crevices.
          Improve global illumination, real highlight transitions,
          HDR cinematic. Filmic HDR / ACES-like color mapping. Light
          film grain, minimal bloom, no stylization. Cinematic
          lighting.

--ar 16:9 --v 4 --style raw
```

**Best for**: producing armor/mech three-view references via Nanobanana
/ MJ before feeding to video generation.

---

## How to mix and match

These prefabs can be combined. Examples:

- **Dark Tokusatsu + Heavy Mech** = battle-damaged transformation
  followed by pull-out revealing apocalyptic battlefield
- **Atompunk + Shaw Brothers HK** = 60s retro palette + Eastern
  martial-arts characters
- **Hollywood Teal-Orange + Heavy Mech** = night sea battle + mech
  slamming into ocean

**Critical**: keep the *Color & tone* block consistent across all
shots in a multi-shot edit. Otherwise editing them together will
produce severe color drift.
