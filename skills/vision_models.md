---
action_types: [analyze, write]
---

## Vision Models — Billedformat Viden

Forskellige LLM-modeller kræver forskellige formater til billeddata i API-kald.

### Formater

| Format | URL-værdi | Eksempel |
|--------|----------|---------|
| **raw_b64** | Kun base64-streng | `/9j/4AAQSkZJRg...` |
| **data_url** | `data:image/{mime};base64,{b64}` | `data:image/png;base64,iVBORw0K...` |

### Model-kompatibilitet (VERIFICERET via error logs)

| Model | Format | JSON-type | Fejlbesked når forkert | Note |
|-------|--------|-----------|------------------------|------|
| `gemma-4-26b-a4b` | **raw_b64** | `"image_url"` | `'url' field must be a base64 encoded image` | Verificeret 2026-05-19 |
| `gemma-4-e4b` | **raw_b64** | `"image_url"` | `'url' field must be a base64 encoded image` | Verificeret 2026-05-18 |
| `gemma` (alle) | **raw_b64** | `"image_url"` | Samme som ovenfor | Google modeller via LM Studio |
| `qwen` | **data_url** | `"image_url"` | - | OpenAI-standard format |
| `gpt` | **data_url** | `"image_url"` | - | OpenAI-standard format |
| `llava` | **data_url** | `"image_url"` | - | Standard vision format |
| Ukendt/andet | **data_url** | `"image_url"` | - | Default til OpenAI-standard |

**VIGTIGT:** LM Studio bruger OpenAI-kompatibel API → `"type": "image_url"` er **altid** påkrævet. Forskellen er KUN `url`-værdien:
- Gemma: `{"type": "image_url", "image_url": {"url": "<raw_base64>"}}`
- Qwen/GPT/Llava: `{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`

### JSON-struktur per model

Gemma (Google-format):
```json
{"type": "image", "url": "<raw_base64>"}
```

Qwen/GPT/Llava (OpenAI-format):
```json
{"type": "image_url", "image_url": {"url": "data:image/png;base64,<base64>"}}
```

Auto-håndteres af `_image_part()` i `llm_wrapper.py`.

### Billed-rækkefølge (Gemma 4 krav)

**Billeder SKAL komme FØR tekst** i prompten. Dette er dokumenteret i Google's model card som "Best Practice #4 — Modality order":

```json
// KORREKT for gemma 4:
{"role": "user", "content": [
    {"type": "image_url", "image_url": {"url": "<base64>"}},
    {"type": "text", "text": "Hvad ser du på billedet?"}
]}

// FEJL for gemma 4 — giver HTTP 400:
{"role": "user", "content": [
    {"type": "text", "text": "Hvad ser du på billedet?"},
    {"type": "image_url", "image_url": {"url": "<base64>"}}
]}
```

### Vision-detektion

`LMStudioWrapper.VISION_KEYWORDS` afgør om billeder overhovedet sendes til en model:

```python
VISION_KEYWORDS = {"vision", "vl", "llava", "gemini", "claude", "gpt-4o", "gemma", ...}
```

**Obs**: `gemma-4-26b-a4b` indeholder `gemma` → matcher → billeder sendes.  
Text-only modeller (f.eks. `qwen/qwen3.5-9b` uden vl) matcher IKKE → billeder udelades → undgår HTTP 400.

### Understøttede billedformater

`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp` — alle accepteres af `api_server.py`, `agent_core.py`, og frontend.

### Sådan fejlsøger du

1. Tjek `flask_output.log` for `✗ HTTP 400: {"error":"..."}` — viser præcis LM Studio's fejlbesked
2. Tjek `🕐 Startet:` i loggen — bekræfter at serveren kører med nyeste kode
3. `📦 llm=HH:MM:SS` — viser seneste ændringstid for `llm_wrapper.py`
4. `/api/version` — returnerer JSON med alle fil-timestamps + server-starttidspunkt

### Common mistakes (undgå disse)

| Fejl | Symptom | Løsning |
|------|---------|---------|
| `data_url` format til gemma | `'url' field must be a base64 encoded image` | Brug `raw_b64` i `IMAGE_FORMATS` |
| Tekst før billeder (gemma) | HTTP 400 uden detaljer | Billeder FØR tekst i content array |
| Text-only model med billeder | HTTP 400 | Model skal matche `VISION_KEYWORDS` |
| Glemt at genstarte server | Gamle fejl trods rettelser | Tjek `🕐 Startet:` i loggen |
