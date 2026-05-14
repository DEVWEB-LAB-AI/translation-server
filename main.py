from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pypinyin import pinyin, Style
import requests
import json

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
        phonetic = ' '.join([p[0] for p in pinyin(translated, style=Style.TONE)])
    
    return {
        "original": text,
        "translated": translated,
        "phonetic": phonetic
    }

@app.get("/")
def root():
    return {"message": "Server dịch Trung-Việt + Pinyin đang chạy", "status": "ok"}
