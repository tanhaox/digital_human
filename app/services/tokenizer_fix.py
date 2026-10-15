"""Auto-patch tokenizer.json + vocabulary files with Whisper special tokens.

This module patches the tokenizer.json AND vocabulary.txt/vocabulary.json used by
faster_whisper/CTranslate2. The issues:

1. tokenizer.json has empty "added_tokens" -> token_to_id("<|startoftranscript|>") = None
2. vocabulary.txt has special tokens shifted by +2 (IDs 50259-51867 instead of 50257-51865)
3. vocabulary.json has NO special tokens at all (all 51868 entries are regular subword tokens)

Root cause: The CTranslate2 model was quantized from the standard HuggingFace Whisper
model (vocab_size=51866, special tokens at 50257-51865), but the vocabulary files were
generated incorrectly during conversion. The model's embedding matrix at positions
50257-51865 already corresponds to the standard special tokens - fixing the vocabulary
files to match makes everything consistent.

This module is idempotent - it checks if the fix is already applied before patching.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

SNAPSHOT = Path(os.path.expanduser(
    "~/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3"
    "/snapshots/edaa852ec7e145841d8ffdb056a99866b5f0a478"
))
TOK_PATH = SNAPSHOT / "tokenizer.json"
VOCAB_TXT_PATH = SNAPSHOT / "vocabulary.txt"
VOCAB_JSON_PATH = SNAPSHOT / "vocabulary.json"

_FIX_APPLIED = False

# Standard Whisper special tokens (IDs 50257-51865, 1609 tokens total)
# 50257: endoftext, 50258: startoftranscript
# 50259-50358: 100 language tokens
# 50359: translate, 50360: transcribe, 50361: startoflm, 50362: startofprev
# 50363: nospeech, 50364: notimestamps
# 50365-51865: 1501 timing tokens (<|0.00|> to <|30.00|>)

LANGUAGES = [
    "en","zh","de","es","ru","ko","fr","ja","pt","tr",
    "pl","ca","nl","ar","sv","it","id","hi","fi","vi",
    "he","uk","el","ms","cs","ro","da","hu","ta","no",
    "th","ur","hr","bg","lt","la","mi","ml","cy","sk",
    "te","fa","lv","bn","sr","az","sl","kn","et","mk",
    "br","eu","is","hy","ne","mn","bs","kk","sq","sw",
    "gl","mr","pa","si","km","sn","yo","so","af","oc",
    "ka","be","tg","sd","gu","am","yi","lo","uz","fo",
    "ht","ps","tk","nn","mt","sa","lb","my","bo","tl",
    "mg","as","tt","haw","ln","ha","ba","jw","su","yue",
]


def _build_special_tokens() -> list[tuple[int, str]]:
    """Return the 1609 standard Whisper special tokens (ID, text)."""
    tokens: list[tuple[int, str]] = [
        (50257, "<|endoftext|>"),
        (50258, "<|startoftranscript|>"),
    ]
    for i, lang in enumerate(LANGUAGES):
        tokens.append((50259 + i, f"<|{lang}|>"))
    tokens += [
        (50359, "<|translate|>"),
        (50360, "<|transcribe|>"),
        (50361, "<|startoflm|>"),
        (50362, "<|startofprev|>"),
        (50363, "<|nospeech|>"),
        (50364, "<|notimestamps|>"),
    ]
    for i in range(1501):
        tokens.append((50365 + i, f"<|{i*0.02:.2f}|>"))
    return tokens


def is_fix_needed() -> bool:
    """Check if tokenizer.json needs the special-tokens fix."""
    if not TOK_PATH.exists():
        logger.warning("tokenizer.json not found at %s", TOK_PATH)
        return False
    try:
        with open(TOK_PATH, "r", encoding="utf-8") as f:
            head = f.read(500)
        if '"added_tokens": []' in head or '"added_tokens":[]' in head:
            return True
        return False
    except Exception:
        return False


def _is_vocab_fix_needed() -> bool:
    """Check if vocabulary.txt has the +1 shift bug."""
    if not VOCAB_TXT_PATH.exists():
        return False
    try:
        with open(VOCAB_TXT_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) < 50259:
            return False
        # Check if line 50257 (0-indexed) is endoftext (correct) or shifted
        line_50257 = lines[50257].strip()
        return line_50257 != "<|endoftext|>"
    except Exception:
        return False


def apply_tokenizer_fix() -> bool:
    """Apply the special-tokens fix to tokenizer.json + vocabulary files.

    Returns True if any fix was applied.
    """
    global _FIX_APPLIED
    if _FIX_APPLIED:
        return False

    fixed_any = False

    # 1. Fix tokenizer.json
    if is_fix_needed():
        fixed_any |= _fix_tokenizer_json()
    else:
        logger.info("tokenizer.json already has special tokens - skipping")

    # 2. Fix vocabulary.txt
    if _is_vocab_fix_needed():
        fixed_any |= _fix_vocabulary_txt()
    else:
        logger.info("vocabulary.txt already has correct special tokens - skipping")

    # 3. Fix vocabulary.json
    fixed_any |= _fix_vocabulary_json()

    _FIX_APPLIED = True
    return fixed_any


def _fix_tokenizer_json() -> bool:
    """Patch tokenizer.json with 1609 special tokens at correct IDs."""
    logger.info("Patching tokenizer.json with 1609 Whisper special tokens...")

    # Backup
    bak = TOK_PATH.with_suffix(".json.bak2")
    if not bak.exists():
        shutil.copy2(TOK_PATH, bak)
        logger.info("Backed up tokenizer.json to %s", bak)

    with open(TOK_PATH, "r", encoding="utf-8") as f:
        tok = json.load(f)

    logger.info("Existing: added_tokens=%d, vocab=%d",
                len(tok.get("added_tokens", [])), len(tok.get("model", {}).get("vocab", {})))

    special_tokens = _build_special_tokens()

    added = []
    for tid, content in special_tokens:
        added.append({
            "id": tid,
            "content": content,
            "single_word": False,
            "lstrip": False,
            "rstrip": False,
            "normalized": False,
            "special": True,
        })
        tok["model"]["vocab"][content] = tid

    tok["added_tokens"] = added

    with open(TOK_PATH, "w", encoding="utf-8") as f:
        json.dump(tok, f, ensure_ascii=False, indent=2)

    logger.info("Wrote %d added_tokens, vocab size now %d",
                len(added), len(tok["model"]["vocab"]))

    # Verify
    try:
        from tokenizers import Tokenizer
        t = Tokenizer.from_file(str(TOK_PATH))
        sot = t.token_to_id("<|startoftranscript|>")
        if sot is None:
            logger.error("FIX FAILED: <|startoftranscript|> still returns None!")
            return False
        logger.info("tokenizer.json verify: sot=%s, zh=%s, transcribe=%s, yue=%s",
                    sot, t.token_to_id("<|zh|>"), t.token_to_id("<|transcribe|>"),
                    t.token_to_id("<|yue|>"))
    except Exception as e:
        logger.warning("Post-fix tokenizer verify skipped: %s", e)

    logger.info("tokenizer.json fix applied successfully")
    return True


def _fix_vocabulary_txt() -> bool:
    """Fix vocabulary.txt: remove +2 shift, place special tokens at IDs 50257-51865.

    The broken vocabulary.txt has 51868 lines with special tokens at IDs 50259-51867.
    We keep the first 50257 regular tokens (IDs 0-50256), then write the 1609
    standard special tokens at IDs 50257-51865. Result: 51866 lines.
    """
    logger.info("Fixing vocabulary.txt (shifting special tokens from +2 to standard IDs)...")

    # Backup
    bak = VOCAB_TXT_PATH.with_suffix(".txt.bak2")
    if not bak.exists():
        shutil.copy2(VOCAB_TXT_PATH, bak)
        logger.info("Backed up vocabulary.txt to %s", bak)

    with open(VOCAB_TXT_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    original_count = len(lines)
    logger.info("vocabulary.txt: %d lines -> will become 51866 lines", original_count)

    # Keep first 50257 regular tokens (IDs 0-50256)
    fixed_lines = lines[:50257]

    # Add 1609 special tokens
    special_tokens = _build_special_tokens()
    for _tid, content in special_tokens:
        fixed_lines.append(content + "\n")

    # Verify length
    if len(fixed_lines) != 51866:
        logger.error("vocabulary.txt fix: expected 51866 lines, got %d", len(fixed_lines))
        return False

    with open(VOCAB_TXT_PATH, "w", encoding="utf-8") as f:
        f.writelines(fixed_lines)

    logger.info("vocabulary.txt fix applied: %d -> %d lines", original_count, len(fixed_lines))

    # Verify key positions
    with open(VOCAB_TXT_PATH, "r", encoding="utf-8") as f:
        verify_lines = f.readlines()
    checks = {
        50257: "<|endoftext|>",
        50258: "<|startoftranscript|>",
        50259: "<|en|>",
        50359: "<|translate|>",
        50360: "<|transcribe|>",
        50363: "<|nospeech|>",
        50364: "<|notimestamps|>",
        50365: "<|0.00|>",
    }
    for idx, expected in checks.items():
        actual = verify_lines[idx].strip()
        if actual != expected:
            logger.error("vocabulary.txt verify FAIL at line %d: expected '%s', got '%s'",
                        idx, expected, actual)
            return False
    logger.info("vocabulary.txt verify: all key positions correct")
    return True


def _fix_vocabulary_json() -> bool:
    """Fix vocabulary.json: replace entries 50257-51865 with standard special tokens.

    The broken vocabulary.json has 51868 entries, all regular subword tokens
    (no special tokens). We replace entries 50257-51865 with the correct special
    token strings and truncate to 51866 entries.
    """
    if not VOCAB_JSON_PATH.exists():
        logger.info("vocabulary.json not found - skipping")
        return False

    # Check if already fixed
    try:
        with open(VOCAB_JSON_PATH, "r", encoding="utf-8") as f:
            vocab = json.load(f)
        if len(vocab) >= 50259 and vocab[50258] == "<|startoftranscript|>":
            logger.info("vocabulary.json already has correct special tokens - skipping")
            return False
    except Exception:
        return False

    logger.info("Fixing vocabulary.json (adding special tokens at IDs 50257-51865)...")

    # Backup
    bak = VOCAB_JSON_PATH.with_suffix(".json.bak2")
    if not bak.exists():
        shutil.copy2(VOCAB_JSON_PATH, bak)
        logger.info("Backed up vocabulary.json to %s", bak)

    with open(VOCAB_JSON_PATH, "r", encoding="utf-8") as f:
        vocab = json.load(f)

    original_count = len(vocab)
    logger.info("vocabulary.json: %d entries", original_count)

    # Keep first 50257 entries, replace 50257-51865 with special tokens
    special_tokens = _build_special_tokens()
    fixed = vocab[:50257]
    for _tid, content in special_tokens:
        fixed.append(content)

    # Truncate to 51866 entries (standard Whisper vocab size)
    if len(fixed) > 51866:
        fixed = fixed[:51866]

    if len(fixed) != 51866:
        logger.error("vocabulary.json fix: expected 51866 entries, got %d", len(fixed))
        return False

    with open(VOCAB_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(fixed, f, ensure_ascii=False, indent=2)

    logger.info("vocabulary.json fix applied: %d -> %d entries", original_count, len(fixed))

    # Verify
    with open(VOCAB_JSON_PATH, "r", encoding="utf-8") as f:
        verify = json.load(f)
    checks = {
        50257: "<|endoftext|>",
        50258: "<|startoftranscript|>",
        50259: "<|en|>",
        50359: "<|translate|>",
        50360: "<|transcribe|>",
        50363: "<|nospeech|>",
        50364: "<|notimestamps|>",
        50365: "<|0.00|>",
    }
    for idx, expected in checks.items():
        actual = verify[idx]
        if actual != expected:
            logger.error("vocabulary.json verify FAIL at index %d: expected '%s', got '%s'",
                        idx, expected, actual)
            return False
    logger.info("vocabulary.json verify: all key positions correct")
    return True


# Auto-apply on import (idempotent)
try:
    apply_tokenizer_fix()
except Exception as _e:
    logger.warning("tokenizer_fix auto-apply failed: %s", _e)