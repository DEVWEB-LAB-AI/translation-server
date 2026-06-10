# Translation Server v3.0 — Trung ↔ Việt + TTS + Lịch sử

FastAPI backend deploy trên Render, phục vụ Android app dịch tài liệu Trung-Việt.

## Tính năng

| Tính năng | v2 | v3 |
|---|---|---|
| Dịch văn bản dài (chunking) | ✅ | ✅ |
| Cache 7 ngày | ✅ | ✅ |
| Fallback 3 provider | ✅ | ✅ |
| Pinyin chính xác | ✅ | ✅ |
| **Phát âm TTS (Edge TTS)** | ❌ | ✅ Giọng tự nhiên Microsoft |
| **Lịch sử dịch (SQLite)** | ❌ | ✅ Tìm kiếm, phân trang |
| **Xuất PDF** | ❌ | ✅ Bảng song ngữ có màu |
| **Xuất Word (.docx)** | ❌ | ✅ Bảng song ngữ có màu |

> ⚠️ Render free tier dùng ephemeral storage — lịch sử và audio cache sẽ mất khi server restart.
> Nâng lên Render Starter ($7/tháng) nếu cần giữ data lâu dài.

---

## Endpoints

### Dịch
```
GET  /translate?text=你好&from_lang=zh&to_lang=vi
GET  /auto-translate?text=Xin chào bạn
POST /translate-doc        body: {"text":"...","from_lang":"vi","to_lang":"zh"}
GET  /pinyin?text=学习中文
```

### Phát âm (TTS)
```
GET /tts?text=你好世界&lang=zh
GET /tts?text=Xin chào&lang=vi
```
Trả về file MP3. Android dùng `MediaPlayer` hoặc download bytes.

**Giọng đọc:**
- Tiếng Trung: `zh-CN-XiaoxiaoNeural` (giọng nữ, tự nhiên nhất)
- Tiếng Việt: `vi-VN-HoaiMyNeural` (giọng nữ)

### Lịch sử
```
GET    /history?limit=50&offset=0&lang=zh&search=xin chào
DELETE /history/{id}      # Xóa 1 mục
DELETE /history            # Xóa tất cả
```

### Xuất file
```
GET /export/pdf?limit=200&search=
GET /export/docx?limit=200&search=
```

### Tiện ích
```
GET    /health
DELETE /cache
```

---

## Gọi từ Android (Kotlin)

```kotlin
// Dịch
@GET("auto-translate")
suspend fun autoTranslate(@Query("text") text: String): TranslateResponse

// Phát âm — tải MP3 về rồi play
@GET("tts")
@Streaming
suspend fun getTTS(
    @Query("text") text: String,
    @Query("lang") lang: String
): ResponseBody

// Lịch sử
@GET("history")
suspend fun getHistory(
    @Query("limit") limit: Int = 50,
    @Query("offset") offset: Int = 0,
    @Query("search") search: String = ""
): HistoryResponse

// Xuất file
@GET("export/pdf")
@Streaming
suspend fun exportPDF(@Query("limit") limit: Int = 200): ResponseBody
```

**Ví dụ play TTS trên Android:**
```kotlin
val url = "https://your-server.onrender.com/tts?text=你好&lang=zh"
val mediaPlayer = MediaPlayer().apply {
    setDataSource(url)
    setAudioAttributes(AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_MEDIA)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build())
    prepareAsync()
    setOnPreparedListener { start() }
}
```

---

## Deploy Render

`render.yaml` đã cấu hình tự động cài font CJK (`fonts-noto-cjk`) để PDF hiển thị đúng chữ Trung/Việt.

### Environment Variables (tùy chọn)
| Biến | Mục đích |
|---|---|
| `LIBRE_TRANSLATE_URL` | Provider dịch thứ 3 |

---

## Kiến trúc

```
Android Request
  │
  ├─► /tts        → Edge TTS (Microsoft Neural) → MP3 stream
  │
  ├─► /translate  → Cache? → Google/MyMemory/Libre → SQLite history
  │
  ├─► /history    → SQLite query (filter/search/paginate)
  │
  └─► /export/pdf  → SQLite → fpdf2 → PDF download
      /export/docx → SQLite → python-docx → DOCX download
```
