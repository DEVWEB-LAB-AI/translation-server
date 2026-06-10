from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pypinyin import pinyin, Style
import requests
import hashlib
import json
import re
import os
import time
from pathlib import Path

app = FastAPI(title="Dịch Trung-Việt API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Cache đơn giản dùng file (không cần Redis) ───────────────────────────────
CACHE_DIR = Path("/tmp/translate_cache")
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL = 60 * 60 * 24 * 7  # 7 ngày

def cache_key(text: str, from_lang: str, to_lang: str) -> str:
    return hashlib.md5(f"{text}|{from_lang}|{to_lang}".encode()).hexdigest()

def cache_get(key: str):
    f = CACHE_DIR / key
    if f.exists():
        data = json.loads(f.read_text())
        if time.time() - data["ts"] < CACHE_TTL:
            return data["value"]
    return None

def cache_set(key: str, value: dict):
    f = CACHE_DIR / key
    f.write_text(json.dumps({"ts": time.time(), "value": value}))


# ─── Chunker: chia văn bản dài thành đoạn ≤ 1000 ký tự ───────────────────────
def chunk_text(text: str, max_len: int = 1000) -> list[str]:
    """Chia theo dấu câu, không cắt giữa từ."""
    if len(text) <= max_len:
        return [text]
    
    chunks, current = [], ""
    # Chia theo câu trước
    sentences = re.split(r'(?<=[。！？.!?\n])', text)
    for s in sentences:
        if len(current) + len(s) <= max_len:
            current += s
        else:
            if current:
                chunks.append(current)
            # Nếu câu đơn lẻ vẫn quá dài, cắt cứng
            if len(s) > max_len:
                for i in range(0, len(s), max_len):
                    chunks.append(s[i:i+max_len])
            else:
                current = s
    if current:
        chunks.append(current)
    return chunks


# ─── Provider 1: Google Translate (endpoint gtx) ──────────────────────────────
def translate_google(text: str, from_lang: str, to_lang: str) -> tuple[str, str]:
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": from_lang, "tl": to_lang, "dt": "t", "q": text}
        r = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
        translated = "".join([item[0] for item in data[0] if item[0]])
        phonetic = ""
        if len(data) > 1 and data[1]:
            phonetic = data[1][0][0] if data[1] else ""
        return translated, phonetic
    except Exception:
        return "", ""


# ─── Provider 2: MyMemory (miễn phí, 5000 ký tự/ngày) ────────────────────────
def translate_mymemory(text: str, from_lang: str, to_lang: str) -> tuple[str, str]:
    try:
        lang_map = {"zh": "zh-CN", "vi": "vi-VN"}
        langpair = f"{lang_map.get(from_lang, from_lang)}|{lang_map.get(to_lang, to_lang)}"
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": langpair},
            timeout=8
        )
        r.raise_for_status()
        data = r.json()
        if data.get("responseStatus") == 200:
            return data["responseData"]["translatedText"], ""
        return "", ""
    except Exception:
        return "", ""


# ─── Provider 3: LibreTranslate (self-hosted hoặc public) ─────────────────────
LIBRE_URL = os.getenv("LIBRE_TRANSLATE_URL", "")  # đặt trong Render env nếu có

def translate_libre(text: str, from_lang: str, to_lang: str) -> tuple[str, str]:
    if not LIBRE_URL:
        return "", ""
    try:
        r = requests.post(
            f"{LIBRE_URL}/translate",
            json={"q": text, "source": from_lang, "target": to_lang, "format": "text"},
            timeout=10
        )
        r.raise_for_status()
        return r.json().get("translatedText", ""), ""
    except Exception:
        return "", ""


# ─── Fallback cascade ─────────────────────────────────────────────────────────
def translate_with_fallback(text: str, from_lang: str, to_lang: str) -> tuple[str, str, str]:
    """Thử lần lượt: Google → MyMemory → LibreTranslate"""
    for provider_fn, name in [
        (translate_google, "google"),
        (translate_mymemory, "mymemory"),
        (translate_libre, "libre"),
    ]:
        result, phonetic = provider_fn(text, from_lang, to_lang)
        if result:
            return result, phonetic, name
    return "Không thể dịch lúc này", "", "none"


# ─── Pinyin chính xác theo ngữ cảnh ──────────────────────────────────────────
def get_pinyin(chinese_text: str) -> str:
    try:
        # TONE3 cho phép xử lý đa âm tốt hơn TONE
        result = pinyin(chinese_text, style=Style.TONE, heteronym=False)
        return ' '.join([p[0] for p in result])
    except Exception:
        return ""


# ─── Hàm dịch chính (có cache + chunking) ────────────────────────────────────
def do_translate(text: str, from_lang: str, to_lang: str) -> dict:
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text không được rỗng")

    ck = cache_key(text, from_lang, to_lang)
    cached = cache_get(ck)
    if cached:
        cached["from_cache"] = True
        return cached

    chunks = chunk_text(text)
    translated_parts, provider_used = [], "unknown"

    for chunk in chunks:
        translated, phonetic, provider = translate_with_fallback(chunk, from_lang, to_lang)
        provider_used = provider
        translated_parts.append(translated)

    full_translated = " ".join(translated_parts)

    # Sinh pinyin nếu kết quả là tiếng Trung
    if to_lang == "zh":
        phonetic = get_pinyin(full_translated)
    else:
        phonetic = ""

    result = {
        "original": text,
        "translated": full_translated,
        "phonetic": phonetic,
        "provider": provider_used,
        "chunks": len(chunks),
        "from_cache": False,
    }
    cache_set(ck, result)
    return result


# ─── Phát hiện ngôn ngữ ───────────────────────────────────────────────────────
def detect_language(text: str) -> str:
    has_chinese = any("\u4e00" <= c <= "\u9fff" for c in text)
    if has_chinese:
        return "zh"
    has_vietnamese = any(c in "àáảãạâầấẩẫậăằắẳẵặêềếểễệđ" for c in text.lower())
    return "vi" if has_vietnamese else "vi"


# ─── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"message": "Translation Server v2.0 — Trung↔Việt + Pinyin", "status": "ok"}


@app.get("/health")
def health():
    """Dùng để monitor trên Render"""
    return {"status": "ok", "cache_dir": str(CACHE_DIR), "version": "2.0"}


@app.get("/translate")
def translate(
    text: str = Query(..., min_length=1, max_length=10000),
    from_lang: str = Query(..., regex="^(zh|vi)$"),
    to_lang: str = Query(..., regex="^(zh|vi)$"),
):
    """Dịch có chỉ định ngôn ngữ nguồn và đích."""
    if from_lang == to_lang:
        raise HTTPException(status_code=400, detail="from_lang và to_lang phải khác nhau")
    return do_translate(text, from_lang, to_lang)


@app.get("/auto-translate")
def auto_translate(
    text: str = Query(..., min_length=1, max_length=10000),
):
    """Tự động phát hiện ngôn ngữ và dịch ngược lại."""
    from_lang = detect_language(text)
    to_lang = "vi" if from_lang == "zh" else "zh"
    result = do_translate(text, from_lang, to_lang)
    result["detected_from"] = from_lang
    return result


@app.post("/translate-doc")
def translate_doc(body: dict):
    """
    Dịch tài liệu dài — nhận JSON: {"text": "...", "from_lang": "vi", "to_lang": "zh"}
    Phù hợp cho văn bản > 1000 ký tự từ Android.
    """
    text = body.get("text", "").strip()
    from_lang = body.get("from_lang", "vi")
    to_lang = body.get("to_lang", "zh")

    if not text:
        raise HTTPException(status_code=400, detail="Thiếu trường 'text'")
    if from_lang not in ("zh", "vi") or to_lang not in ("zh", "vi"):
        raise HTTPException(status_code=400, detail="Ngôn ngữ không hợp lệ")
    if from_lang == to_lang:
        raise HTTPException(status_code=400, detail="from_lang và to_lang phải khác nhau")

    return do_translate(text, from_lang, to_lang)


@app.get("/pinyin")
def get_pinyin_only(text: str = Query(..., min_length=1, max_length=5000)):
    """Chỉ lấy pinyin cho chuỗi tiếng Trung, không cần dịch."""
    phonetic = get_pinyin(text)
    return {"original": text, "phonetic": phonetic}


@app.delete("/cache")
def clear_cache():
    """Xóa toàn bộ cache (dùng khi debug)."""
    count = 0
    for f in CACHE_DIR.glob("*"):
        f.unlink()
        count += 1
    return {"deleted": count}
