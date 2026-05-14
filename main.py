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
    """Dùng MyMemory API (ổn định hơn)"""
    try:
        # Chuyển đổi mã ngôn ngữ
        lang_map = {
            "zh": "zh-CN",
            "vi": "vi"
        }
        
        url = "https://api.mymemory.translated.net/get"
        params = {
            "q": text,
            "langpair": f"{lang_map.get(from_lang, from_lang)}|{lang_map.get(to_lang, to_lang)}",
            "de": "a@b.com"  # Email dummy
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        # Lấy bản dịch
        translated = data.get("responseData", {}).get("translatedText", text)
        
        # Xóa thẻ HTML nếu có
        translated = re.sub(r'<[^>]+>', '', translated)
        
        # Xóa match rate nếu có (ví dụ: "hello (@14 @20)")
        translated = re.sub(r'\s*\(@\d+\s+@\d+\)', '', translated)
        
        # Lấy pinyin cho tiếng Trung
        phonetic = ""
        if to_lang == "zh" and translated and translated != text:
            try:
                phonetic = ' '.join([p[0] for p in pinyin(translated, style=Style.TONE)])
            except:
                phonetic = ""
        
        return translated, phonetic
        
    except Exception as e:
        print(f"Lỗi MyMemory: {e}")
        # Fallback: thử API khác
        return translate_fallback(text, from_lang, to_lang)

def translate_fallback(text, from_lang, to_lang):
    """Fallback dùng Lingva (API ngầm của Google Translate)"""
    try:
        url = "https://lingva.ml/api/v1/translate"
        params = {
            "sl": from_lang,
            "tl": to_lang,
            "q": text
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        translated = data.get("translation", text)
        
        phonetic = ""
        if to_lang == "zh" and translated:
            try:
                phonetic = ' '.join([p[0] for p in pinyin(translated, style=Style.TONE)])
            except:
                phonetic = ""
        
        return translated, phonetic
        
    except Exception as e:
        print(f"Lỗi fallback: {e}")
        return f"[Lỗi] {text}", ""

@app.get("/translate")
def translate(
    text: str,
    from_lang: str = Query(..., regex="^(zh|vi)$"),
    to_lang: str = Query(..., regex="^(zh|vi)$")
):
    translated, phonetic = translate_with_google(text, from_lang, to_lang)
    
    # Nếu vẫn không có pinyin, tự sinh
    if not phonetic and to_lang == "zh" and translated and translated != text:
        try:
            phonetic = ' '.join([p[0] for p in pinyin(translated, style=Style.TONE)])
        except:
            phonetic = ""
    
    return {
        "original": text,
        "translated": translated,
        "phonetic": phonetic
    }

@app.get("/test")
def test():
    """Endpoint test để kiểm tra API hoạt động"""
    return {
        "status": "ok",
        "message": "Server đang chạy, thử /translate?text=hello&from_lang=en&to_lang=vi"
    }

@app.get("/")
def root():
    return {"message": "Server dịch Trung-Việt + Pinyin đang chạy", "status": "ok"}
