# Translation Server v2.0 — Trung ↔ Việt + Pinyin

FastAPI backend deploy trên Render, phục vụ Android app dịch tài liệu Trung-Việt.

## Tính năng mới (v2.0)

| Tính năng | v1 | v2 |
|---|---|---|
| Dịch văn bản dài | ❌ Lỗi nếu > vài trăm ký tự | ✅ Tự động chia đoạn ≤ 1000 ký tự |
| Cache | ❌ Không có | ✅ File cache 7 ngày |
| Fallback provider | ❌ Chỉ Google | ✅ Google → MyMemory → LibreTranslate |
| Pinyin | ⚠️ Không chính xác theo ngữ cảnh | ✅ Dùng `heteronym=False` cho đúng âm |
| Endpoint tài liệu | ❌ Không có | ✅ `POST /translate-doc` |
| Validation | ❌ Không có | ✅ Giới hạn 10.000 ký tự, kiểm tra lỗi rõ |
| Health check | ❌ Không có | ✅ `GET /health` |

## Endpoints

### `GET /translate`
Dịch có chỉ định ngôn ngữ.
```
GET /translate?text=你好&from_lang=zh&to_lang=vi
```
Response:
```json
{
  "original": "你好",
  "translated": "Xin chào",
  "phonetic": "nǐ hǎo",
  "provider": "google",
  "chunks": 1,
  "from_cache": false
}
```

### `GET /auto-translate`
Tự động phát hiện ngôn ngữ.
```
GET /auto-translate?text=Xin chào bạn
```

### `POST /translate-doc`
**Dùng cho tài liệu dài từ Android.** Nhận JSON body, hỗ trợ đến 10.000 ký tự.
```json
POST /translate-doc
{
  "text": "văn bản dài...",
  "from_lang": "vi",
  "to_lang": "zh"
}
```

### `GET /pinyin`
Chỉ lấy pinyin, không dịch.
```
GET /pinyin?text=学习中文很有趣
```

### `GET /health`
Kiểm tra server đang chạy (dùng cho Render health check).

### `DELETE /cache`
Xóa cache (khi debug).

## Deploy lên Render

### Environment Variables (tùy chọn)
| Biến | Giá trị | Mục đích |
|---|---|---|
| `LIBRE_TRANSLATE_URL` | `https://your-libretranslate.com` | Provider thứ 3 nếu muốn |

### render.yaml đã cấu hình sẵn health check path `/health`.

## Gọi từ Android (Kotlin/Retrofit)

```kotlin
// Tài liệu dài dùng POST
@POST("translate-doc")
suspend fun translateDoc(@Body body: TranslateRequest): TranslateResponse

data class TranslateRequest(
    val text: String,
    val from_lang: String,
    val to_lang: String
)

// Câu ngắn dùng GET
@GET("auto-translate")
suspend fun autoTranslate(@Query("text") text: String): TranslateResponse
```

## Kiến trúc fallback

```
Request
  └─► Cache hit? ──► Trả ngay (0ms)
        │
        ▼ Cache miss
      Google Translate
        │ Lỗi/chặn?
        ▼
      MyMemory API (5000 ký tự/ngày miễn phí)
        │ Lỗi?
        ▼
      LibreTranslate (nếu cấu hình)
        │
        ▼
      Lưu cache 7 ngày
```
