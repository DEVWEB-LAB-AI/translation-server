from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pypinyin import pinyin, Style
import requests
import hashlib
import json
import re
import os
import io
import time
import psycopg2
import psycopg2.extras
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

# ─── LIFESPAN ────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    init_db()
    yield

app = FastAPI(title="Dịch Trung-Việt API", version="3.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── PATHS (cache + audio vẫn dùng /tmp — không cần persistent) ──────────────
CACHE_DIR = Path("/tmp/translate_cache")
AUDIO_DIR = Path("/tmp/translate_audio")
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
CACHE_TTL = 60 * 60 * 24 * 7  # 7 ngày

for d in [CACHE_DIR, AUDIO_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── POSTGRESQL (Supabase) ────────────────────────────────────────────────────
# Đặt DATABASE_URL trong Render Environment Variables
# Dạng: postgresql://postgres:PASSWORD@db.xxxx.supabase.co:5432/postgres
DATABASE_URL = os.getenv("DATABASE_URL", "")

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL chưa được cấu hình")
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    return conn

def init_db():
    """Tạo bảng history nếu chưa có — chạy khi server khởi động"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS history (
                        id         SERIAL PRIMARY KEY,
                        original   TEXT NOT NULL,
                        translated TEXT NOT NULL,
                        phonetic   TEXT,
                        from_lang  TEXT NOT NULL,
                        to_lang    TEXT NOT NULL,
                        provider   TEXT,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
            conn.commit()
        print("✅ DB init OK")
    except Exception as e:
        print(f"⚠️ DB init error: {e}")

def save_history(original, translated, phonetic, from_lang, to_lang, provider):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO history (original, translated, phonetic, from_lang, to_lang, provider)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (original, translated, phonetic or "", from_lang, to_lang, provider))
            conn.commit()
    except Exception as e:
        print(f"⚠️ save_history error: {e}")

# ─── CACHE (file /tmp — tăng tốc, không cần persistent) ─────────────────────
def cache_key(text, from_lang, to_lang):
    return hashlib.md5(f"{text}|{from_lang}|{to_lang}".encode()).hexdigest()

def cache_get(key):
    f = CACHE_DIR / key
    if f.exists():
        try:
            data = json.loads(f.read_text())
            if time.time() - data["ts"] < CACHE_TTL:
                return data["value"]
        except Exception:
            pass
    return None

def cache_set(key, value):
    try:
        (CACHE_DIR / key).write_text(json.dumps({"ts": time.time(), "value": value}))
    except Exception:
        pass

# ─── CHUNKER ──────────────────────────────────────────────────────────────────
def chunk_text(text, max_len=1000):
    if len(text) <= max_len:
        return [text]
    chunks, current = [], ""
    for s in re.split(r'(?<=[。！？.!?\n])', text):
        if len(current) + len(s) <= max_len:
            current += s
        else:
            if current:
                chunks.append(current)
            if len(s) > max_len:
                for i in range(0, len(s), max_len):
                    chunks.append(s[i:i+max_len])
            else:
                current = s
    if current:
        chunks.append(current)
    return chunks

# ─── TRANSLATE PROVIDERS ──────────────────────────────────────────────────────
def translate_google(text, from_lang, to_lang):
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": from_lang, "tl": to_lang, "dt": "t", "q": text},
            timeout=8
        )
        r.raise_for_status()
        data = r.json()
        return "".join([i[0] for i in data[0] if i[0]]), ""
    except Exception:
        return "", ""

def translate_mymemory(text, from_lang, to_lang):
    try:
        lmap = {"zh": "zh-CN", "vi": "vi-VN"}
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": f"{lmap.get(from_lang)}|{lmap.get(to_lang)}"},
            timeout=8
        )
        r.raise_for_status()
        data = r.json()
        if data.get("responseStatus") == 200:
            return data["responseData"]["translatedText"], ""
    except Exception:
        pass
    return "", ""

LIBRE_URL = os.getenv("LIBRE_TRANSLATE_URL", "")

def translate_libre(text, from_lang, to_lang):
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

def translate_with_fallback(text, from_lang, to_lang):
    for fn, name in [
        (translate_google, "google"),
        (translate_mymemory, "mymemory"),
        (translate_libre, "libre")
    ]:
        result, phonetic = fn(text, from_lang, to_lang)
        if result:
            return result, phonetic, name
    return "Không thể dịch lúc này", "", "none"

# ─── PINYIN ───────────────────────────────────────────────────────────────────
def get_pinyin(text):
    try:
        return ' '.join([p[0] for p in pinyin(text, style=Style.TONE, heteronym=False)])
    except Exception:
        return ""

# ─── DETECT LANGUAGE ──────────────────────────────────────────────────────────
def detect_language(text):
    if any("\u4e00" <= c <= "\u9fff" for c in text):
        return "zh"
    if any(c in "àáảãạâầấẩẫậăằắẳẵặêềếểễệđ" for c in text.lower()):
        return "vi"
    return "vi"

# ─── CORE TRANSLATE ───────────────────────────────────────────────────────────
def do_translate(text, from_lang, to_lang):
    text = text.strip()
    if not text:
        raise HTTPException(400, "text không được rỗng")

    ck = cache_key(text, from_lang, to_lang)
    cached = cache_get(ck)
    if cached:
        cached["from_cache"] = True
        return cached

    chunks = chunk_text(text)
    parts, provider_used = [], "unknown"
    for chunk in chunks:
        translated, _, provider = translate_with_fallback(chunk, from_lang, to_lang)
        provider_used = provider
        parts.append(translated)

    full_translated = " ".join(parts)
    phonetic = get_pinyin(full_translated) if to_lang == "zh" else ""

    result = {
        "original": text,
        "translated": full_translated,
        "phonetic": phonetic,
        "provider": provider_used,
        "chunks": len(chunks),
        "from_cache": False,
    }
    cache_set(ck, result)
    save_history(text, full_translated, phonetic, from_lang, to_lang, provider_used)
    return result

# ─── EDGE TTS ─────────────────────────────────────────────────────────────────
TTS_VOICES = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "vi": "vi-VN-HoaiMyNeural",
}

async def _generate_tts(text: str, lang: str) -> bytes:
    import edge_tts
    voice = TTS_VOICES.get(lang, "zh-CN-XiaoxiaoNeural")
    buf = io.BytesIO()
    communicate = edge_tts.Communicate(text, voice)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()

# ─── EXPORT PDF ───────────────────────────────────────────────────────────────
def _has_cjk_font():
    return Path(FONT_PATH).exists()

def export_pdf(rows: list) -> bytes:
    from fpdf import FPDF, XPos, YPos

    class HistoryPDF(FPDF):
        def header(self):
            font = "NotoSansCJK" if _has_cjk_font() else "Helvetica"
            self.set_font(font, size=9)
            self.set_text_color(150, 150, 150)
            self.cell(0, 7, "Lịch sử dịch — Translation History", align="C",
                      new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        def footer(self):
            self.set_y(-14)
            font = "NotoSansCJK" if _has_cjk_font() else "Helvetica"
            self.set_font(font, size=8)
            self.set_text_color(150, 150, 150)
            self.cell(0, 10, f"Trang {self.page_no()}", align="C")

    pdf = HistoryPDF()
    if _has_cjk_font():
        pdf.add_font("NotoSansCJK", fname=FONT_PATH)
    font = "NotoSansCJK" if _has_cjk_font() else "Helvetica"

    pdf.add_page()
    pdf.set_font(font, size=15)
    pdf.set_text_color(30, 80, 160)
    pdf.cell(0, 12, "Lịch sử phiên dịch Trung - Việt", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(font, size=9)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 6,
             f"Xuất lúc: {datetime.now().strftime('%d/%m/%Y %H:%M')}  •  Tổng: {len(rows)} mục",
             align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    COL = [60, 60, 45, 25]
    HEADERS = ["Văn bản gốc", "Bản dịch", "Phiên âm (Pinyin)", "Thời gian"]
    pdf.set_font(font, size=10)
    pdf.set_fill_color(30, 80, 160)
    pdf.set_text_color(255, 255, 255)
    for w, h in zip(COL, HEADERS):
        pdf.cell(w, 9, h, border=1, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.ln(9)

    pdf.set_font(font, size=9)
    for i, row in enumerate(rows):
        pdf.set_fill_color(245, 248, 255) if i % 2 == 0 else pdf.set_fill_color(255, 255, 255)
        pdf.set_text_color(30, 30, 30)
        if pdf.get_y() + 8 > pdf.h - 20:
            pdf.add_page()
        pdf.multi_cell(COL[0], 8, str(row.get("original",""))[:80], border=1,
                       fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.multi_cell(COL[1], 8, str(row.get("translated",""))[:80], border=1,
                       fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.multi_cell(COL[2], 8, str(row.get("phonetic",""))[:50], border=1,
                       fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
        ts = str(row.get("created_at",""))[:16]
        pdf.multi_cell(COL[3], 8, ts, border=1,
                       fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())

# ─── EXPORT DOCX ──────────────────────────────────────────────────────────────
def export_docx(rows: list) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    doc = Document()
    for section in doc.sections:
        section.left_margin = section.right_margin = Cm(2)
        section.top_margin = section.bottom_margin = Cm(2)

    title = doc.add_heading("Lịch sử phiên dịch Trung - Việt", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_paragraph(
        f"Xuất lúc: {datetime.now().strftime('%d/%m/%Y %H:%M')}  •  Tổng: {len(rows)} mục"
    )
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.runs[0].font.size = Pt(9)
    sub.runs[0].font.color.rgb = RGBColor(0x78, 0x78, 0x78)
    doc.add_paragraph()

    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    HDR = ["Văn bản gốc", "Bản dịch", "Phiên âm (Pinyin)", "Thời gian"]
    for i, h in enumerate(HDR):
        cell = table.rows[0].cells[i]
        cell.text = h
        run = cell.paragraphs[0].runs[0]
        run.bold = True
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:fill"), "1E50A0")
        shd.set(qn("w:val"), "clear")
        tc_pr.append(shd)

    for i, row in enumerate(rows):
        cells = table.add_row().cells
        cells[0].text = str(row.get("original", ""))
        cells[1].text = str(row.get("translated", ""))
        cells[2].text = str(row.get("phonetic", ""))
        cells[3].text = str(row.get("created_at", ""))[:16]
        fill = "EEF2FF" if i % 2 == 0 else "FFFFFF"
        for cell in cells:
            tc_pr = cell._tc.get_or_add_tcPr()
            shd = OxmlElement("w:shd")
            shd.set(qn("w:fill"), fill)
            shd.set(qn("w:val"), "clear")
            tc_pr.append(shd)
            for para in cell.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(9)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
def root():
    return {"message": "Translation Server v3.1 — Trung↔Việt + TTS + Supabase", "status": "ok"}

@app.get("/health")
def health():
    db_ok = False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        db_ok = True
    except Exception:
        pass
    return {"status": "ok", "version": "3.1", "db": "supabase", "db_connected": db_ok}

# ─── TRANSLATE ────────────────────────────────────────────────────────────────
@app.get("/translate")
def translate(
    text:      str = Query(..., min_length=1, max_length=10000),
    from_lang: str = Query(..., pattern="^(zh|vi)$"),
    to_lang:   str = Query(..., pattern="^(zh|vi)$"),
):
    if from_lang == to_lang:
        raise HTTPException(400, "from_lang và to_lang phải khác nhau")
    return do_translate(text, from_lang, to_lang)

@app.get("/auto-translate")
def auto_translate(text: str = Query(..., min_length=1, max_length=10000)):
    from_lang = detect_language(text)
    to_lang   = "vi" if from_lang == "zh" else "zh"
    result    = do_translate(text, from_lang, to_lang)
    result["detected_from"] = from_lang
    return result

@app.post("/translate-doc")
def translate_doc(body: dict):
    text      = body.get("text", "").strip()
    from_lang = body.get("from_lang", "vi")
    to_lang   = body.get("to_lang",   "zh")
    if not text:
        raise HTTPException(400, "Thiếu trường 'text'")
    if from_lang not in ("zh", "vi") or to_lang not in ("zh", "vi"):
        raise HTTPException(400, "Ngôn ngữ không hợp lệ")
    if from_lang == to_lang:
        raise HTTPException(400, "from_lang và to_lang phải khác nhau")
    return do_translate(text, from_lang, to_lang)

@app.get("/pinyin")
def get_pinyin_only(text: str = Query(..., min_length=1, max_length=5000)):
    return {"original": text, "phonetic": get_pinyin(text)}

# ─── TTS ──────────────────────────────────────────────────────────────────────
@app.get("/tts")
async def tts(
    text: str = Query(..., min_length=1, max_length=500),
    lang: str = Query(..., pattern="^(zh|vi)$"),
):
    audio_key  = hashlib.md5(f"{text}|{lang}".encode()).hexdigest() + ".mp3"
    audio_file = AUDIO_DIR / audio_key
    if not audio_file.exists():
        try:
            audio_data = await _generate_tts(text, lang)
            if not audio_data:
                raise HTTPException(500, "Không tạo được audio")
            audio_file.write_bytes(audio_data)
        except Exception as e:
            raise HTTPException(500, f"TTS lỗi: {str(e)}")
    return StreamingResponse(
        io.BytesIO(audio_file.read_bytes()),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'attachment; filename="tts_{lang}.mp3"'}
    )

# ─── HISTORY ──────────────────────────────────────────────────────────────────
@app.get("/history")
def get_history(
    limit:  int = Query(50,  ge=1, le=500),
    offset: int = Query(0,   ge=0),
    lang:   str = Query("",  description="Lọc: zh hoặc vi"),
    search: str = Query("",  description="Tìm trong văn bản gốc"),
):
    conditions, params = [], []
    if lang in ("zh", "vi"):
        conditions.append("from_lang = %s")
        params.append(lang)
    if search:
        conditions.append("(original ILIKE %s OR translated ILIKE %s)")
        params += [f"%{search}%", f"%{search}%"]

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM history {where}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT * FROM history {where} ORDER BY id DESC LIMIT %s OFFSET %s",
                params + [limit, offset]
            )
            rows = cur.fetchall()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [dict(r) for r in rows]
    }

@app.delete("/history/{item_id}")
def delete_history_item(item_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM history WHERE id = %s", [item_id])
        conn.commit()
    return {"deleted": item_id}

@app.delete("/history")
def clear_history():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM history")
            count = cur.fetchone()[0]
            cur.execute("DELETE FROM history")
        conn.commit()
    return {"deleted": count}

# ─── EXPORT ───────────────────────────────────────────────────────────────────
def _fetch_rows_for_export(limit: int, search: str) -> list:
    conditions, params = [], []
    if search:
        conditions.append("(original ILIKE %s OR translated ILIKE %s)")
        params += [f"%{search}%", f"%{search}%"]
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT * FROM history {where} ORDER BY id DESC LIMIT %s",
                params + [limit]
            )
            return [dict(r) for r in cur.fetchall()]

@app.get("/export/pdf")
def export_history_pdf(
    limit:  int = Query(200, ge=1, le=1000),
    search: str = Query(""),
):
    rows = _fetch_rows_for_export(limit, search)
    if not rows:
        raise HTTPException(404, "Không có dữ liệu để xuất")
    filename = f"lich_su_dich_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        io.BytesIO(export_pdf(rows)),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/export/docx")
def export_history_docx(
    limit:  int = Query(200, ge=1, le=1000),
    search: str = Query(""),
):
    rows = _fetch_rows_for_export(limit, search)
    if not rows:
        raise HTTPException(404, "Không có dữ liệu để xuất")
    filename = f"lich_su_dich_{datetime.now().strftime('%Y%m%d_%H%M')}.docx"
    return StreamingResponse(
        io.BytesIO(export_docx(rows)),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# ─── CACHE ────────────────────────────────────────────────────────────────────
@app.delete("/cache")
def clear_cache():
    count = sum(1 for f in CACHE_DIR.glob("*") if f.unlink() is None)
    return {"deleted": count}
