from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pypinyin import pinyin, Style
import requests
import json
import re

app = FastAPI()

# Cho phép ESP32/Android gọi
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def translate_with_google(text, from_lang, to_lang):
    """Dùng Google Translate API miễn phí"""
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": from_lang,
            "tl": to_lang,
            "dt": "t",
            "q": text
        }
        response = requests.get(url, params=params)
        data = response.json()
        
        # Lấy bản dịch
        translated = "".join([item[0] for item in data[0]])
        
        # Lấy pinyin
        phonetic = ""
        if len(data) > 1 and data[1]:
            phonetic = data[1][0][0] if data[1] else ""
            
        return translated, phonetic
    except:
        return "Lỗi dịch", ""

@app.get("/translate")
def translate(
    text: str,
    from_lang: str = Query(..., regex="^(zh|vi)$"),
    to_lang: str = Query(..., regex="^(zh|vi)$")
):
    translated, phonetic = translate_with_google(text, from_lang, to_lang)
    
    # Nếu không có pinyin, tự sinh
    if not phonetic and to_lang == "zh":
        try:
            phonetic = ' '.join([p[0] for p in pinyin(translated, style=Style.TONE)])
        except:
            phonetic = ""
    
    return {
        "original": text,
        "translated": translated,
        "phonetic": phonetic
    }

# ✅ THÊM ENDPOINT NÀY CHO AUTO-TRANSLATE
@app.get("/auto-translate")
def auto_translate(
    text: str
):
    """Tự động phát hiện ngôn ngữ và dịch ngược lại"""
    # Phát hiện ngôn ngữ
    has_chinese = any("\u4e00" <= c <= "\u9fff" for c in text)
    has_vietnamese = any(c in "àáảãạâầấẩẫậăằắẳẵặêềếểễệđ" for c in text.lower())
    
    if has_chinese:
        from_lang, to_lang = "zh", "vi"
    elif has_vietnamese:
        from_lang, to_lang = "vi", "zh"
    else:
        # Nếu không xác định được, mặc định coi là tiếng Việt
        from_lang, to_lang = "vi", "zh"
    
    translated, phonetic = translate_with_google(text, from_lang, to_lang)
    
    if not phonetic and to_lang == "zh":
        try:
            phonetic = ' '.join([p[0] for p in pinyin(translated, style=Style.TONE)])
        except:
            phonetic = ""
    
    return {
        "original": text,
        "translated": translated,
        "phonetic": phonetic,
        "detected_from": from_lang
    }

@app.get("/")
def root():
    return {"message": "Server dịch Trung-Việt + Pinyin đang chạy", "status": "ok"}
